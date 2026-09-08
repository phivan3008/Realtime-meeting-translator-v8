# ADR-0010: Binary header, acknowledgement, backpressure, error codes and limits

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 3
- **Requirement IDs:** PROT-080, PROT-090, PROT-100, PROT-110, PROT-120, PROT-130, PROT-330, PROT-340, PROT-020, PROT-030, AUD-100, AUD-190, AUD-200, SEC-070, OPS-920
- **Decided by:** user on 2026-09-08 (decisions D20 through D24, all as recommended)

## Context and constraints

This gate inherits hard requirements from the two ADRs before it, which is why
it came third rather than first:

- ADR-0008 D9: the frame header carries an absolute `start_sample`, so gap size
  is a subtraction rather than an inference.
- ADR-0009 D17: resume compares `resume_from_sample` against what the client
  sent, which is only exact because that absolute offset exists.

Fixed by the normative text:

- Wire audio is `pcm_s16le`, 16000 Hz, mono, little-endian (9.2).
- Frame duration is 20 ms or 40 ms, decided by benchmark (9.2).
- The header carries at minimum: protocol version, stream identifier, sequence
  number, monotonic capture timestamp, sample count, flags (9.2).
- The header schema, byte layout, integer sizes and endianness are written as a
  specification and covered by real-capture serialization tests **before**
  server implementation (9.2).
- Every control event carries `protocol_version`, `event_type`, `session_id`,
  `event_id`, `sent_at_utc`. Unknown major versions are rejected; unknown
  optional fields within the same major version are ignored safely (9.1).
- Backpressure defines maximum queue depth, acknowledgement cadence, client
  buffer limit, server overload response, reconnect retention duration, gap
  reporting and termination behaviour. No component creates an unbounded
  queue (9.4).
- Maximum sizes on frames, events, strings, queues and sessions (21).

---

## D20 — Binary header layout

### Decision

28 bytes, little-endian, every field naturally aligned:

```text
offset  size  field                  type   notes
0       1     protocol_major         u8     = 1
1       1     header_size_bytes      u8     = 28
2       2     stream_ordinal         u16    the N in stream-%04d
4       4     sequence               u32    per stream, from 0
8       8     start_sample           i64    ADR-0008 D9
16      8     capture_monotonic_ns   u64    client monotonic, from stream start
24      2     sample_count           u16    samples in this frame
26      2     flags                  u16
```

The payload follows immediately: `sample_count * 2` bytes of `pcm_s16le`.

`header_size_bytes` at offset 1 costs one byte and buys forward compatibility. A
later minor version that appends a field leaves this value larger, and an older
receiver still skips to the payload correctly instead of reading header bytes as
audio. Without it, any header growth is a major version bump.

Field widths, and why each is not smaller or larger:

| Field | Type | Reasoning |
|---|---|---|
| `sequence` | u32 | 4.29e9 frames at 50 per second is 2.7 years of continuous streaming |
| `start_sample` | i64 | u32 wraps after 2^31 samples, about 37 hours at 16 kHz. A silent wrap on the identity authority is not a survivable failure mode, and 8 bytes is cheaper than the class of bug it prevents |
| `capture_monotonic_ns` | u64 | u32 microseconds covers only 71 minutes, which leaves a 30-minute meeting with thin margin; milliseconds lose the resolution that the Section 23.6 latency measurements need |
| `sample_count` | u16 | 65,535 samples is 4.09 s, far above any frame duration under consideration |
| `stream_ordinal` | u16 | 65,535 discontinuities within one session is already pathological |

Flag bits are allocated in the protocol specification, not here. Bit 0 is
reserved for `synthetic_audio`, marking frames whose payload contains
gap-filling silence inserted under ADR-0009 D15, so that a downstream consumer
of a raw capture can identify inserted samples without consulting the event log.

### Correcting a figure from ADR-0008

ADR-0008 D9 justified the absolute `start_sample` as "1.25% overhead". That
number is the **marginal** cost of the 8-byte field alone against a 20 ms frame,
and it is correct as stated. The complete header is larger:

| Frame duration | Payload | Header | Total overhead |
|---|---|---|---|
| 20 ms (320 samples) | 640 B | 28 B | **4.4%** |
| 40 ms (640 samples) | 1280 B | 28 B | **2.2%** |

This does not change the D9 decision — the alternative was inferring gap
duration, which is wrong rather than merely larger — but the full figure belongs
on the record, and it is one input to the frame-duration benchmark that
Section 9.2 requires.

### Options rejected

**A 20-byte header that sends the capture timestamp only on every Nth frame.**
Section 9.2 lists the monotonic capture timestamp among the fields the header
carries at minimum. Making it intermittent does not satisfy "at minimum", and it
would leave latency measurement with holes exactly where a delayed frame is most
interesting.

**Frame duration** is not decided here. Both 20 ms and 40 ms are implemented and
selected by configuration; 20 ms is the provisional default pending the
Section 9.2 benchmark (PROT-090).

---

## D21 — Acknowledgement cadence

### Options considered

#### Option A — acknowledge every frame

| Dimension | Assessment |
|---|---|
| Correctness | Works |
| Latency | 50 upstream events per second carrying no information a cumulative ack would not |
| GPU / RAM usage | n/a |
| Testability | Noisy captures |
| Future maintenance | Pure overhead |

#### Option B — cumulative acknowledgement, every N frames or every T milliseconds

| Dimension | Assessment |
|---|---|
| Correctness | One number expresses the entire receive state |
| Latency | Configurable; the client learns how much buffer it may release |
| GPU / RAM usage | Bounds client retention |
| Testability | A single field to assert against |
| Future maintenance | Familiar semantics, the same shape as TCP |

#### Option C — acknowledge only on gap or on request

| Dimension | Assessment |
|---|---|
| Correctness | Works |
| Latency | Least traffic |
| GPU / RAM usage | The client never learns when it may release buffer, so it must retain the maximum indefinitely |
| Future maintenance | Rejected on that ground |

### Decision

**Option B.** `audio.ack` carries:

```json
{
  "event_type": "audio.ack",
  "stream_id": "stream-0001",
  "acked_through_sample": 26320000,
  "queue_depth": 3,
  "overload": false
}
```

`acked_through_sample` is the last **contiguously** received sample. When a gap
exists it stops at the near edge of the hole and stays there, so the same field
that drives buffer release is also a gap signal — the client sees its
acknowledgement stall and knows why before the `audio.gap` event arrives.

Cadence — the N frames and T milliseconds — is `benchmark_required`.

---

## D22 — Overflow and overload

Both sides bound their queues (PROT-130), and both obey one principle drawn from
Section 8.4: audio may be discarded, but never **silently**. The word doing the
work in "never silently reorder, duplicate, or discard audio" is *silently*.

### Client ring-buffer overflow

| Option | Assessment |
|---|---|
| A — block the audio callback until space frees | Violates AUD-110 directly. Rejected |
| B — drop the newest frame | Keeps stale audio and discards what is being said now. Backwards |
| C — drop the oldest unsent frame, emit `audio.gap` for the dropped interval, count it in the overflow metric | **Selected.** Prefers recent audio, and the lost interval becomes an explicit gap on the timeline exactly as PROT-180 requires |

Because the dropped range is expressed as an `audio.gap`, it flows into the same
gap-class machinery as a network loss (ADR-0009 D15). A dropout caused by a slow
network and one caused by a busy client are indistinguishable downstream, which
is correct: the transcript's problem is the same either way.

### Server overload

| Option | Assessment |
|---|---|
| A — close the connection | Loses a session to a transient burst |
| B — silently drop frames | Violates Section 25.12. Rejected |
| C — stop advancing `acked_through_sample`, emit `pipeline.warning` with overload state and `capability.updated` marking degradation; terminate with `OVERLOAD_SUSTAINED` only if it persists past a configured threshold | **Selected** |

Option C reuses the acknowledgement as the backpressure channel. A stalled ack
is a signal the client already understands, so no separate flow-control
mechanism is needed, and the degradation is visible in the UI as Section 25.12
requires.

---

## D23 — Error codes and version compatibility

### Error codes

Namespaced strings, not numbers:

```text
PROT_UNSUPPORTED_MAJOR_VERSION
PROT_MALFORMED_HEADER
PROT_FRAME_TOO_LARGE
PROT_EVENT_TOO_LARGE
PROT_INVALID_UTF8
PROT_SCHEMA_VIOLATION
PROT_SEQUENCE_REGRESSION
PROT_SAMPLE_OVERLAP
SESSION_UNKNOWN
SESSION_ALREADY_ACTIVE
SESSION_RESUME_REFUSED
SESSION_SERVER_RESTARTED
OVERLOAD_SUSTAINED
INTERNAL_ERROR
```

Strings because a captured fixture must still explain itself years later.
Section 25.15 requires replay fixtures to carry provenance, and a capture whose
failure branch reads `PROT_MALFORMED_HEADER` needs no lookup table, while one
reading `4102` needs a table that may no longer exist. The cost is a few dozen
bytes on the error path, where bandwidth has stopped mattering.

Codes that terminate the connection map to WebSocket close codes; the mapping
table lives in `docs/protocol.md`, not here, because it will grow.

### Version compatibility

`major.minor`. The major appears in the binary header as a `u8` on every frame,
and in the control event envelope as `"1.0"`.

- Unknown major: reject (Section 9.1, PROT-020).
- Unknown optional field within a known major: ignore safely and log
  (Section 9.1, PROT-030).
- Unknown event type within a known major: ignore safely and log
  (ADR-0005 extends the same rule to event types).
- **Minor versions are additive only.** A minor bump may add an optional field
  or an event type. It may never remove a field, narrow a type, or change the
  meaning of an existing field. Anything that would break a conforming reader of
  an earlier minor requires a major bump.

That last rule is what makes "ignore unknown optional fields" safe rather than
merely permissive: it guarantees an old reader that ignores a new field is still
reading correct data, not a stale interpretation of changed data.

---

## D24 — Size limits

Section 21 and SEC-070 require maximum sizes on frames, events, strings, queues
and sessions. Most of these are safety ceilings rather than tuning parameters,
so they are set by reasoning here rather than deferred to measurement. The one
that is genuinely a tuning parameter is marked as such.

| Limit | Value | Reasoning |
|---|---|---|
| Frame, total bytes | **16 KB** | A 40 ms frame is 1,308 bytes including header. 16 KB is over 12× headroom for experiments while still rejecting a hostile frame |
| `sample_count` per frame | **8,000** (500 ms) | A frame longer than half a second defeats real-time endpointing regardless of what the buffer could hold |
| Control event, total bytes | **64 KB** | The largest realistic event is a `transcript.final` carrying text, speaker candidates and decode evidence, which is far below this |
| Text field, characters | **8,192** | Thirty seconds of continuous speech does not reach 1,000 characters in either Japanese or Vietnamese |
| Identifier field, characters | **64** | `seg-000123` is 10; a uuid4 is 36 |
| Concurrent sessions | **1** | Section 3.1 fixes one active meeting at a time |
| Queue depth, per stage | `benchmark_required` | Genuinely a tuning parameter: it trades latency against burst tolerance and cannot be reasoned to a value |

Enforcement differs by severity, and the distinction matters:

- Exceeding the **frame** or **event** byte limit terminates the connection with
  `PROT_FRAME_TOO_LARGE` or `PROT_EVENT_TOO_LARGE`. A peer sending these is
  either broken or hostile, and the framing can no longer be trusted.
- Exceeding a **field** limit inside an otherwise well-formed event rejects that
  event with `PROT_SCHEMA_VIOLATION` and logs it. The session continues, because
  one oversized field does not compromise the stream.

---

## Consequences

- `protocol/` can now be implemented: the wire format is fully specified and
  every remaining number is either fixed above or explicitly
  `benchmark_required`.
- Section 9.2 requires real-capture serialization tests **before** server
  implementation. No real capture exists yet, so Phase 1 covers the codec with
  **category B** conformance vectors only: hand-computed byte sequences and
  malformed headers are purpose-built inputs, which is category B by definition
  (Section 25.15 B), not category A. PROT-110 therefore stays at
  `blocked_real_fixture` until a Phase 4 server run produces a genuine capture.
  That gap is recorded rather than papered over with an invented capture.
- The acknowledgement doubles as the backpressure channel, which removes a
  mechanism rather than adding one.
- Client overflow and network loss converge on the same `audio.gap`
  representation, so the gap-class policy of ADR-0009 covers both without a
  second code path.
- The `synthetic_audio` flag bit means a raw capture is self-describing about
  inserted silence, without needing the event log alongside it.
- 28 bytes per frame is 4.4% at 20 ms. If the frame-duration benchmark finds
  20 ms and 40 ms otherwise equivalent, header overhead is a reason to prefer
  40 ms — recorded so that input is not forgotten when the benchmark runs.

## Rollback plan

The header layout is the single most expensive decision in the project to
reverse, and it stops being cheap at a specific, identifiable moment: **the
first real WebSocket capture recorded as a category C fixture**, which is
Phase 4. Before that, changing the layout costs a code change and a test update.
After it, every stored fixture encodes the old layout and either has to be
regenerated — requiring another real server run — or kept alongside a legacy
decoder.

Two mitigations are built in: `header_size_bytes` allows growth without a major
bump, and `protocol_major` in every frame means a mixed-version capture is
detectable rather than silently misparsed.

Error codes, size limits and acknowledgement cadence are cheap to change
indefinitely; none is load-bearing for the framing.

## Evidence required before `accepted`

None for the structural decisions, approved by the user on 2026-09-08.

Deferred to measurement:

| Value | Gate |
|---|---|
| Frame duration, 20 ms versus 40 ms | Phase 2, with header overhead as one input |
| Acknowledgement cadence, N frames and T ms | Phase 4 |
| Queue depth per stage | Phase 4 |
| Overload persistence threshold before `OVERLOAD_SUSTAINED` | Phase 4 |
| Client retention window for resume | Phase 2, with the ring buffer |

## Open questions

- Full flag-bit allocation beyond bit 0. `docs/protocol.md`, as bits are needed.
- The WebSocket close-code mapping table. `docs/protocol.md`.
- Whether `audio.ack` should also carry a server-side monotonic receive
  timestamp to support the `capture -> server receive` latency measurement of
  Section 23.6 without a separate event. Phase 4, when there is something to
  measure.
