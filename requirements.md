# Live Japanese-Vietnamese Meeting Translator

## 1. Document purpose

This document defines the product, architecture, implementation, test, deployment, and acceptance requirements for a Windows client and GPU server application that performs live transcription and final-only translation for online meetings containing Japanese and Vietnamese speech.

This document is normative. Claude Code must not silently change an architectural decision, model, protocol contract, test policy, or acceptance rule. Any necessary change must be discussed with the user at a design gate and recorded in the repository before implementation.

## 2. Product goal

Build an application with the following capabilities:

- A Windows client captures all audio played through a selected Windows output device by using WASAPI loopback through PyAudioWPatch.
- The client continuously streams audio to a remote GPU server over WebSocket through an SSH tunnel.
- The server detects speech, utterance boundaries, language, speakers, and overlapping speech.
- The server produces low-latency partial transcription and accurate final transcription with Whisper large-v3.
- The server translates only accepted final transcriptions using Qwen3.5-9B served by vLLM.
- Japanese speech is translated to Vietnamese, and Vietnamese speech is translated to Japanese.
- The client displays a timeline containing speaker ID, language ID, transcription, and translation.
- The client writes a detailed debug event log and a final meeting history log.

## 3. Scope

### 3.1 MVP scope

The MVP shall support:

- One Windows client.
- One active meeting/session at a time.
- Any meeting application whose audio is played through the selected Windows output device.
- Mono PCM audio at 16 kHz on the wire.
- Japanese and Vietnamese only.
- Anonymous speaker labels such as `speaker-1`, `speaker-2`.
- Partial and final transcription.
- Final-only translation.
- Overlap detection and annotation.
- Online/provisional speaker assignment and retrospective speaker correction.
- Local model files on the GPU server.
- A single Linux GPU pod with one NVIDIA H100 80 GB GPU and approximately 100 GB system memory.
- Connection through an SSH tunnel, normally using localhost ports at both ends.
- Real-data tests using the supplied meeting recording.

### 3.2 Out of scope for MVP

- Translation of partial transcription.
- Named speaker identification.
- Enrollment of known speakers.
- Guaranteed transcription of every voice during overlapping speech.
- Source separation in the production critical path.
- Project glossary.
- Japanese honorific or role-specific translation policy.
- Multiple simultaneous meetings.
- Cloud APIs for ASR or translation.
- Word-level code-switching quality guarantees.
- Automatic ground-truth creation by an AI model.

## 4. Fixed technology decisions

### 4.1 Client

- Language: Python.
- UI: PySide6.
- Audio capture: PyAudioWPatch WASAPI loopback.
- Network transport: WebSocket. The deployed link shall be accessed through an SSH tunnel.
- Canonical logs: UTF-8 JSON Lines.

### 4.2 Server models and components

- Streaming VAD and endpointing: Silero VAD.
- ASR model: `openai/whisper-large-v3`, downloaded from Hugging Face and loaded locally.
- Production ASR backend: faster-whisper/CTranslate2.
- Reference ASR backend: Hugging Face Transformers, used only for controlled quality comparison on a real-data subset.
- Acoustic language identification: `speechbrain/lang-id-voxlingua107-ecapa`.
- Speaker embedding: `speechbrain/spkrec-ecapa-voxceleb`.
- Overlap detection and retrospective diarization refinement: pyannote in an isolated worker/environment.
- Experimental source separation interface: SpeechBrain SepFormer. Default implementation is disabled. `speechbrain/sepformer-wsj02mix` may be benchmarked only behind a feature flag and shall not be in the MVP production critical path.
- Translation: Qwen3.5-9B served by vLLM.
- Translation mode: final-only.
- Qwen thinking/reasoning output: disabled.

### 4.3 Noise handling

- Noise suppression shall be disabled by default.
- Conservative signal normalization may be enabled.
- A `NoiseReducer` interface shall be provided.
- Noise reduction may be enabled only after an A/B benchmark on real meeting audio demonstrates an ASR improvement without damaging low-volume or clipped speech.

## 5. Quality principles

1. The system shall prefer no output over invented output when speech is absent or unusable.
2. A partial transcription is provisional and replaceable.
3. A final transcription is final only after quality and hallucination gates.
4. Translation shall never make an unreliable ASR fragment appear authoritative.
5. Language decisions shall be explicit and traceable.
6. Speaker IDs may be revised and must be represented as versioned state.
7. Every model decision and every rejected segment shall be observable in the debug log.
8. Thresholds shall be configuration values established by real-data measurement, not unexplained constants.

## 6. High-level architecture

```text
Windows client
  WASAPI loopback capture
  -> channel conversion and resampling
  -> bounded ring buffer
  -> binary WebSocket audio stream
  -> PySide6 timeline UI
  -> debug and history writers

SSH tunnel
  localhost:client_port -> localhost:server_port

Linux GPU server
  WebSocket gateway
  -> session orchestrator
  -> jitter/gap validation
  -> audio preprocessing
  -> Silero VAD and utterance state machine
  -> SpeechBrain language ID
  -> faster-whisper rolling partial decode
  -> stable-prefix calculation
  -> final utterance decode
  -> hallucination and quality acceptance gate
  -> SpeechBrain speaker embedding and online clustering
  -> pyannote overlap detection and retrospective refinement
  -> Qwen final-only translation through vLLM
  -> versioned WebSocket response events
```

## 7. Process and dependency isolation

The server shall not load all ML frameworks into one Python process. At minimum, the design shall isolate:

- `gateway-service`: WebSocket, validation, and client-facing protocol.
- `session-orchestrator`: per-session state and work coordination.
- `asr-worker`: faster-whisper/CTranslate2.
- `speechbrain-worker`: language ID and speaker embeddings.
- `diarization-worker`: pyannote overlap detection and retrospective refinement.
- `translation-service`: vLLM and Qwen3.5-9B.

Deployment options shall be selected only after inspecting the GPU pod:

1. Docker Compose if Docker is available and allowed.
2. Multiple pinned Python virtual environments plus supervised subprocesses otherwise.

The environment inspection is a mandatory design gate. Claude Code shall provide exact commands for the user to run and shall wait for the returned output before locking CUDA, PyTorch, CTranslate2, SpeechBrain, pyannote, vLLM, and driver versions.

## 8. Client requirements

### 8.1 Audio capture

The client shall:

- Enumerate WASAPI output devices and loopback-capable devices.
- Let the user select the capture device.
- Capture output audio using PyAudioWPatch loopback.
- Validate sample format, channel count, sample rate, and device availability.
- Convert captured audio to mono PCM signed 16-bit little-endian at 16 kHz.
- Use a streaming-quality resampler with persistent state across chunks.
- Avoid repeated WAV headers.
- Add monotonically increasing sequence numbers and monotonic capture timestamps.
- Detect callback starvation, device removal, overrun, underrun, and resampler failure.
- Have a bounded ring buffer and an explicit overflow policy.
- Never block the audio callback on network I/O, UI rendering, log writing, or disk I/O.

### 8.2 Capture lifecycle

Supported states:

```text
IDLE -> CONNECTING -> READY -> CAPTURING -> STOPPING -> COMPLETED
                         |          |
                         v          v
                       ERROR <-> RECONNECTING
```

The UI shall make the current state visible. State transitions shall be logged with timestamps and reason codes.

### 8.3 Client UI

The PySide6 UI shall include:

- Device selector.
- Server/tunnel endpoint configuration.
- Connect/disconnect control.
- Start/stop meeting control.
- Current capture and server status.
- Timeline ordered by source audio time.
- For every segment:
  - speaker ID;
  - language ID;
  - source start/end time;
  - transcription row;
  - translation row;
  - partial/final state;
  - overlap indicator;
  - low-confidence/warning indicator.
- Partial transcription shall be visually distinguishable from final transcription.
- Translation shall show a pending state after final ASR and before Qwen returns.
- Translation error shall not remove or alter the final transcript.
- A retry action may retry failed translation without re-running ASR.

The UI shall project records by `segment_id` using the revision model in Section 25. Duplicate and stale events shall be ignored and logged. A speaker-only revision shall not replace newer text, language, or translation state.

### 8.4 Client reconnect

The client shall:

- Send heartbeat/ping messages.
- Detect a dead connection.
- Reconnect with bounded exponential backoff.
- Preserve a bounded amount of unsent audio.
- Attempt session resume within a configurable retention window.
- Clearly report unrecoverable audio gaps.
- Never silently reorder, duplicate, or discard audio.

The exact resume contract shall be discussed and approved at the protocol design gate.

## 9. WebSocket protocol

### 9.1 Versioning

Every control event shall include:

```json
{
  "protocol_version": "1.0",
  "event_type": "session.start",
  "session_id": "uuid",
  "event_id": "uuid",
  "sent_at_utc": "RFC3339 timestamp"
}
```

Unknown major versions shall be rejected. Unknown optional fields in the same major version shall be ignored safely.

### 9.2 Audio wire format

Baseline wire audio:

```yaml
encoding: pcm_s16le
sample_rate_hz: 16000
channels: 1
frame_duration_ms: 20_or_40_to_be_benchmarked
byte_order: little_endian
```

Audio shall use binary WebSocket frames. A compact header shall carry, at minimum:

- protocol version;
- stream identifier;
- sequence number;
- monotonic capture timestamp;
- sample count;
- flags.

The binary header schema, byte layout, integer sizes, and endianness shall be written as a protocol specification and covered by real-capture serialization tests before server implementation.

### 9.3 Required event types

```text
session.start
session.started
session.resume
session.resumed
session.stop
session.stopped
audio.ack
audio.gap
vad.speech_started
vad.speech_ended
transcript.partial
transcript.final
transcript.revised
translation.started
translation.final
translation.failed
speaker.updated
language.updated
pipeline.warning
pipeline.error
metrics.snapshot
```

### 9.4 Backpressure

The protocol shall define:

- maximum accepted queue depth;
- acknowledgement cadence;
- client buffer limit;
- server overload response;
- reconnect retention duration;
- gap reporting;
- termination behavior.

No component may create an unbounded queue.

## 10. Audio preprocessing

The preprocessing order shall be:

```text
wire validation
-> PCM conversion
-> channel validation/downmix if required
-> sample-rate validation/resampling if required
-> DC removal
-> conservative level normalization
-> optional feature-flagged noise reduction
-> VAD
```

The server shall calculate and log non-content quality features such as duration, peak, RMS, clipping ratio, zero ratio, missing frame count, and speech ratio. These features shall be used as quality evidence, not as standalone truth.

## 11. VAD and utterance segmentation

Silero VAD shall operate as a streaming state machine:

```text
IDLE
-> POSSIBLE_SPEECH
-> SPEAKING
-> POSSIBLE_END
-> FINALIZING
-> IDLE
```

Required configurable parameters include:

```yaml
vad:
  threshold: benchmark_required
  min_speech_ms: benchmark_required
  min_silence_ms: benchmark_required
  speech_pad_ms: benchmark_required
  pre_roll_ms: benchmark_required
  post_roll_ms: benchmark_required
  max_utterance_ms: benchmark_required
```

Requirements:

- Hysteresis shall prevent rapid speech/non-speech oscillation.
- Pre-roll shall preserve clipped word beginnings.
- Post-roll shall preserve quiet word endings.
- Natural short pauses shall not automatically split a sentence.
- Maximum utterance duration shall force a controlled cut with overlap/context preservation.
- A network gap shall not be mistaken for a natural speech endpoint.
- Too-short or too-weak speech shall be rejected or marked low confidence rather than expanded into a plausible sentence.

## 12. Language identification and routing

### 12.1 Supported languages

```yaml
allowed_languages:
  - ja
  - vi
translation_direction:
  ja: vi
  vi: ja
```

### 12.2 Language decision sources

The language resolver shall combine:

- SpeechBrain VoxLingua107 acoustic language probabilities.
- Whisper language evidence.
- Recent accepted language associated with the provisional speaker.
- Recent meeting language state.
- Utterance duration and confidence.

SpeechBrain LID is the primary acoustic signal. Whisper is a secondary validation signal.

### 12.3 Language state

Supported language states:

```text
unknown
provisional_ja
provisional_vi
confirmed_ja
confirmed_vi
mixed
uncertain
```

The final protocol shall separate `language_id` from `language_status` and include a `language_revision`.

Requirements:

- Do not run LID independently on tiny transport frames.
- Do not trust very short utterances such as acknowledgements without context.
- Restrict normal decisions to Japanese and Vietnamese.
- If signals disagree, retry or defer the decision according to configured policy.
- Once resolved, explicitly pass `ja` or `vi` to Whisper final decoding.
- Every Qwen request shall explicitly specify source and target language.
- For an unresolved language, do not invoke translation.
- `mixed` is schema-supported but high-quality within-sentence code switching is out of scope for MVP.

## 13. Whisper streaming ASR

### 13.1 General design

Whisper is not treated as a native streaming engine. The application shall implement stateful rolling decode and endpoint-triggered final decode around Whisper large-v3.

### 13.2 Partial decode

While an utterance is active:

- Maintain a rolling audio buffer.
- Decode at a configurable interval.
- Decode a configurable recent window plus necessary left context.
- Prefer the best available provisional language, but do not irrevocably lock low-confidence LID.
- Use a low-latency decode profile.
- Compare consecutive hypotheses.
- Calculate a stable prefix and unstable suffix.
- Publish revisions only when useful content or state has changed.
- Never append a partial as a new independent final line.

Example payload:

```json
{
  "event_type": "transcript.partial",
  "session_id": "session-uuid",
  "segment_id": "seg-000123",
  "revision": 4,
  "status": "partial",
  "stable_text": "明日の会議は",
  "unstable_text": "午前十時",
  "start_ms": 12500,
  "end_ms": 16820,
  "speaker_id": "speaker-2",
  "speaker_status": "provisional",
  "language_id": "ja",
  "language_status": "provisional"
}
```

Stable-prefix behavior shall be benchmarked using metrics such as rollback count, revision count, edit distance between consecutive partials, and time to first useful text.

### 13.3 Final decode

At an accepted endpoint:

- Assemble the complete utterance including pre-roll and post-roll.
- Decode using an accuracy-oriented profile.
- Explicitly pass resolved input language when available.
- Produce segment timestamps and quality metadata.
- Reconcile the result with partial state.
- Apply hallucination and acceptance gates.
- Publish the final only after acceptance processing.

### 13.4 ASR context

Whisper context may contain only:

- a bounded amount of recent accepted final transcription;
- approved names/terms added later by product requirements;
- no translation;
- no rejected segment;
- no low-confidence segment by default;
- no partial segment.

Reset or shorten context after:

- a long silence;
- a strong language switch;
- detected repetition/hallucination;
- a session boundary;
- context budget exhaustion.

### 13.5 Production and reference backend

- faster-whisper is the production backend.
- Transformers shall be run on a manageable, human-reviewed subset as a reference comparison.
- A design gate shall reopen backend choice only if measured quality differs materially.
- Model repository, revision/commit, tokenizer, backend version, compute type, and decode parameters shall be recorded in every benchmark artifact.

## 14. Whisper hallucination control

Hallucination prevention is a first-class design requirement and mandatory test category.

### 14.1 Defensive layers

```text
1. Frame and audio validity gate
2. VAD gate
3. Utterance quality gate
4. Constrained language-aware decode
5. Decode quality signal collection
6. Repetition and implausibility detection
7. Stable partial policy
8. Final retry/accept/reject policy
9. Translation firewall
```

### 14.2 Audio validity gate

Reject or flag audio with evidence such as:

- missing sequence ranges;
- all-zero or near-zero content;
- excessive clipping;
- invalid duration;
- invalid numeric values after conversion;
- resampling failure;
- extremely low RMS;
- too little detected speech;
- a truncated utterance caused by disconnection;
- an unsafe maximum-duration cut.

### 14.3 Decode evidence

For each decode, collect when available:

```json
{
  "avg_logprob": -0.42,
  "no_speech_prob": 0.08,
  "compression_ratio": 1.16,
  "speech_ratio": 0.84,
  "temperature_used": 0.0,
  "language_id": "ja"
}
```

Also calculate:

- repeated token/character/n-gram indicators;
- text length versus speech duration;
- similarity to prior segment when little new audio exists;
- language disagreement;
- timestamp plausibility;
- partial instability;
- audio energy and clipping evidence.

No single threshold is sufficient. Acceptance shall use combined evidence. Thresholds must be tuned from the real recording and documented.

### 14.4 Required retry policy

When a final candidate fails the normal quality gate:

1. Retry with shorter or reset context.
2. Disable previous-text conditioning for the retry.
3. Force the best supported language when the LID decision is reliable.
4. Apply benchmark-approved decode fallback settings.
5. If still unreliable, return `low_confidence` or `rejected` instead of inventing certainty.

Only accepted final transcription may enter ASR context or translation.

### 14.5 Filler, hesitation, and incomplete speech

The system shall not normalize fillers, hesitation, grammatical mistakes, or unfinished speech into a well-formed sentence at the ASR stage. Valid outcomes include a literal short transcription, low confidence, or no text. Any later text normalization must be a separate, explicit feature and is out of scope for MVP.

### 14.6 Translation firewall

Qwen shall receive only an accepted final transcription with resolved `source_language` and `target_language`. It shall never receive:

- partial transcription;
- rejected segments;
- raw unchecked hypotheses;
- silence/non-speech;
- unresolved-language segments;
- low-confidence fragments by default.

## 15. Speaker processing

### 15.1 Required output

The MVP outputs anonymous IDs only:

```text
speaker-1
speaker-2
speaker-3
```

### 15.2 Online speaker assignment

SpeechBrain ECAPA embeddings shall feed an online clustering component. The clustering algorithm and thresholds shall be selected at a design gate after real-data experiments.

Do not create or update a speaker profile from segments that are:

- too short;
- silence or weak filler;
- heavily overlapped;
- extremely noisy;
- rejected by audio validity checks;
- seriously damaged by an experimental separator.

Speaker labels on active/partial segments are provisional. The protocol shall support `speaker.updated` events and retrospective merge/relabel operations.

### 15.3 Pyannote role

Pyannote shall be isolated and used for:

- overlap detection;
- speaker-change/segmentation evidence;
- retrospective diarization refinement;
- controlled comparison with online clustering.

It shall not replace Silero as the live endpointing component. It shall not silently overwrite timeline labels without a versioned correction event.

### 15.4 Unknown speaker count

The system shall not assume a fixed speaker count. If a pyannote API supports bounds, the default shall remain automatic until real data justify a configurable upper bound.

## 16. Overlapping speech

### 16.1 MVP behavior

For detected overlap:

- mark affected segments with `overlap=true`;
- preserve overlap intervals in the debug log;
- run the normal ASR path on mixed audio;
- lower confidence or attach a warning when justified;
- do not claim both speakers were fully recovered;
- avoid using overlapped audio to update clean speaker profiles.

### 16.2 Source separation interface

Create an interface such as `SourceSeparator`, with the production default set to disabled. SepFormer experiments shall:

- run only on detected overlap regions;
- run outside the default critical path;
- preserve the original mixed-audio result;
- compare separated and mixed results using human-reviewed real data;
- record latency and artifacts;
- never automatically prefer separated output without an approved quality gate.

The candidate WSJ0-2Mix model is trained for a different language/domain and sample rate, so no quality assumption is allowed.

## 17. Translation with vLLM and Qwen3.5-9B

### 17.1 Invocation

Translation is invoked only after accepted final ASR.

```text
accepted final transcription
-> resolved source language
-> target language = opposite supported language
-> Qwen request
-> final translation
```

### 17.2 Required prompt contract

Every request shall explicitly state:

- source language and code;
- target language and code;
- current final source text;
- a small bounded amount of accepted final conversation context if enabled;
- translate only, without explanation;
- preserve names, numbers, dates, URLs, code, and identifiers;
- do not add missing information;
- do not correct the source by inventing content;
- produce output conforming to the defined response schema.

The application shall not ask Qwen to identify the language.

### 17.3 Baseline runtime configuration

Initial settings to benchmark:

```yaml
translation:
  model: Qwen3.5-9B
  dtype: bfloat16
  tensor_parallel_size: 1
  thinking: false
  temperature: 0.0
  top_p: 1.0
  max_model_len: 8192_or_16384_to_be_decided
  gpu_memory_utilization: benchmark_required
  max_output_tokens: bounded_and_benchmark_required
```

Do not allocate the model's maximum possible context merely because it is supported. Use the smallest context sufficient for live translation.

### 17.4 Failure handling

- Translation has states `pending`, `completed`, and `failed`.
- A translation timeout or failure shall not invalidate final ASR.
- Translation may be retried idempotently by segment ID.
- A stale translation response for an older transcription revision shall be discarded.
- Qwen output shall be validated before display and persistence.

## 18. Resource management

The server shall measure and report:

- GPU memory idle and peak by worker;
- system RAM;
- ASR real-time factor;
- ASR queue depth;
- translation queue depth;
- translation latency;
- event-loop lag;
- dropped/rejected audio frames;
- utterance processing latency;
- worker restart count.

Models shall be warmed before an active meeting when possible. Queues shall be bounded. GPU out-of-memory shall produce explicit failure and recovery behavior, not process-wide silent corruption.

## 19. Persistence

### 19.1 Debug log

Filename:

```text
meeting_<session-id>_debug.jsonl
```

It shall include all relevant events and decisions:

- session lifecycle;
- client and server status;
- audio gaps and acknowledgements;
- VAD events;
- partial revisions;
- final and corrected transcription;
- speaker revisions;
- language decisions and evidence;
- overlap intervals;
- ASR quality evidence;
- translation lifecycle;
- errors, warnings, and latency metadata;
- model/config/version identifiers.

Do not embed raw PCM in JSONL. If recording is enabled, reference a separate audio file by path and SHA-256.

### 19.2 Meeting history

Filename:

```text
meeting_<session-id>_history.jsonl
```

It shall contain only the latest accepted final state per segment:

- final transcription;
- final translation or translation failure state;
- final speaker ID;
- final language ID;
- source timestamps;
- overlap marker;
- low-confidence marker if user policy allows such segments in history.

The writer shall be crash-tolerant and shall not leave malformed JSON lines. A deterministic command shall rebuild the history file from the debug event log.

## 20. Observability

All services shall use structured logs. Every record shall include relevant identifiers:

- session ID;
- stream ID;
- segment ID;
- event ID;
- revision;
- service and worker;
- monotonic processing timestamps;
- UTC correlation time.

Metrics should be exportable in Prometheus-compatible form where practical. Sensitive audio/text shall not be included in normal operational logs unless debug content logging is explicitly enabled.

## 21. Security and privacy

- Bind server ports to localhost when accessed only through SSH tunnel.
- Do not expose an unauthenticated public WebSocket endpoint.
- Never commit model access tokens, SSH secrets, or environment credentials.
- Use environment variables or local secret files excluded by Git.
- Define retention and deletion behavior for meeting logs and optional audio recordings.
- Sanitize filenames and session metadata.
- Put maximum sizes on frames, events, strings, queues, and sessions.
- Validate all client and worker messages.
- Treat transcription/translation as confidential meeting data.

## 22. Testing policy

### 22.1 Core rule

All behavioral and model-quality tests shall use real data. The supplied meeting recording is the canonical input. No sine waves, text-to-speech audio, invented transcript, invented translation, mocked model output, or fabricated pass result is allowed.

### 22.2 Allowed fixtures

The following are allowed:

- the original real meeting recording;
- clips extracted from that recording with provenance metadata;
- real WebSocket events captured from an actual server run;
- a replay server that replays those events exactly;
- human-reviewed ground-truth annotations;
- pure-function input extracted from real captures.

Every derived audio fixture shall record:

```yaml
source_file_sha256: required
start_ms: required
end_ms: required
clip_sha256: required
labels: human_reviewed
reviewed_by: required
reviewed_at: required
```

### 22.3 Forbidden practices

- Do not mock ASR, LID, speaker, diarization, or translation output in a quality test.
- Do not ask an LLM to invent expected output.
- Do not modify expected output merely to match current implementation.
- Do not claim server tests passed if Claude Code did not receive the real run artifacts from the user.
- Do not silently skip a failing test.
- Do not use synthetic audio to trigger edge cases.

### 22.4 Test ownership

Claude Code shall execute client tests locally when the environment permits.

Claude Code cannot directly execute GPU server tests. For server tests it shall:

1. Implement the production code and real-data test script.
2. Syntax-check and statically validate scripts locally where possible.
3. Write exact environment and execution instructions.
4. Define the artifact schema and metric names, but not fabricate expected values.
5. Commit and push.
6. Stop at `SERVER_TEST_GATE`.
7. Ask the user to execute commands on the H100 pod.
8. Wait for logs, JSON metrics, output transcripts/translations, and GPU observations.
9. Analyze returned artifacts.
10. Continue only after the gate result is documented.

### 22.5 Human ground truth

The recording currently has no human transcript, translation, or speaker timestamp ground truth. Therefore:

- The project shall include an annotation workflow and manifest format.
- Claude Code shall not calculate CER, WER, DER, BLEU, chrF, or similar reference metrics until human references exist.
- A representative subset may be annotated before the full recording.
- The subset shall cover Japanese, Vietnamese, multiple speakers, silence, low volume, clipped speech, hesitation, incomplete sentences, overlap, rapid speaker changes, and noise.

### 22.6 Mandatory hallucination cases

Create human-reviewed real-recording cases for available occurrences of:

```text
silence
low_volume_speech
clipped_beginning
clipped_end
filler
hesitation
incomplete_sentence
background_noise
laughter
non_speech_sound
short_acknowledgement
overlap
rapid_speaker_change
japanese
vietnamese
uncertain_language
```

If a category does not occur in the recording, report it as unavailable. Do not synthesize it.

## 23. Metrics

### 23.1 Client/audio

- frame count and sequence gaps;
- ring-buffer overflow;
- capture callback delays;
- capture and resample duration drift;
- reconnect recovery and lost duration;
- CPU and memory;
- log writer integrity.

### 23.2 ASR

After human reference exists:

- Japanese CER and appropriately tokenized WER;
- Vietnamese WER and CER;
- hallucination rate on silence/non-speech;
- omission and repetition indicators;
- time to first useful partial;
- finalization latency;
- real-time factor;
- partial revision count;
- committed-prefix rollback count;
- edit distance/flicker between revisions;
- timestamp error.

### 23.3 Language ID

- accuracy for Japanese/Vietnamese utterances;
- uncertain rate;
- revision rate;
- error rate on short acknowledgements;
- disagreement rate between SpeechBrain and Whisper evidence;
- time to confirmed language.

### 23.4 Speaker and overlap

When annotation exists:

- DER;
- speaker confusion;
- missed speech;
- false alarm;
- speaker revision/merge count;
- overlap detection precision/recall;
- impact of overlap on ASR.

Until annotation exists, only operational/provisional diagnostics may be reported, not accuracy claims.

### 23.5 Translation

After human references/review exist:

- adequacy and fluency human review;
- omission rate;
- addition/hallucination rate;
- number/date/entity preservation;
- translation direction accuracy;
- latency;
- COMET/BLEU/chrF only as secondary metrics with real references.

### 23.6 End-to-end latency

Measure monotonic timestamps for:

```text
capture -> server receive
capture -> first partial displayed
endpoint -> final transcript displayed
final transcript -> final translation displayed
capture -> final translation displayed
```

Acceptance thresholds shall be set only after the real-data baseline is measured, reviewed with the user, and committed.

## 24. Configuration

All tunable values shall be externalized into validated configuration, including:

- capture device and audio format;
- frame duration;
- buffer limits;
- reconnect policy;
- VAD thresholds and timing;
- partial decode cadence/window;
- stable-prefix policy;
- Whisper decoding parameters;
- language confidence/margin and fallback policy;
- speaker embedding minimum duration;
- clustering thresholds;
- overlap thresholds;
- hallucination quality thresholds;
- Qwen/vLLM runtime parameters;
- translation timeout/retry;
- logging and retention.

Each debug log and benchmark artifact shall include a configuration hash.


## 25. Mandatory cross-pipeline design contracts

This section resolves the cross-component ambiguities that must not be left to implementation guesswork. The values of thresholds and durations remain benchmark-driven, but the state, authority, and dependency rules below are fixed unless changed through an approved ADR.

### 25.1 Canonical media timeline

The canonical position of all media-derived events shall be the integer audio sample offset from the start of the session stream.

```yaml
canonical_sample_rate_hz: 16000
session_start_sample: 0
time_conversion:
  milliseconds: floor(sample_offset * 1000 / 16000)
```

Requirements:

- VAD, utterances, Whisper timestamps, speaker turns, overlap intervals, UI ordering, fixtures, and persisted history shall map to this sample timeline.
- Floating-point seconds shall not be used as identity keys or ordering authorities.
- Client monotonic capture time, server monotonic processing time, and UTC correlation time are observability fields and shall not replace media sample offsets.
- Sequence gaps shall preserve their duration on the media timeline. The timeline shall not be compressed to hide missing audio.
- A reconnect shall continue the same sample timeline only when the server accepts session resume. A new non-resumed stream shall receive a new `stream_id` and an explicit discontinuity event.
- The protocol ADR shall define integer ranges, overflow handling, and conversion rounding.

### 25.2 Utterance, segment, and turn authority

The following identifiers describe different objects and shall not be conflated:

```text
stream_id
  continuous audio stream within a session

utterance_id
  speech-processing unit opened and closed by the session orchestrator,
  primarily from Silero VAD and explicit gap/stop policies

segment_id
  stable text/timeline unit displayed, revised, translated, and persisted

speaker_turn_id
  diarization interval that may overlap or intersect one or more segments
```

Authority rules:

- The session orchestrator is the authority that creates `utterance_id` and `segment_id`.
- Silero VAD is the live endpointing authority for utterances.
- Whisper subsegments and token timestamps are evidence used to divide or align text; their local IDs shall never become public `segment_id` values.
- Pyannote speaker turns shall not directly open or close ASR utterances in MVP. They may refine segment boundaries or metadata through versioned retrospective events.
- One utterance may yield zero, one, or multiple text segments.
- A text segment belongs to exactly one utterance in MVP. Cross-utterance semantic merging is out of scope unless approved later.
- Qwen translates one accepted text segment at a time, not a raw utterance and not a raw Whisper subsegment.
- Revisions must retain the same `segment_id`. A new `segment_id` is created only when a segment split cannot be represented by a normal revision. Split and merge operations require explicit lineage fields.

Required lineage example:

```json
{
  "utterance_id": "utt-000012",
  "segment_id": "seg-000034",
  "parent_segment_ids": [],
  "operation": "create",
  "start_sample": 1248000,
  "end_sample": 1320000
}
```

The architecture ADR shall define deterministic segment split and merge events, ordering, and client projection behavior before ASR integration.

### 25.3 Language identification timing and partial-ASR policy

Language identification shall not block the first partial indefinitely. The orchestrator shall use a staged language policy:

```text
speech starts
-> collect a configurable minimum amount of voiced audio
-> before reliable LID: partial ASR uses the approved speaker/session hint or auto mode
-> run SpeechBrain LID when minimum evidence exists
-> publish provisional language when threshold and margin policy allow
-> subsequent partial decode may force provisional language
-> re-run LID at configured evidence milestones or after disagreement
-> resolve final language at endpoint
-> final Whisper decode forces confirmed language when reliable
```

Required decisions at the Language Resolver ADR:

- minimum voiced duration for first LID attempt;
- LID re-evaluation cadence or evidence milestones;
- precedence between speaker hint, session hint, SpeechBrain, and Whisper evidence;
- confidence and margin definitions;
- behavior when Japanese and Vietnamese scores are both weak;
- behavior when SpeechBrain and Whisper disagree;
- whether a provisional-language change triggers immediate full-buffer partial re-decode or waits for the next cadence;
- timeout/fallback for unresolved final language;
- handling of very short acknowledgements and filler;
- conditions for `mixed` and `uncertain`.

Partial events emitted before a language decision shall identify the basis used:

```json
{
  "language_id": null,
  "language_status": "unknown",
  "asr_language_mode": "session_hint",
  "language_revision": 0
}
```

A change in provisional language may revise partial content. Only the endpoint final decode with the resolved language is eligible for ASR acceptance and translation.

### 25.4 Segment lifecycle and sealing

The segment lifecycle shall be explicit:

```text
CREATED
-> PARTIAL
-> ASR_FINAL_CANDIDATE
-> ACCEPTED | LOW_CONFIDENCE | REJECTED
-> TRANSLATION_PENDING | TRANSLATION_NOT_APPLICABLE
-> TRANSLATED | TRANSLATION_FAILED
-> SEALED
```

Rules:

- `ASR_FINAL_CANDIDATE` is internal and shall not be treated as accepted text.
- `ACCEPTED` means the current content revision passed the quality gate and is eligible for context and translation.
- `LOW_CONFIDENCE` is not eligible for translation by default.
- `REJECTED` shall retain diagnostics but no authoritative text.
- An accepted content revision may be replaced by `transcript.revised` before sealing.
- Speaker metadata may be revised independently before sealing.
- Language changes after acceptance invalidate translation and require a new accepted final decode or explicit revalidation.
- A segment shall be sealed after the configurable retrospective refinement window or during graceful meeting finalization.
- Content, language, and speaker metadata shall not change after sealing, except through an explicit administrative correction workflow that is out of scope for MVP.
- A translation failure does not prevent sealing after the configured drain/retry policy; the sealed segment records the failure state.

### 25.5 Independent revision and dependency model

A single global `revision` is insufficient. Every current segment projection shall carry:

```json
{
  "segment_id": "seg-000123",
  "content_revision": 5,
  "language_revision": 2,
  "speaker_revision": 3,
  "translation_revision": 1,
  "translated_from_content_revision": 5,
  "translated_from_language_revision": 2,
  "sealed": false
}
```

Dependency rules:

- Transcription content change increments `content_revision`, invalidates any translation based on an earlier content revision, and requires retranslation if the new content is accepted.
- Source-language or target-language change increments `language_revision`, invalidates translation, and requires a final ASR revalidation or re-decode according to the Language Resolver ADR.
- Speaker-only change increments `speaker_revision` and does not require retranslation.
- Translation completion increments `translation_revision` and must echo both source revisions.
- Stale asynchronous responses shall be persisted as diagnostic events but shall not update UI/history current state.
- Replaying the same event shall be idempotent by `event_id`.
- Events with equal revision but different payload shall be treated as an integrity conflict, not last-write-wins.
- The debug log is the event source of truth. UI and compacted history are deterministic projections.

The protocol specification shall include event preconditions and projection pseudocode for every revision-bearing event.

### 25.6 Multi-speaker and overlap representation

A speech utterance may contain sequential speaker changes or overlapping speakers. The schema shall not assume that every segment has exactly one certain speaker.

Required representation:

```json
{
  "primary_speaker_id": "speaker-1",
  "speaker_status": "provisional",
  "speaker_candidates": [
    {"speaker_id": "speaker-1", "confidence": 0.61},
    {"speaker_id": "speaker-2", "confidence": 0.39}
  ],
  "overlap": true,
  "overlap_intervals": [
    {"start_sample": 1280000, "end_sample": 1296000}
  ]
}
```

Rules:

- `primary_speaker_id` may be null or `speaker-unknown` when evidence is insufficient.
- `speaker-multiple` may be used only as a presentation label, never as an embedding cluster.
- Where timestamps support it, the orchestrator may split one utterance into multiple text segments aligned to speaker changes, while preserving utterance lineage.
- During true overlap, MVP may assign a primary speaker but shall retain overlap and candidate evidence and shall not claim complete attribution.
- Speaker IDs shall never be inferred from pyannote label order.

### 25.7 Canonical speaker IDs and pyannote reconciliation

Anonymous speaker IDs are session-scoped, monotonically allocated, and never reused within a session.

Required merge event:

```json
{
  "event_type": "speaker.updated",
  "operation": "merge",
  "from_speaker_ids": ["speaker-3"],
  "to_speaker_id": "speaker-1",
  "speaker_revision": 8
}
```

Rules:

- Merged IDs become aliases of the canonical target and remain in the debug history.
- Client and history projection shall rewrite affected current segment views to the canonical ID without modifying content or translation revisions.
- Split operations require new canonical IDs, explicit affected intervals/segments, and lineage.
- Pyannote local labels such as `SPEAKER_00` are worker-local and shall be mapped to canonical IDs using temporal overlap, embedding similarity, duration, and confidence.
- Mapping shall never depend on local label names or order.

The Pyannote Scheduling ADR shall decide:

- rolling-window length and overlap;
- cadence and priority;
- maximum retrospective window;
- mapping confidence policy;
- reconciliation between SpeechBrain online clusters and pyannote turns;
- split/merge thresholds and minimum evidence;
- behavior when mapping is ambiguous.

### 25.8 Audio-gap handling

Every missing sequence range shall create an `audio.gap` event with expected/received sequence, missing sample estimate, and media interval.

The gap policy shall be configurable by duration class:

- **Small gap:** preserve the interval, optionally insert marked silence for model continuity, and attach gap evidence to intersecting utterances.
- **Medium gap:** close the utterance as `truncated_by_gap`, reset VAD appropriately, and require a stricter ASR acceptance check.
- **Large gap:** invalidate session resume continuity, reset VAD and ASR context, and require an explicit stream discontinuity or new stream.

No implementation may decode across an unreported gap. A segment intersecting a gap shall not be translated unless its accepted final passes the gap-aware quality policy.

### 25.9 Graceful stop, drain, and seal

When the user stops a meeting:

```text
client sends session.stop with final sequence
-> server rejects frames after final sequence
-> close or reject incomplete gap ranges
-> flush the active VAD utterance
-> final-decode if minimum speech requirements are met
-> complete/retry/cancel translation within a bounded drain timeout
-> run bounded pending speaker/pyannote refinement
-> seal all remaining segments
-> emit session.summary
-> emit session.stopped
-> client closes persistence projection atomically
```

The Session Lifecycle ADR shall define:

- stop acknowledgement timeout;
- ASR flush timeout;
- translation drain timeout;
- refinement timeout;
- worker cancellation behavior;
- client behavior if `session.stopped` is not received;
- exact conditions for `completed`, `completed_with_warnings`, and `failed`;
- recovery/compaction command after abrupt termination.

### 25.10 Server restart and resume limitation for MVP

MVP server session state is memory-resident. Network reconnect/resume may succeed only while the same server session process remains alive and retains the session.

- Server restart makes the active meeting session unrecoverable.
- The client shall emit and persist `session_unrecoverable` and require a new session.
- The client shall not resend the entire meeting to a restarted server as if live continuity were preserved.
- Durable cross-restart session orchestration is out of scope for MVP.
- Debug/history recovery from already received client events remains required.

### 25.11 Single-H100 scheduling and priority

Process isolation shall be paired with application-level GPU admission and priority control. Initial priority policy:

```text
1. Final ASR and required final-language resolution
2. Active partial ASR
3. Final-only translation
4. Speaker embedding needed for active timeline
5. Pyannote retrospective refinement
6. Offline/reference benchmark
7. Experimental source separation
```

The approved scheduler may adjust priorities after real benchmarks, but shall preserve these principles:

- Final ASR shall not be starved by translation or retrospective work.
- Partial jobs may be coalesced or skipped when backlog grows; final jobs shall not be dropped.
- Translation may wait behind active final ASR.
- Pyannote shall run at bounded cadence and may be deferred.
- Silero shall run on CPU.
- SpeechBrain LID and speaker embedding shall be benchmarked on CPU and GPU before placement is fixed.
- vLLM GPU memory reservation shall account for CTranslate2 and other CUDA contexts.
- No benchmark/reference/separation workload shall run concurrently with a production meeting unless a dedicated concurrency test is being performed.
- The system shall expose queue depth, admission wait, execution time, and memory use by workload class.

A GPU Resource ADR and user-run server gate are mandatory before loading all models concurrently.

### 25.12 Degraded-mode capability matrix

The system shall continue with explicit degraded capabilities where safe:

```yaml
component_failure_behavior:
  final_asr: session_fails
  partial_asr: continue_with_final_only_and_warning
  speechbrain_lid: use_approved_whisper_fallback_or_mark_unresolved
  speaker_embedding: use_speaker_unknown
  pyannote: mark_overlap_and_refinement_unavailable
  translation: preserve_transcript_and_mark_translation_failed
  debug_writer: stop_session_if_event_source_cannot_be_preserved
  history_projection: rebuild_from_debug_log_after_recovery
```

Every degraded mode shall emit start/end warning events, update session capability state, and be visible in UI. No component may silently substitute lower-quality behavior.

### 25.13 Translation context and output validation

MVP shall support translation context as a configurable A/B-tested feature, disabled by default until validated. When enabled:

- Include only a bounded number of recent accepted, non-rejected final segments.
- Place context in fields clearly separated from the current source text.
- Context is reference only and shall not be translated or copied into output.
- Do not include partial or low-confidence text.
- Inclusion of prior translations shall be a separate experiment and disabled by default.

The exact Qwen model identity design gate shall pin:

- exact Hugging Face repository;
- instruct/base variant;
- immutable revision where possible;
- tokenizer and chat template;
- license/access requirements;
- compatible vLLM version;
- dtype and context length.

Output validation shall reject or retry:

- empty output for non-empty accepted source;
- reasoning tags or analysis text;
- Markdown fences or explanatory preamble;
- schema violations or extra fields;
- unexpected output language;
- implausible output/source length ratio;
- loss or alteration of protected numbers, dates, URLs, code, or identifiers;
- stale source revisions.

After bounded retries, emit `translation.failed`. Validators are quality safeguards and shall not silently rewrite translation text.

### 25.14 Persistence projection and compaction

Persistence semantics are:

- `meeting_<session-id>_debug.jsonl` is append-only and is the authoritative event log.
- During an active meeting, a history projection may contain multiple versioned records for a segment.
- At graceful stop, create `meeting_<session-id>_history.final.jsonl` by deterministic compaction, with exactly one latest valid projection per non-rejected segment.
- Write compacted history to a temporary file, validate every line and projection invariant, flush according to the Persistence ADR, and atomically rename.
- A recovery command shall ignore or quarantine an incomplete final JSONL line, validate event IDs/revisions, and rebuild final history.
- Disk-full, permission, invalid path, and interrupted-write behavior shall be tested.
- All files shall use UTF-8 without BOM and sanitized session-derived filenames.

Raw audio persistence is disabled by default. In explicit diagnostic mode, the client may save the exact lossless PCM stream sent to the server, and the log shall record path, sample count, format, and SHA-256. Audio files, meeting logs, and benchmark raw outputs shall not be committed to Git.

### 25.15 Test taxonomy, evaluation split, and annotation rules

The test policy distinguishes three categories:

#### A. ML quality and end-to-end tests

- Must use the real meeting recording or derived real clips with provenance.
- Must use actual models.
- Must not use invented transcript, translation, speaker label, or model output.

#### B. Protocol, security, persistence, and negative tests

Purpose-built conformance vectors are permitted for malformed headers, invalid UTF-8, oversized input, stale revisions, duplicate IDs, queue overflow, path traversal, truncated JSONL, timeout, and similar defensive branches.

- They shall be labeled `protocol_conformance_fixture` or `negative_test_vector`.
- They shall not be represented as meeting data.
- They shall never be used to claim ASR, language, speaker, overlap, or translation quality.

#### C. Client replay tests

- Must use immutable WebSocket traffic captured from an actual server run.
- Must include capture provenance, protocol version, configuration hash, and SHA-256.

The real recording shall be split into non-overlapping time ranges:

```text
development set: threshold and parameter tuning
validation set: architecture/configuration selection
locked evaluation set: final unbiased assessment
```

The split manifest shall balance available Japanese, Vietnamese, speakers, silence, low volume, clipping, filler, hesitation, incomplete speech, noise, overlap, and rapid turn changes. The locked evaluation set shall not be used for threshold tuning.

Before reference metrics are used, an annotation guide shall define:

- filler and hesitation transcription;
- false starts and self-corrections;
- punctuation and casing;
- Japanese number representation and tokenization;
- Vietnamese orthography;
- English/technical term handling;
- unintelligible markers;
- overlap and speaker-unknown notation;
- timestamp precision;
- literal-versus-natural translation reference principles;
- reviewer identity, version, and adjudication process.

A full-recording soak test of more than 30 minutes is mandatory. It shall examine memory growth, queues, GPU fragmentation, segment/speaker ID uniqueness, revision integrity, JSONL validity, translation backlog, deadlock, and graceful shutdown. Steady-state GPU benchmarks shall separate cold start, warm-up, repeated runs, and concurrent workload, and report distribution metrics such as median, P95, and maximum.

### 25.16 Requirement identifiers and traceability

Before Phase 1 implementation, Claude Code shall assign stable identifiers to normative requirements using domains such as:

```text
AUD-xxx  audio/client
PROT-xxx protocol
VAD-xxx  segmentation
ASR-xxx  transcription
LID-xxx  language
SPK-xxx  speaker/overlap
TRN-xxx  translation
PERS-xxx persistence
TEST-xxx testing
OPS-xxx  deployment/operations
SEC-xxx  security/privacy
```

Create and maintain `docs/traceability.md` with:

```text
requirement ID
-> ADR/design section
-> source module/file
-> test or server test script
-> real fixture/conformance vector
-> evidence artifact
-> implementation status
```

Allowed status values:

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

`accepted_exception` requires explicit user approval, rationale, risk, and a tracked follow-up item. A phase cannot be declared complete while mandatory requirement IDs are unmapped or falsely marked tested.


## 26. Required design gates

Claude Code shall discuss alternatives, implications, and a recommendation with the user before implementing each of these:

1. Repository assessment and target directory structure.
2. GPU pod environment and deployment isolation.
3. Binary WebSocket header and resume/backpressure protocol.
4. Client threading/async model and PySide6 integration.
5. Resampler and frame duration.
6. VAD/endpoint baseline parameters.
7. Partial stable-prefix algorithm.
8. Whisper decode profiles and context policy.
9. Language resolver/conflict policy.
10. Online speaker clustering algorithm and revision semantics.
11. Pyannote streaming/retrospective scheduling.
12. Hallucination acceptance/retry policy.
13. vLLM launch profile and Qwen prompt/response schema.
14. Log schema and retention.
15. Baseline results and numeric acceptance thresholds.
16. Packaging and operational runbook.
17. Canonical timeline, utterance/segment authority, lifecycle, and revision projection.
18. Graceful stop, sealing, server restart limitation, and gap classes.
19. Single-H100 GPU admission, priority, and degraded-mode matrix.
20. Pyannote windowing and canonical speaker reconciliation.
21. Exact Qwen model identity, translation context, and output validator.
22. Test taxonomy, data split, annotation guide, soak benchmark, and traceability system.

A design decision record shall be committed for every approved gate.

## 27. Implementation phases

### Phase 0: Repository audit and requirements

- Inspect existing repository without destructive changes.
- Record current files, branches, remotes, status, and existing conventions.
- Add this requirement and `CLAUDE.md`.
- Establish decision-record format.
- Assign stable requirement IDs and create the initial traceability matrix.
- Classify tests into real ML/E2E, protocol/security negative vectors, and real-capture replay.

### Phase 1: Architecture and protocol

- Define module boundaries, canonical sample timeline, utterance/segment/turn authority, lifecycle, independent revisions, sequence diagrams, schemas, binary header, errors, idempotency, backpressure, gap classes, stop/drain/seal, and resume behavior.
- Add schema validation and protocol tests using real capture/replay when available.

### Phase 2: Windows capture client

- Implement device discovery, capture, persistent resampling, bounded buffering, lifecycle, diagnostics, and capture of the real meeting file/device path as appropriate.
- Execute client tests.

### Phase 3: Client UI and persistence shell

- Implement PySide6 state display, timeline upsert/revision behavior, debug writer, history projection, and real event replay support.
- Execute client tests with real server-captured fixtures when available.

### Phase 4: Server ingestion and orchestration

- Implement gateway, validation, memory-resident session state, binary ingestion, canonical timeline, gap policy, lifecycle/revision projection, queue limits, graceful stop, degraded-mode events, worker interfaces, and metrics.
- Create server test scripts and stop at server test gate.

### Phase 5: VAD and segmentation

- Implement preprocessing, Silero state machine, endpointing, utterance assembly, and quality metadata.
- Run real-recording server tests through the user gate.

### Phase 6: Whisper partial/final ASR

- Implement production backend, rolling decode, stable-prefix revisions, accurate final decode, context policy, and reference comparison script.
- Run real-recording server tests through the user gate.

### Phase 7: Language ID and routing

- Implement staged SpeechBrain LID timing, pre-LID partial policy, Whisper cross-check, context/hysteresis, language revisions, forced final ASR language, and required translation direction.
- Run real bilingual recording tests through the user gate.

### Phase 8: Speaker and overlap

- Implement SpeechBrain embeddings, canonical online speaker IDs, merge/split/alias events, multi-speaker representation, pyannote scheduling and reconciliation, overlap events, and retrospective refinement.
- Run real meeting tests through the user gate.

### Phase 9: Hallucination hardening

- Implement combined quality evidence, repetition detection, retry/reset behavior, reject/low-confidence states, and translation firewall.
- Annotate and run real edge cases through the user gate.

### Phase 10: Qwen final-only translation

- Pin the exact Qwen model identity and implement vLLM deployment, explicit source/target prompt, default-off context experiment, strict output validation, timeout/retry, stale revision handling, and metrics.
- Run real final transcript tests through the user gate.

### Phase 11: End-to-end integration

- Integrate capture, tunnel, gateway, complete pipeline, UI, persistence, and replay.
- Validate a full real meeting session.

### Phase 12: Benchmark and tuning

- Create development, validation, and locked evaluation manifests plus annotation guidelines.
- Run the mandatory full-recording soak test.
- Record cold-start, warm, repeated-run, and concurrent baseline metrics.
- Discuss numeric gates with user.
- Tune one controlled variable at a time.
- Commit final acceptance thresholds.

### Phase 13: Packaging and operations

- Add client packaging, server startup/supervision, SSH tunnel instructions, environment verification, model download/pinning, log retention, troubleshooting, and recovery runbooks.

## 28. Phase completion rule

At the end of every phase Claude Code shall:

```text
format
lint
type-check
run all permitted tests
record test evidence
update documentation and decision records
review git diff
commit
push
report commit hash
report tests run
report tests not run
report known issues
stop at any required server test gate
```

## 29. Definition of done

The MVP is done only when:

- All approved architecture and protocol documents match implementation.
- Every mandatory requirement ID maps to implementation, test evidence, or an explicitly approved exception in the traceability matrix.
- Canonical sample timeline, utterance/segment authority, lifecycle, independent revisions, and sealing are enforced end to end.
- Graceful stop drains, seals, compacts history, and exposes incomplete work without hiding it.
- Single-H100 admission control prevents retrospective/translation work from starving final ASR.
- Client captures real WASAPI loopback audio without blocking the callback.
- WebSocket stream detects loss and handles reconnect according to contract.
- Real Japanese/Vietnamese meeting audio produces partial and final transcription.
- Final ASR language is explicit.
- Only accepted final transcription is translated.
- Translation direction is explicit and correct.
- Timeline shows final speaker ID, language ID, transcript, translation, timing, and overlap state.
- Debug and history JSONL are valid and recoverable.
- Hallucination gates are implemented and tested on available real cases.
- Server tests have been run by the user and evidence recorded.
- No required test is falsely marked passed.
- Numeric acceptance thresholds have been established from the baseline and approved.
- Installation, tunnel, startup, troubleshooting, and shutdown instructions are reproducible.
- All phase commits have been pushed.

## 30. References to validate during implementation

Claude Code shall verify current model cards, licenses, access conditions, and compatible package versions during its design gates. At minimum, inspect:

- https://huggingface.co/openai/whisper-large-v3
- https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb
- https://huggingface.co/speechbrain/lang-id-voxlingua107-ecapa
- https://huggingface.co/speechbrain/sepformer-wsj02mix
- https://github.com/snakers4/silero-vad
- https://github.com/pyannote/pyannote-audio
- https://docs.vllm.ai/
- the exact Hugging Face model page selected for Qwen3.5-9B

Do not assume a package command or version from this document is current without checking it on the target pod at the appropriate gate.
