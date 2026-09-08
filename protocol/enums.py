"""Enumerations shared by the wire schema and the projection.

Every value here is fixed by `requirements.md` and may not be extended without
an approved requirement change. They live in one module so that the schema, the
reducer and the persistence layer cannot each grow their own slightly different
spelling of the same state.
"""

from __future__ import annotations

from enum import StrEnum


class LanguageCode(StrEnum):
    """Section 12.1. Supported normal output is only Japanese or Vietnamese."""

    JA = "ja"
    VI = "vi"


class LanguageStatus(StrEnum):
    """Section 12.3.

    ``language_status`` is deliberately separate from ``language_id``: a segment
    can have a language *guess* while the decision is still provisional, and the
    two fields let a consumer tell those apart without inspecting confidences.
    """

    UNKNOWN = "unknown"
    PROVISIONAL_JA = "provisional_ja"
    PROVISIONAL_VI = "provisional_vi"
    CONFIRMED_JA = "confirmed_ja"
    CONFIRMED_VI = "confirmed_vi"
    MIXED = "mixed"
    UNCERTAIN = "uncertain"

    @property
    def is_resolved(self) -> bool:
        """Whether translation may proceed.

        Section 12.3 and Section 14.6: translation requires a resolved source
        language. ``mixed`` and ``uncertain`` are schema-supported states, not
        resolutions, so neither qualifies.
        """
        return self in (LanguageStatus.CONFIRMED_JA, LanguageStatus.CONFIRMED_VI)


class AsrLanguageMode(StrEnum):
    """Section 25.3. What a pre-decision partial used as its language basis.

    A partial emitted before language identification has to say what it assumed,
    otherwise its text cannot be interpreted later.
    """

    AUTO = "auto"
    SESSION_HINT = "session_hint"
    SPEAKER_HINT = "speaker_hint"
    PROVISIONAL_LID = "provisional_lid"
    CONFIRMED_LID = "confirmed_lid"


class SegmentStatus(StrEnum):
    """The Section 25.4 lifecycle.

    ``ASR_FINAL_CANDIDATE`` is internal and is never treated as accepted text.
    ``LOW_CONFIDENCE`` is not eligible for translation by default.
    ``REJECTED`` retains diagnostics but no authoritative text.
    """

    CREATED = "created"
    PARTIAL = "partial"
    ASR_FINAL_CANDIDATE = "asr_final_candidate"
    ACCEPTED = "accepted"
    LOW_CONFIDENCE = "low_confidence"
    REJECTED = "rejected"
    SEALED = "sealed"

    @property
    def is_terminal_asr_outcome(self) -> bool:
        return self in (
            SegmentStatus.ACCEPTED,
            SegmentStatus.LOW_CONFIDENCE,
            SegmentStatus.REJECTED,
        )

    @property
    def is_translatable(self) -> bool:
        """Only an accepted final may be translated (Section 14.6, TRN-160)."""
        return self is SegmentStatus.ACCEPTED


class TranslationStatus(StrEnum):
    """Section 17.4, extended by Section 25.4 with the not-applicable case."""

    NOT_APPLICABLE = "not_applicable"
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Whether sealing may proceed (ADR-0008 D14)."""
        return self in (
            TranslationStatus.COMPLETED,
            TranslationStatus.FAILED,
            TranslationStatus.NOT_APPLICABLE,
        )


class SpeakerStatus(StrEnum):
    """Section 15.2 and 25.6. Speaker labels are provisional and revisable."""

    UNKNOWN = "unknown"
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"


class SegmentOperation(StrEnum):
    """ADR-0008 D11 lineage operations."""

    CREATE = "create"
    REVISE = "revise"
    SPLIT = "split"
    MERGE = "merge"
    SEAL = "seal"


class SpeakerOperation(StrEnum):
    """Section 25.7 speaker reconciliation operations."""

    ASSIGN = "assign"
    MERGE = "merge"
    SPLIT = "split"


class GapClass(StrEnum):
    """Section 25.8 gap duration classes. Thresholds are benchmark_required."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class SessionOutcome(StrEnum):
    """ADR-0009 D19."""

    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"


class Capability(StrEnum):
    """The components whose degradation Section 25.12 requires to be visible."""

    FINAL_ASR = "final_asr"
    PARTIAL_ASR = "partial_asr"
    SPEECHBRAIN_LID = "speechbrain_lid"
    SPEAKER_EMBEDDING = "speaker_embedding"
    PYANNOTE = "pyannote"
    TRANSLATION = "translation"
    DEBUG_WRITER = "debug_writer"
    HISTORY_PROJECTION = "history_projection"


class CapabilityState(StrEnum):
    """Section 25.12. ``unavailable`` is not a silent substitution; it is stated."""

    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class RecordKind(StrEnum):
    """ADR-0011 D25. What a debug log record wraps."""

    WIRE_EVENT = "wire_event"
    CLIENT_LOCAL = "client_local"
    INTEGRITY_CONFLICT = "integrity_conflict"


class RejectionReason(StrEnum):
    """ADR-0008 D13. Why the reducer refused an event.

    These are return values, never exceptions: Section 25.5 requires stale
    results to be persisted as diagnostic events, which a raised exception makes
    awkward and a crash makes impossible.
    """

    DUPLICATE = "duplicate"
    STALE = "stale"
    INTEGRITY_CONFLICT = "integrity_conflict"
    NOT_APPLICABLE = "not_applicable"


TRANSLATION_DIRECTION: dict[LanguageCode, LanguageCode] = {
    LanguageCode.JA: LanguageCode.VI,
    LanguageCode.VI: LanguageCode.JA,
}
"""Section 12.1. Fixed, and never inferred by the translation model (TRN-070)."""


CONFIRMED_STATUS_LANGUAGE: dict[LanguageStatus, LanguageCode] = {
    LanguageStatus.CONFIRMED_JA: LanguageCode.JA,
    LanguageStatus.CONFIRMED_VI: LanguageCode.VI,
}


def target_language(source: LanguageCode) -> LanguageCode:
    """The language a source language translates into.

    Raises:
        KeyError: for a language outside the supported pair, which cannot happen
            through the schema but can through a bug.
    """
    return TRANSLATION_DIRECTION[source]
