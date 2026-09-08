# ADR-0004: Persistence file set and the authoritative debug writer

- **Status:** accepted (policy); detailed schema deferred to the Phase 1 log-schema gate
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, items 14 and 18 (partial)
- **Requirement IDs:** PERS-010, PERS-020, PERS-030, PERS-040, PERS-100
- **Decided by:** user on 2026-09-08 (decisions D5 and D6, both option C)

## Context and constraints

Two readings of the normative text had to be reconciled before any writer is
implemented.

**Ambiguity 1 — how many history files exist.**
`requirements.md` Section 19.2 names `meeting_<session-id>_history.jsonl` and
says it "shall contain only the latest accepted final state per segment".
Section 25.14 says that *during* an active meeting the history projection "may
contain multiple versioned records for a segment", and that graceful stop
creates `meeting_<session-id>_history.final.jsonl` by deterministic compaction
with exactly one latest valid projection per non-rejected segment. Read
together, Section 19.2 describes the compacted artifact while Section 25.14
describes a live projection plus a compacted artifact under a different name.

**Ambiguity 2 — which side is the authoritative writer.**
`requirements.md` Section 2 and Section 8 say the client writes the debug event
log and the final meeting history log. Section 25.14 says the debug JSONL is the
authoritative event log, and Section 25.12 requires
`debug_writer: stop_session_if_event_source_cannot_be_preserved`, without
naming a side. Section 20 separately requires structured logs from all services.

Fixed constraints that bound any answer:

- Section 25.10: server session state is memory-resident and a server restart
  makes the active meeting unrecoverable. Anything that must survive a server
  restart cannot live only on the server.
- Section 25.14: debug JSONL is append-only; history is a deterministic
  projection; compaction is temp-write, validate, atomic rename.
- Section 21: transcription and translation are confidential meeting data.

## Options considered

### Ambiguity 1

**Option A — two files: `_history.jsonl` is the live projection, `_history.final.jsonl` is the compacted artifact.**

| Dimension | Assessment |
|---|---|
| Correctness | Matches Section 25.14 directly; diverges slightly from Section 19.2 wording |
| Latency | Live projection written incrementally, no stop-time spike |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Live projection and compaction independently testable |
| Dependency isolation | n/a |
| Future maintenance | Two artifacts to document |

**Option B — one file, rewritten atomically at stop.**

| Dimension | Assessment |
|---|---|
| Correctness | Matches Section 19.2 wording; loses the Section 25.14 live projection |
| Latency | All work happens at stop |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Nothing observable mid-meeting |
| Dependency isolation | n/a |
| Future maintenance | A crash mid-meeting leaves no history at all, only the debug log |

**Option C — Option A, with authority stated explicitly.**

Same as A, plus: `_debug.jsonl` is the only source of truth, `_history.jsonl` is
a convenience view carrying no authority, `_history.final.jsonl` is the
deliverable.

### Ambiguity 2

**Option A — client is the authoritative writer.**
Survives server restart, matches Sections 2 and 8; loses server-internal
evidence that was never transmitted.

**Option B — server is the authoritative writer.**
Captures everything the pipeline knows; contradicts Section 25.10, because a
server restart would destroy the record of the meeting that just happened.

**Option C — both, with distinct roles.**
Client debug JSONL is authoritative for the meeting record; the server keeps its
own diagnostic log under Section 20; the two are joined by `event_id` and
`session_id`.

| Dimension | Assessment (Option C) |
|---|---|
| Correctness | Satisfies Sections 2, 8, 20, 25.10 and 25.12 simultaneously |
| Latency | Client writing must never block the audio callback or GUI thread (`CLAUDE.md` Section 8) |
| Accuracy | n/a |
| GPU / RAM usage | Negligible |
| Testability | Client-side persistence is testable on the dev machine with no GPU |
| Dependency isolation | Persistence stays out of the model workers entirely |
| Future maintenance | Two logs to correlate; `event_id` makes correlation mechanical |

## Decision

**File set (D5, option C):**

| File | Written by | Authority | Contents |
|---|---|---|---|
| `meeting_<session-id>_debug.jsonl` | client | **authoritative event log**, append-only | every event and decision listed in Section 19.1 |
| `meeting_<session-id>_history.jsonl` | client | convenience projection, **no authority** | live projection; may contain multiple versioned records per segment |
| `meeting_<session-id>_history.final.jsonl` | client | deliverable | one latest valid projection per non-rejected segment, produced at graceful stop by temp-write, validate, atomic rename |

`_history.jsonl` exists so an operator can watch the meeting take shape and so a
crash leaves something readable. It is never used to answer a question that
`_debug.jsonl` can answer. The compaction command rebuilds
`_history.final.jsonl` from `_debug.jsonl` alone, never from `_history.jsonl`.

This is recorded as an interpretation of Section 19.2 in light of Section 25.14.
Neither `requirements.md` nor `CLAUDE.md` is edited.

**Authoritative writer (D6, option C):**

- The **client** writes `_debug.jsonl` and owns the meeting record. If the
  client debug writer fails, the session stops, per Section 25.12
  `debug_writer: stop_session_if_event_source_cannot_be_preserved`.
- The **server** writes its own structured diagnostic log under Section 20. It
  is operational evidence, subject to the Section 20 rule that meeting text and
  audio stay out of ordinary logs unless debug content logging is explicitly
  enabled. It is not the meeting record and is not required to survive a
  restart.
- Correlation is by `event_id` and `session_id`. Every server-emitted event
  carries an `event_id` that appears verbatim in the client debug log, so the
  two logs can be joined without heuristics.

## Consequences

- A server restart destroys the live session (Section 25.10) but not the record
  of what was already transcribed and translated, because that record is on the
  client.
- The client becomes responsible for durability, which means its writer needs
  the full treatment: bounded queue, non-blocking hand-off from the UI and
  network threads, flush policy, crash-tolerant line framing, and the negative
  vectors named in Section 25.14 (truncated tail, duplicate and conflicting
  revision, invalid path, permission failure, disk full).
- Client-side persistence is fully testable on the dev machine with no GPU and
  no pod, which makes it good Phase 3 work.
- Server-internal decisions that are never transmitted are absent from the
  authoritative log. The protocol must therefore carry enough evidence — the
  decode-quality fields of Section 14.3, language evidence, admission timings —
  for the debug log to be self-sufficient. This is a protocol requirement,
  recorded against PROT-300 so the Phase 1 gate cannot forget it.

## Rollback plan

Moving authority to the server later means changing which side opens the JSONL
files, not changing their format. The formats are defined in `protocol/` and are
side-agnostic by construction. Rollback stops being cheap once the recovery and
compaction tooling assumes a client-side path layout, so those tools take the
log directory as a parameter rather than deriving it.

## Evidence required before `accepted`

None for the policy recorded here. The detailed record schema, flush policy and
retention rules require the Phase 1 log-schema gate (`requirements.md`
Section 26, item 14) and are not decided by this ADR.

## Open questions

- Flush and fsync cadence for the debug writer: a durability and latency
  trade-off that needs measurement on the dev machine. Deferred to the Phase 1
  gate.
- Retention and deletion policy for the three files. Deferred to the same gate,
  required by Section 21.
- Whether `_history.jsonl` should be suppressed entirely in a low-overhead mode.
  Deferred; default is to write it.
