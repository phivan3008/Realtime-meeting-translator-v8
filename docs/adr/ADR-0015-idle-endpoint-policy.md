# ADR-0015: What the client does when the capture endpoint goes idle

- **Status:** accepted
- **Date:** 2026-09-09
- **Design gate:** arising from Phase 2; extends `requirements.md` Section 26 items 5 and 18
- **Requirement IDs:** AUD-090, AUD-190, PROT-180, PROT-190, PROT-320, ASR-200, OPS-740
- **Decided by:** user on 2026-09-09 (decision D39)

## Context and constraints

Discovered while running the first real capture, and confirmed on two machines:

**A WASAPI loopback endpoint with nothing playing through it delivers no
callbacks at all. It does not deliver silence.**

| Machine | Condition | Callbacks | Bytes |
|---|---|---|---|
| dev, NVIDIA HDMI endpoint | idle, `is_active()` True, clock advancing | **0** | 0 |
| dev, same endpoint | playback started | 106 | 434,176 (2.26 s at 48 kHz stereo) |
| user, VMware Virtual Audio (DevTap) | real meeting audio playing, 10 s | 468 | 479,232 frames (9.98 s) |

The canonical timeline advances one sample per captured sample (Section 25.1).
An endpoint idle for five minutes therefore produces nothing, the timeline does
not advance, and **every segment after it carries a `start_sample` five minutes
early**. That is the failure PROT-180 forbids — "the timeline shall not be
compressed to hide missing audio" — arriving by a route neither ADR-0009 nor
ADR-0014 anticipated.

The distinction that makes this its own decision: ADR-0009 D15 fills audio that
was **lost**. This is audio that was never **produced**. The two look identical
in a sample counter and are entirely different acoustically.

Constraints that bound any answer:

- Section 8.4 and AUD-200: audio is never silently discarded, reordered or
  duplicated.
- Section 25.8 gap classes exist for *lost* audio and carry `truncated_by_gap`
  plus a stricter ASR acceptance check, because lost audio may have cut through
  speech.
- Section 13.4 and ASR-200 already require resetting ASR context after a long
  silence.
- Section 25.1: a non-resumed stream gets a new `stream_id` and an explicit
  discontinuity event.

---

## D39 — Idle endpoint policy

### Options considered

#### Option A — the client fabricates and sends silence frames

Fill the idle span with zero samples, flagged `SYNTHETIC_AUDIO`, and send them.

| Dimension | Assessment |
|---|---|
| Correctness | The timeline stays aligned with wall time by construction |
| Latency | n/a |
| Accuracy | Feeds the model audio nobody produced |
| GPU / RAM usage | Five minutes idle is 4,800,000 samples — **9.6 MB of zeros** pushed through the tunnel to represent nothing |
| Testability | Simple |
| Dependency isolation | n/a |
| Future maintenance | Bandwidth cost scales with how quiet the meeting is, which is backwards |

#### Option B — the client reports the span; the server advances its own timeline

An event carries the idle interval. The server advances the canonical timeline
and decides locally whether to materialise silence for model continuity.

| Dimension | Assessment |
|---|---|
| Correctness | The timeline advances without anything being fabricated on the wire |
| Latency | One small event per idle span |
| Accuracy | The fill decision sits with the component that owns VAD and ASR context, which is the only place with the information to make it |
| GPU / RAM usage | Zero bytes for an arbitrarily long idle period |
| Testability | A single event to assert, with an explicit span |
| Dependency isolation | Keeps audio-shaped decisions on the server side |
| Future maintenance | One more event type, recorded as an ADR-0005 extension |

#### Option C — duration classes mirroring Section 25.8

| Dimension | Assessment |
|---|---|
| Correctness | One mental model covers both lost and absent audio |
| Future maintenance | Adds thresholds to tune, but they parallel ones that already exist |

### Decision

**Option B for the mechanism, with Option C's duration classes as the policy.**

The client never fabricates audio. It emits an event describing what it did not
receive, and the server advances the timeline.

#### `audio.idle` is a new event, not a reuse of `audio.gap`

This is the part that matters most, and reusing `audio.gap` would have been the
easy mistake.

A gap means audio was **lost**, which is why Section 25.8 marks medium and large
gaps `truncated_by_gap` and requires a stricter ASR acceptance check: lost audio
may have cut through the middle of a word. An idle endpoint means there was
**definitively no sound**. Conflating them would label a stretch of clean
silence as truncated and tighten the ASR gate for no reason — degrading the
transcript in response to nothing having happened.

```json
{
  "event_type": "audio.idle",
  "stream_id": "stream-0001",
  "start_sample": 26320000,
  "end_sample": 31120000,
  "idle_samples": 4800000,
  "idle_class": "long",
  "detected_by": "client_wall_clock"
}
```

`detected_by` is explicit because this is the one place the client uses wall
time to make a media-timeline statement. Section 25.1 keeps the sample offset as
the sole media authority, and a sample counter cannot answer "how much time
passed while nothing arrived" — a counter that never advances looks the same
after one second and after an hour. Naming the mechanism in the event keeps that
exception visible rather than buried.

#### Duration classes

Thresholds are `benchmark_required` and are **not given values here**
(Section 24, OPS-1010).

| Class | Timeline | Utterance | ASR context | Stream |
|---|---|---|---|---|
| **short** | advances | VAD closes it normally — there genuinely was silence, so VAD ending the utterance is correct rather than a workaround | unchanged | unchanged |
| **long** | advances | closed | **reset** | unchanged |
| **very long** | advances | closed | reset | new `stream_id` plus an explicit discontinuity (PROT-190) |

The `long` row costs nothing to justify: Section 13.4 and ASR-200 already
require resetting context after a long silence. This class simply supplies one
more way for a long silence to occur, and it lands on machinery that exists.

#### What the client does

1. `client/idle.py` measures the deficit in canonical samples, using monotonic
   time for the one question sample counting cannot answer.
2. On detecting an idle span the client emits `audio.idle` and advances its own
   `FrameBuilder` cursor by the same count, so the next real frame carries the
   correct `start_sample`.
3. It fabricates nothing, sends no zero-filled frames, and updates no speaker or
   language state from an idle span.

#### What the server does

Advance the canonical timeline by `idle_samples`, apply the class policy, and
decide independently whether to materialise silence for model continuity. That
decision belongs to Phase 4 and is not made here.

---

## Consequences

- A quiet meeting costs no bandwidth, which is the opposite of Option A's
  behaviour and the right way round.
- `audio.idle` joins `session.summary` and `capability.updated` as an ADR-0005
  extension: an event the Section 9.3 minimum does not contain, forced by
  behaviour the requirements did not anticipate, recorded rather than smuggled
  in.
- The client now has two distinct reasons to advance the timeline without
  emitting audio — a ring overflow (`audio.gap`) and an idle endpoint
  (`audio.idle`) — and they must never be conflated, because one means speech
  may have been cut and the other means there was no speech.
- Wall-clock time enters the client's media reasoning at exactly one point,
  which is named in the event that carries it.
- If a meeting application holds its endpoint open continuously, none of this
  ever fires. That is the expected common case; the policy exists because the
  failure mode when it does fire is every subsequent timestamp being wrong.

## Rollback plan

`audio.idle` is additive: a receiver that ignores it still gets correct frames,
merely with a timeline that jumps. Removing the event would mean returning to
that jump, so the rollback is really "choose Option A instead", which is a
client-side change plus a wire-volume change and no schema removal.

The duration thresholds are configuration and reversible at any time.

## Evidence required before `accepted`

Structural decision, approved by the user on 2026-09-09.

Deferred to measurement:

| Value | Gate |
|---|---|
| `idle_threshold_s` — quiet before an endpoint counts as idle rather than between callbacks | Phase 2, against a real device |
| short / long / very-long class boundaries | Phase 5, alongside the VAD parameters they interact with |
| whether the server materialises silence for the `short` class | Phase 4 |

## Open questions

- Whether an idle span should also suppress the `metrics.snapshot` cadence, or
  whether a flat-lining metric stream is itself the useful signal. Phase 4.
- Whether a `very long` idle span should end the session rather than start a new
  stream. Left open deliberately: it depends on how a meeting application behaves
  when a call ends, which is an observation nobody has made yet.
