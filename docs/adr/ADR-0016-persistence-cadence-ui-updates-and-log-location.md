# ADR-0016: Persistence cadence, UI update batching, and where logs live

- **Status:** accepted
- **Date:** 2026-09-09
- **Design gate:** `requirements.md` Section 26, item 14 (the parts ADR-0011 and ADR-0013 deferred)
- **Requirement IDs:** PERS-030, PERS-060, PERS-100, UI-060, UI-120, UI-140, SEC-050, SEC-060, SEC-090
- **Decided by:** user on 2026-09-09 (decisions D40, D41 and D42)

## Context and constraints

Three questions were deliberately left open by earlier ADRs and come due now
that Phase 3 implements the client's persistence and its timeline:

- ADR-0011 D29 left the `_history.jsonl` write cadence open.
- ADR-0013 left open whether the Qt signal carrying projections should batch.
- Neither said where log files go, only what they are called (Section 19.1).

Fixed already:

- `_debug.jsonl` is the authoritative append-only event log; `_history.jsonl` is
  a live projection with **no authority**; `_history.final.jsonl` is the
  compacted deliverable (ADR-0004 D5).
- Every debug record is written and flushed immediately; `fsync` runs at session
  start, every accepted final, every seal, session stop, and on an interval
  (ADR-0011 D29).
- The UI projects by `segment_id` using the shared reducer, ignores duplicate and
  stale events, and never appends a partial as a new permanent line (UI-120,
  UI-140, ADR-0008 D13).
- Filenames are sanitized from the session identifier (PERS-100, SEC-060).

---

## D40 — How often the live history projection is written

### The problem

`_history.jsonl` exists for two reasons and neither is durability: an operator
can watch the meeting take shape, and a crash leaves something readable. The
authoritative record is `_debug.jsonl`.

At fifty partials per second, a thirty-minute meeting produces about 90,000
revisions. Writing each one produces a 90,000-line file in which almost every
line has already been superseded by a later one.

### Options considered

#### Option A — write on every revision

| Dimension | Assessment |
|---|---|
| Correctness | Nothing is missed |
| Latency | 50 writes per second on the writer thread, competing with the debug log |
| GPU / RAM usage | n/a |
| Testability | Simple |
| Future maintenance | The file becomes unreadable, which defeats the only two reasons it exists. Section 13.2 calls every partial replaceable; writing all of them durably records the replaceable |

#### Option B — coalesce on an interval

| Dimension | Assessment |
|---|---|
| Correctness | Some intermediate revisions are skipped, which is fine for a non-authoritative view |
| Latency | Bounded |
| Future maintenance | Still writes partials, merely fewer of them |

#### Option C — write only on lifecycle-significant change

| Dimension | Assessment |
|---|---|
| Correctness | Every state a reader would care about is present |
| Latency | A handful of writes per utterance rather than tens |
| Testability | The trigger set is explicit and enumerable |
| Future maintenance | The file reads like a transcript because it contains transcript-shaped events, not a frame-by-frame trace |

### Decision

**Option C.** The projection is written when, and only when:

```text
a content revision reaches ACCEPTED, LOW_CONFIDENCE or REJECTED
a translation reaches COMPLETED or FAILED
a segment is sealed
a speaker merge rewrites affected views
```

`NOT_APPLICABLE` is absent from that list, and its absence was found by a test
rather than by reading. It is the value every segment carries from creation, so
treating it as a translation outcome makes every partial history-worthy and
defeats the decision entirely. A segment that genuinely ends up
`NOT_APPLICABLE` - a rejected or low-confidence final - reaches that state
alongside an ASR outcome, which triggers on its own.

Partial revisions are **not** written. Losing in-flight partials to a crash is
acceptable precisely because `_debug.jsonl` holds every one of them — which is
the reason that file is the authoritative one. Recording the replaceable twice,
once authoritatively and once not, buys nothing and costs readability.

---

## D41 — How often the UI is told

### Options considered

| Option | Assessment |
|---|---|
| A — one Qt signal per event | 50 signals per second, each triggering a repaint. The GUI thread will notice, and a repaint of a 500-row timeline for one changed row is waste that grows with meeting length |
| B — coalesce on a timer | 10 repaints per second, but each still repaints everything |
| C — coalesce on a timer **and** carry the set of changed `segment_id`s | **Selected.** Only changed rows repaint. A partial arriving at the end of a long meeting does not touch the other 499 rows |

### Decision

**Option C.** The asyncio side accumulates changed segment identifiers; a timer
on the Qt side drains the set, fetches those projections and updates only those
rows.

The interval is `benchmark_required`, with 100 ms as the starting value — below
the threshold at which a person notices, so partial text still reads as live.

One constraint travels with this decision and is not negotiable: **the UI does
not fold events itself.** It renders projections produced by the shared reducer
(ADR-0008 D13). If the UI had its own projection logic, the timeline a user sees
and the file on disk could disagree, and that disagreement would be discovered
after the meeting ended — the worst possible time (UI-120, UI-130, UI-140).

---

## D42 — Where the log files go

### The problem

Section 19.1 gives the filenames and says nothing about the directory. There is a
specific trap: the user machine runs from **an extracted ZIP folder**, which may
be read-only and which a person tidying their Downloads will delete without
thinking about it.

### Options considered

| Option | Assessment |
|---|---|
| A — beside the application | Easy to find. But it puts the meeting record in the single most deletable location on the machine, and an extracted archive may not even be writable |
| B — `%LOCALAPPDATA%` | The Windows convention for application data. But the user has to be told a path they would never think to look in, and they need to find these files to send them back |
| C — `%USERPROFILE%\Documents\MeetingTranslator\`, configurable, with the resolved path shown in the UI | **Selected** |

### Decision

**Option C.** A meeting record is the **user's document**, not application data.
It contains what people said (SEC-090), it is the artifact they will be asked to
send back during testing, and it is the thing they will eventually want to keep
or delete deliberately (SEC-050). Documents is where a person looks for a
document.

```yaml
persistence:
  directory: "%USERPROFILE%/Documents/MeetingTranslator"   # default, configurable
  files:
    debug:        "meeting_<session-id>_debug.jsonl"
    history:      "meeting_<session-id>_history.jsonl"
    history_final: "meeting_<session-id>_history.final.jsonl"
```

Rules that make the path safe:

- The session identifier is validated against the uuid4 pattern **before it
  reaches a path** (PERS-100, SEC-060). `protocol/identifiers.py` already has
  the check; persistence calls it rather than trusting its caller.
- The resolved directory is shown in the UI, so a person asked to send a log
  knows where to find it without being talked through a path.
- The directory is created if missing, and a failure to create or write it stops
  the session (PERS-130): a session that cannot preserve its own event source is
  a session that should not be recording.
- Files are UTF-8 without BOM, LF line endings, one JSON object per line
  (PERS-100).

---

## Consequences

- `_history.jsonl` becomes readable by a human during a meeting, which is what
  makes it worth writing at all.
- The debug writer's queue carries every event; the history writer's carries a
  small fraction. Sizing them separately is now possible and necessary.
- The UI's update path has one input — a set of changed identifiers — which
  makes it testable without a display: assert which identifiers were published
  for a given event sequence, and assert the rendered projection separately.
- Logs land outside the source tree, so deleting an extracted archive does not
  delete a meeting, and a read-only install directory is not a failure.
- Putting meeting records in Documents means they are visible, backed up by
  whatever backs up Documents, and synced by whatever syncs it. That is right
  for a user's own document and worth stating plainly in the privacy notes,
  because "visible and synced" is a property of the location, not an accident
  (SEC-090).

## Rollback plan

All three are configuration or a single module. D42's default can change without
touching code; D40's trigger set is one function; D41's interval is a value and
its batching is contained in the bridge between the asyncio side and Qt.

The one thing that would be expensive to reverse is the constraint attached to
D41 — the UI not folding events itself. That is deliberate, and reversing it
would reintroduce exactly the divergence ADR-0008 D13 exists to prevent.

## Evidence required before `accepted`

Structural decisions, approved by the user on 2026-09-09.

Deferred to measurement at the Phase 3 gate:

| Value | What it trades |
|---|---|
| UI coalescing interval | perceived liveness against repaint cost |
| debug writer queue depth | burst tolerance against how quickly a stall stops the session |
| history writer queue depth | same, at a much lower event rate |
| `fsync` interval | durability against writer-thread cost |

## Open questions

- Whether the UI should offer a "open log folder" action rather than only
  displaying the path. Trivial to add; deferred until there is a UI to add it to.
- Retention defaults. ADR-0011 D28 decided there is no automatic deletion and
  that `retention_days` exists and is off; whether the UI surfaces disk usage is
  a Phase 13 packaging question.
