# Live Japanese-Vietnamese Meeting Translator

A Windows client captures meeting audio played through any output device using
WASAPI loopback, streams it to a single-H100 Linux GPU server over a WebSocket
carried by an SSH tunnel, and displays a live timeline of speaker, language,
transcription and translation.

Japanese speech is translated to Vietnamese, Vietnamese speech to Japanese.
Transcription is partial and final; **translation is final-only**.

## Normative documents

Two files govern this repository. Neither may be edited without an explicit,
approved requirement-change proposal.

| File | Role |
|---|---|
| [`requirements.md`](requirements.md) | product, architecture, test and acceptance requirements. Normative. |
| [`CLAUDE.md`](CLAUDE.md) | working rules: design gates, real-data-only testing, server test gate, Git workflow. |

Where the two conflict, `requirements.md` wins and the conflict is reported
rather than silently resolved. Conflicts and ambiguities found so far are
recorded in the ADRs, not patched into the documents.

## Fixed technology

| Layer | Choice |
|---|---|
| Client | Python, PySide6, PyAudioWPatch WASAPI loopback |
| Wire | mono PCM s16le 16 kHz over WebSocket through an SSH tunnel |
| Endpointing | Silero VAD, on CPU |
| ASR | `openai/whisper-large-v3` via faster-whisper / CTranslate2 |
| Language ID | `speechbrain/lang-id-voxlingua107-ecapa` |
| Speaker embedding | `speechbrain/spkrec-ecapa-voxceleb` |
| Overlap and refinement | pyannote, in an isolated worker |
| Translation | `Qwen/Qwen3.5-9B` served by vLLM, thinking disabled |

Source separation and noise suppression are disabled in the MVP production path.

## Repository layout

```text
client/                     PySide6 UI, WASAPI capture, resampling, WS client
server/gateway/             WebSocket termination, validation, size limits
server/orchestrator/        timeline, ID authority, lifecycle, revisions, admission
server/asr_worker/          faster-whisper / CTranslate2
server/speechbrain_worker/  language ID and speaker embeddings
server/diarization_worker/  pyannote overlap and refinement
server/translation_service/ vLLM client, prompt contract, output validator
protocol/                   wire contract. No ML dependency.
configs/                    validated configuration
requirements/               per-dependency-domain lock files
tests/real_ml/              category A: real recording, real models
tests/conformance/          category B: labelled negative vectors
tests/replay/               category C: real captured WebSocket replay
tools/                      annotation, capture, hashing, history rebuild
docs/                       ADRs, runbooks, traceability
data/                       git-ignored local working data. Never committed.
```

See [ADR-0001](docs/adr/ADR-0001-repository-structure.md) for why.

## Documentation

| Document | Contents |
|---|---|
| [`docs/adr/`](docs/adr/) | architecture decision records, one per design gate |
| [`docs/requirement-ids.md`](docs/requirement-ids.md) | stable ID for every normative clause |
| [`docs/traceability.md`](docs/traceability.md) | ID → ADR → module → test → evidence → status |
| [`docs/environment-matrix.md`](docs/environment-matrix.md) | verified facts about the dev machine and the H100 pod |
| [`docs/model-inventory.md`](docs/model-inventory.md) | model licenses, access conditions, revisions |
| [`docs/test-taxonomy.md`](docs/test-taxonomy.md) | the three test categories and what each may claim |
| [`docs/test-data.md`](docs/test-data.md) | real-data provenance and what is still missing |
| [`docs/protocol.md`](docs/protocol.md) | the wire contract: header layout, events, revisions, errors, limits |
| [`docs/phase-checklists.md`](docs/phase-checklists.md) | per-phase completion checklist |

## How work proceeds

Every phase follows the same shape, defined by `requirements.md` Section 28 and
`CLAUDE.md` Section 21:

```text
design gate -> ADR -> implement -> format, lint, type-check
-> run the tests this environment permits
-> update docs and traceability -> review the diff -> commit -> push
-> report commit hash, tests run, tests not run, known issues
-> stop at any SERVER_TEST_GATE
```

Two rules constrain what may be claimed:

- **Real data only.** No synthetic audio, invented transcript, invented
  translation or mocked model output in any quality test. Purpose-built vectors
  are permitted only for protocol, security and persistence, and are labelled so
  they can never be mistaken for evidence of model quality.
- **No GPU test is claimed without evidence.** Server tests run on the H100 pod,
  which this workspace cannot reach. Scripts and instructions are committed, a
  `SERVER_TEST_GATE` is printed, and work stops until the real run artifacts
  come back.

## Status

Phase 1 complete: all five design gates settled (ADR-0008 through ADR-0012),
`protocol/` implemented, 168 conformance vectors passing.

Next: the condition survey of the real recording, which blocks the evaluation
split and the annotation guide, then Phase 2 (the Windows capture client).
See [`docs/phase-checklists.md`](docs/phase-checklists.md).
