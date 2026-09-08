# ADR-0001: Repository structure and enforced dependency isolation

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 1
- **Requirement IDs:** OPS-010, OPS-020, OPS-030
- **Decided by:** user on 2026-09-08 (Phase 0 proposal, option C)

## Context and constraints

The repository was effectively empty at Phase 0: one tracked `README.md` with no
content, one commit, no source, no tests, no `.gitignore`, no established
conventions. There is therefore no existing layout to preserve, and
`CLAUDE.md` Section 4 item 4 requires proposing a target module layout.

Two clauses constrain the layout:

- `CLAUDE.md` Section 6 sketches an expected separation
  (`client/`, `server/{gateway,orchestrator,asr_worker,speechbrain_worker,diarization_worker,translation_service}/`,
  `protocol/`, `configs/`, `tests/`, `tools/`, `docs/`).
- `requirements.md` Section 7 and `CLAUDE.md` Section 7 require that the server
  not load all ML frameworks into one Python process, and that the
  client/PySide6, gateway/protocol, faster-whisper/CTranslate2, SpeechBrain,
  pyannote and vLLM/Qwen dependency domains stay isolated. A dependency
  conflict must be reproduced, documented and isolated, not solved by upgrading
  the whole repository.

`requirements.md` Section 25.15 additionally splits tests into three categories
that are not layers of a stack but kinds of evidence, which affects where tests
live.

## Options considered

### Option A — Flat layout exactly as sketched in `CLAUDE.md` Section 6

| Dimension | Assessment |
|---|---|
| Correctness | Matches the document verbatim; no interpretation risk |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Test tree organised by layer, not by evidence category |
| Dependency isolation | Stated but not enforced; nothing stops the gateway importing SpeechBrain |
| Future maintenance | Short import paths; isolation erodes silently |

### Option B — One installable distribution per dependency domain

`packages/{protocol,client,gateway,orchestrator,asr-worker,speechbrain-worker,diarization-worker,translation-service}/`,
each with its own `pyproject.toml` and lock file.

| Dimension | Assessment |
|---|---|
| Correctness | Strongest guarantee that domains cannot import each other |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Each package testable in its own environment |
| Dependency isolation | Enforced by packaging |
| Future maintenance | Eight `pyproject.toml` files; editable installs are awkward on the Windows dev machine; slows Phase 1-3 before the pod is even in use |

### Option C — Section 6 layout, with isolation enforced by per-domain lock files and an import-boundary test

| Dimension | Assessment |
|---|---|
| Correctness | Same tree as the document, so no divergence from the normative sketch |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | Per-domain locks make it possible to install only what a worker needs |
| Testability | `tests/` organised by the Section 25.15 evidence categories |
| Dependency isolation | Enforced by a category B conformance test plus separate lock files, rather than by convention |
| Future maintenance | One repository, one import root; boundary violations fail a test instead of appearing at runtime on the pod |

## Decision

Option C. The committed tree is:

```text
client/                     PySide6 UI, WASAPI loopback capture, resampling,
                            WebSocket client, persistence writers
server/
  gateway/                  WebSocket termination, validation, size limits,
                            protocol version negotiation
  orchestrator/             canonical sample timeline, utterance/segment ID
                            authority, lifecycle, revisions, gap policy,
                            GPU admission, degraded-mode state
  asr_worker/               faster-whisper / CTranslate2
  speechbrain_worker/       VoxLingua107 language ID, ECAPA speaker embeddings
  diarization_worker/       pyannote overlap detection and refinement
  translation_service/      vLLM client, Qwen prompt contract, output validator
protocol/                   binary header codec, event schemas, revision
                            projection rules, error codes. No ML dependency.
configs/                    validated configuration
requirements/               <domain>.in and <domain>.lock.txt per dependency domain
tests/
  real_ml/                  category A: real recording, real models
  conformance/              category B: labelled negative and protocol vectors
  replay/                   category C: real captured WebSocket traffic replay
  fixtures/                 provenance manifests only, never media
  manifests/                development / validation / locked evaluation splits
  server_scripts/           scripts the user runs on the H100 pod
tools/                      annotation, capture, hashing, history rebuild
docs/
  adr/                      decision records
  runbooks/                 tunnel, server start, model download, recovery
  traceability.md
scripts/                    format / lint / type-check entry points
data/                       git-ignored local working data (recordings, clips,
                            captures, output). Never committed.
```

Two rules give the layout teeth:

1. **`protocol/` carries no ML dependency.** It is imported by both the client
   and the server and defines the wire contract. Anything that would pull
   torch, CTranslate2, SpeechBrain, pyannote or vLLM into `protocol/` is a
   design error.
2. **Cross-worker imports are forbidden and tested.** A category B conformance
   test asserts that no module under one `server/<worker>/` package imports
   another worker package. Workers communicate through `protocol/` types and
   the orchestrator, never by direct import.

## Consequences

- The tree matches `CLAUDE.md` Section 6, so no interpretation gap opens
  between the normative sketch and the implementation.
- Dependency isolation stops being a promise and becomes a failing test.
- `tests/` mirrors the evidence taxonomy, which makes it structurally hard to
  file a purpose-built negative vector as if it were meeting data — the exact
  confusion `requirements.md` Section 25.15 B forbids.
- Per-domain lock files mean six environments to maintain once the pod is in
  use. The user has asked to keep the number of virtual environments low for
  the MVP; ADR-0007 resolves how many actually get created.

## Rollback plan

Moving to Option B later is a mechanical change: add a `pyproject.toml` per
worker package and relocate directories. The import-boundary test written for
Option C is what makes that move safe, because it already proves no cross-worker
imports exist. Rollback stops being cheap once configuration files and runbooks
hard-code absolute module paths, so runbooks reference package names, not paths.

## Evidence required before `accepted`

None — this is a structural decision with no measurable quantity. The
import-boundary conformance test is an implementation obligation recorded
against OPS-030, not an acceptance precondition for this ADR.

## Open questions

- Whether `server/` becomes an installable package or stays a plain import root
  is deferred to ADR-0007, since it depends on how the pod environments are laid
  out.
