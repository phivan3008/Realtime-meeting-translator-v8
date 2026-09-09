"""The bounded buffer between the audio callback and everything else.

ADR-0013. `requirements.md` Section 8.1 requires a bounded ring buffer with an
explicit overflow policy (AUD-100), and forbids blocking the audio callback on
network I/O, UI rendering, log writing or disk I/O (AUD-110). Section 8.4 adds
that audio is never *silently* reordered, duplicated or discarded (AUD-200) -
the word carrying the weight there is *silently*.

So this buffer does three things and no more:

- accepts a chunk from the callback in constant time;
- when full, drops the **oldest unsent** chunk and **counts what was lost**;
- reports the loss in device frames, so the frame builder can advance the
  canonical timeline by exactly that much and the gap becomes visible as an
  ``audio.gap`` rather than a silent splice (PROT-180).

Dropping the newest chunk instead would discard what is being said now in favour
of what was said a moment ago, which is backwards for a live meeting.

**Locking.** A lock is held, but only across pointer arithmetic on a deque -
never across allocation, I/O or any call that can block. AUD-110 forbids
blocking the callback on slow work; two microseconds of contention with a
consumer doing the same is not that. What the callback must never do is the
resampling, which is why that happens on the consumer side.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DroppedAudio:
    """What overflow cost, in units the timeline understands."""

    chunks: int
    device_frames: int

    @property
    def occurred(self) -> bool:
        return self.device_frames > 0


@dataclass(slots=True)
class RingStats:
    """Counters surfaced as client diagnostics (AUD-210)."""

    chunks_accepted: int = 0
    chunks_dropped: int = 0
    device_frames_accepted: int = 0
    device_frames_dropped: int = 0
    overflow_events: int = 0
    peak_bytes: int = 0


class CaptureRing:
    """A bounded FIFO of raw device-format audio chunks.

    Single producer (the PortAudio callback), single consumer (the asyncio
    thread). Capacity is expressed in **seconds of media time** rather than
    bytes, because the bound exists to cover a duration - how long the consumer
    may fall behind - and because media time is the canonical unit
    (Section 25.1, ADR-0013 D37).
    """

    def __init__(
        self,
        *,
        capacity_seconds: float,
        sample_rate_hz: int,
        channels: int,
        bytes_per_sample: int = 2,
    ) -> None:
        if capacity_seconds <= 0:
            raise ValueError(f"capacity_seconds must be positive, got {capacity_seconds}")
        if sample_rate_hz <= 0 or channels < 1 or bytes_per_sample < 1:
            raise ValueError("sample rate, channels and sample width must all be positive")

        self.capacity_seconds = capacity_seconds
        self.sample_rate_hz = sample_rate_hz
        self.channels = channels
        self.bytes_per_frame = channels * bytes_per_sample
        self.capacity_bytes = int(capacity_seconds * sample_rate_hz) * self.bytes_per_frame

        self._chunks: deque[bytes] = deque()
        self._buffered_bytes = 0
        self._lock = threading.Lock()
        self._stats = RingStats()

    # -- producer side, called from the audio callback -----------------------

    def push(self, chunk: bytes) -> DroppedAudio:
        """Accept one chunk, dropping the oldest if that would exceed capacity.

        Returns what was dropped so the caller can account for it. The callback
        does not act on the return value beyond recording it; the consumer
        translates it into a timeline skip.

        Constant time: at most a few deque operations under the lock.
        """
        if not chunk:
            return DroppedAudio(chunks=0, device_frames=0)
        if len(chunk) % self.bytes_per_frame:
            raise ValueError(
                f"chunk of {len(chunk)} bytes is not a whole number of "
                f"{self.bytes_per_frame}-byte device frames"
            )

        dropped_chunks = 0
        dropped_bytes = 0

        with self._lock:
            self._chunks.append(chunk)
            self._buffered_bytes += len(chunk)
            self._stats.chunks_accepted += 1
            self._stats.device_frames_accepted += len(chunk) // self.bytes_per_frame

            while self._buffered_bytes > self.capacity_bytes and self._chunks:
                oldest = self._chunks.popleft()
                self._buffered_bytes -= len(oldest)
                dropped_chunks += 1
                dropped_bytes += len(oldest)

            if dropped_chunks:
                self._stats.overflow_events += 1
                self._stats.chunks_dropped += dropped_chunks
                self._stats.device_frames_dropped += dropped_bytes // self.bytes_per_frame

            self._stats.peak_bytes = max(self._stats.peak_bytes, self._buffered_bytes)

        return DroppedAudio(
            chunks=dropped_chunks,
            device_frames=dropped_bytes // self.bytes_per_frame,
        )

    # -- consumer side -------------------------------------------------------

    def pop_all(self) -> bytes:
        """Take everything buffered, in order.

        Returns ``b""`` when empty. Joining outside the lock would risk another
        push interleaving, so the join happens inside - it is a memory copy of
        at most the capacity, with no I/O.
        """
        with self._lock:
            if not self._chunks:
                return b""
            chunks = list(self._chunks)
            self._chunks.clear()
            self._buffered_bytes = 0
        return b"".join(chunks)

    def pop_one(self) -> bytes | None:
        """Take the oldest chunk, or None when empty."""
        with self._lock:
            if not self._chunks:
                return None
            chunk = self._chunks.popleft()
            self._buffered_bytes -= len(chunk)
            return chunk

    # -- observation ---------------------------------------------------------

    @property
    def buffered_bytes(self) -> int:
        with self._lock:
            return self._buffered_bytes

    @property
    def buffered_seconds(self) -> float:
        return self.buffered_bytes / self.bytes_per_frame / self.sample_rate_hz

    @property
    def fill_fraction(self) -> float:
        return self.buffered_bytes / self.capacity_bytes if self.capacity_bytes else 0.0

    def stats(self) -> RingStats:
        """A snapshot. Copied so a reader cannot observe a half-updated struct."""
        with self._lock:
            return RingStats(
                chunks_accepted=self._stats.chunks_accepted,
                chunks_dropped=self._stats.chunks_dropped,
                device_frames_accepted=self._stats.device_frames_accepted,
                device_frames_dropped=self._stats.device_frames_dropped,
                overflow_events=self._stats.overflow_events,
                peak_bytes=self._stats.peak_bytes,
            )


@dataclass(slots=True)
class SendRetention:
    """Wire frames kept after sending, so a resume can replay them.

    A separate buffer from :class:`CaptureRing`, and deliberately so. ADR-0013
    D37 described capacity and retention as "the same buffer viewed from
    different ends"; implementation showed that is not quite true, because the
    conversion stage sits between them. The capture ring holds device-format
    audio the consumer has not processed; this holds canonical wire frames the
    server has not acknowledged. Both are bounded in seconds of media time, which
    is the part of D37 that mattered.

    ADR-0009 D17: on resume the client compares ``resume_from_sample`` against
    what it still holds here. Because the bound is in samples, that comparison is
    a subtraction.
    """

    capacity_seconds: float
    sample_rate_hz: int = 16_000
    _frames: deque[tuple[int, bytes]] = field(default_factory=deque, init=False, repr=False)
    _buffered_samples: int = field(default=0, init=False)

    @property
    def capacity_samples(self) -> int:
        return int(self.capacity_seconds * self.sample_rate_hz)

    @property
    def buffered_samples(self) -> int:
        return self._buffered_samples

    @property
    def oldest_retained_sample(self) -> int | None:
        """The earliest sample offset still replayable, or None when empty."""
        return self._frames[0][0] if self._frames else None

    def remember(self, start_sample: int, payload: bytes) -> None:
        """Retain one sent frame, evicting the oldest past capacity."""
        samples = len(payload) // 2
        self._frames.append((start_sample, payload))
        self._buffered_samples += samples

        while self._buffered_samples > self.capacity_samples and self._frames:
            _, evicted = self._frames.popleft()
            self._buffered_samples -= len(evicted) // 2

    def release_through(self, acked_through_sample: int) -> int:
        """Drop frames the server has acknowledged (ADR-0010 D21).

        Returns the number of frames released.
        """
        released = 0
        while self._frames:
            start_sample, payload = self._frames[0]
            end_sample = start_sample + len(payload) // 2
            if end_sample > acked_through_sample:
                break
            self._frames.popleft()
            self._buffered_samples -= len(payload) // 2
            released += 1
        return released

    def replay_from(self, resume_from_sample: int) -> list[tuple[int, bytes]]:
        """Frames at or after ``resume_from_sample``, in order.

        Whatever the server needs but this buffer no longer holds becomes an
        explicit ``audio.gap`` (AUD-190) - the caller compares
        :attr:`oldest_retained_sample` against the request to find it.
        """
        return [
            (start_sample, payload)
            for start_sample, payload in self._frames
            if start_sample + len(payload) // 2 > resume_from_sample
        ]
