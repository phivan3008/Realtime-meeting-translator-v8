# CLAUDE.md

## 1. Mission

Implement the live Japanese-Vietnamese meeting transcription and translation application defined in `requirements.md`.

This repository is an existing Git repository. Work incrementally, preserve history, discuss design before coding each component, use real test data, and create auditable commits after every phase and before every GPU server test gate.

`requirements.md` is normative. If this file and `requirements.md` conflict, stop, report the conflict, and ask the user to select or amend the governing rule. Do not silently choose.

## 2. Non-negotiable product decisions

- Windows client in Python with PySide6.
- Capture any output-device audio using PyAudioWPatch WASAPI loopback.
- Stream mono PCM16LE 16 kHz over WebSocket through an SSH tunnel.
- One active meeting at a time.
- Japanese and Vietnamese only.
- Silero VAD for live endpointing.
- Whisper large-v3 loaded locally.
- faster-whisper/CTranslate2 is the production ASR backend.
- Transformers is a reference comparison only.
- SpeechBrain VoxLingua107 ECAPA provides primary acoustic language ID.
- SpeechBrain ECAPA VoxCeleb provides speaker embeddings for anonymous online clustering.
- Pyannote provides overlap detection and retrospective diarization refinement in an isolated worker/environment.
- Source separation is disabled in the MVP production path.
- SpeechBrain SepFormer may only be benchmarked behind an experimental feature flag.
- Noise suppression is disabled by default and may be enabled only after a real-data A/B quality win.
- Qwen3.5-9B is served by vLLM.
- Qwen translation is final-only.
- Qwen requests must specify source and target language explicitly.
- Japanese translates to Vietnamese; Vietnamese translates to Japanese.
- Qwen thinking is disabled.
- Store an append-only debug JSONL and produce a compacted final meeting-history JSONL.
- Use integer audio sample offsets as the canonical media timeline.
- Distinguish stream, utterance, text segment, and speaker turn IDs.
- Use independent content, language, speaker, and translation revisions.
- Treat accepted ASR final as revisable until the segment is sealed.
- Server restart recovery of an active meeting is out of scope for MVP.
- Maintain stable requirement IDs and a traceability matrix.

## 3. Working style

### 3.1 Design before implementation

Before implementing each phase or major source component:

1. Read all relevant existing code, tests, configuration, and documentation.
2. Describe the problem and constraints.
3. Present viable implementation choices.
4. Explain trade-offs involving correctness, latency, accuracy, GPU/RAM usage, testability, dependency isolation, and future maintenance.
5. Make one recommendation.
6. Ask the user to decide at a design gate.
7. Record the approved decision in a version-controlled Architecture Decision Record.
8. Only then implement.

Do not repeatedly ask for confirmation for routine commands inside an approved phase. Ask only when a genuine design choice, environment fact, destructive action, or requirement ambiguity exists.

### 3.2 No silent architecture changes

Do not silently:

- replace a fixed model or framework;
- change the wire audio format;
- merge isolated workers into one environment;
- remove revision semantics;
- enable partial translation;
- enable source separation;
- enable noise suppression;
- expose a public server port;
- reduce test coverage;
- weaken hallucination gates;
- change persistence format.

Propose a documented change and wait at a design gate.

## 4. Required initial actions

Before writing implementation code:

1. Inspect the repository:

```bash
git status --short --branch
git remote -v
git branch --show-current
git log -5 --oneline
find . -maxdepth 3 -type f | sort
```

2. Do not modify uncommitted user work.
3. Report existing structure, conventions, risks, and missing files.
4. Propose a target module layout.
5. Confirm the current phase and design gate.
6. Add or preserve `requirements.md` and this file.

## 5. Environment discovery gate

Claude Code cannot access the H100 pod unless operating there through the user's active environment. Before pinning server dependencies, provide commands for the user to run and wait for raw output covering at least:

```bash
uname -a
cat /etc/os-release
nvidia-smi
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
python3 --version
which python3
nvcc --version || true
docker --version || true
docker compose version || true
podman --version || true
df -h
free -h
```

Also inspect whether ports can bind to localhost, model storage capacity, Hugging Face cache location, and whether separate virtual environments/processes are permitted.

Do not guess CUDA, driver, PyTorch, vLLM, CTranslate2, SpeechBrain, pyannote, or Python compatibility. After receiving results:

- verify current official installation guidance;
- propose a pinned compatibility matrix;
- discuss Docker Compose versus multiple virtual environments;
- record the selected deployment ADR;
- provide a rollback plan.

## 6. Repository architecture expectations

Propose and discuss the final layout. A likely separation is:

```text
client/
server/
  gateway/
  orchestrator/
  asr_worker/
  speechbrain_worker/
  diarization_worker/
  translation_service/
protocol/
configs/
tests/
  client/
  server_scripts/
  fixtures/
  manifests/
tools/
docs/
  adr/
  runbooks/
```

Do not create this blindly if the existing repository has established conventions. Create `docs/traceability.md` before Phase 1 implementation and keep it updated in every behavior-changing commit.

## 7. Dependency isolation

Keep these dependency domains isolated:

- client/PySide6/PyAudioWPatch;
- gateway and protocol;
- faster-whisper/CTranslate2;
- SpeechBrain;
- pyannote;
- vLLM/Qwen.

Do not solve a dependency conflict by casually upgrading/downgrading the whole repository. Reproduce it, document it, isolate it, and amend only the relevant environment lock.

All dependencies shall be pinned reproducibly after the target environment is known. Record model repository and immutable revision/commit where possible.

## 8. Coding requirements

- Use clear typed interfaces at every worker boundary.
- Use schema validation for control events and worker messages.
- Use bounded queues and explicit overload policies.
- Use monotonic time for latency and UTC for trace correlation.
- Make operations idempotent by event/segment/revision identifiers.
- Separate domain logic from transport, UI, model adapters, and persistence.
- Do not block an audio callback or GUI thread.
- Make cancellation and shutdown explicit.
- Make errors structured and actionable.
- Externalize tunable thresholds into validated configuration.
- Include configuration and model hashes in benchmark output.
- Keep source files focused; do not create a single monolithic pipeline module.
- Update documentation and traceability in the same commit as behavior.
- Do not tune multiple independent variables in one benchmark comparison.
- Do not enable quantization, speculative decoding, a new dtype, or concurrent GPU optimization without an ADR and real benchmark.
- Do not improve latency by weakening a quality, hallucination, persistence, or revision gate.


## 9. Mandatory cross-pipeline contracts

The detailed normative contracts are in Section 25 of `requirements.md`. Before implementing any pipeline component, read that entire section and map the affected requirement IDs in `docs/traceability.md`.

### 9.1 Timeline and identifiers

- Use integer sample offsets at 16 kHz as canonical media time.
- Never use receipt time, floating seconds, Whisper-local IDs, or pyannote-local labels as public identity/ordering authorities.
- Keep `stream_id`, `utterance_id`, `segment_id`, and `speaker_turn_id` distinct.
- The orchestrator creates utterance and segment IDs.
- Preserve lineage for splits and merges.

### 9.2 Staged language detection

Do not block all partial ASR while waiting for SpeechBrain LID. At the language design gate, present and obtain approval for:

- first LID evidence duration;
- LID retry milestones;
- pre-LID partial mode;
- speaker/session hint precedence;
- disagreement policy;
- provisional-language partial re-decode behavior;
- unresolved final fallback.

Final translation is allowed only after accepted final ASR with resolved source and target language.

### 9.3 Lifecycle and revisions

Implement the lifecycle exactly as specified:

```text
CREATED -> PARTIAL -> ASR_FINAL_CANDIDATE
-> ACCEPTED | LOW_CONFIDENCE | REJECTED
-> TRANSLATION_PENDING | TRANSLATION_NOT_APPLICABLE
-> TRANSLATED | TRANSLATION_FAILED
-> SEALED
```

Do not use one global revision. Maintain:

- `content_revision`;
- `language_revision`;
- `speaker_revision`;
- `translation_revision`;
- `translated_from_content_revision`;
- `translated_from_language_revision`.

A text or language revision invalidates stale translation. A speaker-only revision does not. Equal revision with unequal payload is an integrity error. Stale results may be logged diagnostically but must not alter current projection.

### 9.4 Speaker and pyannote reconciliation

- Canonical anonymous IDs are session-scoped, monotonic, and never reused.
- Preserve aliases after merges.
- Do not map pyannote speakers by label name/order.
- Design rolling window, cadence, overlap, maximum retrospective span, mapping confidence, and split/merge behavior before implementation.
- Multi-speaker and overlap schema must support unknown primary speaker and candidates.

### 9.5 Stop, gaps, and restart

- Define small, medium, and large gap behavior before transport implementation.
- Never decode across an unreported gap.
- Implement bounded stop, VAD flush, final ASR, translation drain, refinement, seal, summary, and atomic history compaction.
- Resume is supported only while the same in-memory server session survives.
- Server restart makes the active meeting unrecoverable in MVP. Report this explicitly rather than simulating recovery.

### 9.6 Single-GPU admission

Before concurrent model loading, create a GPU Resource ADR and server test script. Implement admission/priority control so final ASR is not starved. Partial jobs may be coalesced. Translation and retrospective pyannote may wait. Benchmark SpeechBrain placement on CPU versus GPU. Never run offline comparison or SepFormer alongside a live meeting except in a documented concurrency test.

### 9.7 Degraded mode

Implement and test explicit capability degradation:

- final ASR failure fails the session;
- partial ASR failure permits warned final-only mode;
- LID failure uses only the approved Whisper fallback or remains unresolved;
- speaker failure displays unknown;
- pyannote failure marks overlap/refinement unavailable;
- translation failure preserves transcript;
- authoritative debug writer failure stops the session safely.

All degradation start/end events must be visible in logs and UI.

### 9.8 Translation identity and validation

Before implementation, pin exact Qwen repository, variant, revision, tokenizer, chat template, license, vLLM compatibility, dtype, and context limit. Context is default-off and may be enabled only by approved A/B evidence.

Validate output for schema, emptiness, reasoning/preamble, Markdown fences, target language, protected entities, plausible length, and source revision. Do not silently rewrite model output. Retry only within the approved bounded policy, then fail visibly.

### 9.9 Persistence

- Debug JSONL is append-only source of truth.
- Active history is a projection and may contain revisions.
- Graceful close/recovery produces one-record-per-segment `history.final.jsonl` by validated temporary write and atomic rename.
- Test truncated tail, duplicate/conflicting revision, invalid path, permission failure, and disk-full behavior with labeled negative vectors.
- Raw audio saving is default-off and never committed.

### 9.10 Test taxonomy

Use three explicit categories:

1. Real-data ML/E2E tests with actual models and human references where required.
2. Labeled protocol/security/persistence negative vectors. Purpose-built malformed inputs are permitted here and must never support ML quality claims.
3. Real captured WebSocket replay for client regression.

Create development, validation, and locked evaluation time ranges that do not overlap. Tune only on development, select on validation, and report final performance on the locked set. Create an annotation guide before reference metrics. Run the whole recording as a soak test.

### 9.11 Traceability

Assign stable domain IDs such as `AUD-001`, `PROT-001`, `ASR-001`, and maintain:

```text
docs/traceability.md
```

Every requirement must map to ADR, implementation, test/script, fixture/vector, evidence, and status. Allowed statuses are:

```text
planned
implemented
tested
test_script_ready
blocked_environment
blocked_real_fixture
failed
accepted_exception
```

Never mark `tested` without evidence. `accepted_exception` requires explicit user approval and tracked follow-up.


## 10. WebSocket and protocol rules

Before implementation, discuss and document:

- binary header byte layout;
- sample format and frame duration;
- sequence and timestamp semantics;
- maximum frame/event size;
- acknowledgement cadence;
- heartbeat;
- reconnect and resume;
- backpressure;
- buffer overflow;
- duplicate/stale event handling;
- session termination;
- error codes;
- version compatibility.

The client shall upsert UI state by `segment_id` and strictly increasing `revision`. Never make the UI append every partial as a new permanent line.

## 11. Language policy

Language ID is shared pipeline state, not a UI-only label.

Required routing:

```text
SpeechBrain acoustic LID
+ Whisper language evidence
+ recent speaker/session language
-> resolved language
-> force Whisper final input language
-> select explicit Qwen source/target language
```

Rules:

- Supported normal output is only `ja` or `vi`.
- Do not run LID on transport-sized fragments.
- Short acknowledgements need context/hysteresis.
- Record provisional, confirmed, uncertain, and mixed state.
- Record language revisions.
- Never ask Qwen to decide translation direction.
- Never invoke Qwen with unresolved language.
- Do not claim robust word-level code switching in MVP.

Discuss thresholds and conflict policy at a design gate after observing the real recording.

## 12. Whisper partial/final policy

### 11.1 Partial

Implement rolling decode around Whisper. Discuss and benchmark:

- decode interval;
- rolling window;
- left context;
- low-latency decode profile;
- provisional language behavior;
- stable-prefix algorithm;
- minimum publish delta;
- revision/flicker metrics.

Every partial is replaceable.

### 11.2 Final

At a VAD endpoint:

- include pre-roll and post-roll;
- re-decode the complete utterance with the accurate profile;
- force resolved language when reliable;
- collect timestamps and quality evidence;
- apply hallucination acceptance;
- publish final or low-confidence/rejected state;
- translate only an accepted final.

### 11.3 Context contamination prevention

Only accepted final transcription may enter future Whisper context. Partial, rejected, low-confidence, translated, or unvalidated text must not be added.

## 13. Hallucination policy

Whisper hallucination is a required engineering workstream, not a note for later.

Implement these layers:

1. Frame/audio validity.
2. VAD gate.
3. Utterance quality gate.
4. Language-constrained decoding.
5. Decode evidence collection.
6. Repetition and implausibility signals.
7. Partial stability.
8. Retry/reset/accept/reject policy.
9. Translation firewall.

Collect, when available:

- no-speech probability;
- average log probability;
- compression ratio;
- speech ratio;
- RMS/peak/clipping/zero ratio;
- frame gaps;
- language disagreement;
- n-gram/character/token repetition;
- text length versus duration;
- similarity to prior segment;
- timestamp plausibility;
- partial rollback/revision behavior.

Do not rely on one threshold. Do not copy threshold values from an example and call them accepted. Establish baseline values with the real recording.

On a failed final quality gate:

1. Retry with reset/shorter context.
2. Disable previous-text conditioning on retry.
3. Force reliable LID language.
4. Apply only benchmark-approved fallback settings.
5. Return low confidence or rejection if still unreliable.

Never turn filler, hesitation, clipped speech, or an unfinished sentence into a polished ASR sentence. Never send rejected/uncertain text to Qwen.

## 14. Speaker and overlap policy

- Speaker names are out of scope.
- SpeechBrain ECAPA extracts embeddings.
- A separately designed online clustering layer assigns anonymous IDs.
- Speaker IDs are provisional and revisable.
- Do not update clean speaker profiles from short, weak, rejected, or overlapped segments.
- Pyannote runs in an isolated worker for overlap and retrospective refinement.
- Pyannote changes must be emitted as versioned events.
- Unknown speaker count is normal.
- Silero remains the live endpointing authority.

Source separation is experimental only:

- default disabled;
- invoke only on detected overlap regions in experiments;
- always preserve mixed-audio baseline;
- never choose separated output without a human-reviewed real-data quality gate.

## 15. Translation policy

Translation is final-only.

Every Qwen request must explicitly include:

- source language;
- target language;
- accepted final source text;
- bounded accepted context if enabled;
- instruction to output translation only;
- instruction not to add information;
- preservation of names, numbers, dates, URLs, code, and identifiers;
- a validated output schema.

Required mapping:

```text
ja -> vi
vi -> ja
```

Qwen thinking shall be disabled. Initial sampling shall be deterministic. Runtime parameters must be benchmarked on the H100 rather than assumed.

Translation failures shall not invalidate ASR. Implement timeout, idempotent retry, and stale-revision rejection.

## 16. Noise reduction policy

- Default is off.
- Conservative normalization is permitted.
- Implement a replaceable `NoiseReducer` interface.
- Compare raw normalized audio and reduced audio on real clips.
- Include low-volume and clipped speech in the comparison.
- Enable a reducer only after user-approved evidence that ASR improves and quiet speech is not damaged.

## 17. Real-data-only test policy

### 16.1 Absolute prohibitions

Do not use:

- synthetic tones;
- generated speech;
- invented audio;
- invented expected transcripts;
- invented expected translations;
- mocked model output for quality tests;
- fabricated server responses presented as real;
- fabricated pass/fail evidence.

Do not adjust an expected result merely to make a test pass.

### 16.2 Permitted real fixtures

Use:

- the supplied real meeting recording;
- clips cut from it with exact provenance and SHA-256;
- human-reviewed annotations;
- real WebSocket traffic captured from an actual server run;
- an exact replay of captured traffic for client regression;
- pure-function structures extracted from real captures.

A replay fixture is not a mock if it reproduces an immutable captured server exchange and records its provenance.

### 16.3 Tests for every source component

Every source component shall have tests appropriate to its behavior. If a required behavioral case is absent from the real recording/capture, do not synthesize it. Report the coverage gap and discuss how to obtain a real sample.

Prefer layered tests:

- real-data unit tests for pure transformations;
- real-capture contract tests;
- client integration tests with captured replay;
- server scripts using the real recording and actual models;
- end-to-end tests through the SSH tunnel;
- human-reviewed quality evaluation.

Static checks do not replace behavioral tests.

## 18. Ground-truth policy

The current recording has no trusted transcript, translation, or speaker timestamps.

Therefore:

- Build annotation tools/manifests before reporting accuracy metrics.
- Never treat Whisper or Qwen output as ground truth.
- The user/human reviewer must approve references.
- Start with a representative subset rather than requiring full 30-minute annotation immediately.
- Record reviewer, timestamp, source hash, clip interval, and annotation version.
- Do not report CER/WER/DER/translation reference metrics before ground truth exists.

Required available categories include silence, low-volume speech, clipped speech, filler, hesitation, incomplete sentence, background noise, non-speech, short acknowledgement, overlap, rapid speaker changes, Japanese, Vietnamese, and uncertain language. Mark unavailable cases honestly.

## 19. Client test execution

Claude Code shall run client tests itself when it has access to the required Windows/client environment and real fixtures.

For every client test report, include:

- exact command;
- environment summary;
- fixture/capture ID and SHA-256;
- passed/failed/skipped counts;
- duration;
- output artifact paths;
- known limitations.

If not running on Windows or without WASAPI, do not claim live capture passed. Run only the legitimately available replay/pure-client checks and report the missing real-device gate.

## 20. Server test gate

Claude Code shall not claim to execute GPU tests if it cannot access the GPU pod.

For each server phase:

1. Write real-model test scripts.
2. Validate syntax and configuration schema locally where possible.
3. Provide exact commands, working directory, environment activation, required variables, and expected artifact filenames.
4. Define metric fields, not invented target values.
5. Commit and push.
6. Print/report `SERVER_TEST_GATE`.
7. Stop and wait for the user.
8. Ask the user to return raw stdout/stderr, structured result files, transcript/translation artifacts, and `nvidia-smi` observations.
9. Analyze the real outputs.
10. Record the gate outcome in documentation.
11. Fix, recommit, repush, and repeat the gate if necessary.

Do not continue past a mandatory server gate because a script merely imports or compiles.

## 21. Git rules

This is an existing repository.

At the start and before every commit, inspect repository state, untracked files, large files, and likely secrets. Update `.gitignore` before running models so caches, weights, recordings, logs, and raw benchmark outputs are excluded. At minimum run:

```bash
git status --short --branch
git diff --check
git diff
git diff --cached
git ls-files -o --exclude-standard
find . -type f -size +50M -print
```

After every approved phase, and before every server test gate:

1. Run allowed checks/tests.
2. Update documentation and ADRs.
3. Commit a coherent change.
4. Push to the configured non-protected working branch.
5. Report the branch and commit hash.

Commit messages should identify the phase and outcome, for example:

```text
feat(phase-05): add real-audio VAD segmentation pipeline
```

### 20.1 Prohibited Git actions

Do not:

- use `git push --force` or `--force-with-lease`;
- rewrite shared history;
- rebase a shared branch without explicit user instruction;
- discard user changes;
- use destructive reset/clean/checkout commands on unknown work;
- commit secrets, model weights, recordings, large generated outputs, or caches unless explicitly approved;
- bypass failed hooks/tests;
- amend previously pushed commits merely to hide iteration;
- push directly to a protected branch unless the user explicitly instructs it.

If push fails due to authentication or branch policy, report the exact error and leave the local commit intact.

## 22. Phase report template

After every phase, report:

```markdown
## Phase N report

### Design decisions
- ADR links and selected alternatives

### Implementation
- Changed modules and behavior

### Tests executed
- Command
- Environment
- Real fixture/capture and hash
- Result
- Artifact path

### Tests not executed
- Test
- Reason
- User action required

### Quality/metrics
- Measured values only

### Known issues and risks
- Item and impact

### Git
- Branch
- Commit hash
- Push result

### Next gate
- Decision or SERVER_TEST_GATE instructions
```

## 23. Implementation phases

Follow the phases in `requirements.md`. A phase may be split into smaller approved subphases, but it may not skip its design discussion, tests, commit, push, or server gate.

For each phase, keep a checklist in the repository. Do not mark an item complete without evidence.

## 24. Acceptance thresholds

Do not invent numeric acceptance thresholds before baseline measurement. The process is:

1. Define measurement methods and schemas.
2. Run real baseline.
3. Return artifacts.
4. Discuss trade-offs and feasible goals with the user.
5. Record approved numeric thresholds in an ADR/config/test specification.
6. Enforce them in subsequent regression tests.

Qualitative phrases such as “fast” or “high accuracy” are not completion evidence.

## 25. Documentation requirements

Requirement changes require an explicit proposal containing rationale, impact, migration, affected ADRs/tests, and traceability changes. Do not silently edit or delete an approved normative requirement. Preserve the change in Git history.

Maintain:

- architecture overview;
- sequence diagrams;
- WebSocket protocol specification;
- event schemas;
- ADRs;
- environment matrix and lock files;
- client setup and packaging guide;
- GPU server setup/model-download guide;
- SSH tunnel runbook;
- test-data provenance and annotation guide;
- server test runbook;
- benchmark report;
- troubleshooting guide;
- privacy and retention notes;
- third-party package/model inventory, licenses, access conditions, and redistribution restrictions;
- immutable model revision manifest.

Commands must be copyable and identify the environment in which they run. Never include real secrets in examples.

## 26. Security rules

- Bind server endpoints to localhost for SSH tunnel use unless the user approves another secure architecture.
- Never print or commit tokens.
- Validate all external data and impose maximum sizes.
- Avoid meeting text/audio in ordinary operational logs.
- Use safe file paths and atomic writes.
- Document delete/retention behavior.
- Do not add telemetry that sends meeting data externally.

## 27. Definition of done for any task

A task is complete only when:

- its design was approved if it involved a choice;
- implementation follows approved ADRs;
- source has corresponding legitimate tests;
- tests use real data under this policy;
- client tests were run where possible;
- required server scripts and instructions exist;
- required user-run server gate evidence has been received and recorded;
- docs/config/schema are updated;
- formatter, linter, and type checker pass or failures are reported;
- no secret, model weight, recording, meeting log, cache, or large prohibited artifact is staged;
- its requirement IDs and traceability statuses are updated;
- change is committed and pushed;
- commit hash and remaining risks are reported.

## 28. Stop conditions

Stop and ask the user when:

- requirements are contradictory or ambiguous;
- a design gate is reached;
- GPU environment facts are required;
- a server test gate is reached;
- real data needed for an ML/E2E test does not exist;
- a mandatory requirement has no valid traceability mapping;
- a destructive Git/filesystem operation appears necessary;
- a license/access condition requires user acceptance;
- a secret/token is needed;
- measured behavior suggests a fixed architecture decision must change;
- a test fails and proceeding would hide or compound the failure.

Do not stop for routine implementation details already covered by an approved design.
