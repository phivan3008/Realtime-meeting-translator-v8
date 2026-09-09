"""Persistence conformance vectors.

Category B (`requirements.md` Section 25.15 B). Purpose-built inputs exercising
the defensive branches Section 25.14 and PERS-090 name by hand: truncated tail,
duplicate and conflicting records, invalid path, permission failure, and a full
writer queue.

Nothing here claims anything about ASR, language, speaker, overlap or
translation quality (TEST-130). It claims that the meeting record survives being
written badly, and that rebuilding it from the log reproduces what was there.

The strongest assertion in this module is
``test_a_rebuild_reproduces_the_live_compaction_byte_for_byte``. It is the
client-side form of the soak test's central claim (ADR-0012 D33): the same
events folded twice, once live and once from disk, produce identical bytes.
That is what makes "deterministic projection" (Section 25.5) a property rather
than a hope.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import purge_meeting  # noqa: E402
import rebuild_history  # noqa: E402
from client.paths import (  # noqa: E402
    MeetingPaths,
    UnsafeSessionIdError,
    build_paths,
    default_directory,
    find_sessions,
)
from client.persistence import (  # noqa: E402
    DebugWriter,
    HistoryWriter,
    PersistenceError,
    compact,
    events_from,
    is_history_worthy,
    projection_to_record,
    read_debug_log,
    rebuild_projection,
    summarise,
)
from protocol.enums import (  # noqa: E402
    LanguageCode,
    LanguageStatus,
    RecordKind,
    SegmentStatus,
)
from protocol.events import (  # noqa: E402
    Envelope,
    TranscriptFinal,
    TranscriptPartial,
    TranslationFinal,
)
from protocol.projection import SessionProjection  # noqa: E402

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "negative_test_vector"

SESSION_ID = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
SENT_AT = "2026-09-09T07:41:22.481Z"


def event_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


def partial(n: int, revision: int, text: str = "明日の") -> TranscriptPartial:
    return TranscriptPartial(
        session_id=SESSION_ID,
        event_id=event_id(n),
        sent_at_utc=SENT_AT,
        utterance_id="utt-000001",
        segment_id="seg-000001",
        content_revision=revision,
        stable_text=text,
        start_sample=16_000,
        end_sample=48_000,
    )


def final(
    n: int,
    revision: int,
    segment_id: str = "seg-000001",
    status: SegmentStatus = SegmentStatus.ACCEPTED,
    text: str = "明日の会議は午前十時です",
) -> TranscriptFinal:
    return TranscriptFinal(
        session_id=SESSION_ID,
        event_id=event_id(n),
        sent_at_utc=SENT_AT,
        utterance_id="utt-000001",
        segment_id=segment_id,
        content_revision=revision,
        status=status,
        text=text,
        start_sample=16_000,
        end_sample=48_000,
        language_id=LanguageCode.JA,
        language_status=LanguageStatus.CONFIRMED_JA,
    )


def translation(n: int, revision: int, from_content: int) -> TranslationFinal:
    return TranslationFinal(
        session_id=SESSION_ID,
        event_id=event_id(n),
        sent_at_utc=SENT_AT,
        segment_id="seg-000001",
        translation_revision=revision,
        translated_from_content_revision=from_content,
        translated_from_language_revision=0,
        source_language=LanguageCode.JA,
        target_language=LanguageCode.VI,
        text="Cuộc họp ngày mai lúc 10 giờ sáng",
    )


def paths_in(directory: Path) -> MeetingPaths:
    return build_paths(SESSION_ID, directory=directory)


# ---------------------------------------------------------------------------
# Paths - ADR-0016 D42
# ---------------------------------------------------------------------------


class TestPaths:
    def test_the_three_files_follow_section_19_1(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        assert paths.debug.name == f"meeting_{SESSION_ID}_debug.jsonl"
        assert paths.history.name == f"meeting_{SESSION_ID}_history.jsonl"
        assert paths.history_final.name == f"meeting_{SESSION_ID}_history.final.jsonl"

    @pytest.mark.parametrize(
        "hostile",
        [
            pytest.param("../../etc/passwd", id="traversal"),
            pytest.param("..\\..\\windows\\system32", id="windows-traversal"),
            pytest.param("session/with/slashes", id="slashes"),
            pytest.param("", id="empty"),
            pytest.param("3F2504E0-4F89-41D3-9A0C-0305E82C3301", id="uppercase"),
            pytest.param("3f2504e0-4f89-41d3-9a0c-0305e82c3301extra", id="trailing"),
            pytest.param("nul", id="reserved-device-name"),
        ],
    )
    def test_a_hostile_session_id_never_reaches_a_path(self, hostile: str) -> None:
        """PERS-100, SEC-060. Refused rather than sanitised.

        Silently rewriting a bad identifier would produce a file whose name no
        longer matches the session it belongs to - the record would exist and be
        unfindable, which is worse than refusing.
        """
        with pytest.raises(UnsafeSessionIdError):
            build_paths(hostile, directory=Path("/tmp"))

    def test_the_default_directory_is_under_the_user_profile(self) -> None:
        """A meeting record is the user's document, not application data."""
        directory = default_directory()
        assert directory.name == "MeetingTranslator"
        assert "Documents" in directory.parts or directory.parent == Path.home()

    def test_ensure_directory_creates_and_proves_writability(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path / "nested" / "deeper")
        paths.ensure_directory()
        assert paths.directory.is_dir()
        assert not list(paths.directory.glob(".write-probe-*")), "the probe was left behind"

    def test_find_sessions_ignores_names_it_did_not_write(self, tmp_path: Path) -> None:
        (tmp_path / f"meeting_{SESSION_ID}_debug.jsonl").write_text("", encoding="utf-8")
        (tmp_path / "meeting_not-a-uuid_debug.jsonl").write_text("", encoding="utf-8")
        (tmp_path / "random.jsonl").write_text("", encoding="utf-8")

        assert find_sessions(tmp_path) == [SESSION_ID]

    def test_find_sessions_on_a_missing_directory_is_empty(self, tmp_path: Path) -> None:
        assert find_sessions(tmp_path / "absent") == []


# ---------------------------------------------------------------------------
# Debug writer - ADR-0011 D25, D26, D29
# ---------------------------------------------------------------------------


class TestDebugWriter:
    def test_records_carry_a_contiguous_log_seq(self, tmp_path: Path) -> None:
        """ADR-0011 D26. Without it, a clean parse of the last line is
        indistinguishable from a clean close."""
        paths = paths_in(tmp_path)
        with DebugWriter(paths.debug) as writer:
            for index in range(5):
                writer.write_event(partial(index, index), source="server", direction="inbound")

        result = read_debug_log(paths.debug)
        assert [record.log_seq for record in result.records] == [0, 1, 2, 3, 4]
        assert result.log_seq_contiguous

    def test_the_payload_is_the_wire_event_verbatim(self, tmp_path: Path) -> None:
        """ADR-0011 D25: the rebuild feeds the same reducer from one input type."""
        paths = paths_in(tmp_path)
        event = final(1, 1)
        with DebugWriter(paths.debug) as writer:
            writer.write_event(event, source="server", direction="inbound")

        record = read_debug_log(paths.debug).records[0]
        assert record.payload["event_type"] == "transcript.final"
        assert record.payload["segment_id"] == "seg-000001"
        assert record.payload["text"] == event.text

    def test_client_local_records_are_distinguishable(self, tmp_path: Path) -> None:
        """Capture transitions never cross the wire but Section 19.1 needs them."""
        paths = paths_in(tmp_path)
        with DebugWriter(paths.debug) as writer:
            writer.write_client_local({"kind": "capture_state", "to_state": "capturing"})

        record = read_debug_log(paths.debug).records[0]
        assert record.record_kind is RecordKind.CLIENT_LOCAL

    def test_non_ascii_text_survives(self, tmp_path: Path) -> None:
        """The meeting is Japanese and Vietnamese. UTF-8, not escapes."""
        paths = paths_in(tmp_path)
        with DebugWriter(paths.debug) as writer:
            writer.write_event(final(1, 1), source="server", direction="inbound")

        raw = paths.debug.read_text(encoding="utf-8")
        assert "明日の会議は午前十時です" in raw, "the text was escaped rather than written"

    def test_lines_end_with_lf_not_crlf(self, tmp_path: Path) -> None:
        """Section 25.14 fixes the format; a file whose line endings depend on the
        host operating system is not that format."""
        paths = paths_in(tmp_path)
        with DebugWriter(paths.debug) as writer:
            writer.write_event(partial(1, 1), source="server", direction="inbound")

        assert b"\r\n" not in paths.debug.read_bytes()

    def test_a_full_queue_stops_the_session(self, tmp_path: Path) -> None:
        """PERS-130. An authoritative log with holes is not authoritative."""
        paths = paths_in(tmp_path)
        writer = DebugWriter(paths.debug, queue_depth=1)
        # Deliberately not opened, so nothing drains the queue.
        writer.write(payload={"event_type": "x"})

        with pytest.raises(PersistenceError, match="queue is full"):
            for _ in range(10):
                writer.write(payload={"event_type": "x"})

    def test_an_unwritable_path_is_reported_not_swallowed(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocked"
        blocker.write_text("I am a file, not a directory", encoding="utf-8")

        writer = DebugWriter(blocker / "meeting.jsonl")
        with pytest.raises(PersistenceError, match="cannot open"):
            writer.open()

    def test_opening_twice_is_refused(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        writer = DebugWriter(paths.debug)
        writer.open()
        try:
            with pytest.raises(PersistenceError, match="already open"):
                writer.open()
        finally:
            writer.close()

    def test_closing_twice_is_harmless(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        writer = DebugWriter(paths.debug)
        writer.open()
        writer.close()
        writer.close()


# ---------------------------------------------------------------------------
# Reading back a damaged log - PERS-080, PERS-090
# ---------------------------------------------------------------------------


class TestDamagedLogs:
    def _write_lines(self, path: Path, lines: list[str]) -> None:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="")

    def _record_line(self, log_seq: int, event_type: str = "transcript.partial") -> str:
        return json.dumps(
            {
                "log_seq": log_seq,
                "recorded_at_utc": SENT_AT,
                "recorded_monotonic_ns": log_seq * 1000,
                "source": "server",
                "direction": "inbound",
                "record_kind": "wire_event",
                "payload": {"event_type": event_type, "session_id": SESSION_ID},
            },
            separators=(",", ":"),
        )

    def test_a_truncated_tail_is_quarantined_not_fatal(self, tmp_path: Path) -> None:
        """The expected outcome of a crash mid-write (PERS-080)."""
        path = tmp_path / "log.jsonl"
        path.write_text(
            self._record_line(0) + "\n" + self._record_line(1)[:40],
            encoding="utf-8",
            newline="",
        )

        result = read_debug_log(path)

        assert len(result.records) == 1
        assert len(result.quarantined) == 1
        assert "invalid JSON" in result.quarantined[0].reason

    def test_a_quarantined_line_carries_its_address(self, tmp_path: Path) -> None:
        """ "Quarantined the last line" is not an address that survives a second
        recovery run; a line number and a log_seq are."""
        path = tmp_path / "log.jsonl"
        self._write_lines(path, [self._record_line(0), "{not json", self._record_line(2)])

        result = read_debug_log(path)

        assert result.quarantined[0].line_number == 2
        assert len(result.records) == 2

    def test_a_hole_in_log_seq_is_detectable(self, tmp_path: Path) -> None:
        """A file cut mid-write can still end with a complete line if the cut
        fell on a boundary. Only the counter reveals it."""
        path = tmp_path / "log.jsonl"
        self._write_lines(path, [self._record_line(0), self._record_line(2)])

        assert not read_debug_log(path).log_seq_contiguous

    def test_a_line_that_is_not_a_record_is_quarantined(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        self._write_lines(path, [self._record_line(0), json.dumps({"hello": "world"})])

        result = read_debug_log(path)

        assert len(result.records) == 1
        assert result.quarantined[0].reason == "not a log record"

    def test_a_malformed_record_is_quarantined_with_its_log_seq(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        broken = json.dumps({"log_seq": 7, "payload": {}}, separators=(",", ":"))
        self._write_lines(path, [self._record_line(0), broken])

        result = read_debug_log(path)

        assert result.quarantined[0].log_seq == 7
        assert "malformed record" in result.quarantined[0].reason

    def test_blank_lines_are_skipped_silently(self, tmp_path: Path) -> None:
        path = tmp_path / "log.jsonl"
        self._write_lines(path, [self._record_line(0), "", "   ", self._record_line(1)])

        result = read_debug_log(path)

        assert len(result.records) == 2
        assert result.quarantined == []

    def test_a_missing_log_reads_as_empty(self, tmp_path: Path) -> None:
        result = read_debug_log(tmp_path / "absent.jsonl")
        assert result.records == []
        assert result.quarantined == []

    def test_an_unknown_event_type_is_skipped_not_fatal(self, tmp_path: Path) -> None:
        """Section 9.1 forward compatibility applies to a log written by a newer
        build exactly as it does to a peer."""
        path = tmp_path / "log.jsonl"
        self._write_lines(path, [self._record_line(0, event_type="something.invented.later")])

        result = read_debug_log(path)
        assert len(result.records) == 1
        assert list(events_from(result.records)) == []


# ---------------------------------------------------------------------------
# History cadence - ADR-0016 D40
# ---------------------------------------------------------------------------


class TestHistoryCadence:
    def _applied(self, event: Envelope) -> object:
        state = SessionProjection(session_id=SESSION_ID)
        return state.apply(event)

    def test_a_partial_earns_no_line(self) -> None:
        """Section 13.2 calls every partial replaceable. Recording the
        replaceable twice, once authoritatively and once not, buys nothing."""
        assert not is_history_worthy(self._applied(partial(1, 1)))  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "status",
        [SegmentStatus.ACCEPTED, SegmentStatus.LOW_CONFIDENCE, SegmentStatus.REJECTED],
    )
    def test_every_final_outcome_earns_a_line(self, status: SegmentStatus) -> None:
        result = self._applied(final(1, 1, status=status, text=""))
        assert is_history_worthy(result)  # type: ignore[arg-type]

    def test_a_completed_translation_earns_a_line(self) -> None:
        state = SessionProjection(session_id=SESSION_ID)
        state.apply(final(1, 1))
        result = state.apply(translation(2, 1, from_content=1))
        assert is_history_worthy(result)

    def test_a_seal_earns_a_line(self) -> None:
        state = SessionProjection(session_id=SESSION_ID)
        state.apply(final(1, 1))
        assert is_history_worthy(state.seal("seg-000001"))

    def test_a_refused_event_earns_nothing(self) -> None:
        state = SessionProjection(session_id=SESSION_ID)
        state.apply(final(1, 5))
        assert not is_history_worthy(state.apply(final(2, 2)))

    def test_the_writer_skips_what_is_not_worthy(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        state = SessionProjection(session_id=SESSION_ID)

        with HistoryWriter(paths.history) as writer:
            assert not writer.record(state.apply(partial(1, 1)))
            assert writer.record(state.apply(final(2, 2)))

        assert writer.lines_written == 1
        assert len(paths.history.read_text(encoding="utf-8").splitlines()) == 1

    def test_a_history_record_carries_the_section_19_2_fields(self) -> None:
        state = SessionProjection(session_id=SESSION_ID)
        state.apply(final(1, 1))
        record = projection_to_record(state.segments["seg-000001"])

        for required in (
            "text",
            "translation_status",
            "primary_speaker_id",
            "language_id",
            "start_sample",
            "end_sample",
            "overlap",
            "status",
        ):
            assert required in record, f"Section 19.2 requires {required}"


# ---------------------------------------------------------------------------
# Compaction - Section 25.14
# ---------------------------------------------------------------------------


class TestCompaction:
    def _session(self) -> SessionProjection:
        state = SessionProjection(session_id=SESSION_ID)
        state.apply(final(1, 1, segment_id="seg-000001"))
        state.apply(final(2, 1, segment_id="seg-000002"))
        state.apply(final(3, 1, segment_id="seg-000003", status=SegmentStatus.REJECTED, text=""))
        return state

    def test_one_line_per_non_rejected_segment(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        written = compact(paths, self._session())

        lines = paths.history_final.read_text(encoding="utf-8").splitlines()
        assert written == 2
        assert len(lines) == 2
        assert {json.loads(line)["segment_id"] for line in lines} == {
            "seg-000001",
            "seg-000002",
        }

    def test_the_temporary_file_does_not_survive(self, tmp_path: Path) -> None:
        """Section 25.14: temp write, validate, atomic rename."""
        paths = paths_in(tmp_path)
        compact(paths, self._session())
        assert not paths.history_final_temp.exists()

    def test_a_second_compaction_replaces_the_first_atomically(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        compact(paths, self._session())

        state = SessionProjection(session_id=SESSION_ID)
        state.apply(final(9, 1, segment_id="seg-000009"))
        compact(paths, state)

        lines = paths.history_final.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["segment_id"] == "seg-000009"

    def test_an_empty_session_compacts_to_an_empty_file(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        assert compact(paths, SessionProjection(session_id=SESSION_ID)) == 0
        assert paths.history_final.read_text(encoding="utf-8") == ""

    def test_an_unwritable_directory_fails_loudly(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocked"
        blocker.write_text("a file where a directory should be", encoding="utf-8")
        paths = build_paths(SESSION_ID, directory=blocker)

        with pytest.raises(PersistenceError, match="compaction failed"):
            compact(paths, self._session())

    @pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits do not apply on Windows")
    def test_a_read_only_directory_fails_loudly(self, tmp_path: Path) -> None:
        directory = tmp_path / "readonly"
        directory.mkdir()
        directory.chmod(stat.S_IREAD | stat.S_IEXEC)
        paths = build_paths(SESSION_ID, directory=directory)

        try:
            with pytest.raises(PersistenceError):
                compact(paths, self._session())
        finally:
            directory.chmod(stat.S_IRWXU)

    def test_the_summary_counts_what_the_session_summary_needs(self) -> None:
        counts = summarise(self._session())
        assert counts["segment_count"] == 3
        assert counts["rejected_count"] == 1
        assert counts["integrity_conflict_count"] == 0


# ---------------------------------------------------------------------------
# The claim that matters most
# ---------------------------------------------------------------------------


class TestRebuild:
    """PERS-070 and ADR-0008 D13.

    Read, unwrap, fold - with the same reducer the UI uses.
    """

    def _script(self) -> list[Envelope]:
        return [
            partial(1, 1),
            partial(2, 2, text="明日の会議"),
            final(3, 3),
            translation(4, 1, from_content=3),
            final(5, 1, segment_id="seg-000002", text="はい"),
        ]

    def test_a_rebuild_reproduces_the_live_projection(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        live = SessionProjection(session_id=SESSION_ID)

        with DebugWriter(paths.debug) as writer:
            for event in self._script():
                writer.write_event(event, source="server", direction="inbound")
                live.apply(event)

        rebuilt, read = rebuild_projection(paths.debug)

        assert read.quarantined == []
        assert rebuilt.segments == live.segments

    def test_a_rebuild_reproduces_the_live_compaction_byte_for_byte(self, tmp_path: Path) -> None:
        """The client-side form of the soak test's central claim (ADR-0012 D33).

        The same events folded twice - once live, once from disk - must produce
        identical bytes, including key order and number formatting. That is what
        makes Section 25.5's "deterministic projection" a property rather than a
        hope, and it is the strongest thing this project can assert without
        ground truth.
        """
        live_paths = paths_in(tmp_path / "live")
        rebuilt_paths = paths_in(tmp_path / "rebuilt")

        live = SessionProjection(session_id=SESSION_ID)
        with DebugWriter(live_paths.debug) as writer:
            for event in self._script():
                writer.write_event(event, source="server", direction="inbound")
                live.apply(event)
        compact(live_paths, live)

        rebuilt, _ = rebuild_projection(live_paths.debug)
        compact(rebuilt_paths, rebuilt)

        assert rebuilt_paths.history_final.read_bytes() == live_paths.history_final.read_bytes()

    def test_a_rebuild_survives_a_truncated_tail(self, tmp_path: Path) -> None:
        """A crash mid-write must still yield everything written before it."""
        paths = paths_in(tmp_path)
        with DebugWriter(paths.debug) as writer:
            for event in self._script():
                writer.write_event(event, source="server", direction="inbound")

        with paths.debug.open("a", encoding="utf-8", newline="") as handle:
            handle.write('{"log_seq":99,"recorded_at')

        rebuilt, read = rebuild_projection(paths.debug)

        assert len(read.quarantined) == 1
        assert "seg-000001" in rebuilt.segments
        assert rebuilt.segments["seg-000001"].content_revision == 3

    def test_replaying_a_log_twice_is_idempotent(self, tmp_path: Path) -> None:
        """PROT-280 through the file rather than the socket."""
        paths = paths_in(tmp_path)
        with DebugWriter(paths.debug) as writer:
            for event in self._script():
                writer.write_event(event, source="server", direction="inbound")

        once, _ = rebuild_projection(paths.debug)
        twice, _ = rebuild_projection(paths.debug)

        assert once.segments == twice.segments

    def test_the_rebuild_recovers_the_session_id_from_the_log(self, tmp_path: Path) -> None:
        paths = paths_in(tmp_path)
        with DebugWriter(paths.debug) as writer:
            writer.write_event(final(1, 1), source="server", direction="inbound")

        rebuilt, _ = rebuild_projection(paths.debug)
        assert rebuilt.session_id == SESSION_ID


# ---------------------------------------------------------------------------
# The recovery and purge commands
# ---------------------------------------------------------------------------


class TestRebuildCommand:
    """PERS-070 requires a deterministic command, not just a function."""

    def _write_log(self, directory: Path) -> MeetingPaths:
        paths = build_paths(SESSION_ID, directory=directory)
        with DebugWriter(paths.debug) as writer:
            writer.write_event(final(1, 1), source="server", direction="inbound")
            writer.write_event(
                final(2, 1, segment_id="seg-000002", text="はい"),
                source="server",
                direction="inbound",
            )
        return paths

    def test_a_dry_run_writes_nothing(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        paths = self._write_log(tmp_path)

        assert rebuild_history.main([SESSION_ID, "--directory", str(tmp_path), "--dry-run"]) == 0

        assert not paths.history_final.exists()
        assert "dry run" in capsys.readouterr().out

    def test_it_writes_the_final_history(self, tmp_path: Path) -> None:
        paths = self._write_log(tmp_path)

        assert rebuild_history.main([SESSION_ID, "--directory", str(tmp_path)]) == 0

        lines = paths.history_final.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

    def test_a_missing_session_is_reported(self, tmp_path: Path) -> None:
        assert rebuild_history.main([SESSION_ID, "--directory", str(tmp_path)]) == 1

    def test_an_unsafe_session_id_is_refused(self, tmp_path: Path) -> None:
        assert rebuild_history.main(["../escape", "--directory", str(tmp_path)]) == 1

    def test_listing_reports_what_is_there(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self._write_log(tmp_path)

        assert rebuild_history.main(["--list", "--directory", str(tmp_path)]) == 0

        out = capsys.readouterr().out
        assert SESSION_ID in out
        assert "NO FINAL" in out

    def test_a_hole_in_log_seq_is_warned_about(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Reported, not fatal. Rebuilding from what remains is still the best
        available outcome - but the operator has to know it is partial."""
        paths = self._write_log(tmp_path)
        lines = paths.debug.read_text(encoding="utf-8").splitlines()
        paths.debug.write_text(lines[0] + "\n", encoding="utf-8", newline="")
        with paths.debug.open("a", encoding="utf-8", newline="") as handle:
            handle.write(
                json.dumps(
                    {
                        "log_seq": 5,
                        "recorded_at_utc": SENT_AT,
                        "recorded_monotonic_ns": 1,
                        "source": "server",
                        "direction": "inbound",
                        "record_kind": "wire_event",
                        "payload": {"event_type": "audio.ack", "session_id": SESSION_ID},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

        rebuild_history.main([SESSION_ID, "--directory", str(tmp_path), "--dry-run"])

        assert "log_seq is not contiguous" in capsys.readouterr().err


class TestPurgeCommand:
    """ADR-0011 D28. Deletion is explicit, and it leaves a trace."""

    def _write_files(self, directory: Path) -> MeetingPaths:
        paths = build_paths(SESSION_ID, directory=directory)
        directory.mkdir(parents=True, exist_ok=True)
        paths.debug.write_text("{}\n", encoding="utf-8")
        paths.history.write_text("{}\n", encoding="utf-8")
        paths.history_final.write_text("{}\n", encoding="utf-8")
        return paths

    def test_without_confirm_nothing_is_deleted(self, tmp_path: Path) -> None:
        """A destructive default eventually runs against something the user
        wanted to keep."""
        paths = self._write_files(tmp_path)

        assert purge_meeting.main([SESSION_ID, "--directory", str(tmp_path)]) == 0

        assert paths.debug.is_file()
        assert paths.history_final.is_file()

    def test_confirm_deletes_every_file(self, tmp_path: Path) -> None:
        paths = self._write_files(tmp_path)

        assert purge_meeting.main([SESSION_ID, "--directory", str(tmp_path), "--confirm"]) == 0

        assert not paths.debug.exists()
        assert not paths.history.exists()
        assert not paths.history_final.exists()

    def test_deletion_leaves_an_audit_line(self, tmp_path: Path) -> None:
        """A deletion with no trace is indistinguishable from a file that was
        never written."""
        self._write_files(tmp_path)

        purge_meeting.main([SESSION_ID, "--directory", str(tmp_path), "--confirm"])

        audit = (tmp_path / "deletions.log").read_text(encoding="utf-8")
        assert SESSION_ID in audit
        assert "_debug.jsonl" in audit

    def test_dry_run_overrides_confirm(self, tmp_path: Path) -> None:
        paths = self._write_files(tmp_path)

        purge_meeting.main([SESSION_ID, "--directory", str(tmp_path), "--confirm", "--dry-run"])

        assert paths.debug.is_file()

    def test_purging_a_session_that_does_not_exist(self, tmp_path: Path) -> None:
        assert purge_meeting.main([SESSION_ID, "--directory", str(tmp_path)]) == 1

    def test_an_unsafe_session_id_is_refused(self, tmp_path: Path) -> None:
        assert purge_meeting.main(["../escape", "--directory", str(tmp_path), "--confirm"]) == 1

    def test_an_invalid_payload_is_quarantined_rather_than_fatal(self, tmp_path: Path) -> None:
        """A rebuild exists to recover a damaged log (PERS-080). One that crashed
        on the first bad payload would fail at exactly the moment it is needed.

        Found by a test, not by reading: the first version let pydantic's
        ValidationError escape.
        """
        paths = build_paths(SESSION_ID, directory=tmp_path)
        with DebugWriter(paths.debug) as writer:
            writer.write_event(final(1, 1), source="server", direction="inbound")
        with paths.debug.open("a", encoding="utf-8", newline="") as handle:
            handle.write(
                json.dumps(
                    {
                        "log_seq": 1,
                        "recorded_at_utc": SENT_AT,
                        "recorded_monotonic_ns": 1,
                        "source": "server",
                        "direction": "inbound",
                        "record_kind": "wire_event",
                        # A known event type with its required fields missing.
                        "payload": {"event_type": "audio.ack", "session_id": SESSION_ID},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

        projection, read = rebuild_projection(paths.debug)

        assert "seg-000001" in projection.segments
        assert any("failed validation" in line.reason for line in read.quarantined)

    def test_a_quarantined_payload_is_addressed_by_log_seq(self, tmp_path: Path) -> None:
        """The line parsed cleanly, so a line number is not the useful address."""
        paths = build_paths(SESSION_ID, directory=tmp_path)
        paths.debug.write_text(
            json.dumps(
                {
                    "log_seq": 42,
                    "recorded_at_utc": SENT_AT,
                    "recorded_monotonic_ns": 1,
                    "source": "server",
                    "direction": "inbound",
                    "record_kind": "wire_event",
                    "payload": {"event_type": "audio.ack", "session_id": SESSION_ID},
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
            newline="",
        )

        _, read = rebuild_projection(paths.debug)

        assert read.quarantined[0].log_seq == 42
        assert read.quarantined[0].line_number == -1
