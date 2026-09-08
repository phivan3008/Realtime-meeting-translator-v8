"""The canonical media timeline.

ADR-0008. `requirements.md` Section 25.1 makes the integer audio sample offset at
16 kHz, counted from the start of the session stream, the canonical position of
every media-derived event. Floating-point seconds are never identity keys or
ordering authorities; client monotonic, server monotonic and UTC times are
observability fields that never replace a sample offset.

Everything in this module is integer arithmetic. That is the point: two
implementations that both count samples cannot drift, whereas two that both
accumulate floating-point durations will.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from protocol.errors import ErrorCode, ProtocolError
from protocol.limits import CANONICAL_SAMPLE_RATE_HZ

SESSION_START_SAMPLE: Final = 0

#: i64 bounds. ADR-0008 D9 chose 64-bit over 32-bit because a u32 sample counter
#: wraps after roughly 37 hours at 16 kHz, and a silent wrap on the identity
#: authority is not a survivable failure mode.
MIN_SAMPLE_OFFSET: Final = -(2**63)
MAX_SAMPLE_OFFSET: Final = 2**63 - 1

#: Beyond this, a JSON consumer using IEEE-754 doubles would lose precision.
#: It corresponds to about 17,800 years at 16 kHz, so no real value approaches
#: it - which is exactly why sample offsets are emitted as plain JSON integers
#: rather than strings.
MAX_JSON_SAFE_SAMPLE: Final = 2**53 - 1


def samples_to_ms(sample_offset: int) -> int:
    """Convert a sample offset to whole milliseconds.

    `requirements.md` Section 25.1 fixes this as
    ``floor(sample_offset * 1000 / 16000)``. Python's ``//`` floors toward
    negative infinity, which matches for the non-negative offsets this project
    produces and stays consistent for the negative ones a malformed frame might
    carry.
    """
    return sample_offset * 1000 // CANONICAL_SAMPLE_RATE_HZ


def ms_to_samples(milliseconds: int) -> int:
    """Convert whole milliseconds to a sample offset.

    Not the exact inverse of :func:`samples_to_ms`, and cannot be: one
    millisecond is 16 samples, so the conversion to milliseconds discards up to
    15 samples of precision. Use sample offsets for identity and ordering, and
    milliseconds only for display.
    """
    return milliseconds * CANONICAL_SAMPLE_RATE_HZ // 1000


def validate_sample_offset(sample_offset: int, *, field: str = "start_sample") -> None:
    """Raise if a sample offset cannot be represented or is nonsensical.

    Raises:
        ProtocolError: with ``PROT_MALFORMED_HEADER``.
    """
    if not MIN_SAMPLE_OFFSET <= sample_offset <= MAX_SAMPLE_OFFSET:
        raise ProtocolError(
            ErrorCode.PROT_MALFORMED_HEADER,
            f"{field} {sample_offset} is outside the signed 64-bit range",
        )
    if sample_offset < SESSION_START_SAMPLE:
        raise ProtocolError(
            ErrorCode.PROT_MALFORMED_HEADER,
            f"{field} {sample_offset} precedes the session start",
        )


@dataclass(frozen=True, slots=True)
class SampleSpan:
    """A half-open interval on the canonical timeline: ``[start, end)``."""

    start_sample: int
    end_sample: int

    def __post_init__(self) -> None:
        if self.end_sample < self.start_sample:
            raise ValueError(f"span end {self.end_sample} precedes start {self.start_sample}")

    @property
    def sample_count(self) -> int:
        return self.end_sample - self.start_sample

    @property
    def duration_ms(self) -> int:
        return samples_to_ms(self.sample_count)

    def intersects(self, other: SampleSpan) -> bool:
        return self.start_sample < other.end_sample and other.start_sample < self.end_sample

    def contains_sample(self, sample_offset: int) -> bool:
        return self.start_sample <= sample_offset < self.end_sample


@dataclass(frozen=True, slots=True)
class GapMeasurement:
    """The result of comparing two consecutive frames on the timeline."""

    #: Samples missing between the two frames. Zero means contiguous.
    gap_samples: int
    #: The missing interval, or None when contiguous.
    span: SampleSpan | None

    @property
    def is_contiguous(self) -> bool:
        return self.gap_samples == 0

    @property
    def gap_ms(self) -> int:
        return samples_to_ms(self.gap_samples)


def measure_gap(
    previous_start_sample: int,
    previous_sample_count: int,
    next_start_sample: int,
) -> GapMeasurement:
    """Measure the gap between two consecutive frames.

    This is the whole justification for ADR-0008 D9. With only a sequence number
    and a sample count, the size of a gap has to be inferred by assuming every
    lost frame carried the same number of samples - an assumption that breaks on
    the short final frame before a stop, and on any device glitch. With an
    absolute ``start_sample`` in each header it is a subtraction.

    Raises:
        ProtocolError: with ``PROT_SAMPLE_OVERLAP`` when the next frame starts
            before the previous one ended. Overlapping or reordered audio is an
            integrity failure on the identity authority, never something to
            silently merge.
    """
    previous_end = previous_start_sample + previous_sample_count
    gap_samples = next_start_sample - previous_end

    if gap_samples < 0:
        raise ProtocolError(
            ErrorCode.PROT_SAMPLE_OVERLAP,
            f"frame starting at sample {next_start_sample} overlaps the previous "
            f"frame ending at {previous_end} by {-gap_samples} samples",
        )

    if gap_samples == 0:
        return GapMeasurement(gap_samples=0, span=None)

    return GapMeasurement(
        gap_samples=gap_samples,
        span=SampleSpan(start_sample=previous_end, end_sample=next_start_sample),
    )
