"""Timeline, update coalescing and replay-harness conformance vectors.

Category B (`requirements.md` Section 25.15 B). Purpose-built projections and
manifests exercising row rendering, ordering, upsert-not-append, coalescing, and
the provenance checks a replay fixture must pass before it counts as evidence.

Nothing here claims anything about ASR, language, speaker, overlap or
translation quality (TEST-130).

**Category C is blocked, and this module says so rather than filling the gap.**
Section 25.15 C requires replay tests to use immutable traffic captured from an
actual server run. No server exists until Phase 4, so no real capture exists.
`TestReplayIsBlocked` records that as an explicit, visible skip - inventing an
exchange to make the suite green would be exactly the fabricated evidence
Section 22.3 forbids (TEST-210).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from client.replay import (
    Capture,
    CaptureError,
    CaptureManifest,
    check_protocol_version,
    load_capture,
    load_manifests,
    replay,
    verify_capture_file,
)
from client.ui.timeline import (
    UNKNOWN_SPEAKER,
    TimelineRow,
    TimelineState,
    format_timestamp,
)
from client.ui.updates import ChangeAccumulator, CoalescingScheduler
from protocol.enums import (
    LanguageCode,
    LanguageStatus,
    SegmentStatus,
    SpeakerStatus,
    TranslationStatus,
)
from protocol.projection import SegmentProjection

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "protocol_conformance_fixture"

SESSION_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
CAPTURE_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "manifests" / "captures.yaml"


def segment(
    segment_id: str = "seg-000001",
    *,
    start_sample: int = 16_000,
    end_sample: int = 48_000,
    status: SegmentStatus = SegmentStatus.PARTIAL,
    stable_text: str = "",
    unstable_text: str = "",
    text: str = "",
    speaker: str | None = None,
    language: LanguageCode | None = None,
    language_status: LanguageStatus = LanguageStatus.UNKNOWN,
    translation_status: TranslationStatus = TranslationStatus.NOT_APPLICABLE,
    translation_text: str = "",
    overlap: bool = False,
    sealed: bool = False,
) -> SegmentProjection:
    return SegmentProjection(
        segment_id=segment_id,
        utterance_id="utt-000001",
        start_sample=start_sample,
        end_sample=end_sample,
        status=status,
        stable_text=stable_text,
        unstable_text=unstable_text,
        text=text,
        primary_speaker_id=speaker,
        speaker_status=SpeakerStatus.PROVISIONAL if speaker else SpeakerStatus.UNKNOWN,
        language_id=language,
        language_status=language_status,
        translation_status=translation_status,
        translation_text=translation_text,
        overlap=overlap,
        sealed=sealed,
    )


# ---------------------------------------------------------------------------
# Row rendering - Section 8.3
# ---------------------------------------------------------------------------


class TestTimestampDisplay:
    @pytest.mark.parametrize(
        ("samples", "expected"),
        [
            (0, "00:00.000"),
            (16_000, "00:01.000"),
            (16 * 1_500, "00:01.500"),
            (16_000 * 61, "01:01.000"),
            (29_139_328, "30:21.208"),
        ],
    )
    def test_it_reads_as_a_timestamp(self, samples: int, expected: str) -> None:
        """The real recording is 30:21.208, so the last case is not hypothetical."""
        assert format_timestamp(samples) == expected

    def test_it_is_display_only(self) -> None:
        """PROT-160: the sample offset is the ordering authority, not this string.

        Asserted because a lexicographic comparison of these strings happens to
        work up to 99 minutes and then silently does not.
        """
        assert format_timestamp(16_000 * 60 * 100) == "100:00.000"
        assert format_timestamp(16_000 * 60 * 100) < format_timestamp(16_000 * 60 * 99)


class TestRowRendering:
    def test_a_partial_shows_stable_and_unstable_text_together(self) -> None:
        row = TimelineRow.from_projection(
            segment(stable_text="明日の会議は", unstable_text="午前十時")
        )
        assert row.transcript == "明日の会議は午前十時"
        assert not row.is_final

    def test_a_final_replaces_the_partial_text(self) -> None:
        row = TimelineRow.from_projection(
            segment(status=SegmentStatus.ACCEPTED, text="明日の会議は午前十時です")
        )
        assert row.transcript == "明日の会議は午前十時です"
        assert row.is_final

    def test_a_missing_speaker_renders_as_unknown(self) -> None:
        """Section 25.6 allows a null primary speaker; an empty cell would read
        as a missing value rather than an honest one."""
        assert TimelineRow.from_projection(segment()).speaker == UNKNOWN_SPEAKER

    @pytest.mark.parametrize(
        ("code", "status", "expected"),
        [
            (None, LanguageStatus.UNKNOWN, "?"),
            (LanguageCode.JA, LanguageStatus.CONFIRMED_JA, "ja"),
            (LanguageCode.VI, LanguageStatus.CONFIRMED_VI, "vi"),
            (LanguageCode.JA, LanguageStatus.PROVISIONAL_JA, "ja?"),
            (LanguageCode.JA, LanguageStatus.UNCERTAIN, "ja (uncertain)"),
        ],
    )
    def test_provisional_language_is_distinguishable_from_confirmed(
        self, code: LanguageCode | None, status: LanguageStatus, expected: str
    ) -> None:
        """Section 12.3 separates language_id from language_status precisely so a
        guess is distinguishable from a decision. Collapsing them into one label
        would hide the difference the requirement created."""
        row = TimelineRow.from_projection(segment(language=code, language_status=status))
        assert row.language == expected

    def test_a_pending_translation_shows_a_placeholder_slot(self) -> None:
        """Section 8.3 requires a pending state between final ASR and Qwen."""
        row = TimelineRow.from_projection(
            segment(
                status=SegmentStatus.ACCEPTED,
                text="はい",
                translation_status=TranslationStatus.PENDING,
            )
        )
        assert row.shows_translation_placeholder

    def test_an_arrived_translation_stops_being_pending(self) -> None:
        row = TimelineRow.from_projection(
            segment(
                status=SegmentStatus.ACCEPTED,
                text="はい",
                translation_status=TranslationStatus.COMPLETED,
                translation_text="Vâng",
            )
        )
        assert not row.shows_translation_placeholder
        assert row.translation == "Vâng"

    def test_a_failed_translation_leaves_the_transcript_alone(self) -> None:
        """UI-100 and Section 17.4."""
        row = TimelineRow.from_projection(
            segment(
                status=SegmentStatus.ACCEPTED,
                text="明日の会議",
                translation_status=TranslationStatus.FAILED,
            )
        )
        assert row.transcript == "明日の会議"
        assert "translation failed" in row.warnings

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            ({"status": SegmentStatus.LOW_CONFIDENCE}, "low confidence"),
            ({"status": SegmentStatus.REJECTED}, "rejected"),
            ({"overlap": True}, "overlap"),
        ],
    )
    def test_every_section_8_3_marker_appears_in_the_warnings(
        self, kwargs: dict[str, object], expected: str
    ) -> None:
        row = TimelineRow.from_projection(segment(**kwargs))  # type: ignore[arg-type]
        assert expected in row.warnings

    def test_a_clean_final_warns_about_nothing(self) -> None:
        row = TimelineRow.from_projection(segment(status=SegmentStatus.ACCEPTED, text="はい"))
        assert row.warnings == []


# ---------------------------------------------------------------------------
# Upsert, not append - UI-120, UI-140
# ---------------------------------------------------------------------------


class TestTimelineState:
    def test_fifty_partials_produce_one_row(self) -> None:
        """UI-140. The single most important behaviour in the timeline."""
        state = TimelineState()
        for revision in range(50):
            state.upsert(segment(stable_text="明日" * (revision + 1)))

        assert state.count == 1

    def test_an_identical_projection_reports_no_change(self) -> None:
        """A projection that renders the same row costs no repaint."""
        state = TimelineState()
        assert state.upsert(segment(stable_text="明日"))
        assert not state.upsert(segment(stable_text="明日"))

    def test_rows_are_ordered_by_sample_offset_not_arrival(self) -> None:
        """UI-060 and PROT-160: arrival time is never the ordering authority."""
        state = TimelineState()
        state.upsert(segment("seg-000002", start_sample=48_000, end_sample=80_000))
        state.upsert(segment("seg-000001", start_sample=16_000, end_sample=48_000))

        assert [row.segment_id for row in state.ordered()] == ["seg-000001", "seg-000002"]

    def test_a_text_revision_does_not_reorder(self) -> None:
        state = TimelineState()
        state.upsert(segment("seg-000001", start_sample=16_000))
        state.upsert(segment("seg-000002", start_sample=48_000))
        before = [row.segment_id for row in state.ordered()]

        state.upsert(segment("seg-000001", start_sample=16_000, stable_text="new"))

        assert [row.segment_id for row in state.ordered()] == before

    def test_a_moved_start_sample_does_reorder(self) -> None:
        """A split narrows a segment's span (ADR-0008 D11), which can move it."""
        state = TimelineState()
        state.upsert(segment("seg-000001", start_sample=48_000))
        state.upsert(segment("seg-000002", start_sample=64_000))

        state.upsert(segment("seg-000001", start_sample=80_000))

        assert [row.segment_id for row in state.ordered()] == ["seg-000002", "seg-000001"]

    def test_row_index_locates_a_segment(self) -> None:
        state = TimelineState()
        state.upsert(segment("seg-000001", start_sample=16_000))
        state.upsert(segment("seg-000002", start_sample=48_000))

        assert state.row_index("seg-000002") == 1
        assert state.row_index("seg-000404") is None

    def test_the_transcript_view_skips_rejected_segments(self) -> None:
        """Section 25.4: a rejected segment retains diagnostics but no
        authoritative text, so it does not belong in a readable transcript."""
        state = TimelineState()
        state.upsert(segment("seg-000001", status=SegmentStatus.ACCEPTED, text="はい"))
        state.upsert(
            segment(
                "seg-000002",
                start_sample=48_000,
                status=SegmentStatus.REJECTED,
                text="ゴミ",
            )
        )

        assert "はい" in state.transcript_text()
        assert "ゴミ" not in state.transcript_text()


# ---------------------------------------------------------------------------
# Coalescing - ADR-0016 D41
# ---------------------------------------------------------------------------


class TestChangeAccumulator:
    def test_repeated_changes_to_one_segment_collapse(self) -> None:
        accumulator = ChangeAccumulator()
        for _ in range(50):
            accumulator.record("seg-000001")

        assert accumulator.drain() == {"seg-000001"}

    def test_the_saving_is_measured_not_assumed(self) -> None:
        """The coalescing ratio is what justifies the mechanism, so it is a
        counter rather than a claim."""
        accumulator = ChangeAccumulator()
        for _ in range(50):
            accumulator.record("seg-000001")
        accumulator.drain()

        assert accumulator.stats.changes_recorded == 50
        assert accumulator.stats.coalesced_away == 49
        assert accumulator.stats.segments_published == 1
        assert accumulator.stats.coalescing_ratio == pytest.approx(0.98)

    def test_a_drain_empties_the_accumulator(self) -> None:
        accumulator = ChangeAccumulator()
        accumulator.record("seg-000001")

        accumulator.drain()

        assert accumulator.pending_count == 0
        assert accumulator.drain() == set()

    def test_distinct_segments_are_all_published(self) -> None:
        accumulator = ChangeAccumulator()
        accumulator.record_many(["seg-000001", "seg-000002", "seg-000001"])

        assert accumulator.drain() == {"seg-000001", "seg-000002"}


class TestCoalescingScheduler:
    def _clock(self, ticks: list[float]) -> object:
        import contextlib

        stream = iter(ticks)
        last = ticks[-1]

        def read() -> float:
            nonlocal last
            with contextlib.suppress(StopIteration):
                last = next(stream)
            return last

        return read

    def test_nothing_pending_is_never_due(self) -> None:
        """Publishing nothing still costs a signal, a slot call and a repaint
        decision on the GUI thread."""
        scheduler = CoalescingScheduler(interval_s=0.1, clock=self._clock([0.0, 10.0]))  # type: ignore[arg-type]
        assert not scheduler.due(has_pending=False)

    def test_before_the_interval_is_not_due(self) -> None:
        scheduler = CoalescingScheduler(interval_s=0.1, clock=self._clock([0.0, 0.05]))  # type: ignore[arg-type]
        scheduler.start()
        assert not scheduler.due(has_pending=True)

    def test_after_the_interval_is_due(self) -> None:
        scheduler = CoalescingScheduler(interval_s=0.1, clock=self._clock([0.0, 0.2]))  # type: ignore[arg-type]
        scheduler.start()
        assert scheduler.due(has_pending=True)

    def test_draining_resets_the_interval(self) -> None:
        scheduler = CoalescingScheduler(
            interval_s=0.1,
            clock=self._clock([0.0, 0.2, 0.2, 0.25]),  # type: ignore[arg-type]
        )
        scheduler.start()
        assert scheduler.due(has_pending=True)
        scheduler.mark_drained()
        assert not scheduler.due(has_pending=True)


# ---------------------------------------------------------------------------
# Replay provenance - TEST-140
# ---------------------------------------------------------------------------


def manifest_dict(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "capture_id": "capture-001",
        "location": "data/captures/capture-001.jsonl",
        "sha256": "0" * 64,
        "protocol_version": "1.0",
        "config_hash": "abc123",
        "captured_at": "2026-09-09T10:00:00+00:00",
        "captured_from": "pod localhost:8760",
        "session_id": SESSION_ID,
        "event_count": 2,
    }
    data.update(overrides)
    return data


def write_capture(path: Path, events: list[dict[str, object]]) -> str:
    lines = [
        json.dumps(
            {"ordinal": index, "direction": "inbound", "payload": payload},
            separators=(",", ":"),
        )
        for index, payload in enumerate(events)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestCaptureProvenance:
    """TEST-140. A capture without provenance is not a citation to a real run."""

    @pytest.mark.parametrize(
        "missing",
        ["sha256", "protocol_version", "config_hash", "captured_at", "session_id"],
    )
    def test_every_required_provenance_field_is_enforced(self, missing: str) -> None:
        data = manifest_dict()
        del data[missing]

        with pytest.raises(CaptureError, match="missing"):
            CaptureManifest.from_dict(data)

    def test_a_changed_capture_is_refused(self, tmp_path: Path) -> None:
        """Section 22.2 calls a replay fixture "not a mock" only while it
        reproduces an immutable exchange. A file whose bytes changed is a file
        that once was one, which is a different thing and a worse one."""
        path = tmp_path / "capture.jsonl"
        write_capture(path, [{"event_type": "audio.ack"}])
        manifest = CaptureManifest.from_dict(manifest_dict(sha256="f" * 64))

        with pytest.raises(CaptureError, match="hash mismatch"):
            verify_capture_file(path, manifest)

    def test_a_missing_capture_says_where_it_should_be(self, tmp_path: Path) -> None:
        manifest = CaptureManifest.from_dict(manifest_dict())
        with pytest.raises(CaptureError, match="never committed"):
            verify_capture_file(tmp_path / "absent.jsonl", manifest)

    def test_a_different_major_version_cannot_be_folded(self) -> None:
        """Field meanings may differ across a major version (ADR-0010 D23)."""
        manifest = CaptureManifest.from_dict(manifest_dict(protocol_version="2.0"))
        mismatch = check_protocol_version(manifest)
        assert mismatch is not None
        assert "major" in mismatch

    def test_a_different_minor_version_is_fine(self) -> None:
        """Minor versions are additive only, so an older reader is still correct."""
        manifest = CaptureManifest.from_dict(manifest_dict(protocol_version="1.7"))
        assert check_protocol_version(manifest) is None


class TestReplayHarness:
    def _capture(self, tmp_path: Path) -> Capture:
        events: list[dict[str, object]] = [
            {
                "protocol_version": "1.0",
                "event_type": "transcript.final",
                "session_id": SESSION_ID,
                "event_id": "00000000-0000-4000-8000-000000000001",
                "sent_at_utc": "2026-09-09T10:00:00Z",
                "utterance_id": "utt-000001",
                "segment_id": "seg-000001",
                "content_revision": 1,
                "status": "accepted",
                "text": "はい",
                "start_sample": 16000,
                "end_sample": 48000,
            },
            {
                "protocol_version": "1.0",
                "event_type": "invented.in.a.later.version",
                "session_id": SESSION_ID,
                "event_id": "00000000-0000-4000-8000-000000000002",
                "sent_at_utc": "2026-09-09T10:00:01Z",
            },
        ]
        capture_dir = tmp_path / "data" / "captures"
        capture_dir.mkdir(parents=True)
        digest = write_capture(capture_dir / "capture-001.jsonl", events)
        manifest = CaptureManifest.from_dict(manifest_dict(sha256=digest))
        return load_capture(manifest, root=tmp_path)

    def test_a_capture_folds_through_the_shared_reducer(self, tmp_path: Path) -> None:
        """The same reducer the UI and the history use (ADR-0008 D13). A replay
        that exercised a parallel implementation would prove nothing about
        production."""
        result = replay(self._capture(tmp_path))

        assert result.applied == 1
        assert "seg-000001" in result.projection.segments
        assert result.projection.segments["seg-000001"].text == "はい"

    def test_an_unknown_event_type_is_reported_not_hidden(self, tmp_path: Path) -> None:
        """ "The replay folded 1 of 2 events, and here is the one it did not" is a
        usable statement; a silent 1 is not."""
        result = replay(self._capture(tmp_path))
        assert result.capture.unknown_event_types == ["invented.in.a.later.version"]

    def test_an_event_count_disagreement_is_fatal(self, tmp_path: Path) -> None:
        """If the manifest and the file disagree, neither can be trusted."""
        capture_dir = tmp_path / "data" / "captures"
        capture_dir.mkdir(parents=True)
        digest = write_capture(capture_dir / "capture-001.jsonl", [{"event_type": "audio.ack"}])
        manifest = CaptureManifest.from_dict(manifest_dict(sha256=digest, event_count=99))

        with pytest.raises(CaptureError, match="manifest records"):
            load_capture(manifest, root=tmp_path)

    def test_a_damaged_capture_line_is_fatal(self, tmp_path: Path) -> None:
        """A capture is immutable evidence. A malformed line means the file is
        damaged rather than merely surprising, which is different from a debug
        log where a truncated tail is expected."""
        capture_dir = tmp_path / "data" / "captures"
        capture_dir.mkdir(parents=True)
        path = capture_dir / "capture-001.jsonl"
        path.write_text('{"ordinal":0,"payload":{}}\n{not json\n', encoding="utf-8", newline="")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest = CaptureManifest.from_dict(manifest_dict(sha256=digest))

        with pytest.raises(CaptureError, match="not valid JSON"):
            load_capture(manifest, root=tmp_path)

    def test_the_summary_reports_what_happened(self, tmp_path: Path) -> None:
        result = replay(self._capture(tmp_path))
        summary = result.summary()
        assert "capture-001" in summary
        assert "1 applied" in summary


class TestReplayIsBlocked:
    """Category C has no fixtures yet, and this is where that is stated.

    Section 25.15 C requires immutable traffic captured from an actual server
    run. No server exists until Phase 4. Inventing an exchange to make the suite
    green would be the fabricated evidence Section 22.3 forbids, so instead the
    absence is visible as a skip (TEST-210).
    """

    def test_the_capture_manifest_is_empty_and_that_is_reported(self) -> None:
        manifests = load_manifests(CAPTURE_MANIFEST_PATH)
        if manifests:
            pytest.fail(
                "captures.yaml now holds manifests - the category C tests below "
                "should be written rather than skipped"
            )
        pytest.skip(
            "no real WebSocket capture exists; none can until Phase 4 runs a real "
            "server (TEST-140, blocked_real_fixture)"
        )

    def test_the_harness_is_ready_for_the_first_real_capture(self) -> None:
        """What remains is the fixture, not the code that consumes it."""
        manifest = CaptureManifest.from_dict(manifest_dict())
        assert manifest.capture_id == "capture-001"
        assert check_protocol_version(manifest) is None
