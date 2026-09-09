"""Writing the meeting record, and rebuilding it.

ADR-0004 fixed the file set and made the client authoritative. ADR-0011 fixed the
record shape and the flush policy. ADR-0016 fixed the history write cadence and
where the files live. This implements all three.

The three files, and what each is for:

``meeting_<id>_debug.jsonl``
    Append-only, authoritative. Every event, wrapped so that ``payload`` is the
    wire event **verbatim** - which is what lets the rebuild feed the same
    reducer the UI uses, from one input type rather than two (ADR-0011 D25).

``meeting_<id>_history.jsonl``
    A live projection with no authority. Written only on lifecycle-significant
    change (ADR-0016 D40), because Section 13.2 calls every partial replaceable
    and recording the replaceable twice buys nothing.

``meeting_<id>_history.final.jsonl``
    The compacted deliverable: one latest projection per non-rejected segment,
    written to a temporary file, validated line by line, then atomically renamed
    (Section 25.14).

The writer runs on its own thread behind a bounded queue. If that queue
saturates the session stops (PERS-130) rather than records being dropped: an
authoritative log with holes is not authoritative, and a writer that silently
skips under load is a writer that fails exactly when the meeting is busiest.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from client.paths import MeetingPaths
from protocol.enums import RecordKind, SegmentStatus, TranslationStatus
from protocol.events import EVENT_MODELS, Envelope
from protocol.projection import ProjectionResult, SegmentProjection, SessionProjection

#: How many records the writer may fall behind before the session stops. A burst
#: of partials at fifty a second needs headroom; a sustained backlog means the
#: disk cannot keep up and the record is already at risk. `benchmark_required`.
DEFAULT_QUEUE_DEPTH = 4096

#: ADR-0011 D29: every record is flushed out of the application buffer, and
#: `fsync` runs at critical boundaries plus this interval. `benchmark_required`.
DEFAULT_FSYNC_INTERVAL_S = 5.0

#: How long a stop waits for the writer to drain before giving up on it.
DEFAULT_DRAIN_TIMEOUT_S = 10.0


class PersistenceError(RuntimeError):
    """The meeting record could not be preserved.

    Always fatal to the session (PERS-130, Section 25.12
    ``debug_writer: stop_session_if_event_source_cannot_be_preserved``).
    """


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class LogRecord:
    """One line of the debug log (ADR-0011 D25).

    ``payload`` holds the wire event exactly as it was sent or received. The
    wrapper adds only what the file needs: ordering, timing, and which side and
    direction it came from.
    """

    log_seq: int
    recorded_at_utc: str
    recorded_monotonic_ns: int
    source: str
    direction: str
    record_kind: RecordKind
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {
                "log_seq": self.log_seq,
                "recorded_at_utc": self.recorded_at_utc,
                "recorded_monotonic_ns": self.recorded_monotonic_ns,
                "source": self.source,
                "direction": self.direction,
                "record_kind": str(self.record_kind),
                "payload": self.payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


@dataclass(slots=True)
class WriterStats:
    records_written: int = 0
    bytes_written: int = 0
    fsyncs: int = 0
    peak_queue_depth: int = 0
    dropped: int = 0


class DebugWriter:
    """Appends records to the authoritative log, on its own thread.

    Section 8.1 forbids blocking the audio callback on log writing, and
    ADR-0013 puts the writer behind a bounded queue for that reason. Producers
    call :meth:`write` and return; the thread does the I/O.
    """

    def __init__(
        self,
        path: Path,
        *,
        queue_depth: int = DEFAULT_QUEUE_DEPTH,
        fsync_interval_s: float = DEFAULT_FSYNC_INTERVAL_S,
        clock_ns: Callable[[], int] = time.monotonic_ns,
        clock_utc: Callable[[], str] = _utc_now,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self.fsync_interval_s = fsync_interval_s
        self._clock_ns = clock_ns
        self._clock_utc = clock_utc
        self._clock = clock

        self._queue: queue.Queue[LogRecord | None] = queue.Queue(maxsize=queue_depth)
        self._thread: threading.Thread | None = None
        self._handle: Any = None
        self._log_seq = 0
        self._last_fsync = 0.0
        self._failure: str | None = None
        self.stats = WriterStats()

    # -- lifecycle -----------------------------------------------------------

    def open(self) -> None:
        """Open the log and start the writer thread.

        Raises:
            PersistenceError: if the file cannot be opened. The caller stops the
                session; there is no degraded mode for losing the event source.
        """
        if self._thread is not None:
            raise PersistenceError("writer is already open")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # newline="" keeps Python from translating the LF this code writes
            # into CRLF on Windows. Section 25.14 fixes the format, and a file
            # whose line endings depend on the host is not that format.
            self._handle = self.path.open("a", encoding="utf-8", newline="")
        except OSError as exc:
            raise PersistenceError(f"cannot open {self.path}: {exc}") from exc

        self._last_fsync = self._clock()
        self._thread = threading.Thread(target=self._run, name="debug-writer", daemon=True)
        self._thread.start()

    def close(self, *, timeout_s: float = DEFAULT_DRAIN_TIMEOUT_S) -> None:
        """Drain the queue, fsync and close. Safe to call more than once."""
        thread, self._thread = self._thread, None
        if thread is None:
            return

        self._queue.put(None)
        thread.join(timeout=timeout_s)

        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.flush()
                self._fsync(handle)
            finally:
                handle.close()

    def __enter__(self) -> DebugWriter:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- producing -----------------------------------------------------------

    def write(
        self,
        payload: dict[str, Any],
        *,
        record_kind: RecordKind = RecordKind.WIRE_EVENT,
        source: str = "client",
        direction: str = "outbound",
    ) -> None:
        """Queue one record.

        Raises:
            PersistenceError: if the writer has failed, or the queue is full.
                A full queue means the disk cannot keep up, and PERS-130 makes
                that a reason to stop rather than to drop.
        """
        if self._failure is not None:
            raise PersistenceError(f"the debug writer failed earlier: {self._failure}")

        record = LogRecord(
            log_seq=self._log_seq,
            recorded_at_utc=self._clock_utc(),
            recorded_monotonic_ns=self._clock_ns(),
            source=source,
            direction=direction,
            record_kind=record_kind,
            payload=payload,
        )
        self._log_seq += 1

        try:
            self._queue.put_nowait(record)
        except queue.Full as exc:
            self.stats.dropped += 1
            raise PersistenceError(
                f"the debug writer queue is full at {self._queue.maxsize} records. "
                "An authoritative log with holes is not authoritative, so the "
                "session stops rather than dropping records (PERS-130)"
            ) from exc

        self.stats.peak_queue_depth = max(self.stats.peak_queue_depth, self._queue.qsize())

    def write_event(self, event: Envelope, *, source: str, direction: str) -> None:
        """Queue a wire event, serialised exactly as it crosses the wire."""
        self.write(
            event.model_dump(mode="json"),
            record_kind=RecordKind.WIRE_EVENT,
            source=source,
            direction=direction,
        )

    def write_client_local(self, payload: dict[str, Any]) -> None:
        """Queue something Section 19.1 requires that never crosses the wire.

        Capture lifecycle transitions, ring overflow, `session_unrecoverable`,
        device errors (ADR-0011 D25).
        """
        self.write(payload, record_kind=RecordKind.CLIENT_LOCAL, source="client", direction="local")

    def write_integrity_conflict(self, payload: dict[str, Any]) -> None:
        """Queue both contending payloads of a refused event (ADR-0008 D12)."""
        self.write(
            payload,
            record_kind=RecordKind.INTEGRITY_CONFLICT,
            source="client",
            direction="local",
        )

    # -- the writer thread ---------------------------------------------------

    def _run(self) -> None:
        while True:
            record = self._queue.get()
            if record is None:
                return
            try:
                self._emit(record)
            except OSError as exc:
                self._failure = f"{type(exc).__name__}: {exc}"
                return

    def _emit(self, record: LogRecord) -> None:
        handle = self._handle
        if handle is None:
            return

        line = record.to_json() + "\n"
        handle.write(line)
        # Flushed per record: a process crash then loses nothing that was
        # accepted for logging. Buffering here would make the truncated-tail case
        # PERS-090 defends against into the normal failure mode.
        handle.flush()

        self.stats.records_written += 1
        self.stats.bytes_written += len(line.encode("utf-8"))

        if self._should_fsync(record):
            self._fsync(handle)

    def _should_fsync(self, record: LogRecord) -> bool:
        """ADR-0011 D29 boundaries, plus the interval."""
        event_type = record.payload.get("event_type")
        if event_type in ("session.start", "session.stop", "session.stopped"):
            return True
        if event_type in ("transcript.final", "transcript.revised"):
            return record.payload.get("status") == SegmentStatus.ACCEPTED
        if record.payload.get("sealed") is True:
            return True
        return self._clock() - self._last_fsync >= self.fsync_interval_s

    def _fsync(self, handle: Any) -> None:
        try:
            os.fsync(handle.fileno())
        except OSError:
            # A filesystem that refuses fsync is not a reason to lose the
            # meeting: the per-record flush already happened, so a process crash
            # still loses nothing. Only a machine crash is affected.
            return
        self.stats.fsyncs += 1
        self._last_fsync = self._clock()

    @property
    def failure(self) -> str | None:
        return self._failure

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()


# ---------------------------------------------------------------------------
# History projection
# ---------------------------------------------------------------------------


def projection_to_record(segment: SegmentProjection) -> dict[str, Any]:
    """One line of a history file.

    Section 19.2 lists what the history holds: final transcription, final
    translation or its failure state, final speaker, final language, source
    timestamps, the overlap marker, and the low-confidence marker.
    """
    return {
        "segment_id": segment.segment_id,
        "utterance_id": segment.utterance_id,
        "start_sample": segment.start_sample,
        "end_sample": segment.end_sample,
        "status": str(segment.status),
        "text": segment.text,
        "language_id": segment.language_id,
        "language_status": str(segment.language_status),
        "primary_speaker_id": segment.primary_speaker_id,
        "speaker_status": str(segment.speaker_status),
        "overlap": segment.overlap,
        "translation_status": str(segment.translation_status),
        "translation_text": segment.translation_text,
        "source_language": segment.source_language,
        "target_language": segment.target_language,
        "content_revision": segment.content_revision,
        "language_revision": segment.language_revision,
        "speaker_revision": segment.speaker_revision,
        "translation_revision": segment.translation_revision,
        "sealed": segment.sealed,
    }


#: Translation states that represent an *outcome*. `NOT_APPLICABLE` is missing
#: deliberately: it is the initial value every segment carries from creation, so
#: treating it as terminal here would make every partial history-worthy. A
#: segment that ends up NOT_APPLICABLE reaches that state alongside a final ASR
#: outcome, which triggers on its own.
TRANSLATION_OUTCOMES = (TranslationStatus.COMPLETED, TranslationStatus.FAILED)


def is_history_worthy(result: ProjectionResult) -> bool:
    """Whether a projection change earns a line in the live history.

    ADR-0016 D40. Partials do not: Section 13.2 calls every partial replaceable,
    and `_debug.jsonl` already holds all of them. Writing the replaceable twice,
    once authoritatively and once not, costs readability and buys nothing.
    """
    if not result.applied or result.segment is None:
        return False

    segment = result.segment
    if segment.sealed:
        return True
    if segment.status in (
        SegmentStatus.ACCEPTED,
        SegmentStatus.LOW_CONFIDENCE,
        SegmentStatus.REJECTED,
    ):
        return True
    return segment.translation_status in TRANSLATION_OUTCOMES


class HistoryWriter:
    """Appends the live projection. No authority; a view (ADR-0004)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: Any = None
        self.lines_written = 0

    def open(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a", encoding="utf-8", newline="")
        except OSError as exc:
            raise PersistenceError(f"cannot open {self.path}: {exc}") from exc

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            handle.flush()
            handle.close()

    def __enter__(self) -> HistoryWriter:
        self.open()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def record(self, result: ProjectionResult) -> bool:
        """Write the projection if the change earns a line. Returns whether it did."""
        if not is_history_worthy(result) or result.segment is None:
            return False
        if self._handle is None:
            raise PersistenceError("history writer is not open")

        line = json.dumps(
            projection_to_record(result.segment),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        self._handle.write(line + "\n")
        self._handle.flush()
        self.lines_written += 1
        return True


# ---------------------------------------------------------------------------
# Reading back, and compaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class QuarantinedLine:
    """A line the recovery could not use, kept with its address.

    Section 25.14 requires recovery to ignore or quarantine an incomplete final
    line. ``log_seq`` makes "quarantined the last line" into an address that
    survives a second recovery run.
    """

    #: 1-based line in the file, or -1 when the line parsed cleanly but its
    #: payload did not validate - there the address that matters is ``log_seq``.
    line_number: int
    log_seq: int | None
    reason: str
    raw: str


@dataclass(slots=True)
class ReadResult:
    records: list[LogRecord] = field(default_factory=list)
    quarantined: list[QuarantinedLine] = field(default_factory=list)

    @property
    def log_seq_contiguous(self) -> bool:
        """Whether the file's `log_seq` values run 0, 1, 2, ... without a hole.

        A clean parse of the last line is indistinguishable from a clean close
        without this: a file cut mid-write can still end with a complete line if
        the cut fell on a boundary (ADR-0011 D26).
        """
        return [record.log_seq for record in self.records] == list(range(len(self.records)))


def read_debug_log(path: Path) -> ReadResult:
    """Read a debug log, quarantining anything unusable.

    Never raises on malformed content. A truncated tail is the expected outcome
    of a crash, and the recovery command exists precisely to cope with it
    (PERS-080).
    """
    result = ReadResult()
    if not path.is_file():
        return result

    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, raw in enumerate(handle, start=1):
            stripped = raw.rstrip("\n")
            if not stripped.strip():
                continue
            try:
                loaded = json.loads(stripped)
            except json.JSONDecodeError as exc:
                result.quarantined.append(
                    QuarantinedLine(
                        line_number=line_number,
                        log_seq=None,
                        reason=f"invalid JSON: {exc.msg}",
                        raw=stripped,
                    )
                )
                continue

            if not isinstance(loaded, dict) or "payload" not in loaded:
                result.quarantined.append(
                    QuarantinedLine(
                        line_number=line_number,
                        log_seq=loaded.get("log_seq") if isinstance(loaded, dict) else None,
                        reason="not a log record",
                        raw=stripped,
                    )
                )
                continue

            try:
                result.records.append(
                    LogRecord(
                        log_seq=int(loaded["log_seq"]),
                        recorded_at_utc=str(loaded["recorded_at_utc"]),
                        recorded_monotonic_ns=int(loaded["recorded_monotonic_ns"]),
                        source=str(loaded["source"]),
                        direction=str(loaded["direction"]),
                        record_kind=RecordKind(loaded["record_kind"]),
                        payload=loaded["payload"],
                    )
                )
            except (KeyError, TypeError, ValueError) as exc:
                result.quarantined.append(
                    QuarantinedLine(
                        line_number=line_number,
                        log_seq=loaded.get("log_seq"),
                        reason=f"malformed record: {exc}",
                        raw=stripped,
                    )
                )

    return result


def events_from(
    records: list[LogRecord],
    *,
    on_invalid: Callable[[LogRecord, str], None] | None = None,
) -> Iterator[Envelope]:
    """Turn wire records back into events.

    The whole point of ADR-0011 D25: the payload is the wire event verbatim, so
    this is a parse rather than a translation, and the reducer downstream sees
    exactly what it would have seen from a socket.

    Two things are skipped rather than raised, and both matter:

    - **Unknown event types.** Section 9.1's forward compatibility applies to a
      log written by a newer build exactly as it does to a peer.
    - **Payloads that fail validation.** A rebuild exists to recover a damaged
      log (PERS-080); one that crashed on the first bad payload would fail at
      precisely the moment it is needed. ``on_invalid`` receives each one so the
      caller can quarantine it with an address rather than lose it silently.
    """
    for record in records:
        if record.record_kind is not RecordKind.WIRE_EVENT:
            continue
        model = EVENT_MODELS.get(str(record.payload.get("event_type")))
        if model is None:
            continue
        try:
            yield model.model_validate(record.payload)
        except ValidationError as exc:
            if on_invalid is not None:
                on_invalid(record, f"payload failed validation: {exc.error_count()} error(s)")


def rebuild_projection(path: Path) -> tuple[SessionProjection, ReadResult]:
    """Rebuild the session projection from a debug log (PERS-070).

    Read, unwrap, fold - with the same reducer the UI uses. That is what makes
    "deterministic projection" a property rather than a claim (ADR-0008 D13).
    """
    read = read_debug_log(path)
    session_id = ""
    for record in read.records:
        candidate = record.payload.get("session_id")
        if isinstance(candidate, str) and candidate:
            session_id = candidate
            break

    def quarantine(record: LogRecord, reason: str) -> None:
        read.quarantined.append(
            QuarantinedLine(
                line_number=-1,
                log_seq=record.log_seq,
                reason=reason,
                raw=json.dumps(record.payload, ensure_ascii=False, separators=(",", ":")),
            )
        )

    projection = SessionProjection(session_id=session_id)
    for event in events_from(read.records, on_invalid=quarantine):
        projection.apply(event)
    return projection, read


def compact(paths: MeetingPaths, projection: SessionProjection) -> int:
    """Write ``history.final.jsonl`` by temp file, validation and atomic rename.

    Section 25.14: exactly one latest valid projection per **non-rejected**
    segment. Every line is validated before the rename, so a partial or corrupt
    compaction never replaces a good one.

    Returns the number of segments written.

    Raises:
        PersistenceError: if writing, validating or renaming fails. The caller
            records the session outcome as failed (ADR-0009 D19).
    """
    segments = projection.compactable_segments()
    temp = paths.history_final_temp

    try:
        paths.directory.mkdir(parents=True, exist_ok=True)
        with temp.open("w", encoding="utf-8", newline="") as handle:
            for segment in segments:
                record = projection_to_record(segment)
                line = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                # Validate before writing, not after: a line that cannot be read
                # back must never reach the file, because the atomic rename would
                # then publish it.
                json.loads(line)
                handle.write(line + "\n")
            handle.flush()
            with contextlib.suppress(OSError):
                os.fsync(handle.fileno())

        _validate_compaction(temp, expected=len(segments))
        temp.replace(paths.history_final)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise PersistenceError(f"compaction failed: {exc}") from exc

    return len(segments)


def _validate_compaction(path: Path, *, expected: int) -> None:
    """Every line parses, every segment appears once (Section 25.14)."""
    seen: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for line_number, raw in enumerate(handle, start=1):
            try:
                record = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise PersistenceError(
                    f"compacted line {line_number} is not valid JSON: {exc.msg}"
                ) from exc
            segment_id = record.get("segment_id")
            if not isinstance(segment_id, str):
                raise PersistenceError(f"compacted line {line_number} has no segment_id")
            if segment_id in seen:
                raise PersistenceError(
                    f"compacted line {line_number} repeats {segment_id}; Section 25.14 "
                    "requires exactly one projection per segment"
                )
            seen.add(segment_id)

    if len(seen) != expected:
        raise PersistenceError(f"compaction wrote {len(seen)} segments, expected {expected}")


def summarise(projection: SessionProjection) -> dict[str, int]:
    """Counts for the session summary (PROT-050, ADR-0009 D19)."""
    segments = list(projection.segments.values())
    return {
        "segment_count": len(segments),
        "sealed_count": sum(1 for s in segments if s.sealed),
        "rejected_count": sum(1 for s in segments if s.status is SegmentStatus.REJECTED),
        "low_confidence_count": sum(
            1 for s in segments if s.status is SegmentStatus.LOW_CONFIDENCE
        ),
        "translation_failed_count": sum(
            1 for s in segments if s.translation_status is TranslationStatus.FAILED
        ),
        "integrity_conflict_count": len(projection.conflicts),
    }
