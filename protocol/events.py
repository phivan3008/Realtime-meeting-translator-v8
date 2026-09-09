"""Wire event schemas.

`requirements.md` Section 9.1 fixes the envelope and the two compatibility
rules; Section 9.3 lists the required event types; ADR-0005 records the two
extensions that Sections 25.9 and 25.12 force.

Every model sets ``extra="ignore"``. That is Section 9.1's "unknown optional
fields in the same major version shall be ignored safely", and it is only sound
because ADR-0010 D23 made minor versions additive-only: a field this build does
not know about cannot be a changed meaning of one it does.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from protocol import limits
from protocol.enums import (
    AsrLanguageMode,
    Capability,
    CapabilityState,
    GapClass,
    IdleClass,
    LanguageCode,
    LanguageStatus,
    SegmentOperation,
    SegmentStatus,
    SessionOutcome,
    SpeakerOperation,
    SpeakerStatus,
)
from protocol.identifiers import is_valid_session_id
from protocol.timeline import MAX_JSON_SAFE_SAMPLE
from protocol.version import PROTOCOL_VERSION

Identifier = Annotated[str, Field(max_length=limits.MAX_IDENTIFIER_CHARS)]
Text = Annotated[str, Field(max_length=limits.MAX_TEXT_CHARS)]
SampleOffset = Annotated[int, Field(ge=0, le=MAX_JSON_SAFE_SAMPLE)]
Revision = Annotated[int, Field(ge=0)]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class WireModel(BaseModel):
    """Base for everything that crosses the wire."""

    model_config = ConfigDict(
        extra="ignore",
        frozen=True,
        str_strip_whitespace=False,
        validate_assignment=False,
    )


class Envelope(WireModel):
    """The Section 9.1 envelope every control event carries."""

    protocol_version: str = PROTOCOL_VERSION
    event_type: str
    session_id: Identifier
    event_id: Identifier
    sent_at_utc: str

    @field_validator("session_id")
    @classmethod
    def _session_id_is_uuid4(cls, value: str) -> str:
        # Validated here rather than at the persistence layer because the id
        # reaches a filesystem path (PERS-100, SEC-060), and the earliest
        # rejection is the safest one.
        if not is_valid_session_id(value):
            raise ValueError("session_id must be a lowercase uuid4")
        return value


# --------------------------------------------------------------------------
# Shared payload structures
# --------------------------------------------------------------------------


class SpeakerCandidate(WireModel):
    """One entry in the Section 25.6 candidate list."""

    speaker_id: Identifier
    confidence: Confidence


class OverlapInterval(WireModel):
    """A detected overlap region, on the canonical timeline."""

    start_sample: SampleOffset
    end_sample: SampleOffset


class DecodeEvidence(WireModel):
    """Section 14.3 decode signals, collected when available.

    Every field is optional because Section 14.3 says "when available", and a
    backend that cannot report one must not be forced to invent it. A missing
    signal and a zero signal mean very different things to the acceptance gate.
    """

    avg_logprob: float | None = None
    no_speech_prob: float | None = None
    compression_ratio: float | None = None
    speech_ratio: float | None = None
    temperature_used: float | None = None
    peak: float | None = None
    rms: float | None = None
    clipping_ratio: float | None = None
    zero_ratio: float | None = None
    #: Samples inserted by the ADR-0009 D15 gap fill. Excluded from every ratio
    #: above; carried separately so the acceptance gate can see the dilution it
    #: was spared.
    synthetic_samples: int = 0


class RevisionVector(WireModel):
    """The Section 25.5 independent revisions carried by every projection."""

    content_revision: Revision = 0
    language_revision: Revision = 0
    speaker_revision: Revision = 0
    translation_revision: Revision = 0
    translated_from_content_revision: Revision | None = None
    translated_from_language_revision: Revision | None = None


class SegmentLineage(WireModel):
    """ADR-0008 D11. Present on create, split and merge; absent on a revision."""

    operation: SegmentOperation
    parent_segment_ids: list[Identifier] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------


class SessionStart(Envelope):
    event_type: Literal["session.start"] = "session.start"
    client_version: str
    requested_frame_ms: int = Field(ge=1, le=500)


class SessionStarted(Envelope):
    event_type: Literal["session.started"] = "session.started"
    #: ADR-0009 D16. The client records this and treats any later change as a
    #: server restart, which is unrecoverable in MVP (Section 25.10).
    server_epoch: Identifier
    stream_id: Identifier
    accepted_frame_ms: int = Field(ge=1, le=500)


class SessionResume(Envelope):
    """ADR-0009 D17. What the client knows it sent."""

    event_type: Literal["session.resume"] = "session.resume"
    stream_id: Identifier
    last_sent_sequence: int = Field(ge=0)
    last_sent_start_sample: SampleOffset


class SessionResumed(Envelope):
    """ADR-0009 D17. The first sample the server *needs*, not the last it holds.

    Asking for what is needed rather than reporting what is held turns every
    disagreement into a subtraction the client can act on.
    """

    event_type: Literal["session.resumed"] = "session.resumed"
    server_epoch: Identifier
    stream_id: Identifier
    resume_from_sample: SampleOffset


class SessionStop(Envelope):
    event_type: Literal["session.stop"] = "session.stop"
    final_sequence: int = Field(ge=0)
    final_end_sample: SampleOffset


class GapCount(WireModel):
    gap_class: GapClass
    count: int = Field(ge=0)
    total_samples: int = Field(ge=0)


class SessionSummary(Envelope):
    """ADR-0005 PROT-050, forced by the Section 25.9 stop sequence."""

    event_type: Literal["session.summary"] = "session.summary"
    outcome: SessionOutcome
    #: Specific reasons, never a bare label. ADR-0009 D19 makes `completed`
    #: strict, so the warnings list is what makes the distinction useful.
    reasons: list[str] = Field(default_factory=list)
    total_samples: SampleOffset
    segment_count: int = Field(ge=0)
    sealed_count: int = Field(ge=0)
    rejected_count: int = Field(ge=0)
    low_confidence_count: int = Field(ge=0)
    translation_failed_count: int = Field(ge=0)
    integrity_conflict_count: int = Field(ge=0)
    synthetic_samples_inserted: int = Field(ge=0)
    gaps: list[GapCount] = Field(default_factory=list)
    drain_overruns: list[str] = Field(default_factory=list)


class SessionStopped(Envelope):
    event_type: Literal["session.stopped"] = "session.stopped"
    server_epoch: Identifier
    outcome: SessionOutcome


# --------------------------------------------------------------------------
# Audio transport
# --------------------------------------------------------------------------


class AudioAck(Envelope):
    """ADR-0010 D21. Cumulative acknowledgement.

    ``acked_through_sample`` is the last *contiguously* received sample, so when
    a gap exists it stops at the near edge and stays there. The field that
    drives client buffer release is therefore also a gap signal.
    """

    event_type: Literal["audio.ack"] = "audio.ack"
    stream_id: Identifier
    acked_through_sample: SampleOffset
    queue_depth: int = Field(ge=0)
    overload: bool = False


class AudioGap(Envelope):
    """Section 25.8. A measured gap, not an estimated one (ADR-0008 D9)."""

    event_type: Literal["audio.gap"] = "audio.gap"
    stream_id: Identifier
    expected_sequence: int = Field(ge=0)
    received_sequence: int = Field(ge=0)
    start_sample: SampleOffset
    end_sample: SampleOffset
    missing_samples: int = Field(ge=0)
    gap_class: GapClass
    #: True when the gap was caused by client ring-buffer overflow rather than
    #: by the network (ADR-0010 D22). Downstream policy is identical; the
    #: distinction exists so the cause is diagnosable.
    from_client_overflow: bool = False


class AudioIdle(Envelope):
    """ADR-0015 D39. The endpoint produced nothing for a measurable span.

    **Not an `audio.gap`.** A gap means audio was lost and may have cut through
    speech; this means there was definitively no sound. Reusing the gap event
    would mark clean silence ``truncated_by_gap`` and tighten the ASR acceptance
    gate for no reason.

    ``detected_by`` is explicit because this is the single place the client uses
    wall time to make a statement about the media timeline. Section 25.1 keeps
    the sample offset as the only media authority, and a sample counter cannot
    answer "how much time passed while nothing arrived" - a counter that never
    advances looks the same after a second and after an hour.
    """

    event_type: Literal["audio.idle"] = "audio.idle"
    stream_id: Identifier
    start_sample: SampleOffset
    end_sample: SampleOffset
    idle_samples: int = Field(ge=0)
    idle_class: IdleClass
    detected_by: Literal["client_wall_clock"] = "client_wall_clock"


# --------------------------------------------------------------------------
# VAD
# --------------------------------------------------------------------------


class VadSpeechStarted(Envelope):
    event_type: Literal["vad.speech_started"] = "vad.speech_started"
    utterance_id: Identifier
    start_sample: SampleOffset


class VadSpeechEnded(Envelope):
    event_type: Literal["vad.speech_ended"] = "vad.speech_ended"
    utterance_id: Identifier
    start_sample: SampleOffset
    end_sample: SampleOffset
    truncated_by_gap: bool = False


# --------------------------------------------------------------------------
# Transcription
# --------------------------------------------------------------------------


class TranscriptPartial(Envelope):
    """Section 13.2. Every partial is replaceable."""

    event_type: Literal["transcript.partial"] = "transcript.partial"
    utterance_id: Identifier
    segment_id: Identifier
    lineage: SegmentLineage | None = None
    content_revision: Revision
    status: Literal[SegmentStatus.PARTIAL] = SegmentStatus.PARTIAL
    stable_text: Text = ""
    unstable_text: Text = ""
    start_sample: SampleOffset
    end_sample: SampleOffset
    language_id: LanguageCode | None = None
    language_status: LanguageStatus = LanguageStatus.UNKNOWN
    language_revision: Revision = 0
    #: Section 25.3: a partial emitted before a language decision must say what
    #: basis it used, or its text cannot be interpreted afterwards.
    asr_language_mode: AsrLanguageMode = AsrLanguageMode.AUTO
    primary_speaker_id: Identifier | None = None
    speaker_status: SpeakerStatus = SpeakerStatus.UNKNOWN
    speaker_revision: Revision = 0


class TranscriptFinal(Envelope):
    """Section 13.3, published only after the acceptance gate."""

    event_type: Literal["transcript.final"] = "transcript.final"
    utterance_id: Identifier
    segment_id: Identifier
    lineage: SegmentLineage | None = None
    content_revision: Revision
    status: SegmentStatus
    text: Text = ""
    start_sample: SampleOffset
    end_sample: SampleOffset
    language_id: LanguageCode | None = None
    language_status: LanguageStatus = LanguageStatus.UNKNOWN
    language_revision: Revision = 0
    primary_speaker_id: Identifier | None = None
    speaker_status: SpeakerStatus = SpeakerStatus.UNKNOWN
    speaker_revision: Revision = 0
    speaker_candidates: list[SpeakerCandidate] = Field(default_factory=list)
    overlap: bool = False
    overlap_intervals: list[OverlapInterval] = Field(default_factory=list)
    evidence: DecodeEvidence | None = None
    intersects_gap: bool = False

    @field_validator("status")
    @classmethod
    def _status_is_a_final_outcome(cls, value: SegmentStatus) -> SegmentStatus:
        # ASR_FINAL_CANDIDATE is internal (Section 25.4) and must never reach the
        # wire as if it were an outcome.
        if not value.is_terminal_asr_outcome:
            raise ValueError("transcript.final status must be accepted, low_confidence or rejected")
        return value


class TranscriptRevised(TranscriptFinal):
    """Section 25.4. An accepted content revision may be replaced before sealing."""

    event_type: Literal["transcript.revised"] = "transcript.revised"  # type: ignore[assignment]


# --------------------------------------------------------------------------
# Language and speaker
# --------------------------------------------------------------------------


class LanguageUpdated(Envelope):
    """Section 12.3. Language ID is shared pipeline state, not a UI label."""

    event_type: Literal["language.updated"] = "language.updated"
    segment_id: Identifier
    language_revision: Revision
    language_id: LanguageCode | None = None
    language_status: LanguageStatus
    speechbrain_confidence: Confidence | None = None
    whisper_language: LanguageCode | None = None
    disagreement: bool = False


class SpeakerUpdated(Envelope):
    """Section 25.7. Assign, merge or split, with aliases preserved."""

    event_type: Literal["speaker.updated"] = "speaker.updated"
    operation: SpeakerOperation
    speaker_revision: Revision
    segment_id: Identifier | None = None
    primary_speaker_id: Identifier | None = None
    speaker_status: SpeakerStatus = SpeakerStatus.PROVISIONAL
    speaker_candidates: list[SpeakerCandidate] = Field(default_factory=list)
    from_speaker_ids: list[Identifier] = Field(default_factory=list)
    to_speaker_id: Identifier | None = None

    @model_validator(mode="after")
    def _merge_needs_a_target(self) -> SpeakerUpdated:
        # Section 25.7: merged IDs become aliases of a canonical target. A merge
        # without a target would leave the sources aliasing nothing, and the
        # projection could not rewrite affected views.
        if self.operation is SpeakerOperation.MERGE:
            if not self.from_speaker_ids or self.to_speaker_id is None:
                raise ValueError("a merge requires from_speaker_ids and to_speaker_id")
            if self.to_speaker_id in self.from_speaker_ids:
                raise ValueError("a merge target cannot also be one of its sources")
        return self


# --------------------------------------------------------------------------
# Translation
# --------------------------------------------------------------------------


class TranslationStarted(Envelope):
    event_type: Literal["translation.started"] = "translation.started"
    segment_id: Identifier
    source_language: LanguageCode
    target_language: LanguageCode


class TranslationFinal(Envelope):
    """Section 17.4 and 25.5.

    Both echoed source revisions are required. A response whose echoes no longer
    match the segment's current revisions is stale and is refused by the
    reducer, which is how a translation of superseded text is prevented from
    reaching the timeline.
    """

    event_type: Literal["translation.final"] = "translation.final"
    segment_id: Identifier
    translation_revision: Revision
    translated_from_content_revision: Revision
    translated_from_language_revision: Revision
    source_language: LanguageCode
    target_language: LanguageCode
    text: Text


class TranslationFailed(Envelope):
    """Section 17.4: a translation failure never invalidates the transcript."""

    event_type: Literal["translation.failed"] = "translation.failed"
    segment_id: Identifier
    translation_revision: Revision
    translated_from_content_revision: Revision
    translated_from_language_revision: Revision
    reason: str
    retryable: bool = True


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------


class CapabilityEntry(WireModel):
    capability: Capability
    state: CapabilityState
    detail: str | None = None


class CapabilityUpdated(Envelope):
    """ADR-0005 PROT-060, forced by Section 25.12.

    Carries the whole capability state rather than a delta, so a client that
    joined late or missed a warning still renders degradation correctly. A
    warning is an occurrence; this is state.
    """

    event_type: Literal["capability.updated"] = "capability.updated"
    capabilities: list[CapabilityEntry]


class PipelineWarning(Envelope):
    event_type: Literal["pipeline.warning"] = "pipeline.warning"
    code: str
    detail: str
    segment_id: Identifier | None = None


class PipelineError(Envelope):
    event_type: Literal["pipeline.error"] = "pipeline.error"
    code: str
    detail: str
    fatal: bool = False
    segment_id: Identifier | None = None


class MetricsSnapshot(Envelope):
    """Section 18. Measured values only; never a target or a threshold."""

    event_type: Literal["metrics.snapshot"] = "metrics.snapshot"
    metrics: dict[str, float] = Field(default_factory=dict)
    queue_depths: dict[str, int] = Field(default_factory=dict)


#: Every event type this build knows. An unknown type within a known major is
#: ignored safely and logged (ADR-0005), so this map is a dispatch table, not a
#: gate.
EVENT_MODELS: dict[str, type[Envelope]] = {
    "session.start": SessionStart,
    "session.started": SessionStarted,
    "session.resume": SessionResume,
    "session.resumed": SessionResumed,
    "session.stop": SessionStop,
    "session.stopped": SessionStopped,
    "session.summary": SessionSummary,
    "audio.ack": AudioAck,
    "audio.gap": AudioGap,
    "audio.idle": AudioIdle,
    "vad.speech_started": VadSpeechStarted,
    "vad.speech_ended": VadSpeechEnded,
    "transcript.partial": TranscriptPartial,
    "transcript.final": TranscriptFinal,
    "transcript.revised": TranscriptRevised,
    "translation.started": TranslationStarted,
    "translation.final": TranslationFinal,
    "translation.failed": TranslationFailed,
    "speaker.updated": SpeakerUpdated,
    "language.updated": LanguageUpdated,
    "capability.updated": CapabilityUpdated,
    "pipeline.warning": PipelineWarning,
    "pipeline.error": PipelineError,
    "metrics.snapshot": MetricsSnapshot,
}
