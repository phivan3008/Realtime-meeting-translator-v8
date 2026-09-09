"""The timeline, without Qt.

`requirements.md` Section 8.3 describes what a timeline row shows and how it
behaves. All of that is decided here, in plain Python, so it can be tested
without a display: what a row contains, how rows are ordered, when a row is
replaced, and how a partial is distinguished from a final.

The Qt layer above is a renderer. That split is deliberate and follows from
ADR-0016 D41: **the UI never folds events itself.** It receives projections from
the shared reducer (ADR-0008 D13) and turns them into rows. If it had its own
projection logic, the timeline a user sees and the file on disk could disagree,
and that disagreement would surface after the meeting ended.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from protocol.enums import LanguageCode, LanguageStatus, SegmentStatus, TranslationStatus
from protocol.projection import SegmentProjection
from protocol.timeline import samples_to_ms

#: What a row shows when there is no speaker yet. Section 25.6 allows a null
#: primary speaker, and "unknown" is an honest rendering of it.
UNKNOWN_SPEAKER = "speaker-unknown"


def format_timestamp(sample_offset: int) -> str:
    """``mm:ss.mmm`` from a canonical sample offset.

    Display only. Section 25.1 keeps the sample offset as the ordering and
    identity authority, and PROT-160 forbids anything else taking that role -
    including this string, which exists to be read rather than compared.
    """
    total_ms = samples_to_ms(sample_offset)
    minutes, remainder_ms = divmod(total_ms, 60_000)
    seconds, milliseconds = divmod(remainder_ms, 1000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


@dataclass(frozen=True, slots=True)
class TimelineRow:
    """One rendered line of the meeting.

    Section 8.3 lists what must be visible: speaker, language, source start and
    end time, a transcription row, a translation row, the partial or final state,
    an overlap indicator, and a low-confidence warning.
    """

    segment_id: str
    start_sample: int
    end_sample: int
    speaker: str
    language: str
    transcript: str
    translation: str
    is_final: bool
    is_low_confidence: bool
    is_rejected: bool
    has_overlap: bool
    translation_pending: bool
    translation_failed: bool
    sealed: bool

    @property
    def start_display(self) -> str:
        return format_timestamp(self.start_sample)

    @property
    def end_display(self) -> str:
        return format_timestamp(self.end_sample)

    @property
    def warnings(self) -> list[str]:
        """Everything the row needs to say about itself beyond its text.

        Assembled here rather than in the renderer so the set is testable and so
        two different views cannot disagree about what a row is warning about.
        """
        notes: list[str] = []
        if self.is_rejected:
            notes.append("rejected")
        if self.is_low_confidence:
            notes.append("low confidence")
        if self.has_overlap:
            notes.append("overlap")
        if self.translation_failed:
            notes.append("translation failed")
        return notes

    @property
    def shows_translation_placeholder(self) -> bool:
        """Section 8.3: a pending state after final ASR and before Qwen returns."""
        return self.translation_pending and not self.translation

    @classmethod
    def from_projection(cls, segment: SegmentProjection) -> TimelineRow:
        return cls(
            segment_id=segment.segment_id,
            start_sample=segment.start_sample,
            end_sample=segment.end_sample,
            speaker=segment.primary_speaker_id or UNKNOWN_SPEAKER,
            language=_language_display(segment.language_id, segment.language_status),
            transcript=segment.display_text,
            translation=segment.translation_text,
            is_final=segment.status is not SegmentStatus.PARTIAL,
            is_low_confidence=segment.status is SegmentStatus.LOW_CONFIDENCE,
            is_rejected=segment.status is SegmentStatus.REJECTED,
            has_overlap=segment.overlap,
            translation_pending=segment.translation_status is TranslationStatus.PENDING,
            translation_failed=segment.translation_status is TranslationStatus.FAILED,
            sealed=segment.sealed,
        )


def _language_display(code: LanguageCode | None, status: LanguageStatus) -> str:
    """What the language column shows.

    Section 12.3 separates ``language_id`` from ``language_status`` precisely so a
    provisional guess is distinguishable from a confirmed decision. Collapsing
    them into one label would hide the difference the requirement created.
    """
    if code is None:
        return "?" if status is LanguageStatus.UNKNOWN else str(status)
    if status in (LanguageStatus.CONFIRMED_JA, LanguageStatus.CONFIRMED_VI):
        return str(code)
    if status in (LanguageStatus.PROVISIONAL_JA, LanguageStatus.PROVISIONAL_VI):
        return f"{code}?"
    return f"{code} ({status})"


@dataclass(slots=True)
class TimelineState:
    """Rows keyed by ``segment_id``, ordered by source audio time.

    Upsert by identifier, never append (UI-120, UI-140). A partial that arrives
    fifty times a second replaces one row fifty times; it does not produce fifty
    rows.
    """

    rows: dict[str, TimelineRow] = field(default_factory=dict)
    _order_dirty: bool = field(default=True, init=False)
    _ordered: list[TimelineRow] = field(default_factory=list, init=False)

    def upsert(self, segment: SegmentProjection) -> bool:
        """Insert or replace one row. Returns whether anything changed.

        The return value drives the UI's dirty set (ADR-0016 D41): a projection
        that produces an identical row costs no repaint.
        """
        row = TimelineRow.from_projection(segment)
        previous = self.rows.get(row.segment_id)
        if previous == row:
            return False

        self.rows[row.segment_id] = row
        # Ordering only changes when a row appears or its start moves; a text
        # revision leaves the sequence alone.
        if previous is None or previous.start_sample != row.start_sample:
            self._order_dirty = True
        else:
            self._replace_in_place(row)
        return True

    def _replace_in_place(self, row: TimelineRow) -> None:
        for index, existing in enumerate(self._ordered):
            if existing.segment_id == row.segment_id:
                self._ordered[index] = row
                return
        self._order_dirty = True

    def ordered(self) -> list[TimelineRow]:
        """Rows in source audio time order (Section 8.3, UI-060).

        Sorted by ``start_sample`` then ``segment_id`` - both integers or
        monotonic strings, so the order is total and reproducible. PROT-160
        forbids any other ordering authority, which rules out arrival time.
        """
        if self._order_dirty:
            self._ordered = sorted(
                self.rows.values(), key=lambda row: (row.start_sample, row.segment_id)
            )
            self._order_dirty = False
        return list(self._ordered)

    def row_index(self, segment_id: str) -> int | None:
        """Where a row sits in the ordered view, or None if it is not there."""
        for index, row in enumerate(self.ordered()):
            if row.segment_id == segment_id:
                return index
        return None

    def clear(self) -> None:
        self.rows.clear()
        self._ordered.clear()
        self._order_dirty = True

    @property
    def count(self) -> int:
        return len(self.rows)

    def transcript_text(self) -> str:
        """The meeting as plain text, in order. Used for copy-out and for tests."""
        return "\n".join(
            f"[{row.start_display}] {row.speaker} ({row.language}): {row.transcript}"
            for row in self.ordered()
            if row.transcript and not row.is_rejected
        )
