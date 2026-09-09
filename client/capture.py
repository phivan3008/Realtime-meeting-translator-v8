"""WASAPI loopback capture.

ADR-0013. The rule this module exists to obey is AUD-110: never block the audio
callback on network I/O, UI rendering, log writing or disk I/O. PortAudio owns
the callback thread, and if it stalls, samples are lost at the source where no
downstream buffering can recover them.

So :meth:`LoopbackCapture._callback` does exactly three things: push bytes into
the ring, record the status flags PortAudio handed it, and return. It does not
resample - the converter is stateful and its cost per chunk is uneven, which
belongs on the consumer side. It does not log, allocate or lock anything a slow
consumer could be holding.

Everything else here is setup, teardown, and the counters that make a stall
visible (AUD-090).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from client.devices import DeviceUnavailableError, LoopbackDevice, validate_device
from client.ringbuffer import CaptureRing, DroppedAudio

#: How much audio PortAudio hands over per callback. Independent of the wire
#: frame duration (ADR-0014 D36): this is a device-side buffer size, and the
#: framing stage regroups the stream into wire frames regardless.
DEFAULT_CALLBACK_FRAMES = 1024

#: A callback that has not fired for this long while capturing means the device
#: has stalled. AUD-090 requires detecting starvation; the value is a starting
#: point and is `benchmark_required` like the rest of the timing constants.
DEFAULT_STARVATION_TIMEOUT_S = 2.0


@dataclass(slots=True)
class CaptureStats:
    """What the callback observed. AUD-090, AUD-210."""

    callbacks: int = 0
    device_frames: int = 0
    #: PortAudio's own overflow flag: samples were lost before we saw them.
    #: Distinct from ring overflow, which is our own consumer falling behind.
    input_overflows: int = 0
    #: Underflow on an input stream is unusual but PortAudio can report it.
    input_underflows: int = 0
    #: Chunks the ring evicted because the consumer fell behind.
    ring_dropped_chunks: int = 0
    ring_dropped_device_frames: int = 0
    last_callback_monotonic: float = 0.0
    errors: int = 0


class CaptureError(RuntimeError):
    """Capture could not start, or stopped unexpectedly."""


@dataclass(slots=True)
class LoopbackCapture:
    """Opens a WASAPI loopback stream and feeds a :class:`CaptureRing`.

    The PyAudio instance is injected rather than constructed, so a caller that
    already opened one for device enumeration reuses it, and so the lifetime of
    the host API is owned by whoever created it.
    """

    audio: Any
    device: LoopbackDevice
    ring: CaptureRing
    callback_frames: int = DEFAULT_CALLBACK_FRAMES
    starvation_timeout_s: float = DEFAULT_STARVATION_TIMEOUT_S
    clock: Callable[[], float] = time.monotonic

    #: Invoked from the callback thread when the ring evicts audio. Must be
    #: cheap and non-blocking: it runs inside the callback. The client wires it
    #: to a counter, and the *consumer* turns the loss into an `audio.gap`.
    on_dropped: Callable[[DroppedAudio], None] | None = None

    stats: CaptureStats = field(default_factory=CaptureStats)
    _stream: Any = field(default=None, init=False, repr=False)
    _running: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _failure: str | None = field(default=None, init=False)

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Open and start the stream.

        Raises:
            CaptureError: if the device cannot be opened.
        """
        if self._stream is not None:
            raise CaptureError("capture is already running")

        validate_device(self.device)

        try:
            import pyaudiowpatch as pyaudio

            self._stream = self.audio.open(
                format=pyaudio.paInt16,
                channels=self.device.channels,
                rate=self.device.sample_rate_hz,
                input=True,
                input_device_index=self.device.index,
                frames_per_buffer=self.callback_frames,
                stream_callback=self._callback,
            )
        except Exception as exc:
            self.stats.errors += 1
            raise CaptureError(
                f"could not open loopback device {self.device.describe()}: {exc}"
            ) from exc

        self.stats.last_callback_monotonic = self.clock()
        self._running.set()

    def stop(self) -> None:
        """Stop and close the stream. Safe to call more than once."""
        self._running.clear()
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop_stream()
        finally:
            stream.close()

    def __enter__(self) -> LoopbackCapture:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- the callback --------------------------------------------------------

    def _callback(
        self,
        in_data: bytes | None,
        frame_count: int,
        time_info: dict[str, float],
        status_flags: int,
    ) -> tuple[bytes | None, int]:
        """PortAudio's callback. Runs on PortAudio's thread.

        Three operations, no more (AUD-110). Any exception is swallowed into a
        counter rather than propagated: an exception crossing back into C would
        take the stream down, and losing the whole capture because one chunk
        went wrong is a worse outcome than losing the chunk.
        """
        import pyaudiowpatch as pyaudio

        try:
            self.stats.callbacks += 1
            self.stats.device_frames += frame_count
            self.stats.last_callback_monotonic = self.clock()

            if status_flags & pyaudio.paInputOverflow:
                self.stats.input_overflows += 1
            if status_flags & pyaudio.paInputUnderflow:
                self.stats.input_underflows += 1

            if in_data:
                dropped = self.ring.push(in_data)
                if dropped.occurred:
                    self.stats.ring_dropped_chunks += dropped.chunks
                    self.stats.ring_dropped_device_frames += dropped.device_frames
                    if self.on_dropped is not None:
                        self.on_dropped(dropped)
        except Exception as exc:  # pragma: no cover - defensive, see docstring
            self.stats.errors += 1
            self._failure = f"{type(exc).__name__}: {exc}"

        return (None, pyaudio.paContinue if self._running.is_set() else pyaudio.paComplete)

    # -- health --------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._running.is_set() and self._stream is not None

    @property
    def failure(self) -> str | None:
        """The last exception the callback swallowed, if any."""
        return self._failure

    def seconds_since_last_callback(self) -> float:
        return self.clock() - self.stats.last_callback_monotonic

    def check_starvation(self) -> str | None:
        """Report a stalled device, or None if healthy.

        AUD-090. Called from the consumer side on a timer, never from the
        callback - a callback that is not firing cannot notice that it is not
        firing.
        """
        if not self.is_running:
            return None
        elapsed = self.seconds_since_last_callback()
        if elapsed > self.starvation_timeout_s:
            return (
                f"no audio callback for {elapsed:.1f} s "
                f"(threshold {self.starvation_timeout_s:.1f} s); "
                "the capture device may have been removed or reconfigured"
            )
        return None


def open_default_capture(
    audio: Any,
    *,
    capacity_seconds: float,
    preferred_device_name: str | None = None,
    callback_frames: int = DEFAULT_CALLBACK_FRAMES,
) -> tuple[LoopbackCapture, LoopbackDevice]:
    """Convenience: resolve a device and build a capture around it.

    Raises:
        DeviceUnavailableError: when no usable loopback device exists.
    """
    from client.devices import select_device

    selection = select_device(audio, preferred_name=preferred_device_name)
    device = selection.device

    if device.channels < 1:
        raise DeviceUnavailableError(f"{device.name!r} reports no input channels")

    ring = CaptureRing(
        capacity_seconds=capacity_seconds,
        sample_rate_hz=device.sample_rate_hz,
        channels=device.channels,
    )
    return LoopbackCapture(
        audio=audio, device=device, ring=ring, callback_frames=callback_frames
    ), device
