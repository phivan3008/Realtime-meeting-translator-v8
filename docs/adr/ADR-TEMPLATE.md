# ADR-NNNN: <short imperative title>

- **Status:** proposed | accepted | rejected | superseded by ADR-NNNN
- **Date:** YYYY-MM-DD
- **Design gate:** `requirements.md` Section 26, item N (or "none — internal decision")
- **Requirement IDs:** XXX-000, XXX-010
- **Decided by:** <user> on <date>

> An ADR is `accepted` only when the user has explicitly approved it.
> Claude Code may author an ADR and mark it `proposed`; it may not mark it
> `accepted` on its own authority.

## Context and constraints

What problem forces a decision. Which requirement clauses constrain it. What is
already fixed by `requirements.md` and therefore not up for debate here.

## Options considered

### Option A — <name>

Description.

| Dimension | Assessment |
|---|---|
| Correctness | |
| Latency | |
| Accuracy | |
| GPU / RAM usage | |
| Testability | |
| Dependency isolation | |
| Future maintenance | |

### Option B — <name>

Same table.

## Decision

The selected option and the precise scope of what is being decided. State
explicitly what this ADR does *not* decide.

## Consequences

Positive, negative, and what becomes harder.

## Rollback plan

Concrete steps to undo this decision, and the point after which rollback stops
being cheap. Required by `CLAUDE.md` Section 5.

## Evidence required before `accepted`

Which numbers must be measured on real data, by which script, at which gate.
An ADR that fixes a numeric threshold cannot be accepted without measured
evidence (`requirements.md` Section 24 / `CLAUDE.md` Section 24).

For a decision that needs no measurement, write: "None — this is a process or
structural decision with no measurable quantity."

## Open questions

Anything deliberately deferred, and to which gate.
