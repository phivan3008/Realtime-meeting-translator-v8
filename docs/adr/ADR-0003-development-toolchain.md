# ADR-0003: Development toolchain — format, lint, type-check, schema validation

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** none — process decision supporting `requirements.md` Section 28
- **Requirement IDs:** OPS-200, OPS-210, OPS-220, PROT-400
- **Decided by:** user on 2026-09-08 (decision D8, option A)

## Context and constraints

`requirements.md` Section 28 requires format, lint and type-check at the end of
every phase, but names no tool. `CLAUDE.md` Section 8 requires clear typed
interfaces at every worker boundary and schema validation for control events and
worker messages. `requirements.md` Section 21 requires validating all client and
worker messages and imposing maximum sizes on frames, events, strings, queues
and sessions.

The Windows dev machine runs Python 3.11.9; the H100 pod runs Python 3.11.13.
Both are 3.11, so no version-conditional tooling is needed.

## Options considered

### Option A — `ruff format` + `ruff check` + `mypy --strict` + `pydantic` v2

| Dimension | Assessment |
|---|---|
| Correctness | `mypy --strict` matches the "clear typed interfaces at every worker boundary" requirement directly |
| Latency | Ruff is fast enough to run on every save and in every phase gate |
| Accuracy | n/a |
| GPU / RAM usage | None; all tooling is CPU and runs on the dev machine |
| Testability | Static checks are cheap and do not substitute for behavioural tests (`CLAUDE.md` Section 17) |
| Dependency isolation | Dev tooling lives in its own `requirements/dev.lock.txt`, never in a worker environment |
| Future maintenance | One tool for format and lint means one configuration block |

### Option B — `black` + `flake8` + `mypy`

| Dimension | Assessment |
|---|---|
| Correctness | Equivalent outcome |
| Latency | Noticeably slower on a repository of this size |
| Accuracy | n/a |
| GPU / RAM usage | None |
| Testability | Same |
| Dependency isolation | Three tools, three configuration files, more plugin surface |
| Future maintenance | More moving parts for no gain |

### Option C — `ruff` + `pyright`

| Dimension | Assessment |
|---|---|
| Correctness | Pyright's inference is stronger in some generic cases |
| Latency | Fast |
| Accuracy | n/a |
| GPU / RAM usage | None |
| Testability | Same |
| Dependency isolation | Adds a Node runtime to the dev machine |
| Future maintenance | Extra runtime dependency for a marginal gain |

## Decision

Option A.

- **Format:** `ruff format`
- **Lint:** `ruff check`
- **Type-check:** `mypy --strict` over `protocol/`, `client/`, `server/`,
  `tools/`. Third-party stubs that are missing get an explicit per-module
  `ignore_missing_imports`, recorded in configuration rather than as a blanket
  suppression.
- **Schema validation:** `pydantic` v2 for control events, worker messages and
  configuration files. Configuration is validated at load, not at first use, so
  a bad threshold fails at startup rather than mid-meeting.
- **Test runner:** `pytest`.
- Entry points live in `scripts/` so the phase-completion sequence in
  `requirements.md` Section 28 is one documented command per step.

Exact pinned versions are deferred: `CLAUDE.md` Section 7 requires dependencies
to be pinned reproducibly *after* the target environment is known, and no
dependency is installed before the environment gate. The dev toolchain will be
pinned in `requirements/dev.lock.txt` as the first act of Phase 1.

## Consequences

- One tool covers format and lint, so the Section 28 sequence stays short.
- `mypy --strict` will make the protocol layer verbose in places; that verbosity
  is the point at a worker boundary, where an untyped dict is how revision
  fields get silently dropped.
- Pydantic v2 models double as the schema documentation required by
  `CLAUDE.md` Section 25, so the event schema and its validator cannot drift
  apart.
- Static checks are explicitly not evidence of behaviour
  (`CLAUDE.md` Section 17.3); phase reports list them separately from tests.

## Rollback plan

Swapping any single tool is a configuration change plus one pass of the
formatter. Rollback stays cheap indefinitely because none of these tools appear
in runtime code. Pydantic is the exception: it is a runtime dependency of
`protocol/`, and replacing it after schemas are written would touch every event
definition, so that choice is the one worth revisiting only with cause.

## Evidence required before `accepted`

None — process decision with no measurable quantity.

## Open questions

- Whether `pydantic` is acceptable inside `protocol/` given that `protocol/` must
  stay free of ML dependencies. Pydantic is pure Python with a Rust core and
  pulls in no ML stack, so it does not violate ADR-0001's rule. Recorded here so
  the reasoning is not re-litigated later.
