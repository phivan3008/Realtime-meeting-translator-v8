"""Projection reducer conformance vectors.

Category B (`requirements.md` Section 25.15 B). Purpose-built events exercising
stale revisions, duplicate identifiers and revision conflicts - defensive
branches this category explicitly covers.

**Nothing here supports a claim about ASR, language, speaker, overlap or
translation quality** (TEST-130). The reducer never sees audio; it decides what
an event is allowed to do to a projection.

The rules under test are the Section 25.5 dependency model as fixed by ADR-0008:
a content or language revision invalidates a translation whose echoed source
revisions no longer match; a speaker revision never does; equal revision with
unequal payload is an integrity conflict rather than last-write-wins; and replay
is idempotent by ``event_id``.
"""

from __future__ import annotations

import itertools

import pytest

from protocol.enums import (
    LanguageCode,
    LanguageStatus,
    RejectionReason,
    SegmentStatus,
    SpeakerOperation,
    SpeakerStatus,
    TranslationStatus,
)
from protocol.events import (
    Envelope,
    LanguageUpdated,
    SpeakerUpdated,
    TranscriptFinal,
    TranscriptPartial,
    TranslationFailed,
    TranslationFinal,
    TranslationStarted,
)
from protocol.projection import SessionProjection

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "negative_test_vector"

SESSION_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
SEGMENT = "seg-000001"
UTTERANCE = "utt-000001"
SENT_AT = "2026-09-08T07:41:22.481Z"

_event_counter = itertools.count()


def _event_id() -> str:
    """A distinct, deterministic event id per constructed event.

    Deterministic on purpose: a random id would make a failing test hard to
    reproduce, and this module is about reproducibility.
    """
    return f"00000000-0000-4000-8000-{next(_event_counter):012d}"


def partial(
    revision: int,
    stable: str = "明日の",
    unstable: str = "会議",
    *,
    event_id: str | None = None,
    segment_id: str = SEGMENT,
    start_sample: int = 16000,
    end_sample: int = 48000,
) -> TranscriptPartial:
    return TranscriptPartial(
        session_id=SESSION_ID,
        event_id=event_id or _event_id(),
        sent_at_utc=SENT_AT,
        utterance_id=UTTERANCE,
        segment_id=segment_id,
        content_revision=revision,
        stable_text=stable,
        unstable_text=unstable,
        start_sample=start_sample,
        end_sample=end_sample,
    )


def final(
    revision: int,
    text: str = "明日の会議は午前十時です",
    status: SegmentStatus = SegmentStatus.ACCEPTED,
    *,
    event_id: str | None = None,
    segment_id: str = SEGMENT,
    start_sample: int = 16000,
    end_sample: int = 48000,
) -> TranscriptFinal:
    return TranscriptFinal(
        session_id=SESSION_ID,
        event_id=event_id or _event_id(),
        sent_at_utc=SENT_AT,
        utterance_id=UTTERANCE,
        segment_id=segment_id,
        content_revision=revision,
        status=status,
        text=text,
        start_sample=start_sample,
        end_sample=end_sample,
        language_id=LanguageCode.JA,
        language_status=LanguageStatus.CONFIRMED_JA,
    )


def translated(
    translation_revision: int,
    from_content: int,
    from_language: int,
    text: str = "Cuộc họp ngày mai lúc 10 giờ sáng",
) -> TranslationFinal:
    return TranslationFinal(
        session_id=SESSION_ID,
        event_id=_event_id(),
        sent_at_utc=SENT_AT,
        segment_id=SEGMENT,
        translation_revision=translation_revision,
        translated_from_content_revision=from_content,
        translated_from_language_revision=from_language,
        source_language=LanguageCode.JA,
        target_language=LanguageCode.VI,
        text=text,
    )


def speaker_assign(revision: int, speaker_id: str) -> SpeakerUpdated:
    return SpeakerUpdated(
        session_id=SESSION_ID,
        event_id=_event_id(),
        sent_at_utc=SENT_AT,
        operation=SpeakerOperation.ASSIGN,
        speaker_revision=revision,
        segment_id=SEGMENT,
        primary_speaker_id=speaker_id,
        speaker_status=SpeakerStatus.PROVISIONAL,
    )


def session() -> SessionProjection:
    return SessionProjection(session_id=SESSION_ID)


class TestIdempotency:
    """PROT-280: replaying the same event is idempotent by event_id."""

    def test_replaying_an_event_id_is_refused(self) -> None:
        state = session()
        event = partial(1)

        assert state.apply(event).applied
        second = state.apply(event)

        assert not second.applied
        assert second.reason is RejectionReason.DUPLICATE

    def test_a_refused_event_id_is_still_remembered(self) -> None:
        """A retry of a refused event must not be re-evaluated against state that
        has since moved on."""
        state = session()
        state.apply(final(5))
        stale_event = partial(2)

        first = state.apply(stale_event)
        second = state.apply(stale_event)

        assert first.reason is RejectionReason.STALE
        assert second.reason is RejectionReason.DUPLICATE


class TestStaleAndConflict:
    def test_a_lower_content_revision_is_stale(self) -> None:
        state = session()
        state.apply(partial(3))

        result = state.apply(partial(2))

        assert result.reason is RejectionReason.STALE
        assert state.segments[SEGMENT].content_revision == 3

    def test_equal_revision_identical_payload_is_a_duplicate(self) -> None:
        state = session()
        state.apply(partial(1, "明日の", "会議"))

        result = state.apply(partial(1, "明日の", "会議"))

        assert result.reason is RejectionReason.DUPLICATE
        assert result.conflict is None

    def test_equal_revision_different_payload_is_an_integrity_conflict(self) -> None:
        """Section 25.5 and ADR-0008 D12. Never last-write-wins."""
        state = session()
        state.apply(partial(1, "明日の", "会議"))

        result = state.apply(partial(1, "明後日の", "打合せ"))

        assert result.reason is RejectionReason.INTEGRITY_CONFLICT
        assert state.segments[SEGMENT].stable_text == "明日の", "existing payload was overwritten"
        assert len(state.conflicts) == 1
        assert state.conflicts[0].dimension == "content"
        assert state.conflicts[0].revision == 1

    def test_a_conflict_records_both_payloads(self) -> None:
        state = session()
        state.apply(partial(1, "明日の", "会議"))
        state.apply(partial(1, "明後日の", "打合せ"))

        conflict = state.conflicts[0]
        assert "明日の" in conflict.existing
        assert "明後日の" in conflict.incoming

    def test_a_conflict_does_not_stop_the_session(self) -> None:
        """ADR-0008 D12: a session stop is reserved for losing the source of
        truth, not for one contradictory event against an intact log."""
        state = session()
        state.apply(partial(1))
        state.apply(partial(1, "違う", "テキスト"))

        recovered = state.apply(partial(2, "明日の", "会議は"))

        assert recovered.applied
        assert state.segments[SEGMENT].content_revision == 2


class TestRevisionDependencies:
    """The single sentence the whole revision model reduces to."""

    def _accepted_and_translated(self) -> SessionProjection:
        state = session()
        state.apply(final(1))
        state.apply(translated(translation_revision=1, from_content=1, from_language=0))
        assert state.segments[SEGMENT].translation_is_current
        return state

    def test_a_content_revision_invalidates_the_translation(self) -> None:
        state = self._accepted_and_translated()

        state.apply(final(2, text="明日の会議は午前十一時です"))

        segment = state.segments[SEGMENT]
        assert not segment.translation_is_current
        assert segment.translation_text == ""
        assert segment.translation_status is TranslationStatus.PENDING
        assert segment.translated_from_content_revision is None

    def test_a_language_revision_invalidates_the_translation(self) -> None:
        state = self._accepted_and_translated()

        state.apply(
            LanguageUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                segment_id=SEGMENT,
                language_revision=1,
                language_id=LanguageCode.VI,
                language_status=LanguageStatus.CONFIRMED_VI,
            )
        )

        segment = state.segments[SEGMENT]
        assert not segment.translation_is_current
        assert segment.translation_text == ""

    def test_a_speaker_revision_does_not_invalidate_the_translation(self) -> None:
        """Section 25.5, stated as plainly as the requirement states it."""
        state = self._accepted_and_translated()
        before = state.segments[SEGMENT]

        state.apply(speaker_assign(1, "speaker-2"))

        segment = state.segments[SEGMENT]
        assert segment.primary_speaker_id == "speaker-2"
        assert segment.speaker_revision == 1
        assert segment.translation_is_current, "a speaker change invalidated a translation"
        assert segment.translation_text == before.translation_text
        assert segment.translation_revision == before.translation_revision
        assert segment.content_revision == before.content_revision

    def test_a_translation_echoing_superseded_text_is_stale(self) -> None:
        """TRN-110. This is the branch that keeps superseded text off the timeline."""
        state = session()
        state.apply(final(1))
        state.apply(final(2, text="訂正されたテキスト"))

        result = state.apply(translated(translation_revision=1, from_content=1, from_language=0))

        assert result.reason is RejectionReason.STALE
        assert state.segments[SEGMENT].translation_text == ""

    def test_a_translation_echoing_a_stale_language_is_refused(self) -> None:
        state = session()
        state.apply(final(1))
        state.apply(
            LanguageUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                segment_id=SEGMENT,
                language_revision=3,
                language_id=LanguageCode.JA,
                language_status=LanguageStatus.CONFIRMED_JA,
            )
        )

        result = state.apply(translated(translation_revision=1, from_content=1, from_language=0))

        assert result.reason is RejectionReason.STALE


class TestTranslationFirewall:
    def test_only_an_accepted_final_may_start_translation(self) -> None:
        """TRN-160 and Section 14.6."""
        state = session()
        state.apply(final(1, status=SegmentStatus.LOW_CONFIDENCE))

        result = state.apply(
            TranslationStarted(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                segment_id=SEGMENT,
                source_language=LanguageCode.JA,
                target_language=LanguageCode.VI,
            )
        )

        assert result.reason is RejectionReason.NOT_APPLICABLE

    def test_a_rejected_final_is_not_translatable(self) -> None:
        state = session()
        state.apply(final(1, status=SegmentStatus.REJECTED, text=""))

        assert state.segments[SEGMENT].translation_status is TranslationStatus.NOT_APPLICABLE

    def test_a_translation_failure_preserves_the_transcript(self) -> None:
        """Section 17.4 and UI-100."""
        state = session()
        state.apply(final(1))
        text_before = state.segments[SEGMENT].text

        state.apply(
            TranslationFailed(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                segment_id=SEGMENT,
                translation_revision=1,
                translated_from_content_revision=1,
                translated_from_language_revision=0,
                reason="timeout",
            )
        )

        segment = state.segments[SEGMENT]
        assert segment.translation_status is TranslationStatus.FAILED
        assert segment.text == text_before
        assert segment.status is SegmentStatus.ACCEPTED


class TestSealing:
    def test_nothing_changes_a_sealed_segment(self) -> None:
        """Section 25.4: content, language and speaker are frozen after sealing."""
        state = session()
        state.apply(final(1))
        state.seal(SEGMENT)

        results = [
            state.apply(final(2, text="遅すぎる")),
            state.apply(speaker_assign(9, "speaker-3")),
            state.apply(translated(translation_revision=9, from_content=1, from_language=0)),
        ]

        assert all(r.reason is RejectionReason.NOT_APPLICABLE for r in results)
        assert state.segments[SEGMENT].text == "明日の会議は午前十時です"

    def test_sealing_twice_is_refused(self) -> None:
        state = session()
        state.apply(final(1))
        state.seal(SEGMENT)

        assert state.seal(SEGMENT).reason is RejectionReason.DUPLICATE


class TestSpeakerMerge:
    """Section 25.7 and SPK-210."""

    def _two_segments(self) -> SessionProjection:
        state = session()
        state.apply(final(1, segment_id="seg-000001", start_sample=16000, end_sample=48000))
        state.apply(final(1, segment_id="seg-000002", start_sample=48000, end_sample=80000))
        state.apply(
            SpeakerUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                operation=SpeakerOperation.ASSIGN,
                speaker_revision=1,
                segment_id="seg-000001",
                primary_speaker_id="speaker-1",
            )
        )
        state.apply(
            SpeakerUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                operation=SpeakerOperation.ASSIGN,
                speaker_revision=1,
                segment_id="seg-000002",
                primary_speaker_id="speaker-3",
            )
        )
        return state

    def test_merge_rewrites_affected_views(self) -> None:
        state = self._two_segments()

        state.apply(
            SpeakerUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                operation=SpeakerOperation.MERGE,
                speaker_revision=8,
                from_speaker_ids=["speaker-3"],
                to_speaker_id="speaker-1",
            )
        )

        assert state.segments["seg-000002"].primary_speaker_id == "speaker-1"

    def test_merge_leaves_content_and_translation_revisions_alone(self) -> None:
        state = self._two_segments()
        before = state.segments["seg-000002"]

        state.apply(
            SpeakerUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                operation=SpeakerOperation.MERGE,
                speaker_revision=8,
                from_speaker_ids=["speaker-3"],
                to_speaker_id="speaker-1",
            )
        )

        after = state.segments["seg-000002"]
        assert after.content_revision == before.content_revision
        assert after.translation_revision == before.translation_revision
        assert after.text == before.text

    def test_a_merged_id_becomes_an_alias(self) -> None:
        """Merged IDs remain as aliases and are never reused."""
        state = self._two_segments()
        state.apply(
            SpeakerUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                operation=SpeakerOperation.MERGE,
                speaker_revision=8,
                from_speaker_ids=["speaker-3"],
                to_speaker_id="speaker-1",
            )
        )

        assert state.canonical_speaker("speaker-3") == "speaker-1"

    def test_a_merge_without_a_target_is_rejected_by_the_schema(self) -> None:
        with pytest.raises(ValueError, match="from_speaker_ids and to_speaker_id"):
            SpeakerUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                operation=SpeakerOperation.MERGE,
                speaker_revision=8,
                from_speaker_ids=["speaker-3"],
            )

    def test_a_merge_cannot_target_one_of_its_own_sources(self) -> None:
        with pytest.raises(ValueError, match="cannot also be one of its sources"):
            SpeakerUpdated(
                session_id=SESSION_ID,
                event_id=_event_id(),
                sent_at_utc=SENT_AT,
                operation=SpeakerOperation.MERGE,
                speaker_revision=8,
                from_speaker_ids=["speaker-1"],
                to_speaker_id="speaker-1",
            )


class TestProjectionOutput:
    def test_segments_are_ordered_by_sample_offset(self) -> None:
        """Section 8.3 and PROT-160: the sample offset is the ordering authority."""
        state = session()
        state.apply(final(1, segment_id="seg-000002", start_sample=48000, end_sample=80000))
        state.apply(final(1, segment_id="seg-000001", start_sample=16000, end_sample=48000))

        assert [s.segment_id for s in state.ordered_segments()] == [
            "seg-000001",
            "seg-000002",
        ]

    def test_compaction_excludes_rejected_segments(self) -> None:
        """Section 25.14: one latest projection per non-rejected segment."""
        state = session()
        state.apply(final(1, segment_id="seg-000001"))
        state.apply(final(1, segment_id="seg-000002", status=SegmentStatus.REJECTED, text=""))

        assert [s.segment_id for s in state.compactable_segments()] == ["seg-000001"]

    def test_low_confidence_segments_are_kept(self) -> None:
        """Only REJECTED is excluded; low confidence is a marker, not a deletion."""
        state = session()
        state.apply(final(1, status=SegmentStatus.LOW_CONFIDENCE))

        assert len(state.compactable_segments()) == 1


class TestDeterminism:
    """The property the whole design of ADR-0008 D13 exists to guarantee."""

    def _script(self) -> list[Envelope]:
        return [
            partial(1, event_id="00000000-0000-4000-8000-000000009001"),
            partial(2, "明日の会議", "は", event_id="00000000-0000-4000-8000-000000009002"),
            final(3, event_id="00000000-0000-4000-8000-000000009003"),
            TranslationStarted(
                session_id=SESSION_ID,
                event_id="00000000-0000-4000-8000-000000009004",
                sent_at_utc=SENT_AT,
                segment_id=SEGMENT,
                source_language=LanguageCode.JA,
                target_language=LanguageCode.VI,
            ),
            TranslationFinal(
                session_id=SESSION_ID,
                event_id="00000000-0000-4000-8000-000000009005",
                sent_at_utc=SENT_AT,
                segment_id=SEGMENT,
                translation_revision=1,
                translated_from_content_revision=3,
                translated_from_language_revision=0,
                source_language=LanguageCode.JA,
                target_language=LanguageCode.VI,
                text="Cuộc họp ngày mai",
            ),
        ]

    def test_folding_the_same_events_twice_gives_the_same_state(self) -> None:
        first = session()
        first.fold(self._script())

        second = session()
        second.fold(self._script())

        assert first.segments == second.segments
        assert first.conflicts == second.conflicts

    def test_the_final_projection_matches_the_expected_state(self) -> None:
        state = session()
        state.fold(self._script())

        segment = state.segments[SEGMENT]
        assert segment.status is SegmentStatus.ACCEPTED
        assert segment.text == "明日の会議は午前十時です"
        assert segment.content_revision == 3
        assert segment.translation_text == "Cuộc họp ngày mai"
        assert segment.translation_is_current
