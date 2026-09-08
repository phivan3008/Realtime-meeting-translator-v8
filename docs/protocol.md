# WebSocket protocol specification

Protocol version **1.0**.

This is the contract between the Windows client and the GPU server. It is
normative for implementation; the *reasoning* behind each choice lives in the
ADRs it cites, and the requirements it satisfies live in
`docs/requirement-ids.md`.

Implemented by `protocol/`, which carries no ML dependency so that the client,
the gateway and the orchestrator all use the same code (ADR-0001, OPS-020).

---

## 1. Transport

```text
Windows client
  --> WebSocket over an SSH tunnel
      localhost:<client_port> -> localhost:8760
  --> gateway
```

- The gateway binds `127.0.0.1` only (SEC-010). No public endpoint is exposed
  (SEC-020).
- Binary WebSocket frames carry audio. Text frames carry JSON control events.
- One active session at a time (Section 3.1, `MAX_CONCURRENT_SESSIONS = 1`).

Port allocation, from ADR-0007:

| Port | Use |
|---|---|
| 8760 | gateway WebSocket — the only port the tunnel forwards |
| 8761+ | worker IPC |
| 8000 | this project's vLLM |
| ~~8001~~, ~~3000~~ | another project's services on the pod; permanently avoided |

---

## 2. Versioning

`major.minor`. The major appears in every binary frame header as a `u8`, and in
every control event envelope as the string `"1.0"`.

| Situation | Behaviour | Requirement |
|---|---|---|
| Unknown **major** | Reject with `PROT_UNSUPPORTED_MAJOR_VERSION`, close | PROT-020 |
| Unknown optional **field**, known major | Ignore safely, log | PROT-030 |
| Unknown **event type**, known major | Ignore safely, log | ADR-0005 |

**Minor versions are additive only.** A minor bump may add an optional field or
an event type. It may never remove a field, narrow a type, or change the meaning
of an existing field. That guarantee is what makes "ignore what you do not
recognise" safe rather than merely permissive: an old reader that ignores a new
field is still reading correct data, not a stale interpretation of changed data.

Version strings are parsed strictly. `"1.0.0"`, `"01.0"` and `" 1.0"` are
rejected — a version that round-trips differently is a version two
implementations can disagree about.

---

## 3. Canonical timeline

**The canonical position of every media-derived event is the integer audio
sample offset at 16 kHz from the start of the session stream** (Section 25.1,
PROT-140).

```yaml
canonical_sample_rate_hz: 16000
session_start_sample: 0
time_conversion:
  milliseconds: floor(sample_offset * 1000 / 16000)
```

Rules:

- Floating-point seconds are never identity keys or ordering authorities
  (PROT-160).
- Client monotonic, server monotonic and UTC times are observability fields.
  They never replace a sample offset (PROT-170).
- A sequence gap preserves its duration on the timeline. The timeline is never
  compressed to hide missing audio (PROT-180).
- Sample offsets are signed 64-bit on the wire and plain integers in JSON. The
  IEEE-754 safe integer limit of 2^53 is about 17,800 years at 16 kHz, so no
  real value can lose precision in a JSON consumer (ADR-0010 D20).
- All intervals are **half-open**: `[start_sample, end_sample)`. Back-to-back
  frames therefore do not overlap.

Gap arithmetic:

```text
frame_end_sample = start_sample + sample_count
gap_samples      = next.start_sample - previous.frame_end_sample
```

`gap_samples == 0` is contiguous. `> 0` raises `audio.gap` with a **measured**
size. `< 0` is overlapping or reordered audio: `PROT_SAMPLE_OVERLAP`, fatal,
never silently merged.

---

## 4. Binary audio frame

### 4.1 Wire format

```yaml
encoding: pcm_s16le
sample_rate_hz: 16000
channels: 1
byte_order: little_endian
frame_duration_ms: 20 or 40   # config-selected; benchmark pending (PROT-090)
```

### 4.2 Header — 28 bytes, little-endian

ADR-0010 D20. Every field is naturally aligned.

| Offset | Size | Field | Type | Notes |
|---|---|---|---|---|
| 0 | 1 | `protocol_major` | `u8` | `1` |
| 1 | 1 | `header_size_bytes` | `u8` | `28` for this version |
| 2 | 2 | `stream_ordinal` | `u16` | the N in `stream-%04d` |
| 4 | 4 | `sequence` | `u32` | per stream, from 0 |
| 8 | 8 | `start_sample` | `i64` | canonical offset of this frame's first sample |
| 16 | 8 | `capture_monotonic_ns` | `u64` | client monotonic, from stream start |
| 24 | 2 | `sample_count` | `u16` | samples in this frame |
| 26 | 2 | `flags` | `u16` | see below |

Payload follows immediately: `sample_count * 2` bytes.

**`header_size_bytes` is read from the frame, not assumed.** A receiver skips
`header_size_bytes` to reach the payload, so a frame written by a newer minor
version with appended header fields still parses correctly instead of feeding
header bytes into the decoder as audio.

**`start_sample` is absolute, not accumulated.** This is the decision ADR-0008
D9 exists for: with a sequence number alone, gap size can only be inferred by
assuming every lost frame carried the same sample count, and that assumption is
wrong exactly where it matters — the short final frame before a stop, and any
device glitch.

Overhead: 28 bytes against a 640-byte payload is 4.2% at 20 ms, 2.1% at 40 ms.
That figure is an input to the frame-duration benchmark.

### 4.3 Flags

| Bit | Name | Meaning |
|---|---|---|
| 0 | `SYNTHETIC_AUDIO` | payload contains gap-filling silence inserted under ADR-0009 D15 |
| 1-15 | reserved | must be zero |

Bit 0 makes a raw capture self-describing about inserted samples, without
needing the event log beside it — which matters because a category C fixture can
outlive the session that produced it.

---

## 5. Control event envelope

Every control event carries (Section 9.1, PROT-010):

```json
{
  "protocol_version": "1.0",
  "event_type": "transcript.final",
  "session_id": "3f2504e0-4f89-41d3-9a0c-0305e82c3301",
  "event_id": "8c6d1f2a-2b41-4c7e-9f3a-1d2e3f4a5b6c",
  "sent_at_utc": "2026-09-08T07:41:22.481Z"
}
```

- `session_id` is a lowercase uuid4, validated on receipt because it reaches a
  filesystem path (PERS-100, SEC-060).
- `event_id` is a uuid4 and is the idempotency key. Replaying an `event_id`
  applies nothing (PROT-280).

---

## 6. Event catalogue

The 21 types of Section 9.3, plus the two extensions ADR-0005 records as forced
by Sections 25.9 and 25.12.

### 6.1 Session lifecycle

| Event | Direction | Key fields |
|---|---|---|
| `session.start` | client → server | `client_version`, `requested_frame_ms` |
| `session.started` | server → client | `server_epoch`, `stream_id`, `accepted_frame_ms` |
| `session.resume` | client → server | `stream_id`, `last_sent_sequence`, `last_sent_start_sample` |
| `session.resumed` | server → client | `server_epoch`, `stream_id`, `resume_from_sample` |
| `session.stop` | client → server | `final_sequence`, `final_end_sample` |
| `session.summary` | server → client | outcome, reasons, counts, gaps, drain overruns |
| `session.stopped` | server → client | `server_epoch`, `outcome` |

**`server_epoch`** is a uuid4 generated once at server process start. The client
records it from `session.started`; a different value in any later response means
the server restarted and the meeting is unrecoverable (Section 25.10, ADR-0009
D16). The client then writes `session_unrecoverable` to its debug log, compacts
what it holds, and requires a new session. It does not retry and does not resend
the meeting (OPS-730).

`session_unrecoverable` is a **client-local record, not a wire event**
(ADR-0005, PROT-070). It exists precisely because the server is gone; a server
able to send it would not be in the state it describes.

### 6.2 Audio transport

| Event | Direction | Key fields |
|---|---|---|
| `audio.ack` | server → client | `acked_through_sample`, `queue_depth`, `overload` |
| `audio.gap` | server → client | `expected_sequence`, `received_sequence`, `start_sample`, `end_sample`, `missing_samples`, `gap_class`, `from_client_overflow` |

### 6.3 Segmentation and transcription

| Event | Direction | Key fields |
|---|---|---|
| `vad.speech_started` | server → client | `utterance_id`, `start_sample` |
| `vad.speech_ended` | server → client | `utterance_id`, span, `truncated_by_gap` |
| `transcript.partial` | server → client | `segment_id`, `content_revision`, `stable_text`, `unstable_text`, `asr_language_mode` |
| `transcript.final` | server → client | `segment_id`, `content_revision`, `status`, `text`, evidence, overlap |
| `transcript.revised` | server → client | same shape as `transcript.final` |

`transcript.final.status` is one of `accepted`, `low_confidence`, `rejected`.
`asr_final_candidate` is internal (Section 25.4) and never reaches the wire as
an outcome — the schema rejects it.

`asr_language_mode` on a partial records what language basis was used before a
decision existed: `auto`, `session_hint`, `speaker_hint`, `provisional_lid`,
`confirmed_lid`. Without it, a pre-decision partial's text cannot be interpreted
later (Section 25.3).

### 6.4 Language, speaker, translation

| Event | Direction | Key fields |
|---|---|---|
| `language.updated` | server → client | `language_revision`, `language_id`, `language_status`, evidence |
| `speaker.updated` | server → client | `operation`, `speaker_revision`, plus assign or merge fields |
| `translation.started` | server → client | `source_language`, `target_language` |
| `translation.final` | server → client | `translation_revision`, both source-revision echoes, `text` |
| `translation.failed` | server → client | `translation_revision`, echoes, `reason`, `retryable` |

A merge carries `from_speaker_ids` and `to_speaker_id`; the schema rejects a
merge with no target, and a merge whose target is one of its own sources.

### 6.5 Diagnostics

| Event | Direction | Key fields |
|---|---|---|
| `capability.updated` | server → client | the **whole** capability state |
| `pipeline.warning` | server → client | `code`, `detail`, optional `segment_id` |
| `pipeline.error` | server → client | `code`, `detail`, `fatal` |
| `metrics.snapshot` | server → client | measured values only |

`capability.updated` carries complete state rather than a delta, so a client
that missed a warning still renders degradation correctly. A warning is an
occurrence; capability is state (Section 25.12).

---

## 7. Identity

Four kinds of identifier, never conflated (Section 25.2, PROT-210):

```text
stream_id        stream-%04d       a continuous audio stream within a session
utterance_id     utt-%06d          opened and closed by the orchestrator from VAD
segment_id       seg-%06d          the text/timeline unit displayed and translated
speaker_turn_id  spk-turn-%06d     a diarization interval
speaker_id       speaker-N         anonymous, session-scoped
```

- The **orchestrator** is the sole authority creating `utterance_id` and
  `segment_id` (PROT-220).
- Whisper subsegment and token-timestamp local IDs never become public
  `segment_id` values (PROT-230).
- Pyannote local labels such as `SPEAKER_00` are worker-local and are mapped to
  canonical IDs by temporal overlap, embedding similarity, duration and
  confidence — **never by label name or order** (SPK-170).
- Allocators are monotonic and **widen rather than wrap**. A wrapped identifier
  is a reused identifier, and Section 25.7 forbids reuse within a session.
- `speaker-unknown` and `speaker-multiple` are reserved. `speaker-multiple` is a
  presentation label only and never names an embedding cluster (SPK-150).

A segment is allocated when its utterance opens, so the client can upsert from
the first partial (ADR-0008 D10). An utterance that yields no text burns one
integer, which is cheaper than a projection that must handle a segment-less
utterance.

### Lineage

`operation` is one of `create`, `revise`, `split`, `merge`, `seal`.

On a **split**, the original segment keeps its id and narrows to the first part;
each additional part gets a new id carrying `parent_segment_ids` (ADR-0008 D11).
The surviving part emits an ordinary content revision — from the client's point
of view its row simply got shorter, and no rendered row ever disappears.

---

## 8. Lifecycle and revisions

### 8.1 Lifecycle

```text
CREATED
-> PARTIAL
-> ASR_FINAL_CANDIDATE
-> ACCEPTED | LOW_CONFIDENCE | REJECTED
-> TRANSLATION_PENDING | TRANSLATION_NOT_APPLICABLE
-> TRANSLATED | TRANSLATION_FAILED
-> SEALED
```

### 8.2 The revision vector

Every current segment projection carries six numbers (Section 25.5, PROT-260):

```json
{
  "content_revision": 5,
  "language_revision": 2,
  "speaker_revision": 3,
  "translation_revision": 1,
  "translated_from_content_revision": 5,
  "translated_from_language_revision": 2
}
```

**The dependency rule in one sentence:** a content or language revision
invalidates a translation whose echoed source revisions no longer match; a
speaker revision never does.

### 8.3 Projection rules

Preconditions and effects for every revision-bearing event (PROT-310).
Implemented once, in `protocol/projection.py`, and used by the UI, the live
history projection, the compaction and the recovery rebuild — because
Section 25.5 calls these deterministic projections, and that is only true if
they are the same code (ADR-0008 D13).

| Incoming | Precondition | Effect |
|---|---|---|
| `transcript.partial` | not sealed; `content_revision >` current | replace stable/unstable text |
| `transcript.final` | not sealed; `content_revision >` current; status is a final outcome | set text and status; **invalidate translation** |
| `transcript.revised` | as `transcript.final` | as `transcript.final` |
| `language.updated` | not sealed; `language_revision >` current | set language; **invalidate translation** |
| `speaker.updated` assign | not sealed; `speaker_revision >` current | set speaker; **translation untouched** |
| `speaker.updated` merge | not sealed | rewrite affected views to the canonical id; content and translation revisions **unchanged** |
| `translation.started` | not sealed; segment `ACCEPTED` | translation status `pending` |
| `translation.final` | not sealed; **both echoes match current revisions** | set translation |
| `translation.failed` | not sealed; content echo matches | record failure; transcript untouched |

### 8.4 Refusals

The reducer returns a reason rather than raising, because Section 25.5 requires
stale results to be persisted as diagnostics:

| Reason | When |
|---|---|
| `duplicate` | `event_id` already applied, or identical payload at the same revision |
| `stale` | revision behind current, or a translation echoing superseded revisions |
| `integrity_conflict` | **equal revision, different payload** |
| `not_applicable` | segment sealed, unknown, or the event carries no segment state |

**`integrity_conflict` is never last-write-wins** (Section 25.5, ADR-0008 D12).
The incoming event is refused, existing state is kept, both payloads are written
to the debug log, `pipeline.error` is emitted, and session capability is marked
degraded. It does **not** stop the session — that is reserved for losing the
source of truth, not for one contradictory event against an intact log.

### 8.5 Sealing

A segment seals when both hold:

```text
current_sample - segment.end_sample > seal_window_samples
AND translation status is terminal
```

and unconditionally during graceful finalization. The deadline is on the **media
timeline**, not the wall clock, so a replay reproduces seal points exactly and
the compacted history is byte-comparable across runs of the same capture
(ADR-0008 D14).

`seal_window_samples` is `benchmark_required`. Its lower bounds come from the
translation drain timeout and pyannote's retrospective span.

---

## 9. Gaps

Every missing sequence range produces `audio.gap` with a measured size
(PROT-320). Behaviour by duration class (Section 25.8, ADR-0009 D15):

| Class | Utterance | VAD | ASR | Timeline |
|---|---|---|---|---|
| small | stays open; silence inserted if open | unchanged | gap evidence attached; stricter acceptance | preserved, region marked synthetic |
| medium | closed as `truncated_by_gap` | reset | stricter acceptance | preserved, no insertion |
| large | closed as `truncated_by_gap` | reset | context reset | resume continuity invalidated; new `stream_id` and explicit discontinuity |

Thresholds are `benchmark_required`.

**Silence is inserted only inside an already-open utterance, only below the
small threshold, and only marked.** The inserted samples are excluded from
`speech_ratio`, RMS, peak, clipping ratio and zero ratio — diluting quality
evidence at the exact moment an utterance suffered a dropout is the opposite of
what the hallucination gate needs. They never update a speaker profile and never
feed a language decision, and the session total appears in `session.summary`.

No implementation decodes across an unreported gap (OPS-750). A gap-intersecting
segment is not translated unless its accepted final passes the gap-aware quality
policy (OPS-760).

---

## 10. Resume

ADR-0009 D17. The client sends what it knows it sent; the server answers with
**the first sample it needs**, not the last it holds.

```json
{ "event_type": "session.resume",
  "stream_id": "stream-0001",
  "last_sent_sequence": 41207,
  "last_sent_start_sample": 26372608 }
```

```json
{ "event_type": "session.resumed",
  "server_epoch": "...",
  "stream_id": "stream-0001",
  "resume_from_sample": 26320000 }
```

| Client's buffer versus `resume_from_sample` | Action |
|---|---|
| covers it | resend from there; timeline continuous, no gap |
| already dropped the earliest part | send what remains, emit an explicit `audio.gap` (AUD-190) |
| `resume_from_sample` exceeds anything ever sent | **integrity error**; refuse, log, start a new stream (AUD-200) |

Asking for what is needed rather than reporting what is held turns every
disagreement into a subtraction the client can act on. This is exact only
because the frame header carries an absolute `start_sample`.

A refused resume, or a large-class gap, produces a new `stream_id` and an
explicit discontinuity. The sample timeline continues across a resume **only**
when the server accepted it (PROT-190).

---

## 11. Acknowledgement and backpressure

`audio.ack` carries `acked_through_sample`: the last **contiguously** received
sample. When a gap exists it stops at the near edge and stays there, so the
field that drives client buffer release is also a gap signal — the client sees
its acknowledgement stall before the `audio.gap` arrives.

Cadence is every N frames or every T milliseconds, whichever comes first. Both
are `benchmark_required`.

**Audio may be discarded; never silently.** The word doing the work in
Section 8.4 is *silently*.

| Condition | Response |
|---|---|
| Client ring buffer full | drop the **oldest unsent** frame, emit `audio.gap` for the interval, count it (AUD-100) |
| Server overload | stop advancing `acked_through_sample`; emit `pipeline.warning` and `capability.updated` degraded |
| Overload sustained past threshold | close with `OVERLOAD_SUSTAINED` |

A stalled acknowledgement is backpressure the client already understands, so no
separate flow-control mechanism exists. Client overflow and network loss produce
the same `audio.gap` representation, and downstream policy is identical —
correctly, because the transcript's problem is the same either way.

No component creates an unbounded queue (PROT-130).

---

## 12. Error codes

Namespaced strings, so a captured fixture still explains itself years later.

| Code | Fatal | Meaning |
|---|---|---|
| `PROT_UNSUPPORTED_MAJOR_VERSION` | yes | major this build cannot speak |
| `PROT_MALFORMED_HEADER` | yes | truncated or self-inconsistent header |
| `PROT_FRAME_TOO_LARGE` | yes | over 16 KiB, or `sample_count` over 8000 |
| `PROT_EVENT_TOO_LARGE` | yes | control event over 64 KiB |
| `PROT_INVALID_UTF8` | yes | text frame is not valid UTF-8 |
| `PROT_SCHEMA_VIOLATION` | no | well-formed frame, invalid content |
| `PROT_SEQUENCE_REGRESSION` | yes | sequence went backwards |
| `PROT_SAMPLE_OVERLAP` | yes | a frame overlaps the previous one |
| `SESSION_UNKNOWN` | no | no such session |
| `SESSION_ALREADY_ACTIVE` | no | one meeting at a time |
| `SESSION_RESUME_REFUSED` | no | resume declined; start a new stream |
| `SESSION_SERVER_RESTARTED` | yes | epoch changed; meeting unrecoverable |
| `OVERLOAD_SUSTAINED` | yes | overload past the configured threshold |
| `INTERNAL_ERROR` | no | unexpected server fault |

**Fatal** means the connection closes: the framing or the session can no longer
be trusted. Everything else rejects one event and the session continues.

WebSocket close codes:

| Error class | Close code |
|---|---|
| Version or framing (`PROT_UNSUPPORTED_MAJOR_VERSION`, `PROT_MALFORMED_HEADER`, `PROT_INVALID_UTF8`) | 1002 protocol error |
| Size (`PROT_FRAME_TOO_LARGE`, `PROT_EVENT_TOO_LARGE`) | 1009 message too big |
| Timeline integrity (`PROT_SEQUENCE_REGRESSION`, `PROT_SAMPLE_OVERLAP`) | 1002 protocol error |
| `SESSION_SERVER_RESTARTED` | 1001 going away |
| `OVERLOAD_SUSTAINED` | 1013 try again later |
| `INTERNAL_ERROR` when fatal | 1011 internal error |

---

## 13. Limits

ADR-0010 D24, satisfying SEC-070.

| Limit | Value | Enforcement |
|---|---|---|
| Frame, total bytes | 16 KiB | close, `PROT_FRAME_TOO_LARGE` |
| `sample_count` per frame | 8,000 (500 ms) | close, `PROT_FRAME_TOO_LARGE` |
| Control event, total bytes | 64 KiB | close, `PROT_EVENT_TOO_LARGE` |
| Text field, characters | 8,192 | reject event, `PROT_SCHEMA_VIOLATION` |
| Identifier field, characters | 64 | reject event, `PROT_SCHEMA_VIOLATION` |
| Concurrent sessions | 1 | `SESSION_ALREADY_ACTIVE` |
| Queue depth, per stage | `benchmark_required` | overload response |

The severity split is deliberate: a peer exceeding a *frame* or *event* byte
limit is broken or hostile and its framing cannot be trusted, while one
oversized *field* inside a well-formed event compromises nothing beyond that
event.

---

## 14. Conformance status

| Area | Covered by | Status |
|---|---|---|
| Header layout, per-field offsets | `tests/conformance/test_frame_codec.py` | tested |
| Malformed headers, size limits, version rejection | same | tested |
| Timeline arithmetic, gap measurement, overlap detection | `tests/conformance/test_timeline.py` | tested |
| Revision model, conflicts, idempotency, firewall, sealing | `tests/conformance/test_projection.py` | tested |
| Dependency isolation | `tests/conformance/test_import_boundaries.py` | tested |
| **Real-capture serialization** (Section 9.2) | — | **blocked** until Phase 4 produces a real capture |

The last row is a real gap, recorded rather than filled. Section 9.2 asks for
serialization tests against a real capture before server implementation, and no
real capture can exist until a real server runs. The category B vectors prove
the codec is self-consistent; they cannot prove it matches something that has
never been produced.
