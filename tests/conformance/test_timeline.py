"""Canonical timeline conformance vectors.

Category B (`requirements.md` Section 25.15 B). Purpose-built offsets and frame
sequences exercising the arithmetic and the overlap defence. Nothing here says
anything about model quality (TEST-130).

The property under test is the one ADR-0008 D9 was decided for: with an absolute
``start_sample`` in every header, a gap is measured rather than inferred, and it
is measured correctly even when frames vary in length.
"""

from __future__ import annotations

import pytest

from protocol.errors import ErrorCode, ProtocolError
from protocol.limits import CANONICAL_SAMPLE_RATE_HZ
from protocol.timeline import (
    MAX_JSON_SAFE_SAMPLE,
    SampleSpan,
    measure_gap,
    ms_to_samples,
    samples_to_ms,
    validate_sample_offset,
)

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "protocol_conformance_fixture"

#: The real recording, from tests/manifests/source-recordings.yaml. Used here
#: only as an integer, never as audio - no fixture is read by this module.
MEETING_SAMPLES = 29_139_328


class TestConversion:
    @pytest.mark.parametrize(
        ("samples", "expected_ms"),
        [
            (0, 0),
            (16, 1),
            (15, 0),
            (16_000, 1_000),
            (320, 20),
            (640, 40),
            (MEETING_SAMPLES, 1_821_208),
        ],
    )
    def test_samples_to_ms_floors(self, samples: int, expected_ms: int) -> None:
        """Section 25.1 fixes floor(sample_offset * 1000 / 16000)."""
        assert samples_to_ms(samples) == expected_ms

    def test_the_conversion_is_lossy_in_one_direction(self) -> None:
        """One millisecond is 16 samples, so ms cannot round-trip a sample offset.

        Asserted rather than merely documented, because treating milliseconds as
        interchangeable with samples is exactly what PROT-160 forbids.
        """
        assert samples_to_ms(31) == 1
        assert ms_to_samples(1) == 16
        assert ms_to_samples(samples_to_ms(31)) != 31

    def test_a_millisecond_is_sixteen_samples(self) -> None:
        assert CANONICAL_SAMPLE_RATE_HZ // 1000 == 16


class TestSampleOffsetValidation:
    def test_zero_is_the_session_start(self) -> None:
        validate_sample_offset(0)

    def test_negative_offsets_are_refused(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            validate_sample_offset(-1)
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_beyond_int64_is_refused(self) -> None:
        with pytest.raises(ProtocolError):
            validate_sample_offset(2**63)

    def test_the_json_safe_ceiling_is_far_beyond_any_real_meeting(self) -> None:
        """ADR-0010 D20: sample offsets are plain JSON integers, not strings.

        That is only safe because no real value approaches 2**53.
        """
        years = MAX_JSON_SAFE_SAMPLE / CANONICAL_SAMPLE_RATE_HZ / 60 / 60 / 24 / 365
        assert years > 17_000
        assert MEETING_SAMPLES < MAX_JSON_SAFE_SAMPLE / 1_000_000


class TestSampleSpan:
    def test_span_is_half_open(self) -> None:
        span = SampleSpan(start_sample=100, end_sample=200)
        assert span.sample_count == 100
        assert span.contains_sample(100)
        assert span.contains_sample(199)
        assert not span.contains_sample(200)

    def test_adjacent_spans_do_not_intersect(self) -> None:
        """Half-open intervals are what makes back-to-back frames non-overlapping."""
        first = SampleSpan(start_sample=0, end_sample=320)
        second = SampleSpan(start_sample=320, end_sample=640)
        assert not first.intersects(second)

    def test_overlapping_spans_intersect(self) -> None:
        first = SampleSpan(start_sample=0, end_sample=400)
        second = SampleSpan(start_sample=320, end_sample=640)
        assert first.intersects(second)

    def test_an_inverted_span_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="precedes start"):
            SampleSpan(start_sample=200, end_sample=100)

    def test_an_empty_span_is_allowed(self) -> None:
        """A zero-length span is a legitimate degenerate case, not an error."""
        assert SampleSpan(start_sample=100, end_sample=100).sample_count == 0


class TestGapMeasurement:
    def test_contiguous_frames_have_no_gap(self) -> None:
        result = measure_gap(
            previous_start_sample=0, previous_sample_count=320, next_start_sample=320
        )
        assert result.is_contiguous
        assert result.gap_samples == 0
        assert result.span is None

    def test_a_gap_is_measured_exactly(self) -> None:
        result = measure_gap(
            previous_start_sample=0, previous_sample_count=320, next_start_sample=960
        )
        assert result.gap_samples == 640
        assert result.gap_ms == 40
        assert result.span == SampleSpan(start_sample=320, end_sample=960)

    def test_a_gap_after_a_short_frame_is_still_exact(self) -> None:
        """The case that defeats sequence-only accumulation.

        ADR-0008 D9: inferring gap size requires assuming every frame carried the
        same sample count. The final frame before a stop is short by nature, so
        that assumption is wrong exactly when a gap follows one.
        """
        result = measure_gap(
            previous_start_sample=1000, previous_sample_count=37, next_start_sample=2000
        )
        assert result.gap_samples == 963

    def test_overlapping_frames_are_an_integrity_error(self) -> None:
        """PROT-160: the sample timeline is the identity authority. A contradiction
        in it is not something to continue past."""
        with pytest.raises(ProtocolError) as exc:
            measure_gap(previous_start_sample=0, previous_sample_count=320, next_start_sample=160)
        assert exc.value.code is ErrorCode.PROT_SAMPLE_OVERLAP
        assert exc.value.fatal

    def test_a_repeated_frame_is_an_overlap(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            measure_gap(previous_start_sample=320, previous_sample_count=320, next_start_sample=320)
        assert exc.value.code is ErrorCode.PROT_SAMPLE_OVERLAP

    def test_a_gap_preserves_its_duration(self) -> None:
        """Section 25.1: the timeline is never compressed to hide missing audio."""
        frames = [(0, 320), (320, 320)]
        after_gap_start = 16_000

        gap = measure_gap(frames[-1][0], frames[-1][1], after_gap_start)
        recovered_end = after_gap_start
        naive_end = sum(count for _, count in frames) + gap.gap_samples

        assert naive_end == recovered_end, "the gap did not account for the missing interval"
        assert gap.gap_ms == 960
