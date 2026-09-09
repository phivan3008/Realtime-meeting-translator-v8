"""Turn a stream of canonical audio into stamped protocol frames.

`requirements.md` Section 8.1 requires monotonically increasing sequence numbers
and monotonic capture timestamps (AUD-080), and forbids repeated WAV headers
(AUD-070) - this emits raw PCM behind a binary header, never a container.

The part worth reading carefully is :meth:`FrameBuilder.skip`. When audio is
lost - a ring overflow, a device glitch - the canonical timeline must still
advance by exactly the lost duration (Section 25.1, PROT-180: "the timeline
shall not be compressed to hide missing audio"). Skipping is therefore an
explicit operation that moves the cursor without emitting frames, and the caller
turns its return value into an ``audio.gap``. Simply not producing frames would
splice the two sides of the hole together, and every timestamp after it would be
wrong by the size of the gap.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from protocol.frame import FrameFlags, FrameHeader, encode_frame
from protocol.limits import BYTES_PER_SAMPLE, CANONICAL_SAMPLE_RATE_HZ, MAX_SAMPLES_PER_FRAME
from protocol.timeline import SampleSpan
from protocol.version import PROTOCOL_MAJOR

#: Both are implemented and selected by configuration. 20 ms is the default
#: pending the Section 9.2 benchmark (ADR-0014 D36): latency is the product's
#: value, and the competing argument was worth 1.4 kB/s.
FRAME_MS_LOW_LATENCY = 20
FRAME_MS_LOW_OVERHEAD = 40
DEFAULT_FRAME_MS = FRAME_MS_LOW_LATENCY


def samples_per_frame(frame_ms: int, sample_rate_hz: int = CANONICAL_SAMPLE_RATE_HZ) -> int:
    """Samples in one frame of the given duration.

    Raises:
        ValueError: if the duration does not divide into a whole number of
            samples, or exceeds the per-frame limit. A fractional frame would
            make ``start_sample`` drift away from wall time by a fraction of a
            sample per frame, which over a 30-minute meeting is a real offset.
    """
    if frame_ms <= 0:
        raise ValueError(f"frame_ms must be positive, got {frame_ms}")
    total = frame_ms * sample_rate_hz
    if total % 1000:
        raise ValueError(f"{frame_ms} ms at {sample_rate_hz} Hz is not a whole number of samples")
    count = total // 1000
    if count > MAX_SAMPLES_PER_FRAME:
        raise ValueError(
            f"{frame_ms} ms is {count} samples, over the {MAX_SAMPLES_PER_FRAME} limit"
        )
    return count


@dataclass(frozen=True, slots=True)
class WireFrame:
    """One encoded frame, ready to send."""

    header: FrameHeader
    payload: bytes
    encoded: bytes

    @property
    def start_sample(self) -> int:
        return self.header.start_sample

    @property
    def end_sample(self) -> int:
        return self.header.end_sample


@dataclass(slots=True)
class FramingStats:
    frames_emitted: int = 0
    samples_emitted: int = 0
    samples_skipped: int = 0
    skips: int = 0


@dataclass(slots=True)
class FrameBuilder:
    """Accumulates canonical audio and emits fixed-size stamped frames.

    One instance per stream. A new ``stream_ordinal`` means a new builder, and
    the sample cursor continues across a resume but restarts on a discontinuity
    (PROT-190) - which the caller expresses by passing ``start_sample``.
    """

    stream_ordinal: int
    frame_ms: int = DEFAULT_FRAME_MS
    start_sample: int = 0
    sample_rate_hz: int = CANONICAL_SAMPLE_RATE_HZ
    #: Injected so tests are deterministic and so the source of monotonic time is
    #: a decision rather than an accident. Section 25.1: this is an observability
    #: field and never an ordering authority.
    clock_ns: Callable[[], int] = time.monotonic_ns

    _samples_per_frame: int = field(init=False)
    _bytes_per_frame: int = field(init=False)
    _pending: bytearray = field(default_factory=bytearray, init=False, repr=False)
    _next_sample: int = field(init=False)
    _sequence: int = field(default=0, init=False)
    _origin_ns: int | None = field(default=None, init=False, repr=False)
    stats: FramingStats = field(default_factory=FramingStats)

    def __post_init__(self) -> None:
        self._samples_per_frame = samples_per_frame(self.frame_ms, self.sample_rate_hz)
        self._bytes_per_frame = self._samples_per_frame * BYTES_PER_SAMPLE
        self._next_sample = self.start_sample

    @property
    def samples_per_frame(self) -> int:
        return self._samples_per_frame

    @property
    def next_sample(self) -> int:
        """Where the next emitted frame will begin on the canonical timeline."""
        return self._next_sample

    @property
    def next_sequence(self) -> int:
        return self._sequence

    @property
    def pending_samples(self) -> int:
        """Buffered audio not yet forming a whole frame."""
        return len(self._pending) // BYTES_PER_SAMPLE

    def _elapsed_ns(self) -> int:
        """Monotonic nanoseconds since the first frame of this stream.

        Relative rather than absolute so the value is meaningful across machines
        and small enough to read, while staying monotonic within the stream.
        """
        now = self.clock_ns()
        if self._origin_ns is None:
            self._origin_ns = now
        return now - self._origin_ns

    def push(self, payload: bytes) -> list[WireFrame]:
        """Add canonical audio, returning every whole frame it completes.

        Args:
            payload: mono ``pcm_s16le`` at 16 kHz.

        Raises:
            ValueError: if the payload is not a whole number of samples.
        """
        if len(payload) % BYTES_PER_SAMPLE:
            raise ValueError(
                f"{len(payload)} bytes is not a whole number of {BYTES_PER_SAMPLE}-byte samples"
            )
        self._pending.extend(payload)
        return list(self._drain())

    def _drain(self) -> Iterator[WireFrame]:
        while len(self._pending) >= self._bytes_per_frame:
            chunk = bytes(self._pending[: self._bytes_per_frame])
            del self._pending[: self._bytes_per_frame]
            yield self._emit(chunk, flags=FrameFlags.NONE)

    def _emit(self, payload: bytes, *, flags: FrameFlags) -> WireFrame:
        header = FrameHeader(
            protocol_major=PROTOCOL_MAJOR,
            stream_ordinal=self.stream_ordinal,
            sequence=self._sequence,
            start_sample=self._next_sample,
            capture_monotonic_ns=self._elapsed_ns(),
            sample_count=len(payload) // BYTES_PER_SAMPLE,
            flags=flags,
        )
        encoded = encode_frame(header, payload)

        self._sequence += 1
        self._next_sample += header.sample_count
        self.stats.frames_emitted += 1
        self.stats.samples_emitted += header.sample_count

        return WireFrame(header=header, payload=payload, encoded=encoded)

    def skip(self, samples: int) -> SampleSpan:
        """Advance the timeline over lost audio without emitting frames.

        The caller turns the returned span into an ``audio.gap`` (PROT-320). The
        cursor moves so that every subsequent frame carries a ``start_sample``
        reflecting when its audio was actually captured, rather than being
        shifted earlier by the size of the hole.

        Any partial frame still buffered is discarded: it is adjacent to lost
        audio and joining it to what follows would fabricate continuity across
        the gap.

        Raises:
            ValueError: for a non-positive skip.
        """
        if samples <= 0:
            raise ValueError(f"skip requires a positive sample count, got {samples}")

        self._pending.clear()
        span = SampleSpan(
            start_sample=self._next_sample,
            end_sample=self._next_sample + samples,
        )
        self._next_sample += samples
        self.stats.samples_skipped += samples
        self.stats.skips += 1
        return span

    def flush(self, *, pad: bool = False) -> list[WireFrame]:
        """Emit whatever remains at end of stream.

        Args:
            pad: when True, zero-pad the final partial frame to full size.
                Default False, so the last frame is short rather than padded -
                a padded frame would claim samples that were never captured, and
                the header's ``sample_count`` is what makes a short final frame
                unambiguous.
        """
        frames = list(self._drain())
        if not self._pending:
            return frames

        remainder = bytes(self._pending)
        self._pending.clear()
        if pad:
            remainder = remainder.ljust(self._bytes_per_frame, b"\x00")
        frames.append(self._emit(remainder, flags=FrameFlags.NONE))
        return frames

    def emit_synthetic(self, samples: int) -> list[WireFrame]:
        """Emit marked silence filling a small gap (ADR-0009 D15).

        Every frame produced carries ``SYNTHETIC_AUDIO``, so a raw capture is
        self-describing about inserted samples without the event log beside it.
        Downstream, these samples are excluded from every quality ratio and never
        update a speaker profile.

        Raises:
            ValueError: for a non-positive count.
        """
        if samples <= 0:
            raise ValueError(f"emit_synthetic requires a positive count, got {samples}")

        frames: list[WireFrame] = []
        remaining = samples
        while remaining > 0:
            count = min(remaining, self._samples_per_frame)
            frames.append(
                self._emit(b"\x00" * (count * BYTES_PER_SAMPLE), flags=FrameFlags.SYNTHETIC_AUDIO)
            )
            remaining -= count
        return frames
