"""The shared projection reducer.

ADR-0008 D13. `requirements.md` Section 25.5 calls the UI and the compacted
history "deterministic projections" of the debug log. That is only true if they
are the same code, so this module is the single place an event becomes state,
used by four consumers: the client UI, the live history projection, the
compaction that produces ``history.final.jsonl``, and the recovery rebuild
(PERS-070).

**Determinism, not immutability.** Folding the same events in the same order
always produces the same state: no I/O, no clock, no randomness, no iteration
over an unordered set. :class:`SessionProjection` mutates in place because a
30-minute meeting is tens of thousands of events over hundreds of segments, and
copying the whole map per event would make the mandatory soak test (TEST-180)
pointlessly slow. Purity in the sense that matters here is reproducibility, and
that is what is tested.

Refusals are **return values**, never exceptions. Section 25.5 requires stale
results to be persisted as diagnostic events, which a raised exception makes
awkward and a crash makes impossible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

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
    OverlapInterval,
    SpeakerCandidate,
    SpeakerUpdated,
    TranscriptFinal,
    TranscriptPartial,
    TranscriptRevised,
    TranslationFailed,
    TranslationFinal,
    TranslationStarted,
)


@dataclass(frozen=True, slots=True)
class SegmentProjection:
    """The current state of one segment.

    Frozen: a segment is replaced wholesale on every change, so a caller holding
    a reference to a previous state keeps a valid snapshot. That is what lets the
    UI diff against what it last rendered.
    """

    segment_id: str
    utterance_id: str
    start_sample: int
    end_sample: int

    status: SegmentStatus = SegmentStatus.CREATED
    stable_text: str = ""
    unstable_text: str = ""
    text: str = ""

    language_id: LanguageCode | None = None
    language_status: LanguageStatus = LanguageStatus.UNKNOWN

    primary_speaker_id: str | None = None
    speaker_status: SpeakerStatus = SpeakerStatus.UNKNOWN
    speaker_candidates: tuple[SpeakerCandidate, ...] = ()

    overlap: bool = False
    overlap_intervals: tuple[OverlapInterval, ...] = ()

    translation_status: TranslationStatus = TranslationStatus.NOT_APPLICABLE
    translation_text: str = ""
    source_language: LanguageCode | None = None
    target_language: LanguageCode | None = None

    content_revision: int = 0
    language_revision: int = 0
    speaker_revision: int = 0
    translation_revision: int = 0
    translated_from_content_revision: int | None = None
    translated_from_language_revision: int | None = None

    parent_segment_ids: tuple[str, ...] = ()
    sealed: bool = False

    @property
    def display_text(self) -> str:
        """What a timeline row shows: final text once there is one, else the partial."""
        return self.text if self.text else f"{self.stable_text}{self.unstable_text}"

    @property
    def translation_is_current(self) -> bool:
        """Whether the stored translation still matches the text it was made from.

        Section 25.5: a content or language revision invalidates a translation
        whose echoed source revisions no longer match. A speaker revision never
        does, which is why ``speaker_revision`` is absent from this comparison.
        """
        if self.translation_status is not TranslationStatus.COMPLETED:
            return False
        return (
            self.translated_from_content_revision == self.content_revision
            and self.translated_from_language_revision == self.language_revision
        )


@dataclass(frozen=True, slots=True)
class IntegrityConflict:
    """A refused event that contradicted state at the same revision.

    ADR-0008 D12: keep the existing payload, refuse the incoming one, record
    both. Never last-write-wins, and never a session stop - that is reserved for
    losing the source of truth, not for one contradictory event against an
    intact log.
    """

    segment_id: str
    event_id: str
    dimension: str
    revision: int
    existing: str
    incoming: str


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """The outcome of folding one event."""

    applied: bool
    segment: SegmentProjection | None = None
    reason: RejectionReason | None = None
    detail: str = ""
    conflict: IntegrityConflict | None = None

    @classmethod
    def ok(cls, segment: SegmentProjection) -> ProjectionResult:
        return cls(applied=True, segment=segment)

    @classmethod
    def refuse(
        cls,
        reason: RejectionReason,
        detail: str,
        *,
        conflict: IntegrityConflict | None = None,
    ) -> ProjectionResult:
        return cls(applied=False, reason=reason, detail=detail, conflict=conflict)


def _conflict(
    segment_id: str,
    event: Envelope,
    dimension: str,
    revision: int,
    existing: object,
    incoming: object,
) -> ProjectionResult:
    return ProjectionResult.refuse(
        RejectionReason.INTEGRITY_CONFLICT,
        f"{dimension} revision {revision} arrived twice with different payloads",
        conflict=IntegrityConflict(
            segment_id=segment_id,
            event_id=event.event_id,
            dimension=dimension,
            revision=revision,
            existing=repr(existing),
            incoming=repr(incoming),
        ),
    )


@dataclass(slots=True)
class SessionProjection:
    """Every segment of one session, plus the bookkeeping the rules need.

    Speaker aliases live here rather than on a segment because a merge rewrites
    views across segments (Section 25.7, SPK-210), and ``seen_event_ids`` lives
    here because idempotency is a session-wide property (PROT-280).
    """

    session_id: str
    segments: dict[str, SegmentProjection] = field(default_factory=dict)
    seen_event_ids: set[str] = field(default_factory=set)
    #: alias speaker id -> canonical speaker id. Merged IDs remain as aliases
    #: and are never reused (Section 25.7).
    speaker_aliases: dict[str, str] = field(default_factory=dict)
    conflicts: list[IntegrityConflict] = field(default_factory=list)

    # -- speaker alias resolution -------------------------------------------

    def canonical_speaker(self, speaker_id: str | None) -> str | None:
        """Follow the alias chain to the canonical speaker id."""
        if speaker_id is None:
            return None
        seen: set[str] = set()
        current = speaker_id
        while current in self.speaker_aliases and current not in seen:
            seen.add(current)
            current = self.speaker_aliases[current]
        return current

    # -- the reducer ---------------------------------------------------------

    def apply(self, event: Envelope) -> ProjectionResult:
        """Fold one event into the session.

        Deterministic: the same sequence of events always produces the same
        state. Never raises for a protocol-level disagreement; refusals come
        back as :class:`ProjectionResult` so the caller can persist them as the
        diagnostics Section 25.5 requires.
        """
        if event.event_id in self.seen_event_ids:
            return ProjectionResult.refuse(
                RejectionReason.DUPLICATE,
                f"event_id {event.event_id} was already applied",
            )

        result = self._dispatch(event)

        # A refused event is still an event that was seen. Recording it keeps a
        # retry of the same event_id idempotent rather than letting it be
        # re-evaluated against state that has since moved on.
        self.seen_event_ids.add(event.event_id)

        if result.conflict is not None:
            self.conflicts.append(result.conflict)
        if result.applied and result.segment is not None:
            self.segments[result.segment.segment_id] = result.segment
        return result

    def fold(self, events: list[Envelope]) -> list[ProjectionResult]:
        """Fold many events in order. Convenience over :meth:`apply`."""
        return [self.apply(event) for event in events]

    # -- dispatch ------------------------------------------------------------

    def _dispatch(self, event: Envelope) -> ProjectionResult:
        match event:
            case TranscriptPartial():
                return self._apply_partial(event)
            # TranscriptRevised subclasses TranscriptFinal, so it must be
            # matched first or it would never be reached.
            case TranscriptRevised():
                return self._apply_final(event)
            case TranscriptFinal():
                return self._apply_final(event)
            case LanguageUpdated():
                return self._apply_language(event)
            case SpeakerUpdated():
                return self._apply_speaker(event)
            case TranslationStarted():
                return self._apply_translation_started(event)
            case TranslationFinal():
                return self._apply_translation_final(event)
            case TranslationFailed():
                return self._apply_translation_failed(event)
            case _:
                return ProjectionResult.refuse(
                    RejectionReason.NOT_APPLICABLE,
                    f"{event.event_type} does not carry segment state",
                )

    def _sealed_refusal(self, segment: SegmentProjection, event: Envelope) -> ProjectionResult:
        return ProjectionResult.refuse(
            RejectionReason.NOT_APPLICABLE,
            f"segment {segment.segment_id} is sealed; {event.event_type} arrived after seal",
        )

    # -- transcript ----------------------------------------------------------

    def _apply_partial(self, event: TranscriptPartial) -> ProjectionResult:
        existing = self.segments.get(event.segment_id)

        if existing is None:
            return ProjectionResult.ok(
                SegmentProjection(
                    segment_id=event.segment_id,
                    utterance_id=event.utterance_id,
                    start_sample=event.start_sample,
                    end_sample=event.end_sample,
                    status=SegmentStatus.PARTIAL,
                    stable_text=event.stable_text,
                    unstable_text=event.unstable_text,
                    language_id=event.language_id,
                    language_status=event.language_status,
                    primary_speaker_id=self.canonical_speaker(event.primary_speaker_id),
                    speaker_status=event.speaker_status,
                    content_revision=event.content_revision,
                    language_revision=event.language_revision,
                    speaker_revision=event.speaker_revision,
                    parent_segment_ids=tuple(
                        event.lineage.parent_segment_ids if event.lineage else ()
                    ),
                )
            )

        if existing.sealed:
            return self._sealed_refusal(existing, event)

        if event.content_revision < existing.content_revision:
            return ProjectionResult.refuse(
                RejectionReason.STALE,
                f"content revision {event.content_revision} is behind {existing.content_revision}",
            )

        if event.content_revision == existing.content_revision:
            incoming = (event.stable_text, event.unstable_text)
            current = (existing.stable_text, existing.unstable_text)
            if incoming != current:
                return _conflict(
                    event.segment_id, event, "content", event.content_revision, current, incoming
                )
            return ProjectionResult.refuse(
                RejectionReason.DUPLICATE,
                f"content revision {event.content_revision} repeated with identical payload",
            )

        return ProjectionResult.ok(
            replace(
                existing,
                status=SegmentStatus.PARTIAL,
                stable_text=event.stable_text,
                unstable_text=event.unstable_text,
                end_sample=event.end_sample,
                content_revision=event.content_revision,
            )
        )

    def _apply_final(self, event: TranscriptFinal) -> ProjectionResult:
        existing = self.segments.get(event.segment_id)

        translation_status = (
            TranslationStatus.NOT_APPLICABLE
            if event.status is not SegmentStatus.ACCEPTED
            else TranslationStatus.PENDING
        )

        if existing is None:
            return ProjectionResult.ok(
                SegmentProjection(
                    segment_id=event.segment_id,
                    utterance_id=event.utterance_id,
                    start_sample=event.start_sample,
                    end_sample=event.end_sample,
                    status=event.status,
                    text=event.text,
                    language_id=event.language_id,
                    language_status=event.language_status,
                    primary_speaker_id=self.canonical_speaker(event.primary_speaker_id),
                    speaker_status=event.speaker_status,
                    speaker_candidates=tuple(event.speaker_candidates),
                    overlap=event.overlap,
                    overlap_intervals=tuple(event.overlap_intervals),
                    translation_status=translation_status,
                    content_revision=event.content_revision,
                    language_revision=event.language_revision,
                    speaker_revision=event.speaker_revision,
                    parent_segment_ids=tuple(
                        event.lineage.parent_segment_ids if event.lineage else ()
                    ),
                )
            )

        if existing.sealed:
            return self._sealed_refusal(existing, event)

        if event.content_revision < existing.content_revision:
            return ProjectionResult.refuse(
                RejectionReason.STALE,
                f"content revision {event.content_revision} is behind {existing.content_revision}",
            )

        if event.content_revision == existing.content_revision:
            if (event.text, event.status) != (existing.text, existing.status):
                return _conflict(
                    event.segment_id,
                    event,
                    "content",
                    event.content_revision,
                    (existing.text, existing.status),
                    (event.text, event.status),
                )
            return ProjectionResult.refuse(
                RejectionReason.DUPLICATE,
                f"content revision {event.content_revision} repeated with identical payload",
            )

        # A new content revision invalidates any translation built on the old
        # one (Section 25.5). The echoes are cleared rather than merely
        # ignored, so nothing downstream can mistake the stale text for current.
        return ProjectionResult.ok(
            replace(
                existing,
                status=event.status,
                text=event.text,
                stable_text="",
                unstable_text="",
                start_sample=event.start_sample,
                end_sample=event.end_sample,
                language_id=event.language_id,
                language_status=event.language_status,
                speaker_candidates=tuple(event.speaker_candidates),
                overlap=event.overlap,
                overlap_intervals=tuple(event.overlap_intervals),
                content_revision=event.content_revision,
                translation_status=translation_status,
                translation_text="",
                translated_from_content_revision=None,
                translated_from_language_revision=None,
            )
        )

    # -- language ------------------------------------------------------------

    def _apply_language(self, event: LanguageUpdated) -> ProjectionResult:
        existing = self.segments.get(event.segment_id)
        if existing is None:
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE,
                f"language.updated for unknown segment {event.segment_id}",
            )
        if existing.sealed:
            return self._sealed_refusal(existing, event)

        if event.language_revision < existing.language_revision:
            return ProjectionResult.refuse(
                RejectionReason.STALE,
                f"language revision {event.language_revision} is behind "
                f"{existing.language_revision}",
            )

        if event.language_revision == existing.language_revision:
            incoming = (event.language_id, event.language_status)
            current = (existing.language_id, existing.language_status)
            if incoming != current:
                return _conflict(
                    event.segment_id,
                    event,
                    "language",
                    event.language_revision,
                    current,
                    incoming,
                )
            return ProjectionResult.refuse(
                RejectionReason.DUPLICATE,
                f"language revision {event.language_revision} repeated with identical payload",
            )

        # Section 25.4: a language change after acceptance invalidates the
        # translation and requires a new accepted final or explicit
        # revalidation. Clearing the echoes is what makes the invalidation
        # observable rather than implicit.
        return ProjectionResult.ok(
            replace(
                existing,
                language_id=event.language_id,
                language_status=event.language_status,
                language_revision=event.language_revision,
                translation_status=(
                    TranslationStatus.PENDING
                    if existing.status is SegmentStatus.ACCEPTED
                    else TranslationStatus.NOT_APPLICABLE
                ),
                translation_text="",
                translated_from_content_revision=None,
                translated_from_language_revision=None,
            )
        )

    # -- speaker -------------------------------------------------------------

    def _apply_speaker(self, event: SpeakerUpdated) -> ProjectionResult:
        if event.operation is SpeakerOperation.MERGE:
            return self._apply_speaker_merge(event)

        if event.segment_id is None:
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE,
                "speaker.updated assign carries no segment_id",
            )

        existing = self.segments.get(event.segment_id)
        if existing is None:
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE,
                f"speaker.updated for unknown segment {event.segment_id}",
            )
        if existing.sealed:
            return self._sealed_refusal(existing, event)

        if event.speaker_revision < existing.speaker_revision:
            return ProjectionResult.refuse(
                RejectionReason.STALE,
                f"speaker revision {event.speaker_revision} is behind {existing.speaker_revision}",
            )

        incoming_speaker = self.canonical_speaker(event.primary_speaker_id)

        if event.speaker_revision == existing.speaker_revision:
            incoming = (incoming_speaker, event.speaker_status)
            current = (existing.primary_speaker_id, existing.speaker_status)
            if incoming != current:
                return _conflict(
                    event.segment_id,
                    event,
                    "speaker",
                    event.speaker_revision,
                    current,
                    incoming,
                )
            return ProjectionResult.refuse(
                RejectionReason.DUPLICATE,
                f"speaker revision {event.speaker_revision} repeated with identical payload",
            )

        # Section 25.5: a speaker-only change never invalidates a translation.
        # Every translation field below is deliberately untouched.
        return ProjectionResult.ok(
            replace(
                existing,
                primary_speaker_id=incoming_speaker,
                speaker_status=event.speaker_status,
                speaker_candidates=tuple(event.speaker_candidates),
                speaker_revision=event.speaker_revision,
            )
        )

    def _apply_speaker_merge(self, event: SpeakerUpdated) -> ProjectionResult:
        """Rewrite affected views to the canonical id, touching nothing else.

        Section 25.7 and SPK-210: a merge rewrites the speaker on every affected
        segment without modifying content or translation revisions. The schema
        guarantees a target and sources, so this cannot be reached without them.
        """
        target = event.to_speaker_id
        assert target is not None  # enforced by SpeakerUpdated's model validator

        for alias in event.from_speaker_ids:
            self.speaker_aliases[alias] = target

        rewritten = 0
        for segment_id, segment in list(self.segments.items()):
            if segment.sealed:
                continue
            if segment.primary_speaker_id in event.from_speaker_ids:
                self.segments[segment_id] = replace(
                    segment,
                    primary_speaker_id=target,
                    speaker_revision=event.speaker_revision,
                )
                rewritten += 1

        return ProjectionResult(
            applied=True,
            segment=None,
            detail=f"merged {len(event.from_speaker_ids)} speaker ids into {target}, "
            f"rewriting {rewritten} segments",
        )

    # -- translation ---------------------------------------------------------

    def _apply_translation_started(self, event: TranslationStarted) -> ProjectionResult:
        existing = self.segments.get(event.segment_id)
        if existing is None:
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE,
                f"translation.started for unknown segment {event.segment_id}",
            )
        if existing.sealed:
            return self._sealed_refusal(existing, event)
        if existing.status is not SegmentStatus.ACCEPTED:
            # TRN-160: only an accepted final may be translated.
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE,
                f"segment {event.segment_id} is {existing.status}, not accepted",
            )

        return ProjectionResult.ok(
            replace(
                existing,
                translation_status=TranslationStatus.PENDING,
                source_language=event.source_language,
                target_language=event.target_language,
            )
        )

    def _apply_translation_final(self, event: TranslationFinal) -> ProjectionResult:
        existing = self.segments.get(event.segment_id)
        if existing is None:
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE,
                f"translation.final for unknown segment {event.segment_id}",
            )
        if existing.sealed:
            return self._sealed_refusal(existing, event)

        # Section 25.5 and TRN-110. Both echoes must match, or this translation
        # describes text that no longer exists. Checked before the revision
        # comparison because a stale echo is stale regardless of how high the
        # translation revision has climbed.
        if (
            event.translated_from_content_revision != existing.content_revision
            or event.translated_from_language_revision != existing.language_revision
        ):
            return ProjectionResult.refuse(
                RejectionReason.STALE,
                f"translation echoes content {event.translated_from_content_revision} "
                f"language {event.translated_from_language_revision}, but the segment is at "
                f"content {existing.content_revision} language {existing.language_revision}",
            )

        if event.translation_revision < existing.translation_revision:
            return ProjectionResult.refuse(
                RejectionReason.STALE,
                f"translation revision {event.translation_revision} is behind "
                f"{existing.translation_revision}",
            )

        if event.translation_revision == existing.translation_revision:
            if event.text != existing.translation_text:
                return _conflict(
                    event.segment_id,
                    event,
                    "translation",
                    event.translation_revision,
                    existing.translation_text,
                    event.text,
                )
            return ProjectionResult.refuse(
                RejectionReason.DUPLICATE,
                f"translation revision {event.translation_revision} repeated identically",
            )

        return ProjectionResult.ok(
            replace(
                existing,
                translation_status=TranslationStatus.COMPLETED,
                translation_text=event.text,
                source_language=event.source_language,
                target_language=event.target_language,
                translation_revision=event.translation_revision,
                translated_from_content_revision=event.translated_from_content_revision,
                translated_from_language_revision=event.translated_from_language_revision,
            )
        )

    def _apply_translation_failed(self, event: TranslationFailed) -> ProjectionResult:
        existing = self.segments.get(event.segment_id)
        if existing is None:
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE,
                f"translation.failed for unknown segment {event.segment_id}",
            )
        if existing.sealed:
            return self._sealed_refusal(existing, event)

        if event.translated_from_content_revision != existing.content_revision:
            return ProjectionResult.refuse(
                RejectionReason.STALE,
                f"failure echoes content revision {event.translated_from_content_revision}, "
                f"segment is at {existing.content_revision}",
            )

        # Section 17.4 and UI-100: a translation failure never removes or alters
        # the transcript. Only the translation fields move.
        return ProjectionResult.ok(
            replace(
                existing,
                translation_status=TranslationStatus.FAILED,
                translation_text="",
                translation_revision=event.translation_revision,
            )
        )

    # -- sealing -------------------------------------------------------------

    def seal(self, segment_id: str) -> ProjectionResult:
        """Seal one segment.

        ADR-0008 D14: sealing is triggered by a media-timeline deadline plus a
        terminal translation status, or unconditionally during graceful
        finalization. This method performs the seal; deciding *when* belongs to
        the orchestrator, which is the only component that knows the current
        sample position.
        """
        existing = self.segments.get(segment_id)
        if existing is None:
            return ProjectionResult.refuse(
                RejectionReason.NOT_APPLICABLE, f"unknown segment {segment_id}"
            )
        if existing.sealed:
            return ProjectionResult.refuse(
                RejectionReason.DUPLICATE, f"segment {segment_id} is already sealed"
            )

        sealed = replace(existing, sealed=True)
        self.segments[segment_id] = sealed
        return ProjectionResult.ok(sealed)

    # -- projection ----------------------------------------------------------

    def ordered_segments(self) -> list[SegmentProjection]:
        """Segments in timeline order.

        Ordered by ``start_sample`` and then ``segment_id``, both integers or
        monotonic strings, so the order is total and reproducible. Section 8.3
        requires the timeline to be ordered by source audio time, and PROT-160
        forbids using anything but the sample offset as the ordering authority.
        """
        return sorted(self.segments.values(), key=lambda s: (s.start_sample, s.segment_id))

    def compactable_segments(self) -> list[SegmentProjection]:
        """What ``history.final.jsonl`` contains.

        Section 25.14: exactly one latest valid projection per **non-rejected**
        segment.
        """
        return [
            segment
            for segment in self.ordered_segments()
            if segment.status is not SegmentStatus.REJECTED
        ]
