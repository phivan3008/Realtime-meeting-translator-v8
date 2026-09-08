# ADR-0005: The Section 9.3 event list is a minimum, and how it is extended

- **Status:** accepted (policy); the extended event set itself is decided at the Phase 1 protocol gate
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 3 (partial)
- **Requirement IDs:** PROT-050, PROT-060, PROT-070
- **Decided by:** user on 2026-09-08 (decision D4, option C)

## Context and constraints

`requirements.md` Section 9.3 lists twenty-one required event types. Two later
sections require behaviour that no listed event can express:

- Section 25.9 ends the graceful-stop sequence with "emit `session.summary`"
  followed by "emit `session.stopped`". `session.summary` is not in the
  Section 9.3 list.
- Section 25.12 requires that every degraded mode "emit start/end warning
  events, **update session capability state**, and be visible in UI". A warning
  is an occurrence; capability state is state. `pipeline.warning` can carry the
  former but cannot carry the latter, and Section 25.12 explicitly forbids a
  component silently substituting lower-quality behaviour, which is exactly what
  happens if the client cannot tell that a capability is currently off.
- Section 25.10 requires the client to "emit and persist `session_unrecoverable`".
  Whether that is a wire event or a client-local record is not stated.

Section 9.1 already establishes forward-compatibility rules: unknown major
versions are rejected, unknown optional fields within the same major version are
ignored safely. It says nothing about unknown *event types*.

`requirements.md` Section 1 forbids silently changing a protocol contract, and
`CLAUDE.md` Section 9 requires reading Section 25 in full before implementing
any pipeline component. Editing Section 9.3 is not an option available to
Claude Code.

## Options considered

### Option A — treat Section 9.3 as complete and add the missing events to it

| Dimension | Assessment |
|---|---|
| Correctness | Produces the right wire behaviour |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Clear |
| Dependency isolation | n/a |
| Future maintenance | Requires editing a normative section, which is forbidden without an approved requirement-change proposal |

### Option B — carry capability changes inside `pipeline.warning`

| Dimension | Assessment |
|---|---|
| Correctness | Loses the distinction between an event and a state; the client would have to reconstruct current capability by replaying every warning |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Hard to assert "capabilities are currently X" |
| Dependency isolation | n/a |
| Future maintenance | Warning payloads accrete a second, undocumented purpose |

### Option C — treat Section 9.3 as the minimum set, record extensions in an ADR

| Dimension | Assessment |
|---|---|
| Correctness | Every event in Section 9.3 still exists with its stated meaning; nothing is removed or redefined |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Each extension gets its own conformance vector and requirement ID |
| Dependency isolation | n/a |
| Future maintenance | The normative documents stay untouched; the delta is auditable in one place |

## Decision

Option C.

- `requirements.md` Section 9.3 is read as the **required minimum** event set.
  Every one of its twenty-one types is implemented with the meaning the
  document gives it. None is removed, renamed or repurposed.
- Additional event types are permitted only when a normative clause requires
  behaviour that the minimum set cannot express. Each addition is recorded in
  this ADR with the clause that forces it, is assigned a requirement ID, and
  gets a conformance vector under `tests/conformance/`.
- Unknown event types within the same major protocol version are ignored safely
  and logged, extending the Section 9.1 rule for unknown optional fields.
  A receiver never fails a session because it saw an event type it does not know.

**Extensions forced by Section 25 (to be specified at the Phase 1 protocol gate):**

| Event | Forced by | Purpose | Requirement ID |
|---|---|---|---|
| `session.summary` | Section 25.9 | Emitted before `session.stopped`; carries the final session outcome (`completed`, `completed_with_warnings`, `failed`) and segment counts | PROT-050 |
| `capability.updated` | Section 25.12 | Carries current session capability state as a whole, so the client can render degraded mode without replaying warnings | PROT-060 |

**`session_unrecoverable` (Section 25.10)** is a **client-local persisted
record, not a wire event.** The reasoning: it exists precisely because the
server is gone. A server that could send it would not be in the state the event
describes. The client synthesises it on detecting that resume was refused by a
restarted server, writes it to `_debug.jsonl`, and surfaces it in the UI. It is
recorded against PROT-070 and tested as a client-side conformance case with a
replay fixture, not as a server event.

This ADR does not decide the payload schema of any of these events. That is the
Phase 1 protocol gate (`requirements.md` Section 26, item 3), together with the
binary header layout, resume contract and backpressure rules.

## Consequences

- The normative documents are never edited, and the delta between them and the
  implemented wire contract lives in exactly one auditable place.
- `capability.updated` gives the client a state to render rather than a stream
  of warnings to fold, which is what makes "no component may silently substitute
  lower-quality behaviour" testable rather than aspirational.
- Treating unknown event types as ignorable means a newer server can be pointed
  at an older client without a hard failure, at the cost of the older client
  silently missing information. The log entry for the ignored event is what
  makes that cost visible.
- Any future extension needs an ADR amendment, so the event set cannot grow
  informally.

## Rollback plan

Removing an extension event means deleting its schema, its conformance vector
and its emit sites. Because the Section 9.3 minimum is never modified, removing
an extension cannot break a Section 9.3 behaviour. Rollback stays cheap as long
as no extension event becomes load-bearing for a Section 9.3 guarantee, which
is a constraint on future design rather than a point in time.

## Evidence required before `accepted`

None for the policy. Each individual extension requires a conformance vector
under `tests/conformance/` before its requirement ID may reach `tested`.

## Open questions

- Payload schemas for `session.summary` and `capability.updated`: Phase 1
  protocol gate.
- Whether `metrics.snapshot` (already in Section 9.3) should carry capability
  state as well, making `capability.updated` an edge-triggered companion to a
  level-triggered snapshot. Phase 1 protocol gate.
