# ADR-0014: Client audio pipeline — device selection, resampling, framing

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 5
- **Requirement IDs:** AUD-010, AUD-020, AUD-030, AUD-040, AUD-050, AUD-060, AUD-070, AUD-080, AUD-090, AUD-220, PROT-080, PROT-090
- **Decided by:** user on 2026-09-08 (decisions D35, D36 and D38)

## Context and constraints

Fixed by the normative text:

- Capture output audio with PyAudioWPatch WASAPI loopback (Section 4.1, AUD-030).
- Enumerate loopback-capable devices and let the user select one (AUD-010,
  AUD-020).
- Convert to mono PCM signed 16-bit little-endian at 16 kHz (AUD-050).
- Use a streaming-quality resampler with **persistent state across chunks**
  (AUD-060).
- Add monotonically increasing sequence numbers and monotonic capture timestamps
  (AUD-080).
- Detect callback starvation, device removal, overrun, underrun and resampler
  failure (AUD-090).
- Frame duration is 20 ms or 40 ms, decided by benchmark (Section 9.2, PROT-090).

Added by the user on 2026-09-08: the client must select a correct loopback
device automatically on an arbitrary Windows machine, given only Python and the
project's dependencies (AUD-220).

### What was verified, not assumed

Read on 2026-09-08, per Section 30 and `CLAUDE.md` Section 5:

| Package | Version | cp311 win_amd64 wheel | License | Relevant API |
|---|---|---|---|---|
| PyAudioWPatch | 0.2.12.8 | yes | MIT | `get_default_wasapi_loopback`, `get_loopback_device_info_generator`, `get_wasapi_loopback_analogue_by_dict` |
| soxr | 1.1.0 | yes | **LGPL-2.1-or-later** | `ResampleStream.resample_chunk` |
| samplerate | 0.2.4 | yes | MIT wrapper over libsamplerate (BSD-2-Clause) | `samplerate.Resampler` |

Two facts about WASAPI loopback shape everything below:

1. **`get_default_wasapi_loopback()` exists.** AUD-220 does not require a
   heuristic; the platform answers the question directly.
2. **Loopback devices appear at the end of the device list as virtual *input*
   devices, at the endpoint's shared-mode mix format** — commonly 48000 Hz
   stereo, sometimes 44100. The client therefore always downmixes and always
   resamples. There is no configuration in which the capture format already
   matches the wire.

---

## D35 — Which resampler

### Options considered

#### Option A — `soxr`

| Dimension | Assessment |
|---|---|
| Correctness | `ResampleStream` keeps filter state across chunks, which is exactly AUD-060 |
| Latency | Excellent |
| Accuracy | Best-in-class conversion quality |
| GPU / RAM usage | Small |
| Testability | Fine |
| Dependency isolation | Small wheel, client domain only |
| Future maintenance | **LGPL-2.1-or-later.** `CLAUDE.md` Section 25 requires recording redistribution restrictions, and Phase 13 packages this client for distribution. LGPL is satisfiable for a dynamically linked wheel, but it is an obligation to discharge rather than a non-issue |

#### Option B — `samplerate` (libsamplerate)

| Dimension | Assessment |
|---|---|
| Correctness | `samplerate.Resampler` keeps state across successive chunks |
| Latency | Good |
| Accuracy | libsamplerate's sinc converters are well established for exactly this job |
| GPU / RAM usage | Small |
| Testability | Fine |
| Dependency isolation | MIT wrapper over a BSD-2-Clause core - nothing to discharge at packaging time |
| Future maintenance | Quality setting is a tunable rather than a fixed choice |

#### Option C — `scipy.signal.resample_poly`

| Dimension | Assessment |
|---|---|
| Correctness | **Stateless.** Overlap and filter continuity would have to be managed by hand - writing the hardest part of a resampler ourselves |
| Dependency isolation | Pulls roughly 40 MB of scipy into a client whose only other need for it is this |
| Future maintenance | Rejected on both counts |

#### Option D — `audioop.ratecv` from the standard library

| Dimension | Assessment |
|---|---|
| Correctness | Genuinely stateful, and zero dependencies |
| Accuracy | Linear interpolation. This audio feeds Whisper; conversion artefacts become transcription errors, and Section 14 spends nine defensive layers on exactly that class of problem |
| Future maintenance | **`audioop` was removed in Python 3.13.** A dead end with a known expiry date |

### Decision

**Option B, `samplerate`.**

The license is the deciding factor between A and B, because they are otherwise
close. A client that ships to Windows machines is redistribution, and choosing
MIT-over-BSD instead of LGPL removes an obligation rather than managing one.
`CLAUDE.md` Section 25 requires the inventory to record redistribution
restrictions; the best outcome is having none to record.

Pipeline order:

```text
WASAPI loopback capture (device mix format, e.g. 48000 Hz stereo)
  -> downmix to mono          (average the channels)
  -> resample to 16000 Hz     (samplerate.Resampler, state persists)
  -> convert to s16le
  -> frame and stamp          (sequence, start_sample, capture_monotonic_ns)
```

**Downmix before resampling.** Half as many samples reach the filter, and
averaging channels before band-limiting is correct rather than merely cheaper.

The converter quality (`sinc_medium` versus `sinc_best`) is
`benchmark_required` and is **not chosen here**. It is measured against real
audio at the Phase 2 gate.

A `Resampler` interface sits in front of the backend so that replacing it later
touches one module rather than every call site — the same shape Section 4.3
requires for `NoiseReducer` and Section 16.2 for `SourceSeparator`.

---

## D36 — Default frame duration

### Correcting an emphasis from ADR-0010

ADR-0010 D20 called header overhead "one input to the frame-duration benchmark".
The ratio quoted there is right, but the emphasis was wrong, and the absolute
numbers say why:

```text
audio           16000 samples/s x 2 bytes  = 32,000 B/s
header at 20 ms 28 bytes x 50 frames/s     =  1,400 B/s
```

1.4 kB/s over an SSH tunnel to a pod is not a consideration. The
frame-duration benchmark is really about **latency against per-frame wakeup and
syscall cost**, and bandwidth does not enter into it.

### Decision

Implement both; select by configuration; **default to 20 ms**.

Latency is the product's core value, and the one argument that pointed toward
40 ms turns out to be worth 1.4 kB/s. The benchmark still decides the final
value (PROT-090); this is what runs until it does.

---

## D38 — Device discovery and selection

### Decision

Automatic by default, overridable, and remembered by **name**.

1. **Default:** `get_default_wasapi_loopback()` — the loopback counterpart of
   the system's default speakers. This satisfies AUD-220 with a platform call
   rather than a guess.
2. **Override:** enumerate every loopback device with
   `get_loopback_device_info_generator()` and let the user choose (AUD-010,
   AUD-020).
3. **Persist the choice by device name, never by index.** Indices shift across
   reboots and whenever a device is plugged or unplugged. On the next launch the
   name is resolved again; if it no longer exists the client falls back to the
   default **and says so in the UI**. It never silently records from a different
   device than the one the user chose — writing an index into configuration is
   how an application ends up capturing the wrong endpoint with nobody noticing.
4. **Device removal mid-session** (AUD-090): stop capture, enter `ERROR`, retain
   buffered audio, and do **not** migrate to another device. An automatic
   failover would splice two different acoustic sources into one meeting
   timeline with no marker.

Validation before capture starts (AUD-040): sample format, channel count,
sample rate and device availability are read from the device info and checked
against what the pipeline can convert. A device the client cannot convert from
is refused loudly rather than opened and mishandled.

---

## Discovered after acceptance: an idle endpoint delivers nothing

Found while running the first real capture on the dev machine, 2026-09-08, and
recorded here because it changes what the client has to handle.

**A WASAPI loopback endpoint with nothing playing through it delivers no
callbacks at all. It does not deliver silence.**

Measured:

| Condition | Callbacks in 2-3 s | Bytes |
|---|---|---|
| Idle endpoint, `is_active()` True, stream clock advancing | **0** | 0 |
| Same endpoint with playback started | 106 | 434,176 (= 2.26 s at 48 kHz stereo) |

Two consequences, one immediate and one requiring a decision.

**Immediate:** a self-check that captures nothing must not report success.
`tools/capture_selfcheck.py` now exits non-zero on zero callbacks and says
plainly that the run established nothing about the capture, conversion or
framing paths. The first version of that tool printed "every code path ran" over
a run in which none of them had; that wording was wrong and is gone.

**Requiring a decision:** the canonical timeline advances one sample per
captured sample (Section 25.1). If an endpoint is idle for five minutes and
produces nothing, the timeline does not advance, and every segment after it
carries a `start_sample` five minutes early. That is the same failure PROT-180
forbids for gaps, arriving by a route neither ADR-0009 nor this one anticipated:
ADR-0009 D15 covers filling audio that was *lost*, and this is audio that was
never *produced*.

`client/idle.py` measures the deficit and reports it in canonical samples. It
deliberately does not act on it. Choosing a fill policy is a design gate, and
picking a default here would be exactly the silent architecture decision
`requirements.md` Section 1 forbids.

A meeting application in an active call normally holds the endpoint open
continuously, so the common case may never go idle. "May" is doing real work in
that sentence, and the failure mode is a whole meeting's timestamps being wrong,
so it is not something to leave to the common case.

## Consequences

- The client always downmixes and always resamples, so those paths are exercised
  on every run rather than being rare branches that rot.
- `samplerate`, `numpy` and `PyAudioWPatch` join the client dependency domain.
  `numpy` is needed for buffer manipulation and is what `samplerate` operates
  on. None of them may appear in `protocol/`, which the import-boundary test
  already enforces (OPS-020).
- Storing the device by name means the config file is portable between machines
  in a way an index never was, which matters because the user machine and the
  dev machine are different computers running the same build.
- The real recording is already mono `pcm_s16le` at 16 kHz, so a replay of it
  bypasses the downmix and resample stages entirely. That is convenient for
  server tests and a **coverage gap for client tests**: the conversion path
  cannot be exercised by the one piece of real audio the project owns. It has to
  be tested against real capture on a real device instead, which is the user
  machine's job (TEST-210).

## Rollback plan

The `Resampler` interface makes swapping to `soxr` a one-module change, and the
license question is the only reason to. Device selection is configuration plus
one module. Frame duration is a configuration value with both paths implemented,
so changing it is not a code change at all.

Nothing here becomes expensive to reverse, which is the point of putting the
interface in before the backend.

## Evidence required before `accepted`

None for the structural decisions, approved by the user on 2026-09-08.

Deferred to the Phase 2 gate, measured on the dev machine:

| Value | Measurement |
|---|---|
| Converter quality, `sinc_medium` versus `sinc_best` | conversion cost per chunk and its variance, against real captured audio |
| Frame duration, 20 ms versus 40 ms | end-to-end latency and callback headroom, not bandwidth |
| `ring.capacity_seconds` | ADR-0013 |

## Open questions

- **What the client does about an idle endpoint's uncovered media time.** The
  measurement exists; the policy does not. Options span inserting marked silence
  so the timeline advances, emitting a discontinuity, or treating a long idle
  period as the end of a stream. This needs a design gate before Phase 3.
- Whether conservative level normalisation belongs on the client or the server.
  Section 10 places the preprocessing chain on the server, so the client's job
  ends at format conversion; recorded here because "the client should just
  normalise it" is a tempting shortcut that would put an unlogged transformation
  upstream of every quality measurement.
- What the client does when the device's mix format changes mid-session, which
  Windows permits. Phase 2, alongside device removal.
