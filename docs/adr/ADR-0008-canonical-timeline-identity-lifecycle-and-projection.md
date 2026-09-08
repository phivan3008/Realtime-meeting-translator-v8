# ADR-0008: Canonical timeline, identity authority, lifecycle and revision projection

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 17
- **Requirement IDs:** PROT-140, PROT-150, PROT-160, PROT-170, PROT-180, PROT-190, PROT-200, PROT-210, PROT-220, PROT-230, PROT-240, PROT-250, PROT-260, PROT-270, PROT-280, PROT-290, PROT-310, PROT-320, PROT-360, PROT-370, PROT-380
- **Decided by:** user on 2026-09-08 (decisions D9 through D14, all as recommended)

## Context and constraints

This is the foundational gate. `requirements.md` Section 25.1, 25.2, 25.4 and
25.5 fix a great deal, and the binary header gate (Section 26 item 3) cannot be
settled before this one, because the header must carry whatever this decision
says it must carry.

Already fixed by the normative text, and therefore not options here:

- The canonical position of every media-derived event is the integer audio
  sample offset at 16 kHz from the start of the session stream (25.1).
- Floating-point seconds are never identity keys or ordering authorities (25.1).
- Client monotonic, server monotonic and UTC times are observability fields and
  never replace sample offsets (25.1).
- Sequence gaps preserve their duration on the timeline; the timeline is never
  compressed to hide missing audio (25.1).
- `stream_id`, `utterance_id`, `segment_id` and `speaker_turn_id` are four
  different objects (25.2).
- The session orchestrator is the sole authority creating `utterance_id` and
  `segment_id`; Silero is the live endpointing authority; Whisper local IDs are
  never public `segment_id` values; pyannote turns never open or close ASR
  utterances in MVP (25.2).
- The eight-state segment lifecycle (25.4).
- Four independent revisions plus two echo fields; equal revision with different
  payload is an integrity conflict, not last-write-wins; replay is idempotent by
  `event_id`; stale results are diagnostic only (25.5).
- The debug log is the event source of truth; UI and compacted history are
  deterministic projections (25.5).

What remained open were six mechanism questions that Section 25 states the
*requirement* for without stating the *implementation*.

---

## D9 — Where the server gets the sample offset

### The problem

Section 25.1 requires that a sequence gap preserve its duration on the media
timeline. If the binary header carries only `sequence_number` and
`sample_count`, the server reconstructs position by accumulation. When frames
7 through 9 are lost, the server cannot know how many samples went missing until
frame 10 arrives — and even then only by assuming every frame carried an
identical sample count.

That assumption breaks in ordinary operation: the final frame before a stop is
short by nature, and a device glitch or a resampler boundary can produce a short
frame at any time. A gap measured under a false assumption is a guess, and
PROT-320 requires `audio.gap` to carry a missing sample estimate.

### Options considered

#### Option A — `sequence` and `sample_count` only; the server accumulates

| Dimension | Assessment |
|---|---|
| Correctness | Gap duration is inferred, and wrong whenever frame sizes vary |
| Latency | n/a |
| Accuracy | Timeline drift accumulates silently across a long meeting |
| GPU / RAM usage | n/a |
| Testability | A replay fixture cannot verify a single frame in isolation |
| Dependency isolation | n/a |
| Future maintenance | Smallest header; largest class of subtle bug |

#### Option B — the header also carries an absolute `start_sample`

| Dimension | Assessment |
|---|---|
| Correctness | Gap size is `next.start_sample - (prev.start_sample + prev.sample_count)`, a measurement rather than an inference |
| Latency | None |
| Accuracy | The timeline is self-describing: one frame is enough to locate itself |
| GPU / RAM usage | n/a |
| Testability | Every frame in a replay fixture is independently checkable |
| Dependency isolation | n/a |
| Future maintenance | 8 bytes per frame. At 20 ms frames that is 400 B/s against 32,000 B/s of audio — **1.25% overhead** |

#### Option C — send `start_sample` periodically, every N frames

| Dimension | Assessment |
|---|---|
| Correctness | Between resync points it degrades to Option A |
| Latency | n/a |
| Accuracy | Gap detection is delayed to the next resync frame |
| GPU / RAM usage | n/a |
| Testability | Adds a resync state machine to test |
| Dependency isolation | n/a |
| Future maintenance | Saves a fraction of 1.25% and buys real complexity |

### Decision

**Option B.** The binary frame header carries an absolute `start_sample`.

```yaml
start_sample:
  type: signed 64-bit integer
  unit: samples at 16000 Hz
  origin: 0 at the first sample of the session stream
  meaning: the sample offset of the first sample in this frame
```

Signed 64-bit, not 32-bit: a 32-bit counter overflows after 2^31 samples, which
at 16 kHz is about 37 hours. That is beyond one meeting but not beyond a bug or
a soak test, and the failure mode of a silent wrap on the identity authority is
severe. 64 bits is 18 quintillion samples.

In JSON, sample offsets are emitted as plain integers. The IEEE-754 safe integer
limit of 2^53 corresponds to roughly 17,800 years at 16 kHz, so no JSON encoder
or JavaScript consumer can lose precision on a real value. The protocol
specification records this as the reason no string encoding is used.

Derived rules:

```text
frame_end_sample   = start_sample + sample_count
gap_samples        = next.start_sample - previous.frame_end_sample
gap_ms             = floor(gap_samples * 1000 / 16000)
```

`gap_samples == 0` is contiguous. `gap_samples > 0` raises `audio.gap` with a
measured, not estimated, size. `gap_samples < 0` is overlapping or reordered
audio: an integrity error, never silently merged.

---

## D10 — When `segment_id` is allocated

### The problem

The Section 13.2 example payload shows `transcript.partial` already carrying a
`segment_id`, so a segment cannot wait for the final decode to exist. But
Section 25.2 also says one utterance may yield zero, one or many text segments,
which means the count is not known while partials are still arriving.

### Options considered

#### Option A — allocate one segment eagerly when the utterance opens

Partials revise that segment. If the final decode requires a split, the original
segment keeps its ID and narrows, and the additional parts get new IDs.

| Dimension | Assessment |
|---|---|
| Correctness | Matches Section 25.2: a new `segment_id` appears only when a split cannot be represented by a revision |
| Latency | The client can upsert from the very first partial |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | One code path from first partial to seal |
| Dependency isolation | n/a |
| Future maintenance | An utterance that yields no text burns one integer. Harmless |

#### Option B — allocate lazily at the first partial that carries text

| Dimension | Assessment |
|---|---|
| Correctness | Avoids burning an ID |
| Latency | Same |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Two shapes to test: utterance with a segment and utterance without |
| Dependency isolation | n/a |
| Future maintenance | VAD events and text events fall out of phase, and the client must handle an utterance that has no segment to attach a state to |

#### Option C — key partials by `utterance_id`; create segments only at final

| Dimension | Assessment |
|---|---|
| Correctness | Contradicts the Section 13.2 payload example |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | n/a |
| Dependency isolation | n/a |
| Future maintenance | Rejected on conformance grounds |

### Decision

**Option A.** A segment is created when the orchestrator opens an utterance, and
enters `CREATED`. Burning an integer on a silent utterance costs nothing;
complexity in the projection costs a great deal.

Identifier formats, following the examples in Sections 13.2, 25.2 and 25.7:

```text
session_id       uuid4, generated by the client, echoed by the server
stream_id        stream-%04d   allocated per session, incremented on discontinuity
utterance_id     utt-%06d      allocated per session, monotonic, never reused
segment_id       seg-%06d      allocated per session, monotonic, never reused
speaker_turn_id  spk-turn-%06d allocated per session, monotonic, never reused
speaker_id       speaker-%d    allocated per session, monotonic, never reused
```

All are session-scoped and monotonic. On exhausting the zero-padded width the
allocator **widens the field, it does not wrap**: Section 25.7 forbids reuse
within a session, and a wrapped identifier is a reused identifier. The formats
are therefore parsed as prefix plus decimal integer, never as fixed-width
strings.

---

## D11 — How a split is represented

### The problem

Section 25.6 permits the orchestrator to split one utterance into multiple text
segments aligned to speaker changes, preserving lineage. Section 25.2 requires
explicit lineage fields. Neither says which part keeps the original identifier.

The question matters because the client has already been rendering the original
segment for the whole duration of the utterance's partials.

### Options considered

#### Option A — the original keeps its ID and narrows; later parts get new IDs

| Dimension | Assessment |
|---|---|
| Correctness | Lineage is one-directional and easy to reason about |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | The client's existing row survives; only new rows appear |
| Dependency isolation | n/a |
| Future maintenance | Slight asymmetry between the surviving part and the new parts |

#### Option B — every part gets a new ID; the original is superseded

| Dimension | Assessment |
|---|---|
| Correctness | Symmetric and arguably cleaner as a model |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Every split makes a visible row disappear and two appear |
| Dependency isolation | n/a |
| Future maintenance | The flicker is exactly what Section 8.3 and CLAUDE.md Section 10 are trying to prevent elsewhere; reintroducing it at split time is inconsistent |

### Decision

**Option A.** On a split, the original `segment_id` survives, its span narrows to
the first part, and each additional part is created with a new `segment_id`
carrying lineage:

```json
{
  "utterance_id": "utt-000012",
  "segment_id": "seg-000035",
  "parent_segment_ids": ["seg-000034"],
  "operation": "split",
  "start_sample": 1296000,
  "end_sample": 1320000
}
```

The surviving part emits a normal content revision with its narrowed span, not a
lineage event: from the client's point of view its row simply got shorter.

Merge, if a later phase needs it, uses the mirror rule: the target keeps its ID,
`parent_segment_ids` lists the sources, `operation` is `"merge"`, and the source
segments become aliases. Cross-utterance merging remains out of scope in MVP per
Section 25.2.

Allowed `operation` values:

```text
create   a new segment for a newly opened utterance
revise   an existing segment's content, language, speaker or translation changed
split    a new segment carved from an existing one, with parent lineage
merge    an existing segment absorbed one or more others
seal     no further change is permitted
```

---

## D12 — What an integrity conflict does

### The problem

Section 25.5 says events with equal revision but different payload "shall be
treated as an integrity conflict, not last-write-wins". It does not say what
treating it as one entails operationally.

### Options considered

#### Option A — stop the session

| Dimension | Assessment |
|---|---|
| Correctness | Maximally safe |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | Easy to assert |
| Dependency isolation | n/a |
| Future maintenance | Loses an entire meeting to one protocol bug. Disproportionate |

#### Option B — keep the existing payload, refuse the new one, and make it loud

| Dimension | Assessment |
|---|---|
| Correctness | No silent overwrite; both payloads survive in the debug log |
| Latency | n/a |
| Accuracy | The projection stays on the first-seen payload, which is at least self-consistent |
| GPU / RAM usage | n/a |
| Testability | A conformance vector can assert the refusal, the log record and the emitted error |
| Dependency isolation | n/a |
| Future maintenance | The meeting survives; the bug is visible in three places |

#### Option C — log it and move on silently

| Dimension | Assessment |
|---|---|
| Correctness | Violates Section 25.12: no component may silently substitute lower-quality behaviour |
| — | Rejected |

### Decision

**Option B.** On detecting equal revision with unequal payload:

1. The projection **refuses** the incoming event. The existing state is kept
   unchanged. Last-write-wins is never applied.
2. An `integrity_conflict` record is appended to the debug log containing both
   payloads, both `event_id`s, and the segment's current revision vector.
3. `pipeline.error` is emitted.
4. The session capability state is marked degraded and the condition is shown in
   the UI, per Section 25.12 and PROT-060.

This is deliberately different from a debug-writer failure, which Section 25.12
requires to stop the session. That case destroys the source of truth; this one
is a single contradictory event against an intact log.

---

## D13 — Where the projection logic lives

### The problem

Four different places need to turn an event stream into current state: the
client UI, the live `_history.jsonl` projection, the compaction that produces
`_history.final.jsonl`, and the recovery command that rebuilds history from the
debug log (PERS-070). Section 25.5 calls the latter two "deterministic
projections", which is only true if they agree.

### Options considered

#### Option A — one pure reducer in `protocol/`, shared by all four

| Dimension | Assessment |
|---|---|
| Correctness | "Deterministic projection" becomes a property that can be tested, not asserted |
| Latency | Pure function, no I/O |
| Accuracy | n/a |
| GPU / RAM usage | Negligible |
| Testability | Category A pure-function tests over a real capture; category B vectors for every rejection branch |
| Dependency isolation | No ML dependency, so it belongs in `protocol/` under ADR-0001 |
| Future maintenance | One place to change a revision rule |

#### Option B — separate implementations per consumer

| Dimension | Assessment |
|---|---|
| Correctness | The implementations drift |
| — | The resulting bug class — UI correct, persisted history wrong — is discovered after the meeting is over, which is the worst possible time. Rejected |

### Decision

**Option A.** A pure reducer in `protocol/`, with this contract:

```python
def apply(
    state: SegmentProjection | None,
    event: Event,
) -> ProjectionResult:
    """Fold one event into a segment's projection.

    Pure: no I/O, no clock, no randomness. The same (state, event) pair
    always produces the same result, which is what makes the UI, the live
    history, the compaction and the recovery rebuild agree by construction.
    """
```

`ProjectionResult` carries either the new state or a refusal reason:

```text
applied              the event advanced the projection
duplicate            same event_id already folded in - idempotent no-op (PROT-280)
stale                revision older than current - diagnostic only (PROT-380)
integrity_conflict   equal revision, different payload (PROT-290, D12)
not_applicable       event does not target this segment, or the segment is sealed
```

Refusal is a **return value, not an exception**. Section 25.5 requires stale
results to be persisted as diagnostic events, which a thrown exception makes
awkward and a crash makes impossible.

### Projection rules, per event class

Preconditions and effects for every revision-bearing event (PROT-310). `cr`,
`lr`, `sr`, `tr` abbreviate the four revisions.

| Incoming | Precondition | Effect |
|---|---|---|
| `transcript.partial` | segment not sealed; `content_revision > cr` | replace stable/unstable text; `cr := incoming` |
| `transcript.final` | not sealed; `cr_in > cr`; status in ACCEPTED, LOW_CONFIDENCE, REJECTED | set text and status; `cr := cr_in`; **invalidate translation** if `tr` echoed an older `cr` |
| `transcript.revised` | not sealed; `cr_in > cr` | as `transcript.final`; invalidate translation |
| `language.updated` | not sealed; `lr_in > lr` | set `language_id` and `language_status`; `lr := lr_in`; **invalidate translation**; a change after acceptance requires a new accepted final or explicit revalidation (25.4) |
| `speaker.updated` (assign) | not sealed; `sr_in > sr` | set primary speaker, status, candidates; `sr := sr_in`; **translation untouched** (25.5) |
| `speaker.updated` (merge) | not sealed | rewrite affected current views to the canonical ID; `sr := sr_in`; content and translation revisions **unchanged** (25.7, SPK-210) |
| `translation.started` | not sealed; segment ACCEPTED | set translation status `pending` |
| `translation.final` | not sealed; `translated_from_content_revision == cr` **and** `translated_from_language_revision == lr` | set translation; `tr := tr_in`. If either echo is older, refuse as `stale` |
| `translation.failed` | not sealed | record failure state; transcript untouched (17.4, UI-100) |
| any, when sealed | — | `not_applicable`; recorded as a diagnostic |
| any, `event_id` already seen | — | `duplicate` |
| any, revision equal but payload differs | — | `integrity_conflict` (D12) |

The invalidation rule is one sentence: **a content or language revision
invalidates a translation whose echoed source revisions no longer match; a
speaker revision never does.**

---

## D14 — When a segment is sealed

### The problem

Section 25.4 says a segment is sealed after "the configurable retrospective
refinement window" or during graceful finalization, without saying what the
window is measured against.

### Options considered

#### Option A — a deadline on the media timeline

Seal when `current_sample - segment.end_sample > seal_window_samples`, and
translation has reached a terminal state.

| Dimension | Assessment |
|---|---|
| Correctness | Measured in the same unit as everything else in Section 25.1 |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | Bounds how many segments stay mutable, which bounds memory |
| Testability | **Replay reproduces the exact seal points**, because the trigger is a function of the capture, not of the machine running it |
| Dependency isolation | n/a |
| Future maintenance | The window value must be at least pyannote's maximum retrospective span |

#### Option B — a wall-clock deadline

| Dimension | Assessment |
|---|---|
| Correctness | Works live |
| Testability | A replay run on a faster machine seals at different points, so category C tests stop being deterministic. Rejected |

#### Option C — seal only at graceful stop

| Dimension | Assessment |
|---|---|
| Correctness | Simplest |
| GPU / RAM usage | Every segment of a 30-minute meeting stays mutable to the end |
| Future maintenance | Retrospective refinement gets no deadline at all. Rejected |

### Decision

**Option A.** A segment seals when both hold:

```text
current_sample - segment.end_sample > seal_window_samples
AND translation status is terminal (TRANSLATED, TRANSLATION_FAILED,
                                    or TRANSLATION_NOT_APPLICABLE)
```

and unconditionally during graceful finalization, after the drain policy of
Section 25.9 has run.

`seal_window_samples` is `benchmark_required` and is **not given a value here**
(Section 24, OPS-1010). Two constraints bound it, and both come from later
phases:

- it must be at least pyannote's maximum retrospective span, decided at the
  Phase 8 gate (SPK-240);
- it must be at least the translation drain timeout, decided at the Phase 1
  session-lifecycle gate (OPS-710).

Phase 1 therefore implements the mechanism and leaves the value unset, with
configuration validation refusing to start if it is not supplied.

A translation failure does not prevent sealing; the sealed segment records the
failure state (25.4).

---

## Consequences

- The binary header gate (Section 26 item 3) inherits a hard requirement: an
  8-byte absolute `start_sample` per frame. That gate now decides layout and
  packing, not whether the field exists.
- Gap handling becomes arithmetic rather than inference, which is what lets
  OPS-750 ("never decode across an unreported gap") be enforced by an assertion
  instead of a heuristic.
- The client renders one row per segment from the first partial to the seal,
  through splits and speaker merges, without a row ever disappearing. That is
  the behavioural form of UI-140.
- The projection reducer becomes the most heavily tested piece of code in the
  repository, and the natural home for the category B vector set: duplicate
  `event_id`, stale revision, equal-revision-different-payload, post-seal
  arrival, translation echoing an outdated source revision.
- Sealing on the media timeline makes replay tests reproduce seal points exactly,
  which in turn makes the compacted `history.final.jsonl` byte-comparable across
  runs of the same capture. That is a strong regression signal and it comes for
  free from choosing the right clock.
- One integer per silent utterance is wasted. Nobody will ever notice.

## Rollback plan

- **D9** is the expensive one to reverse: removing `start_sample` from the header
  is a wire-format change and a protocol major version bump. It stops being
  cheap the moment the first real WebSocket capture is recorded as a category C
  fixture, because that fixture encodes the header layout. That is Phase 4.
- **D10, D11, D13, D14** are internal to the orchestrator and the reducer. They
  are reversible by changing code and re-running the replay suite, with no wire
  change, indefinitely.
- **D12** is a behavioural change in one branch of the reducer, reversible at any
  time.

## Evidence required before `accepted`

None for the structural decisions; they are settled by the normative text plus
the reasoning above, and were approved by the user on 2026-09-08.

Two numbers deliberately left unset, each with its own gate:

- `seal_window_samples` — Phase 1 session-lifecycle gate for its lower bound
  from translation drain, Phase 8 for its lower bound from pyannote span.
- frame duration, and therefore the exact per-second cost of the 8-byte
  `start_sample` — Phase 1 protocol gate, benchmarked per Section 9.2.

## Open questions

- Exact byte layout, field order and packing of the binary header. Phase 1
  protocol gate (Section 26 item 3).
- Gap duration classes and their thresholds. Next gate, G18
  (Section 26 item 18).
- Whether `speaker_turn_id` is emitted on the wire in MVP or stays internal to
  the diarization worker. Phase 8; the identifier is reserved here either way.
- Whether a sealed segment can be reopened by the administrative correction
  workflow. Section 25.4 places that out of scope for MVP; recorded so the
  reducer's `not_applicable` branch is understood as deliberate.
