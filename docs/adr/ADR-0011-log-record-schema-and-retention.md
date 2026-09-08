# ADR-0011: Log record schema, redaction and retention

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 14
- **Requirement IDs:** PERS-010, PERS-020, PERS-050, PERS-060, PERS-070, PERS-080, PERS-090, PERS-100, PERS-110, OPS-530, OPS-550, SEC-050, SEC-060, SEC-090, SEC-110
- **Decided by:** user on 2026-09-08 (decisions D25 through D29, all as recommended)

## Context and constraints

ADR-0004 already fixed the file set and made the client the authoritative
writer. What remained was the shape of a record, how the server's operational
log avoids meeting content, when bytes reach disk, and what deletion means.

Fixed by the normative text:

- The debug JSONL is append-only and is the authoritative event log (25.14).
- It includes session lifecycle, client and server status, gaps and
  acknowledgements, VAD events, partial revisions, final and corrected
  transcription, speaker revisions, language decisions and evidence, overlap
  intervals, ASR quality evidence, translation lifecycle, errors, warnings,
  latency metadata, and model, config and version identifiers (19.1).
- Raw PCM is never embedded in JSONL; audio is referenced by path and SHA-256
  (19.1).
- A deterministic command rebuilds history from the debug event log (19.2).
- Recovery ignores or quarantines an incomplete final line and validates event
  IDs and revisions (25.14).
- UTF-8 without BOM, sanitized session-derived filenames (25.14).
- All services use structured logs carrying session, stream, segment and event
  identifiers, revision, service and worker, monotonic processing timestamps and
  UTC correlation time (20).
- Sensitive audio and text stay out of ordinary operational logs unless debug
  content logging is explicitly enabled (20).

---

## D25 — The shape of a debug record

### The problem

PERS-070 requires a deterministic rebuild of history from the debug log, and
ADR-0008 D13 put a single shared reducer behind every projection. Those two
together constrain the log format more than either does alone.

### Options considered

#### Option A — a purpose-designed log schema, transformed from the wire event

| Dimension | Assessment |
|---|---|
| Correctness | The reducer would need two input formats, one from the socket and one from the file, and "deterministic projection" becomes a claim about two code paths agreeing |
| Latency | A transform per record on the write path |
| Testability | Every reducer test would need doubling |
| Future maintenance | Every new event field must be mapped twice. The mapping is where they drift |

#### Option B — a wrapper whose payload is the wire event verbatim

| Dimension | Assessment |
|---|---|
| Correctness | The reducer has exactly one input type regardless of source |
| Latency | No transform; serialise once |
| Testability | A replay from file and a replay from socket exercise identical code |
| Future maintenance | A new event field needs no log change at all |

### Decision

**Option B.**

```json
{
  "log_seq": 41207,
  "recorded_at_utc": "2026-09-08T07:41:22.481Z",
  "recorded_monotonic_ns": 918273645000,
  "source": "server",
  "direction": "inbound",
  "record_kind": "wire_event",
  "payload": { "...": "the wire event, byte-for-byte as received" }
}
```

`record_kind` discriminates:

| `record_kind` | Meaning |
|---|---|
| `wire_event` | `payload` is a protocol event exactly as sent or received |
| `client_local` | Something that never crosses the wire but Section 19.1 requires in the meeting record |
| `integrity_conflict` | Both contending payloads plus the segment's revision vector (ADR-0008 D12) |

`client_local` covers capture lifecycle transitions (AUD-120, AUD-130), ring
buffer overflow (AUD-100), `session_unrecoverable` (ADR-0005, ADR-0009 D16),
device errors and resampler failures (AUD-090). None of these appears on the
wire, and all of them are required in the record.

Rebuilding history is therefore: read the file, keep `wire_event` records,
unwrap `payload`, fold with the same reducer the UI uses. No second format, no
second code path.

---

## D26 — `log_seq`

### Decision

Every record carries `log_seq`, a monotonic integer starting at 0 within each
file.

The normative text does not require it. It is added because it converts three
otherwise-vague conditions into exact ones:

- **Truncated tail detection** (PERS-080). A file whose last line parses cleanly
  but whose `log_seq` skips was cut mid-write. Without the counter, a clean
  parse of the last line is indistinguishable from a clean close.
- **Quarantine gets an address.** "Quarantined `log_seq` 41207" rather than
  "quarantined the last line", which matters when recovery runs twice.
- **Negative vectors become precise** (PERS-090). Missing `log_seq`, duplicate
  `log_seq`, and regressing `log_seq` are three distinct, writable vectors
  instead of one vague notion of corruption.

---

## D27 — Meeting text and the server's operational log

### The problem

Section 20 forbids sensitive text in ordinary operational logs. Section 19.1
requires the debug JSONL to contain final and corrected transcription. Read
carelessly these conflict.

They do not, because they describe different files.

### Decision

Two logs, two rules.

| Log | Contains meeting text | Governing rule |
|---|---|---|
| Client `meeting_<id>_debug.jsonl` | **Yes, by definition** | It *is* the meeting record. Confidential data (SEC-090), subject to retention |
| Server structured log | **No, by default** | The operational log of Section 20. Text is redacted unless `debug_content_logging` is explicitly enabled |

Redaction form, of two candidates:

| Option | Assessment |
|---|---|
| A — `{"redacted": true, "chars": 47, "sha256_8": "ab12cd34"}` | Lets an operator confirm client and server saw the same text without seeing it. But a truncated hash of a sentence is still a fingerprint of that sentence, and the log is exactly where a fingerprint should not be |
| B — `{"redacted": true, "chars": 47}` | **Selected.** A length mismatch still surfaces divergence, which covers the realistic failure. When content genuinely needs inspecting, `debug_content_logging` gives the full text, and a hash adds nothing over that |

Redaction applies to transcription text, translation text, and any field derived
from meeting content. It does not apply to identifiers, revisions, timings,
decode evidence, language codes or speaker labels: those are the fields the
operational log exists for.

---

## D28 — Retention and deletion

### Decision

**No automatic deletion.** Deletion is explicit and audited.

- `tools/purge_meeting.py <session-id>` removes every artifact belonging to that
  session — debug log, live history, final history, and any diagnostic audio —
  and appends one audit line recording what was deleted, when, and by whom.
- A `retention_days` configuration key exists and is **disabled by default**.
  Enabling it is a deliberate act.

SEC-050 and Section 21 require retention and deletion behaviour to be
*defined*, not automated. Automatic deletion of a meeting record is a
destructive operation, and a destructive default is one that eventually runs
against something the user wanted. The user deletes; the system does not decide.

Filenames are sanitized from the session identifier (PERS-100, SEC-060): the
session id is a uuid4, and the writer validates it against a strict pattern
before it reaches a path, so a hostile or malformed id cannot traverse.

---

## D29 — Flush and fsync cadence

### Options considered

| Option | Assessment |
|---|---|
| A — `fsync` every record | Loses nothing on any crash. But 50-200 fsyncs per second sit on the write path, and AUD-110 forbids blocking the callback, so the writes queue — and then the queue is the thing that overflows |
| B — `write` + `flush` every record; `fsync` at critical boundaries and on an interval | **Selected** |
| C — buffer and flush periodically | Fastest. A process crash loses the whole application buffer, which is precisely the truncated-tail case PERS-090 exists to test. Choosing the failure mode you are trying to defend against is backwards |

### Decision

**Option B.**

Every record is written and flushed out of the application buffer immediately, so
a process crash loses nothing that was accepted for logging. `fsync` runs at
boundaries where losing the preceding work would be materially worse than losing
a second of diagnostics:

```text
session.start
every accepted transcript.final
every seal
session.stop
plus a configurable interval
```

A machine-level crash therefore loses at most one interval of diagnostic
records, while every accepted final and every seal is already durable. The
interval is `benchmark_required`.

The writer runs on its own thread behind a bounded queue. If that queue
saturates, the session stops (PERS-130, Section 25.12
`debug_writer: stop_session_if_event_source_cannot_be_preserved`) rather than
dropping records — an authoritative log with holes is not authoritative.

---

## Consequences

- The reducer's input type is the same whether it comes from a socket or a file,
  which is what makes "deterministic projection" testable rather than asserted.
  The soak test's strongest assertion — rebuilding `history.final.jsonl` from
  the debug log byte-identically (ADR-0012 D33) — depends on this decision.
- `log_seq` gives the persistence negative vectors of PERS-090 something exact
  to violate.
- The server's operational log can be shared, attached to a bug report, or kept
  longer than the meeting record, because it contains no meeting content.
- Nothing deletes a meeting on its own. The consequence is that meeting records
  accumulate until the user purges them, which the client surfaces as a disk
  usage figure rather than solving unilaterally.
- Flushing every record puts real work on the writer thread. If that turns out
  to cost too much, the queue depth and the fsync interval are the tuning knobs;
  the per-record flush is not, because removing it reintroduces Option C.

## Rollback plan

D25 is the load-bearing one: changing the wrapper shape invalidates existing
debug logs for the rebuild tool. It stays cheap until the first real meeting is
recorded and kept, after which a version field in the wrapper — present from the
start — lets the rebuild tool support both. The remaining decisions are internal
and reversible at any time.

## Evidence required before `accepted`

None for the structural decisions, approved by the user on 2026-09-08.

Deferred to measurement: the `fsync` interval and the writer queue depth, both
at the Phase 3 gate when the client persistence shell exists and can be measured
against a real event rate.

## Open questions

- The exact `session.summary` payload, which this ADR's records must carry.
  Specified in `docs/protocol.md` alongside the other event schemas.
- Whether the live `_history.jsonl` projection is written on every revision or
  coalesced on an interval. Phase 3, once the event rate is observed.
