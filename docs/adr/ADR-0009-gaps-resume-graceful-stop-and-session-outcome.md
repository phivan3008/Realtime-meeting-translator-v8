# ADR-0009: Gap classes, resume, graceful stop, and session outcome

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 18
- **Requirement IDs:** OPS-700, OPS-710, OPS-720, OPS-730, OPS-740, OPS-750, OPS-760, PROT-190, PROT-320, PROT-350, PROT-050, AUD-170, AUD-180, AUD-190, AUD-200, VAD-110, PERS-040, PERS-070
- **Decided by:** user on 2026-09-08 (decisions D15 through D19, all as recommended)

## Context and constraints

This gate covers what happens when audio goes missing, when a connection comes
back, and when a meeting ends. It follows ADR-0008 because every answer here is
expressed on the canonical sample timeline that ADR-0008 fixed, and it precedes
the binary header gate because the header must carry whatever resume needs.

Fixed by the normative text:

- Every missing sequence range creates an `audio.gap` event carrying expected
  and received sequence, a missing sample estimate, and the media interval
  (25.8).
- Three gap duration classes with prescribed behaviour: small preserves the
  interval and attaches gap evidence; medium closes the utterance as
  `truncated_by_gap`, resets VAD and requires a stricter ASR acceptance check;
  large invalidates resume continuity and requires a stream discontinuity (25.8).
- No implementation decodes across an unreported gap. A segment intersecting a
  gap is not translated unless its accepted final passes the gap-aware quality
  policy (25.8).
- The ten-step graceful stop sequence (25.9).
- Server session state is memory-resident; a server restart makes the active
  meeting unrecoverable; the client emits and persists `session_unrecoverable`
  and must not resend the whole meeting to a restarted server (25.10).
- A network gap is never mistaken for a natural speech endpoint (11).

Five mechanism questions were open.

---

## D15 — Whether a small gap is filled with silence

### The problem

Section 25.8 says a small gap should "preserve the interval, **optionally**
insert marked silence for model continuity, and attach gap evidence to
intersecting utterances". The word *optionally* puts the decision here.

The tension is real in both directions. Not filling means a 40 ms dropout in the
middle of a sentence splits the utterance and produces two fragments where there
was one thought. Filling means feeding fabricated audio to Whisper — and
Section 14.2 requires the audio validity gate to flag "all-zero or near-zero
content", so a naive fill trips the project's own defence.

### Options considered

#### Option A — always insert marked silence

| Dimension | Assessment |
|---|---|
| Correctness | The decoder sees a continuous buffer |
| Latency | None |
| Accuracy | Fabricated samples reach the model even across large gaps, where the fabrication exceeds the real audio |
| GPU / RAM usage | n/a |
| Testability | Simple |
| Dependency isolation | n/a |
| Future maintenance | Trips the ASR-260 all-zero check by construction, so either the check gets weakened or every gapped utterance gets flagged. Both are bad |

#### Option B — never insert; close the utterance at any gap

| Dimension | Assessment |
|---|---|
| Correctness | No fabricated audio anywhere |
| Latency | None |
| Accuracy | A 40 ms dropout splits a sentence into two fragments, each with less context than the whole |
| GPU / RAM usage | More utterances, more final decodes |
| Testability | Simple |
| Dependency isolation | n/a |
| Future maintenance | Turns every transport hiccup into a transcript artifact |

#### Option C — insert only inside an open utterance, only below the small threshold, and mark it

| Dimension | Assessment |
|---|---|
| Correctness | Fabrication is bounded to the case Section 25.8 contemplates |
| Latency | None |
| Accuracy | Preserves sentence continuity where the gap is short; refuses to paper over anything longer |
| GPU / RAM usage | n/a |
| Testability | The marking makes the fabricated region visible to every downstream check |
| Dependency isolation | n/a |
| Future maintenance | Requires the quality-evidence code to know which samples are synthetic — a real constraint, but the right one |

### Decision

**Option C**, with three hard constraints that make it safe.

Silence is inserted only when **all** hold:

```text
the gap falls inside an utterance that is currently open
AND gap_samples <= small_gap_threshold_samples
```

The inserted samples are tagged `synthetic` on the media timeline. The three
constraints:

1. **Synthetic samples never count toward quality evidence.** They are excluded
   from `speech_ratio`, RMS, peak, clipping ratio and zero ratio. Counting them
   would dilute the very evidence the hallucination gate needs at exactly the
   moment it matters most — an utterance that already suffered a dropout.
2. **A gap-intersecting segment is not translated** unless its accepted final
   passes the gap-aware quality policy (OPS-760). The translation firewall is
   not relaxed for a segment whose audio was partly invented.
3. **Synthetic audio never updates a speaker profile** (SPK-040), and never
   contributes to a language ID decision.

The inserted sample count is written to the debug log per occurrence, and the
session total appears in `session.summary`. A meeting whose transcript rests
partly on inserted silence says so.

`small_gap_threshold_samples`, `medium_gap_threshold_samples` and the acceptance
tightening for `truncated_by_gap` are all `benchmark_required` and are **not
given values here** (Section 24, OPS-1010).

Gap class behaviour, restating Section 25.8 in terms of this project's units:

| Class | Condition | Utterance | VAD | ASR | Timeline |
|---|---|---|---|---|---|
| small | `gap_samples <= small_threshold` | stays open; silence inserted if open | unchanged | gap evidence attached; stricter acceptance | preserved, region marked synthetic |
| medium | `small < gap_samples <= medium_threshold` | closed as `truncated_by_gap` | reset | stricter acceptance check required | preserved, no insertion |
| large | `gap_samples > medium_threshold` | closed as `truncated_by_gap` | reset | ASR context reset | resume continuity invalidated; new `stream_id` and explicit discontinuity |

---

## D16 — How the client detects a server restart

### The problem

Section 25.10 requires the client to emit and persist `session_unrecoverable`
when the server has restarted, without saying how the client learns that.

### Options considered

#### Option A — infer it from a refused resume

| Dimension | Assessment |
|---|---|
| Correctness | Works when resume is refused |
| — | Fails silently in the case that matters: if a restarted server happens to accept a `session_id` it does not actually have state for, resume "succeeds" against an empty session and the client carries on believing continuity was preserved |

#### Option B — a server epoch echoed on every session response

A UUID generated once at server process start.

| Dimension | Assessment |
|---|---|
| Correctness | Detects a restart even when resume would have succeeded |
| Latency | One field |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | A conformance vector can flip the epoch and assert the client's reaction |
| Dependency isolation | n/a |
| Future maintenance | One more field to keep in the envelope |

#### Option C — both

### Decision

**Option C.** The epoch is primary, the error code is explanatory.

- The server generates `server_epoch` (uuid4) once at process start.
- It appears in `session.started`, `session.resumed` and `session.stopped`.
- The client records the epoch from `session.started`. A **different** epoch in
  any later response means the server restarted.

On detecting a changed epoch, or on a resume refused with a
restart-indicating error code, the client:

1. writes `session_unrecoverable` to `_debug.jsonl` with the old and new epoch;
2. stops sending audio;
3. closes the persistence projection and compacts `history.final.jsonl` from
   what it already has;
4. surfaces the condition in the UI and requires a new session.

It does not retry, does not resend the meeting (OPS-730), and does not attempt
to reconstruct server state.

---

## D17 — What `session.resume` carries, and how the server answers

### The problem

This is the point in the protocol where audio is most easily lost without
anyone noticing. The client knows what it sent; the server knows what it
processed; the two can disagree, and the disagreement is exactly a gap.

### Decision

The client sends:

```json
{
  "event_type": "session.resume",
  "session_id": "...",
  "stream_id": "stream-0001",
  "last_sent_sequence": 41207,
  "last_sent_start_sample": 26372608
}
```

The server answers `session.resumed` with **the first sample it needs**, not the
last sample it holds:

```json
{
  "event_type": "session.resumed",
  "server_epoch": "...",
  "stream_id": "stream-0001",
  "resume_from_sample": 26320000
}
```

Asking for what is needed rather than reporting what is held is the whole point:
it turns every disagreement into a subtraction the client can act on.

The client compares `resume_from_sample` against its retained buffer:

| Case | Action |
|---|---|
| Buffer covers `resume_from_sample` onward | Resend from there. Timeline continuous, no gap |
| Buffer has already dropped the earliest part | Send what remains and emit an explicit `audio.gap` for the missing interval (AUD-190, AUD-200) |
| `resume_from_sample` exceeds anything the client ever sent | **Integrity error.** The server claims audio the client never produced. Refuse the resume, log it, start a new stream. Never silently accept |

A refused resume, or a large-class gap, produces a new `stream_id` and an
explicit discontinuity event (PROT-190). The sample timeline continues across a
resume **only** when the server accepted it.

This works because ADR-0008 put an absolute `start_sample` in the frame header:
the comparison is arithmetic on two integers, not a reconstruction from sequence
numbers and assumed frame sizes.

---

## D18 — How the stop budget is allocated

### The problem

Section 25.9 requires the ADR to define a stop acknowledgement timeout, an ASR
flush timeout, a translation drain timeout and a refinement timeout. It does not
say whether these are independent budgets or slices of one.

### Options considered

#### Option A — an independent budget per stage

| Dimension | Assessment |
|---|---|
| Correctness | Matches the four separately named timeouts in Section 25.9 |
| Latency | Worst-case stop time is the sum, which is bounded and knowable |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | `session.summary` can name **which** stage overran |
| Dependency isolation | n/a |
| Future maintenance | Four values to tune instead of one |

#### Option B — one overall stop budget, allocated greedily

| Dimension | Assessment |
|---|---|
| Correctness | One number |
| Latency | Same bound |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | A slow stop is attributable only to "stop", not to a stage |
| Dependency isolation | n/a |
| Future maintenance | Refinement — the lowest priority work in Section 25.11 — consumes budget that final ASR flush needed, because greedy allocation has no priority |

### Decision

**Option A.** Four independent budgets, each `benchmark_required` and unset here:

```yaml
stop:
  ack_timeout: benchmark_required          # client waits for session.stopped
  asr_flush_timeout: benchmark_required    # final-decode the flushed utterance
  translation_drain_timeout: benchmark_required
  refinement_timeout: benchmark_required   # bounded pyannote/speaker catch-up
```

Each stage that exceeds its budget is recorded by name in `session.summary` and
contributes a warning to the session outcome. Cancellation is explicit:
exceeding a budget cancels that stage's outstanding work rather than letting it
run into the next stage.

**Drain is measured on the wall clock, not the media timeline.** This is a
deliberate exception to ADR-0008's D14, where sealing is a media-timeline
deadline. The reason is simple: at stop the audio has ended, so the sample
counter no longer advances and a media-timeline deadline would never fire.
Recorded explicitly so the difference is not later mistaken for an
inconsistency.

Ordering follows Section 25.11's priority: ASR flush first, then translation
drain, then refinement. Refinement is the stage that gets cut when time runs
out, which is correct — it is the lowest-priority workload class.

---

## D19 — How a session outcome is classified

### Decision

| Outcome | Condition |
|---|---|
| `completed` | Every utterance final-decoded; every ACCEPTED segment either `TRANSLATED` or `TRANSLATION_NOT_APPLICABLE`; every segment sealed; no degraded mode was ever entered; no gap of any class occurred; `history.final.jsonl` written and atomically renamed |
| `completed_with_warnings` | All segments sealed and `history.final.jsonl` written successfully, **but** at least one of: any gap; any `LOW_CONFIDENCE` or `REJECTED` segment; any `TRANSLATION_FAILED`; any degraded mode entered; any drain stage exceeded its budget; any `integrity_conflict`; any synthetic silence inserted |
| `failed` | Final ASR failed (Section 25.12 `final_asr: session_fails`); or the client debug writer failed (PERS-130); or `session_unrecoverable`; or compaction could not be written |

`completed` is deliberately strict. A meeting with a single 40 ms dropout is
`completed_with_warnings`, not `completed`. The distinction is only useful if it
means something, and the summary lists the specific reasons rather than a bare
label.

### The client-side rule Section 25.9 does not state

ADR-0004 made the client the authoritative writer. It follows that the client
must produce a record even when the server says nothing.

If `session.stopped` does not arrive within `ack_timeout`, the client:

1. stops waiting;
2. compacts `history.final.jsonl` from the debug log it already holds;
3. records the outcome as `completed_with_warnings` with reason
   `no_server_stop_ack`.

Losing the meeting record because the server went quiet at the last moment would
defeat the purpose of putting the authoritative log on the client at all.

---

## Consequences

- The binary header gate inherits a second requirement from this ADR: whatever
  carries `last_sent_sequence` and `last_sent_start_sample` must be expressible,
  and the frame header's `start_sample` is what makes the resume comparison
  exact.
- The quality-evidence code gains a real constraint: it must know which samples
  are synthetic. That constraint propagates into preprocessing (VAD-020) and the
  ASR evidence collector (ASR-270), and it is the price of D15.
- `session.summary` becomes substantial: gap counts by class, inserted synthetic
  sample total, per-stage drain overruns, degraded-mode episodes, integrity
  conflicts, and the outcome with its reasons. PROT-050 now has a payload
  shape to specify at the next gate.
- Server restart handling is one comparison of two UUIDs, which is testable with
  a category B vector long before a real server exists.
- Four timeouts and three gap thresholds are added to the configuration surface,
  all `benchmark_required`. Configuration validation refuses to start without
  them (OPS-600), so an unset threshold fails loudly at startup rather than
  quietly at the first dropout.

## Rollback plan

- **D15** is reversible by configuration: setting `small_gap_threshold_samples`
  to zero disables insertion entirely and degrades to Option B. That makes it
  the cheapest of the five to revisit, and it should be the first thing tried if
  gapped utterances turn out to transcribe badly.
- **D16** adds one field to three events. Removing it is a minor version change,
  but there is no reason to: it costs 36 bytes per session.
- **D17** shapes `session.resume` and `session.resumed`. It becomes expensive to
  change once a real capture containing a resume exists as a category C fixture,
  which is Phase 4 at the earliest.
- **D18** and **D19** are internal policy, reversible at any time.

## Evidence required before `accepted`

None for the structural decisions, which the user approved on 2026-09-08.

Seven numbers deliberately unset, each needing real measurement:

| Value | Decided at |
|---|---|
| `small_gap_threshold_samples` | Phase 5 gate, on real recording dropout behaviour |
| `medium_gap_threshold_samples` | Phase 5 gate |
| acceptance tightening for `truncated_by_gap` | Phase 9, with the hallucination policy |
| `stop.ack_timeout` | Phase 4 gate |
| `stop.asr_flush_timeout` | Phase 6 gate |
| `stop.translation_drain_timeout` | Phase 10 gate |
| `stop.refinement_timeout` | Phase 8 gate |

`seal_window_samples` from ADR-0008 has a lower bound of
`stop.translation_drain_timeout` converted to samples, so those two are decided
together at Phase 10.

## Open questions

- The exact `session.summary` payload. Next gate, G3 (Section 26 item 3),
  together with the rest of the event schemas.
- Whether a medium-class gap should also force a language re-resolution, given
  that VAD reset discards the accumulated LID evidence. Phase 7 gate.
- How much unsent audio the client retains for resume (AUD-170), which bounds
  how often the "buffer already dropped the earliest part" branch fires.
  Phase 2 gate, once the ring buffer is sized.
