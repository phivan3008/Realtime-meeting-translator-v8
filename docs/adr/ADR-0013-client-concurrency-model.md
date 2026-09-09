# ADR-0013: Client concurrency model and the buffer boundary

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 4
- **Requirement IDs:** AUD-090, AUD-100, AUD-110, AUD-120, AUD-130, AUD-170, PERS-130, TEST-050
- **Decided by:** user on 2026-09-08 (decisions D34 and D37)

## Context and constraints

One clause dominates the client's architecture:

> Never block the audio callback on network I/O, UI rendering, log writing, or
> disk I/O. — Section 8.1, AUD-110

Everything else follows from taking that literally. PyAudio delivers audio on a
thread owned by PortAudio. If that thread stalls, samples are lost at the
source, and no amount of downstream buffering recovers them. So the callback's
work is fixed before any choice is made: copy bytes into a preallocated ring,
advance a counter, return. No allocation, no lock that anything slow can hold,
no I/O.

The real question is what consumes the ring, and where the WebSocket and the UI
live relative to it.

Other constraints:

- Bounded ring buffer with an explicit overflow policy (AUD-100), and audio may
  be discarded but never silently (AUD-200, ADR-0010 D22).
- A bounded amount of unsent audio is retained for resume (AUD-170).
- The debug writer failing stops the session rather than dropping records
  (PERS-130).
- Claude Code runs client tests where the environment permits (TEST-050), and
  category C replay tests are a mandatory evidence category (Section 25.15 C).

---

## D34 — Where the network and the UI live

### Options considered

#### Option A — all Qt: `QWebSocket` in a `QThread` worker

PySide6 ships `QtWebSockets`, so the whole client could run on one Qt event
loop with a worker thread for the socket.

| Dimension | Assessment |
|---|---|
| Correctness | One event loop, no asyncio-to-Qt bridge - a classic source of subtle lifetime bugs |
| Latency | Fine; the socket worker is off the GUI thread |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | **A category C replay test would need a Qt event loop.** Verifying protocol handling would require `pytest-qt` and a GUI toolkit to exercise logic that has nothing to do with a GUI |
| Dependency isolation | No new dependency; QtWebSockets is already there |
| Future maintenance | Fewer moving parts, but the network layer is welded to the UI framework |

#### Option B — hybrid: asyncio for the network, Qt for the UI only

| Dimension | Assessment |
|---|---|
| Correctness | Two loops to keep alive, with an explicit hand-off between them |
| Latency | Same |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | **The network layer is entirely independent of Qt**, so replay tests run headless against a plain asyncio server with no toolkit involved |
| Dependency isolation | Adds `websockets`, a small pure-Python package |
| Future maintenance | The protocol client can be exercised, scripted and replayed without a display |

#### Option C — `qasync`: one asyncio loop driving the Qt event loop

| Dimension | Assessment |
|---|---|
| Correctness | Conceptually tidy |
| Testability | Better than A, worse than B: tests still import Qt |
| Dependency isolation | Adds a dependency whose job is coupling two loop lifetimes together |
| Future maintenance | The coupling is the part that breaks |

### Decision

**Option B.** The deciding argument is testability, and it is not a preference.
`requirements.md` Section 25.15 makes real-capture replay one of three mandatory
evidence categories, and TEST-050 requires those tests to run wherever the
environment permits. A network layer that cannot be exercised without a GUI
toolkit makes the most important client test the most awkward one to run.

```text
PortAudio callback thread
  memcpy into a preallocated ring, advance counters, return
        │
        ▼
  bounded ring buffer  (the only object both sides touch)
        │
        ├──────────────────────────────┐
        ▼                              ▼
  asyncio thread                  Qt GUI thread
  downmix, resample, frame,       timeline projection,
  WebSocket send and receive      device and status display
        │                              ▲
        └── thread-safe queue ─────────┘
            + Qt signal (queued)
```

Three rules make the diagram enforceable:

1. **The callback only copies.** No allocation, no logging, no resampling. The
   resampler is stateful and its cost per chunk is uneven, so it belongs on the
   consumer side where a slow chunk delays a send rather than dropping samples.
2. **Every cross-thread hand-off is a bounded queue.** Nothing shares mutable
   state across threads except the ring, whose contract is one producer and one
   consumer.
3. **The debug writer runs on its own thread behind its own bounded queue**
   (ADR-0011 D29). If that queue saturates, the session stops (PERS-130): an
   authoritative log with holes is not authoritative, and dropping records to
   keep up would be exactly that.

The UI receives projections, never raw events to fold itself. It calls the same
`protocol/projection.py` reducer the history writer and the recovery rebuild use
(ADR-0008 D13), so the timeline the user sees and the file on disk cannot
disagree.

---

## D37 — Ring buffer capacity and retention

### The problem

AUD-100 requires a bounded ring with an explicit overflow policy; AUD-170
requires retaining a bounded amount of unsent audio for resume. The two are the
same buffer viewed from different ends, and the question is what unit bounds it.

### Decision

**Capacity and retention are measured in seconds of media time, not bytes.**

Two reasons, and the second is the one that matters:

- Retention exists to cover a reconnect window, and a reconnect window is a
  duration. Expressing the bound in bytes means restating it every time the
  sample format is discussed.
- Media time is the canonical timeline (Section 25.1). ADR-0009 D17 has the
  client compare `resume_from_sample` against what it still holds; if the buffer
  bound is already in samples, that comparison is a subtraction rather than a
  conversion.

```yaml
ring:
  capacity_seconds: benchmark_required   # candidate 30 s = 960 kB at 16 kHz mono s16le
  overflow_policy: drop_oldest_unsent    # ADR-0010 D22
```

On overflow the client drops the **oldest unsent** frame, emits `audio.gap` for
that interval, and counts it (AUD-100, AUD-190). Dropping the newest would
discard what is being said now in favour of what was said a moment ago, which is
backwards for a live meeting.

Because the dropped range becomes an ordinary `audio.gap`, it flows into the
same gap-class machinery as network loss (ADR-0009 D15). A dropout caused by a
busy client and one caused by a slow tunnel are indistinguishable downstream,
which is correct: the transcript's problem is identical either way. The
`from_client_overflow` flag on the event preserves the cause for diagnosis
without changing policy.

---

## Consequences

- The network client is a plain asyncio object with no Qt import, so a replay
  test constructs it, feeds it a captured exchange and asserts the resulting
  projection - with no display, no event loop integration and no toolkit.
- Two event loops exist, and their startup and shutdown ordering is real work
  that has to be got right. The lifecycle state machine (AUD-120) is where that
  ordering is expressed, which is why it is a first-class module rather than a
  set of flags.
- `websockets` joins the client dependency domain. It is pure Python and pulls
  in nothing else.
- The callback doing nothing but memcpy means a starvation or overrun is
  detectable only by counters the callback increments (AUD-090). Those counters
  are the client's early-warning system and are surfaced in metrics rather than
  kept internal.
- Ring capacity in seconds means one number governs both overflow and resume
  retention, so they cannot drift apart.

## Rollback plan

Moving the network layer to `QWebSocket` later is a rewrite of one module with a
defined interface, and the replay tests written against the asyncio client would
have to be rewritten too - which is precisely the cost this decision avoids
paying. It stays cheap until the first category C replay suite exists, at which
point the tests are the sunk cost rather than the client.

The ring buffer's unit is internal and reversible at any time.

## Evidence required before `accepted`

None for the structural decisions, approved by the user on 2026-09-08.

Deferred to measurement, all at the Phase 2 gate on the dev machine:

| Value | Why it needs measuring |
|---|---|
| `ring.capacity_seconds` | trades memory against how long a reconnect can take before audio is lost |
| queue depth, callback to consumer | trades latency against tolerance for an uneven resample |
| queue depth, debug writer | how much burst the writer absorbs before the session stops |

## Open questions

- Whether the Qt signal carrying projections should batch on an interval rather
  than fire per event. At 50 partials per second the GUI thread will notice.
  Phase 3, when there is a timeline to render.
- How the asyncio thread and the Qt thread agree on shutdown order when the user
  closes the window mid-session. Phase 2, in the lifecycle state machine.
