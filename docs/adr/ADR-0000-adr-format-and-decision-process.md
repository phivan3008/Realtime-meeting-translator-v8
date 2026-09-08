# ADR-0000: ADR format and decision process

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** none — process decision supporting `requirements.md` Section 26
- **Requirement IDs:** OPS-400, OPS-410
- **Decided by:** user on 2026-09-08 (decision D2, option C)

## Context and constraints

`CLAUDE.md` Section 3.1 requires that every phase and major component be
preceded by a design discussion that presents alternatives, weighs trade-offs
across correctness, latency, accuracy, GPU/RAM usage, testability, dependency
isolation and maintenance, makes one recommendation, and is then recorded in a
version-controlled decision record. `requirements.md` Section 26 enumerates 22
mandatory design gates, each of which must produce a committed decision record.

Two further constraints shape the format:

- `CLAUDE.md` Section 5 requires a rollback plan for the deployment decision,
  and the same discipline is worth applying to every reversible decision.
- `requirements.md` Section 24 and `CLAUDE.md` Section 24 forbid inventing
  numeric acceptance thresholds before a real-data baseline exists. A decision
  record that carries numbers must therefore state what evidence justified them.

## Options considered

### Option A — MADR-lite

Standard lightweight Markdown ADR: context, options, decision, consequences.

| Dimension | Assessment |
|---|---|
| Correctness | Adequate |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | No slot for evidence or server-gate outcome |
| Dependency isolation | n/a |
| Future maintenance | Familiar, low friction |

### Option B — Nygard classic

Context / Decision / Status / Consequences only.

| Dimension | Assessment |
|---|---|
| Correctness | Adequate |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | No slot for alternatives, which this project mandates |
| Dependency isolation | n/a |
| Future maintenance | Shortest, but loses the reasoning the project requires |

### Option C — MADR-lite plus three project-mandated sections

MADR-lite with **Rollback plan**, **Evidence required before `accepted`** and
**Requirement IDs** as required sections.

| Dimension | Assessment |
|---|---|
| Correctness | Adequate |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Evidence section ties each decision to a test or server gate |
| Dependency isolation | Options table forces the isolation trade-off to be stated |
| Future maintenance | Slightly longer to write; far easier to audit later |

## Decision

Option C.

- Filename: `docs/adr/ADR-NNNN-<kebab-slug>.md`, `NNNN` allocated sequentially
  and never reused.
- Template: `docs/adr/ADR-TEMPLATE.md`.
- Required sections: Status, Date, Design gate, Requirement IDs, Decided by,
  Context and constraints, Options considered, Decision, Consequences,
  Rollback plan, Evidence required before `accepted`, Open questions.
- Every option must be assessed against the seven dimensions listed in
  `CLAUDE.md` Section 3.1.

Status rules:

- Claude Code may create an ADR with status `proposed`.
- Only the user moves an ADR to `accepted`, and the **Decided by** line records
  who and when.
- A superseded ADR is never deleted or rewritten; it is marked
  `superseded by ADR-NNNN` and left in history.
- An ADR that fixes a numeric threshold cannot reach `accepted` while its
  **Evidence required** section is unsatisfied.

## Consequences

- Every design gate in `requirements.md` Section 26 gets exactly one traceable
  artifact, referenced from `docs/traceability.md`.
- Writing an ADR costs more up front than an informal note; that cost is the
  point, because it is what keeps an architecture decision from being made
  silently.
- ADR numbers become stable identifiers usable in commit messages and in the
  traceability matrix.

## Rollback plan

Changing the ADR format later does not require rewriting existing records.
Add a new ADR superseding this one and apply the new format from that number
forward. Existing records stay valid as historical evidence.

## Evidence required before `accepted`

None — this is a process decision with no measurable quantity.

## Open questions

None.
