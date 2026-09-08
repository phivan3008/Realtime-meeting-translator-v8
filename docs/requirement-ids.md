# Requirement identifiers

Stable identifiers for every normative clause in `requirements.md` and
`CLAUDE.md`, assigned per `requirements.md` Section 25.16.

`docs/traceability.md` maps each ID to its ADR, module, test, fixture, evidence
and status. This file is the definition of what each ID *means*; that file is
the record of what has been *done* about it.

Last updated: 2026-09-08 (Phase 0).

---

## Conventions

**Domains** are those listed in `requirements.md` Section 25.16, which introduces
them with "domains such as" and is therefore explicitly non-exhaustive. One
domain is added:

| Prefix | Domain | Note |
|---|---|---|
| `AUD` | audio capture and client runtime | Section 25.16 |
| `UI` | client user interface | **added** — Section 8.3 is substantial and separating it from `AUD` keeps capture-thread requirements distinct from rendering requirements |
| `PROT` | protocol, wire format, timeline, identity, revisions | Section 25.16 |
| `VAD` | preprocessing and segmentation | Section 25.16 |
| `ASR` | transcription and hallucination control | Section 25.16 |
| `LID` | language identification and routing | Section 25.16 |
| `SPK` | speaker, clustering, overlap, diarization | Section 25.16 |
| `TRN` | translation | Section 25.16 |
| `PERS` | persistence | Section 25.16 |
| `TEST` | testing, fixtures, evaluation | Section 25.16 |
| `OPS` | deployment, operations, resources, process | Section 25.16 |
| `SEC` | security and privacy | Section 25.16 |

**Numbering.** Assigned in steps of 10 so later clauses can be inserted without
renumbering. IDs are permanent: a withdrawn requirement is marked
`superseded_by`, never deleted and never reused.

**Granularity.** One ID per independently verifiable normative clause — a
"shall", "must", or prohibition that a test or a review can pass or fail. Not
one per paragraph, and not one per sentence when several sentences state a
single obligation.

**Source column.** `R` is `requirements.md`, `C` is `CLAUDE.md`. Where both
state the same obligation, one ID carries both sources.

---

## AUD — audio capture and client runtime

| ID | Requirement | Source |
|---|---|---|
| AUD-010 | Enumerate WASAPI output devices and loopback-capable devices | R 8.1 |
| AUD-020 | Let the user select the capture device | R 8.1 |
| AUD-030 | Capture output audio using PyAudioWPatch WASAPI loopback | R 4.1, 8.1; C 2 |
| AUD-040 | Validate sample format, channel count, sample rate and device availability | R 8.1 |
| AUD-050 | Convert captured audio to mono PCM signed 16-bit little-endian at 16 kHz | R 8.1 |
| AUD-060 | Use a streaming-quality resampler with persistent state across chunks | R 8.1 |
| AUD-070 | Avoid repeated WAV headers in the stream | R 8.1 |
| AUD-080 | Add monotonically increasing sequence numbers and monotonic capture timestamps | R 8.1 |
| AUD-090 | Detect callback starvation, device removal, overrun, underrun and resampler failure | R 8.1 |
| AUD-100 | Bounded ring buffer with an explicit overflow policy | R 8.1 |
| AUD-110 | Never block the audio callback on network I/O, UI rendering, log writing or disk I/O | R 8.1; C 8 |
| AUD-120 | Capture lifecycle: IDLE → CONNECTING → READY → CAPTURING → STOPPING → COMPLETED, with ERROR ↔ RECONNECTING | R 8.2 |
| AUD-130 | Log every state transition with timestamp and reason code | R 8.2 |
| AUD-140 | Send heartbeat/ping messages | R 8.4 |
| AUD-150 | Detect a dead connection | R 8.4 |
| AUD-160 | Reconnect with bounded exponential backoff | R 8.4 |
| AUD-170 | Preserve a bounded amount of unsent audio across reconnect | R 8.4 |
| AUD-180 | Attempt session resume within a configurable retention window | R 8.4 |
| AUD-190 | Report unrecoverable audio gaps clearly | R 8.4 |
| AUD-200 | Never silently reorder, duplicate or discard audio | R 8.4 |
| AUD-210 | Client/audio metrics: frame count, sequence gaps, ring-buffer overflow, callback delays, capture and resample drift, reconnect recovery and lost duration, CPU and memory, log writer integrity | R 23.1 |
| AUD-220 | Select a correct WASAPI loopback device automatically on an arbitrary Windows user machine, given only Python and the project's dependencies | user instruction, 2026-09-08 |

---

## UI — client user interface

| ID | Requirement | Source |
|---|---|---|
| UI-010 | Device selector | R 8.3 |
| UI-020 | Server/tunnel endpoint configuration | R 8.3 |
| UI-030 | Connect/disconnect control | R 8.3 |
| UI-040 | Start/stop meeting control | R 8.3 |
| UI-050 | Current capture and server status visible | R 8.2, 8.3 |
| UI-060 | Timeline ordered by source audio time | R 8.3 |
| UI-070 | Per segment show: speaker ID, language ID, source start/end time, transcription row, translation row, partial/final state, overlap indicator, low-confidence/warning indicator | R 8.3 |
| UI-080 | Partial transcription visually distinguishable from final | R 8.3 |
| UI-090 | Translation shows a pending state between final ASR and Qwen returning | R 8.3 |
| UI-100 | A translation error never removes or alters the final transcript | R 8.3, 17.4 |
| UI-110 | A retry action may retry failed translation without re-running ASR | R 8.3 |
| UI-120 | Project records by `segment_id` using the Section 25 revision model; ignore and log duplicate and stale events | R 8.3; C 10 |
| UI-130 | A speaker-only revision must not replace newer text, language or translation state | R 8.3 |
| UI-140 | Never append every partial as a new permanent line | C 10; R 13.2 |
| UI-150 | Degraded mode start and end are visible in the UI | R 25.12; C 9.7 |

---

## PROT — protocol, timeline, identity, revisions

| ID | Requirement | Source |
|---|---|---|
| PROT-010 | Every control event carries `protocol_version`, `event_type`, `session_id`, `event_id`, `sent_at_utc` | R 9.1 |
| PROT-020 | Reject unknown major protocol versions | R 9.1 |
| PROT-030 | Ignore unknown optional fields safely within the same major version | R 9.1 |
| PROT-040 | Implement all 21 event types listed in Section 9.3 | R 9.3 |
| PROT-050 | `session.summary` extension event, forced by the graceful-stop sequence | R 25.9; ADR-0005 |
| PROT-060 | `capability.updated` extension event, forced by degraded-mode capability state | R 25.12; ADR-0005 |
| PROT-070 | `session_unrecoverable` is a client-local persisted record, not a wire event | R 25.10; ADR-0005 |
| PROT-080 | Wire audio is `pcm_s16le`, 16000 Hz, 1 channel, little-endian | R 9.2; C 2 |
| PROT-090 | Frame duration is 20 ms or 40 ms, decided by benchmark | R 9.2 |
| PROT-100 | Binary frame header carries at minimum: protocol version, stream identifier, sequence number, monotonic capture timestamp, sample count, flags | R 9.2 |
| PROT-110 | Binary header schema, byte layout, integer sizes and endianness written as a specification and covered by real-capture serialization tests **before** server implementation | R 9.2 |
| PROT-120 | Backpressure defines maximum queue depth, acknowledgement cadence, client buffer limit, server overload response, reconnect retention duration, gap reporting, termination behaviour | R 9.4 |
| PROT-130 | No component creates an unbounded queue | R 9.4; C 8 |
| PROT-140 | Canonical media position is the integer audio sample offset at 16 kHz from session start | R 25.1; C 9.1 |
| PROT-150 | Time conversion is `floor(sample_offset * 1000 / 16000)` | R 25.1 |
| PROT-160 | Floating-point seconds are never identity keys or ordering authorities | R 25.1; C 9.1 |
| PROT-170 | Client monotonic, server monotonic and UTC times are observability fields only | R 25.1 |
| PROT-180 | Sequence gaps preserve their duration on the media timeline; the timeline is never compressed to hide missing audio | R 25.1 |
| PROT-190 | A reconnect continues the same sample timeline only on accepted resume; a non-resumed stream gets a new `stream_id` and an explicit discontinuity event | R 25.1 |
| PROT-200 | The protocol ADR defines integer ranges, overflow handling and conversion rounding | R 25.1 |
| PROT-210 | `stream_id`, `utterance_id`, `segment_id` and `speaker_turn_id` are distinct and never conflated | R 25.2; C 9.1 |
| PROT-220 | The session orchestrator is the sole authority creating `utterance_id` and `segment_id` | R 25.2; C 9.1 |
| PROT-230 | Whisper subsegment and token-timestamp local IDs never become public `segment_id` values | R 25.2 |
| PROT-240 | Split and merge operations carry explicit lineage fields (`parent_segment_ids`, `operation`) | R 25.2; C 9.1 |
| PROT-250 | A revision retains the same `segment_id`; a new `segment_id` is created only when a split cannot be represented as a revision | R 25.2 |
| PROT-260 | Every current segment projection carries `content_revision`, `language_revision`, `speaker_revision`, `translation_revision`, `translated_from_content_revision`, `translated_from_language_revision` | R 25.5; C 9.3 |
| PROT-270 | Revision dependency rules: content change invalidates translation; language change invalidates translation and requires revalidation; speaker-only change does not | R 25.5; C 9.3 |
| PROT-280 | Replaying the same event is idempotent by `event_id` | R 25.5 |
| PROT-290 | Equal revision with different payload is an integrity conflict, never last-write-wins | R 25.5; C 9.3 |
| PROT-300 | The protocol carries enough evidence for the client debug log to be a self-sufficient event source | ADR-0004; R 19.1 |
| PROT-310 | The protocol specification includes event preconditions and projection pseudocode for every revision-bearing event | R 25.5 |
| PROT-320 | Every missing sequence range creates an `audio.gap` event with expected and received sequence, missing sample estimate and media interval | R 25.8 |
| PROT-330 | Maximum frame and event sizes are defined and enforced | R 9.4; C 10 |
| PROT-340 | Error codes and version compatibility rules are documented | C 10 |
| PROT-350 | The exact resume contract is approved at the protocol design gate | R 8.4; C 10 |
| PROT-360 | One utterance may yield zero, one or many text segments; a text segment belongs to exactly one utterance in MVP | R 25.2 |
| PROT-370 | Qwen translates one accepted text segment at a time, never a raw utterance or a raw Whisper subsegment | R 25.2 |
| PROT-380 | Stale asynchronous responses are persisted as diagnostics but never update current projection | R 25.5; C 9.3 |
| PROT-400 | Schema validation for all control events and worker messages | C 8; R 21 |

---

## VAD — preprocessing and segmentation

| ID | Requirement | Source |
|---|---|---|
| VAD-010 | Preprocessing order: wire validation → PCM conversion → channel validation/downmix → sample-rate validation/resampling → DC removal → conservative level normalization → optional feature-flagged noise reduction → VAD | R 10 |
| VAD-020 | Calculate and log non-content quality features: duration, peak, RMS, clipping ratio, zero ratio, missing frame count, speech ratio | R 10 |
| VAD-030 | Quality features are evidence, never standalone truth | R 10 |
| VAD-040 | Silero streaming state machine: IDLE → POSSIBLE_SPEECH → SPEAKING → POSSIBLE_END → FINALIZING → IDLE | R 11 |
| VAD-050 | Configurable, benchmark-derived parameters: `threshold`, `min_speech_ms`, `min_silence_ms`, `speech_pad_ms`, `pre_roll_ms`, `post_roll_ms`, `max_utterance_ms` | R 11 |
| VAD-060 | Hysteresis prevents rapid speech/non-speech oscillation | R 11 |
| VAD-070 | Pre-roll preserves clipped word beginnings | R 11 |
| VAD-080 | Post-roll preserves quiet word endings | R 11 |
| VAD-090 | Natural short pauses do not automatically split a sentence | R 11 |
| VAD-100 | Maximum utterance duration forces a controlled cut with overlap and context preservation | R 11 |
| VAD-110 | A network gap is never mistaken for a natural speech endpoint | R 11; R 25.8 |
| VAD-120 | Too-short or too-weak speech is rejected or marked low confidence, never expanded into a plausible sentence | R 11 |
| VAD-130 | Silero runs on CPU | R 25.11 |
| VAD-140 | Silero is the live endpointing authority; pyannote does not open or close ASR utterances in MVP | R 15.3, 25.2; C 14 |

---

## ASR — transcription and hallucination control

| ID | Requirement | Source |
|---|---|---|
| ASR-010 | Whisper is not treated as a native streaming engine; implement stateful rolling decode plus endpoint-triggered final decode | R 13.1 |
| ASR-020 | Maintain a rolling audio buffer while an utterance is active | R 13.2 |
| ASR-030 | Decode at a configurable interval | R 13.2 |
| ASR-040 | Decode a configurable recent window plus necessary left context | R 13.2 |
| ASR-050 | Prefer the best available provisional language without irrevocably locking low-confidence LID | R 13.2 |
| ASR-060 | Use a low-latency decode profile for partials | R 13.2 |
| ASR-070 | Compare consecutive hypotheses | R 13.2 |
| ASR-080 | Calculate a stable prefix and an unstable suffix | R 13.2 |
| ASR-090 | Publish revisions only when useful content or state changed (minimum publish delta) | R 13.2; C 12 |
| ASR-100 | Never append a partial as a new independent final line | R 13.2 |
| ASR-110 | Benchmark stable-prefix behaviour by rollback count, revision count, edit distance between consecutive partials, and time to first useful text | R 13.2 |
| ASR-120 | Final decode assembles the complete utterance including pre-roll and post-roll | R 13.3 |
| ASR-130 | Final decode uses an accuracy-oriented profile | R 13.3 |
| ASR-140 | Final decode explicitly passes the resolved input language when available | R 13.3; R 12.2 |
| ASR-150 | Final decode produces segment timestamps and quality metadata | R 13.3 |
| ASR-160 | Final decode reconciles with partial state | R 13.3 |
| ASR-170 | Apply hallucination and acceptance gates before publishing a final | R 13.3 |
| ASR-180 | Publish the final only after acceptance processing | R 13.3 |
| ASR-190 | Whisper context may contain only bounded recent **accepted** final transcription — never translation, rejected, low-confidence or partial text | R 13.4, 14.4; C 12 |
| ASR-200 | Reset or shorten context after long silence, strong language switch, detected repetition, session boundary, or context budget exhaustion | R 13.4 |
| ASR-210 | faster-whisper / CTranslate2 is the production backend | R 4.2, 13.5; C 2 |
| ASR-220 | Transformers is a reference comparison on a manageable, human-reviewed subset only | R 4.2, 13.5 |
| ASR-230 | A design gate reopens backend choice only if measured quality differs materially | R 13.5 |
| ASR-240 | Every benchmark artifact records model repository, revision/commit, tokenizer, backend version, compute type and decode parameters | R 13.5 |
| ASR-250 | Implement all nine defensive hallucination layers | R 14.1; C 13 |
| ASR-260 | Audio validity gate rejects or flags: missing sequence ranges, all-zero or near-zero content, excessive clipping, invalid duration, invalid numeric values after conversion, resampling failure, extremely low RMS, too little detected speech, truncation by disconnection, unsafe maximum-duration cut | R 14.2 |
| ASR-270 | Collect decode evidence where available: `avg_logprob`, `no_speech_prob`, `compression_ratio`, `speech_ratio`, `temperature_used`, `language_id` | R 14.3 |
| ASR-280 | Also calculate repeated token/character/n-gram indicators, text length versus speech duration, similarity to the prior segment, language disagreement, timestamp plausibility, partial instability, and audio energy/clipping evidence | R 14.3 |
| ASR-290 | No single threshold is sufficient; acceptance uses combined evidence, tuned from the real recording and documented | R 14.3; C 13 |
| ASR-300 | Failed-gate retry policy: shorter or reset context, disable previous-text conditioning, force the reliable LID language, apply only benchmark-approved fallback settings, then return `low_confidence` or `rejected` | R 14.4 |
| ASR-310 | Only accepted final transcription enters ASR context or translation | R 14.4, 13.4 |
| ASR-320 | Never normalize filler, hesitation, grammatical mistakes or unfinished speech into a well-formed sentence at the ASR stage | R 14.5; C 13 |
| ASR-330 | ASR accuracy metrics are reported only after human reference exists | R 23.2 |

---

## LID — language identification and routing

| ID | Requirement | Source |
|---|---|---|
| LID-010 | Allowed languages are `ja` and `vi`; translation direction is `ja → vi` and `vi → ja` | R 12.1 |
| LID-020 | The resolver combines SpeechBrain probabilities, Whisper evidence, recent speaker language, recent meeting language state, utterance duration and confidence | R 12.2 |
| LID-030 | SpeechBrain is the primary acoustic signal; Whisper is a secondary validation signal | R 12.2; C 11 |
| LID-040 | Language states: `unknown`, `provisional_ja`, `provisional_vi`, `confirmed_ja`, `confirmed_vi`, `mixed`, `uncertain` | R 12.3 |
| LID-050 | The protocol separates `language_id`, `language_status` and `language_revision` | R 12.3 |
| LID-060 | Never run LID independently on transport-sized fragments | R 12.3; C 11 |
| LID-070 | Never trust very short utterances such as acknowledgements without context and hysteresis | R 12.3; C 11 |
| LID-080 | Restrict normal decisions to Japanese and Vietnamese | R 12.3 |
| LID-090 | On signal disagreement, retry or defer according to configured policy | R 12.3 |
| LID-100 | Once resolved, explicitly pass `ja` or `vi` to Whisper final decoding | R 12.3 |
| LID-110 | Every Qwen request explicitly specifies source and target language | R 12.3, 17.2 |
| LID-120 | Never invoke translation for an unresolved language | R 12.3; C 11 |
| LID-130 | `mixed` is schema-supported; within-sentence code switching quality is not claimed for MVP | R 12.3, 3.2 |
| LID-140 | Staged language policy must not block the first partial indefinitely | R 25.3; C 9.2 |
| LID-150 | Partial events emitted before a language decision identify the basis used (`asr_language_mode`) | R 25.3 |
| LID-160 | The Language Resolver ADR decides: minimum voiced duration for first LID, re-evaluation cadence/milestones, precedence among speaker hint, session hint, SpeechBrain and Whisper, confidence and margin definitions, both-weak behaviour, disagreement behaviour, whether a provisional-language change triggers immediate re-decode, unresolved-final timeout/fallback, short acknowledgement handling, and conditions for `mixed` and `uncertain` | R 25.3; C 9.2 |
| LID-170 | A provisional language change may revise partial content | R 25.3 |
| LID-180 | Only the endpoint final decode with resolved language is eligible for ASR acceptance and translation | R 25.3; C 9.2 |
| LID-190 | Language ID metrics are reported only against human reference | R 23.3 |
| LID-200 | Language ID is shared pipeline state, not a UI-only label | C 11 |
| LID-210 | Never ask Qwen to decide translation direction or identify language | R 17.2; C 11 |

---

## SPK — speaker, clustering, overlap, diarization

| ID | Requirement | Source |
|---|---|---|
| SPK-010 | MVP outputs anonymous IDs only (`speaker-1`, `speaker-2`, …) | R 15.1 |
| SPK-020 | SpeechBrain ECAPA embeddings feed an online clustering component | R 15.2 |
| SPK-030 | Clustering algorithm and thresholds are selected at a design gate after real-data experiments | R 15.2 |
| SPK-040 | Never create or update a speaker profile from segments that are too short, silence or weak filler, heavily overlapped, extremely noisy, rejected by audio validity checks, or damaged by an experimental separator | R 15.2, 16.1; C 14 |
| SPK-050 | Speaker labels on active and partial segments are provisional and revisable | R 15.2 |
| SPK-060 | `speaker.updated` events support retrospective merge and relabel | R 15.2 |
| SPK-070 | Pyannote is isolated and used for overlap detection, speaker-change evidence, retrospective refinement and controlled comparison | R 15.3; C 14 |
| SPK-080 | Pyannote never replaces Silero as the live endpointing component | R 15.3 |
| SPK-090 | Pyannote never silently overwrites timeline labels without a versioned correction event | R 15.3; C 14 |
| SPK-100 | Never assume a fixed speaker count | R 15.4 |
| SPK-110 | For detected overlap: mark `overlap=true`, preserve overlap intervals in the debug log, run the normal ASR path on mixed audio, lower confidence or attach a warning when justified, never claim both speakers were fully recovered | R 16.1 |
| SPK-120 | Provide a `SourceSeparator` interface with the production default disabled | R 16.2; C 14 |
| SPK-130 | SepFormer experiments run only on detected overlap regions, outside the default critical path, always preserving the mixed-audio baseline, compared on human-reviewed real data, recording latency and artifacts, and never automatically preferred | R 16.2; C 14 |
| SPK-140 | Multi-speaker schema supports `primary_speaker_id` (nullable or `speaker-unknown`), `speaker_status`, `speaker_candidates` with confidences, `overlap`, `overlap_intervals` | R 25.6; C 9.4 |
| SPK-150 | `speaker-multiple` is a presentation label only, never an embedding cluster | R 25.6 |
| SPK-160 | The orchestrator may split one utterance into multiple text segments aligned to speaker changes while preserving utterance lineage | R 25.6 |
| SPK-170 | Speaker IDs are never inferred from pyannote label name or order | R 25.6, 25.7; C 9.4 |
| SPK-180 | Canonical anonymous speaker IDs are session-scoped, monotonically allocated and never reused | R 25.7; C 9.4 |
| SPK-190 | Merge events carry `operation`, `from_speaker_ids`, `to_speaker_id`, `speaker_revision` | R 25.7 |
| SPK-200 | Merged IDs become aliases of the canonical target and remain in debug history | R 25.7; C 9.4 |
| SPK-210 | Projection rewrites affected current segment views to the canonical ID without modifying content or translation revisions | R 25.7 |
| SPK-220 | Split operations require new canonical IDs, explicit affected intervals and segments, and lineage | R 25.7 |
| SPK-230 | Pyannote local labels are mapped to canonical IDs using temporal overlap, embedding similarity, duration and confidence | R 25.7 |
| SPK-240 | The Pyannote Scheduling ADR decides rolling-window length and overlap, cadence and priority, maximum retrospective window, mapping confidence policy, reconciliation with SpeechBrain online clusters, split/merge thresholds and minimum evidence, and ambiguous-mapping behaviour | R 25.7; C 9.4 |
| SPK-250 | Speaker and overlap accuracy metrics are reported only when annotation exists; until then only operational diagnostics | R 23.4 |
| SPK-260 | Speaker names and enrollment are out of scope | R 3.2; C 14 |

---

## TRN — translation

| ID | Requirement | Source |
|---|---|---|
| TRN-010 | Pin exact Qwen repository, instruct/base variant, immutable revision, tokenizer, chat template, license and access requirements, compatible vLLM version, dtype and context length | R 25.13; C 9.8 |
| TRN-020 | Qwen thinking/reasoning output is disabled | R 4.2, 17.3; C 2 |
| TRN-030 | The immutable model revision is recorded in the manifest and in every benchmark artifact | R 13.5; C 7, 25 |
| TRN-040 | Runtime parameters are benchmarked on the H100, not assumed; use the smallest context sufficient for live translation | R 17.3; C 15 |
| TRN-050 | Translation is invoked only after accepted final ASR | R 17.1; C 15 |
| TRN-060 | Every request states source language and code, target language and code, the current final source text, bounded accepted context if enabled, translate-only instruction, preservation of names/numbers/dates/URLs/code/identifiers, no added information, no invented source correction, and a defined response schema | R 17.2; C 15 |
| TRN-070 | Never ask Qwen to identify the language | R 17.2 |
| TRN-080 | Translation states are `pending`, `completed`, `failed` | R 17.4 |
| TRN-090 | A translation timeout or failure never invalidates final ASR | R 17.4; C 15 |
| TRN-100 | Translation may be retried idempotently by segment ID | R 17.4 |
| TRN-110 | A stale translation response for an older transcription revision is discarded | R 17.4; R 25.5 |
| TRN-120 | Qwen output is validated before display and persistence | R 17.4 |
| TRN-130 | Translation context is a configurable A/B feature, disabled by default: bounded recent accepted non-rejected finals, in clearly separated fields, reference only, never translated or copied into output, excluding partial and low-confidence text; including prior translations is a separate default-off experiment | R 25.13; C 9.8 |
| TRN-140 | Output validation rejects or retries: empty output for non-empty accepted source, reasoning tags or analysis text, Markdown fences or explanatory preamble, schema violations or extra fields, unexpected output language, implausible output/source length ratio, loss or alteration of protected numbers/dates/URLs/code/identifiers, stale source revisions | R 25.13 |
| TRN-150 | After bounded retries emit `translation.failed`; validators never silently rewrite translation text | R 25.13; C 9.8 |
| TRN-160 | Translation firewall: Qwen never receives partial transcription, rejected segments, raw unchecked hypotheses, silence or non-speech, unresolved-language segments, or low-confidence fragments by default | R 14.6; C 13 |
| TRN-170 | Translation quality metrics are reported only after human references and review exist | R 23.5 |
| TRN-180 | Translation is final-only; partial translation is out of scope | R 3.2, 4.2; C 2 |

---

## PERS — persistence

| ID | Requirement | Source |
|---|---|---|
| PERS-010 | `meeting_<session-id>_debug.jsonl` is the append-only authoritative event log | R 19.1, 25.14; C 9.9 |
| PERS-020 | The debug log includes session lifecycle, client and server status, audio gaps and acknowledgements, VAD events, partial revisions, final and corrected transcription, speaker revisions, language decisions and evidence, overlap intervals, ASR quality evidence, translation lifecycle, errors, warnings, latency metadata, and model/config/version identifiers | R 19.1 |
| PERS-030 | `meeting_<session-id>_history.jsonl` is a live projection carrying no authority; `meeting_<session-id>_history.final.jsonl` is the compacted deliverable | R 19.2, 25.14; ADR-0004 |
| PERS-040 | Compaction writes to a temporary file, validates every line and projection invariant, flushes, then atomically renames | R 25.14 |
| PERS-050 | Never embed raw PCM in JSONL; reference a separate audio file by path and SHA-256 | R 19.1 |
| PERS-060 | The writer is crash-tolerant and never leaves a malformed JSON line | R 19.2 |
| PERS-070 | A deterministic command rebuilds the history file from the debug event log | R 19.2, 25.14 |
| PERS-080 | Recovery ignores or quarantines an incomplete final JSONL line and validates event IDs and revisions | R 25.14 |
| PERS-090 | Test truncated tail, duplicate and conflicting revision, invalid path, permission failure and disk-full behaviour with labelled negative vectors | R 25.14; C 9.9 |
| PERS-100 | All files use UTF-8 without BOM and sanitized session-derived filenames | R 25.14; R 21 |
| PERS-110 | Raw audio persistence is disabled by default; in explicit diagnostic mode the log records path, sample count, format and SHA-256 | R 25.14; C 9.9 |
| PERS-120 | Audio files, meeting logs and benchmark raw outputs are never committed to Git | R 25.14; C 21 |
| PERS-130 | The client debug writer failing stops the session safely | R 25.12; C 9.7 |

---

## OPS — deployment, operations, resources, process

| ID | Requirement | Source |
|---|---|---|
| OPS-010 | Repository structure as decided at the repository design gate | R 26.1; C 6; ADR-0001 |
| OPS-020 | `protocol/` carries no ML dependency | ADR-0001; R 7 |
| OPS-030 | Cross-worker imports are forbidden and enforced by a conformance test | ADR-0001; R 7; C 7 |
| OPS-040 | The server isolates gateway, orchestrator, asr-worker, speechbrain-worker, diarization-worker and translation-service; not all ML frameworks in one process | R 7; C 7 |
| OPS-100 | One Git branch per phase, merged to `main` only after the phase report is approved | C 21; ADR-0002 |
| OPS-110 | Run the full repository inspection set before every commit | C 21 |
| OPS-120 | Never force push, rewrite shared history, rebase a shared branch unasked, discard user changes, or use destructive reset/clean/checkout | C 21.1 |
| OPS-200 | Format, lint and type-check toolchain established and run at every phase end | R 28; ADR-0003 |
| OPS-210 | Execute the full phase completion sequence at the end of every phase | R 28; C 21 |
| OPS-220 | Static checks never substitute for behavioural tests | C 17.3 |
| OPS-300 | The environment inspection is a mandatory design gate; no CUDA, PyTorch, CTranslate2, SpeechBrain, pyannote, vLLM or driver version is locked before returned output exists | R 7; C 5 |
| OPS-310 | Deployment isolation mechanism selected only after pod inspection | R 7; ADR-0007 |
| OPS-320 | Check disk, license, access requirements and model revision before downloading any model | user instruction; C 5, 7 |
| OPS-330 | Dependencies pinned reproducibly per dependency domain after the target environment is known | C 7 |
| OPS-340 | Benchmarks run under GPU contention are labelled as such, and no acceptance threshold is derived from them | R 25.11; ADR-0007 |
| OPS-350 | Hugging Face cache stays on the persistent volume (`HF_HOME=/workspace/cache`) | ADR-0007 |
| OPS-400 | Every approved design gate produces a committed decision record | R 26; C 3.1 |
| OPS-410 | An ADR reaches `accepted` only by explicit user approval | C 3.1; ADR-0000 |
| OPS-500 | Measure and report GPU memory idle and peak by worker, system RAM, ASR real-time factor, ASR queue depth, translation queue depth, translation latency, event-loop lag, dropped/rejected audio frames, utterance processing latency, worker restart count | R 18 |
| OPS-510 | Warm models before an active meeting when possible | R 18 |
| OPS-520 | Bound all queues; GPU out-of-memory produces explicit failure and recovery, never silent process-wide corruption | R 18 |
| OPS-530 | All services use structured logs carrying session ID, stream ID, segment ID, event ID, revision, service and worker, monotonic processing timestamps and UTC correlation time | R 20 |
| OPS-540 | Metrics exportable in Prometheus-compatible form where practical | R 20 |
| OPS-550 | Sensitive audio and text excluded from normal operational logs unless debug content logging is explicitly enabled | R 20; C 26 |
| OPS-600 | All tunable values externalized into validated configuration | R 24; C 8 |
| OPS-610 | Every debug log and benchmark artifact includes a configuration hash | R 24; C 8 |
| OPS-700 | Graceful stop executes the full sequence: reject frames after final sequence, close or reject incomplete gap ranges, flush the active VAD utterance, final-decode if minimum speech requirements are met, complete/retry/cancel translation within a bounded drain timeout, run bounded pending refinement, seal remaining segments, emit `session.summary`, emit `session.stopped`, close persistence projection atomically | R 25.9; C 9.5 |
| OPS-710 | The Session Lifecycle ADR defines stop acknowledgement timeout, ASR flush timeout, translation drain timeout, refinement timeout, worker cancellation behaviour, client behaviour when `session.stopped` is not received, conditions for `completed` / `completed_with_warnings` / `failed`, and the recovery/compaction command after abrupt termination | R 25.9 |
| OPS-720 | A server restart makes the active meeting unrecoverable; the client emits and persists `session_unrecoverable` and requires a new session | R 25.10; C 9.5 |
| OPS-730 | The client never resends an entire meeting to a restarted server as if live continuity were preserved | R 25.10 |
| OPS-740 | Gap policy is configurable by duration class — small: preserve the interval, optionally insert marked silence, attach gap evidence; medium: close the utterance as `truncated_by_gap`, reset VAD, require stricter ASR acceptance; large: invalidate resume continuity, reset VAD and ASR context, require explicit discontinuity or a new stream | R 25.8; C 9.5 |
| OPS-750 | No implementation decodes across an unreported gap | R 25.8; C 9.5 |
| OPS-760 | A segment intersecting a gap is not translated unless its accepted final passes the gap-aware quality policy | R 25.8 |
| OPS-800 | GPU admission implements the seven-level priority policy of Section 25.11 | R 25.11; C 9.6 |
| OPS-810 | Final ASR is never starved by translation or retrospective work; partial jobs may be coalesced or skipped; final jobs are never dropped | R 25.11; C 9.6 |
| OPS-820 | SpeechBrain LID and speaker embedding are benchmarked on CPU and GPU before placement is fixed | R 25.11; C 9.6 |
| OPS-830 | vLLM GPU memory reservation accounts for CTranslate2 and other CUDA contexts | R 25.11 |
| OPS-840 | No benchmark, reference or separation workload runs concurrently with a production meeting except in a documented concurrency test | R 25.11; C 9.6 |
| OPS-850 | Expose queue depth, admission wait, execution time and memory use by workload class | R 25.11 |
| OPS-860 | A GPU Resource ADR and a user-run server gate precede loading all models concurrently | R 25.11; C 9.6 |
| OPS-900 | Implement the degraded-mode capability matrix exactly as specified in Section 25.12 | R 25.12; C 9.7 |
| OPS-910 | Every degraded mode emits start and end events, updates session capability state, and is visible in the UI | R 25.12 |
| OPS-920 | No component silently substitutes lower-quality behaviour | R 25.12; C 3.2 |
| OPS-1000 | All 22 design gates of Section 26 are discussed before the corresponding implementation | R 26; C 3.1 |
| OPS-1010 | Numeric acceptance thresholds are established only after a real baseline is measured, reviewed and committed | R 23.6, 24; C 24 |
| OPS-1020 | Maintain the full documentation set: architecture overview, sequence diagrams, protocol specification, event schemas, ADRs, environment matrix and lock files, client setup and packaging guide, GPU server setup and model-download guide, SSH tunnel runbook, test-data provenance and annotation guide, server test runbook, benchmark report, troubleshooting guide, privacy and retention notes, third-party inventory and licenses, immutable model revision manifest | C 25 |
| OPS-1030 | A requirement change requires an explicit proposal with rationale, impact, migration, affected ADRs and tests, and traceability changes; never a silent edit | C 25 |
| OPS-1040 | Definition of done as stated in Section 29 | R 29; C 27 |
| OPS-1050 | Noise suppression is disabled by default; a `NoiseReducer` interface exists; raw normalized and reduced audio are compared on real clips including low-volume and clipped speech; a reducer is enabled only after user-approved evidence | R 4.3, 16; C 16 |
| OPS-1060 | Never tune multiple independent variables in one benchmark comparison | C 8 |
| OPS-1070 | Never enable quantization, speculative decoding, a new dtype or a concurrent GPU optimization without an ADR and a real benchmark | C 8 |
| OPS-1080 | Never improve latency by weakening a quality, hallucination, persistence or revision gate | C 8; R 5 |

---

## SEC — security and privacy

| ID | Requirement | Source |
|---|---|---|
| SEC-010 | Bind server ports to localhost for SSH tunnel use unless another secure architecture is approved | R 21; C 26 |
| SEC-020 | Never expose an unauthenticated public WebSocket endpoint | R 21 |
| SEC-030 | Never print or commit model access tokens, SSH secrets or environment credentials | R 21; C 26 |
| SEC-040 | Use environment variables or local secret files excluded by Git | R 21 |
| SEC-050 | Define retention and deletion behaviour for meeting logs and optional audio recordings | R 21; C 26 |
| SEC-060 | Sanitize filenames and session metadata | R 21 |
| SEC-070 | Impose maximum sizes on frames, events, strings, queues and sessions | R 21; C 26 |
| SEC-080 | Validate all client and worker messages | R 21; C 26 |
| SEC-090 | Treat transcription and translation as confidential meeting data | R 21 |
| SEC-100 | Add no telemetry that sends meeting data externally | C 26 |
| SEC-110 | Use safe file paths and atomic writes | C 26 |

---

## TEST — testing, fixtures, evaluation

| ID | Requirement | Source |
|---|---|---|
| TEST-010 | All behavioural and model-quality tests use real data; the supplied meeting recording is the canonical input | R 22.1; C 17 |
| TEST-020 | Allowed fixtures are: the original real recording, clips from it with provenance, real captured WebSocket events, a replay of those events, human-reviewed annotations, and pure-function input extracted from real captures | R 22.2; C 17.2 |
| TEST-030 | Every derived audio fixture records `source_file_sha256`, `start_ms`, `end_ms`, `clip_sha256`, human-reviewed `labels`, `reviewed_by`, `reviewed_at` | R 22.2 |
| TEST-040 | Never mock ASR/LID/speaker/diarization/translation output in a quality test, ask an LLM to invent expected output, modify expected output to match the implementation, claim server tests passed without real artifacts, silently skip a failing test, or use synthetic audio to trigger edge cases | R 22.3; C 17.1 |
| TEST-050 | Claude Code executes client tests locally when the environment permits, and reports the missing real-device gate when it does not | R 22.4; C 19 |
| TEST-060 | The ten-step server test gate procedure is followed for every server phase | R 22.4; C 20 |
| TEST-070 | No CER, WER, DER, BLEU, chrF or COMET before human references exist | R 22.5; C 18 |
| TEST-080 | An annotation workflow and manifest format exist before reference metrics | R 22.5; C 18 |
| TEST-090 | A representative subset may be annotated before the full recording | R 22.5; C 18 |
| TEST-100 | The subset covers Japanese, Vietnamese, multiple speakers, silence, low volume, clipped speech, hesitation, incomplete sentences, overlap, rapid speaker changes and noise | R 22.5 |
| TEST-110 | Create human-reviewed real cases for the sixteen hallucination categories of Section 22.6; report an absent category as unavailable and never synthesize it | R 22.6; C 18 |
| TEST-120 | Tests are classified into exactly three categories: A real ML/E2E, B labelled protocol/security/persistence negative vectors, C real-capture replay | R 25.15; C 9.10 |
| TEST-130 | Category B fixtures are labelled `protocol_conformance_fixture` or `negative_test_vector`, are never represented as meeting data, and never support an ASR, language, speaker, overlap or translation quality claim | R 25.15; C 9.10 |
| TEST-140 | Replay fixtures include capture provenance, protocol version, configuration hash and SHA-256 | R 25.15 |
| TEST-150 | The real recording is split into non-overlapping development, validation and locked evaluation ranges, balanced across the available acoustic and linguistic conditions | R 25.15; C 9.10 |
| TEST-160 | The locked evaluation set is never used for threshold tuning | R 25.15 |
| TEST-170 | The annotation guide defines filler and hesitation transcription, false starts and self-corrections, punctuation and casing, Japanese number representation and tokenization, Vietnamese orthography, English and technical term handling, unintelligible markers, overlap and speaker-unknown notation, timestamp precision, literal-versus-natural translation principles, and reviewer identity, version and adjudication | R 25.15 |
| TEST-180 | A full-recording soak test over 30 minutes examines memory growth, queues, GPU fragmentation, segment and speaker ID uniqueness, revision integrity, JSONL validity, translation backlog, deadlock and graceful shutdown | R 25.15; C 9.10 |
| TEST-190 | Steady-state GPU benchmarks separate cold start, warm-up, repeated runs and concurrent workload, and report median, P95 and maximum | R 25.15 |
| TEST-200 | Every client test report gives the exact command, environment summary, fixture/capture ID and SHA-256, passed/failed/skipped counts, duration, output artifact paths and known limitations | C 19 |
| TEST-210 | Every source component has tests appropriate to its behaviour; a behavioural case absent from real data is reported as a coverage gap, never synthesized | C 17.3 |
| TEST-220 | Maintain `docs/traceability.md`, mapping every requirement to ADR, implementation, test, fixture, evidence and status | R 25.16; C 9.11 |
| TEST-230 | Never mark a requirement `tested` without evidence | R 25.16; C 9.11 |
| TEST-240 | `accepted_exception` requires explicit user approval, rationale, risk and a tracked follow-up | R 25.16; C 9.11 |
| TEST-250 | A phase cannot be declared complete while mandatory requirement IDs are unmapped or falsely marked tested | R 25.16 |
| TEST-260 | Never treat Whisper, Qwen, SpeechBrain or pyannote output as ground truth | C 18; R 3.2 |
| TEST-270 | Ground-truth annotations record reviewer, timestamp, source hash, clip interval and annotation version | C 18 |
