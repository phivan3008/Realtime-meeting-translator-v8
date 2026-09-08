# Test taxonomy

`requirements.md` Section 25.15 defines three test categories and forbids using
one to make a claim that belongs to another. This file is the operational form
of that rule: where each kind of test lives, what it may claim, and what makes
it invalid.

Requirement IDs: TEST-120, TEST-130, TEST-140, TEST-010, TEST-040.

---

## The three categories

| | A — real ML / end-to-end | B — protocol, security, persistence negative vectors | C — real-capture replay |
|---|---|---|---|
| Lives in | `tests/real_ml/`, `tests/server_scripts/` | `tests/conformance/` | `tests/replay/` |
| Input | the real meeting recording, or clips cut from it with provenance | purpose-built malformed or adversarial input | immutable WebSocket traffic captured from an actual server run |
| Models | actual models | none | none |
| Runs where | H100 pod, behind a server test gate (mostly) | dev machine | dev machine |
| May claim | ASR, language, speaker, overlap, translation quality; end-to-end latency | protocol conformance, input validation, error handling, persistence integrity, security limits | client projection correctness, UI revision handling, regression against a known server exchange |
| May **never** claim | — | **anything about ML quality** | **anything about ML quality** |

The single most important rule, stated in Section 25.15 B and repeated in
`CLAUDE.md` Section 9.10: a purpose-built vector proves a defensive branch
works. It proves nothing about how well Whisper transcribes, how well the
language resolver decides, how well clustering separates speakers, or how well
Qwen translates. Category B files are labelled so that this cannot be forgotten.

---

## Category A — real ML and end-to-end

**Permitted input.** The real meeting recording supplied by the user, or clips
cut from it. Every derived clip carries the provenance block required by
Section 22.2 (TEST-030):

```yaml
source_file_sha256: required
start_ms: required
end_ms: required
clip_sha256: required
labels: human_reviewed
reviewed_by: required
reviewed_at: required
```

**Forbidden input.** Synthetic tones, generated or text-to-speech speech,
invented audio, invented expected transcripts, invented expected translations,
mocked model output, fabricated server responses, fabricated pass evidence
(TEST-040). An expected result is never adjusted merely to make a test pass.

**Ground truth.** The recording currently has none. Until human-reviewed
references exist, no category A test may report CER, WER, DER, BLEU, chrF or
COMET (TEST-070). Whisper, Qwen, SpeechBrain and pyannote output is never
treated as ground truth (TEST-260). What category A tests *can* do before
annotation exists: run the real audio through the real pipeline and assert
structural and operational properties — no crash, no unbounded queue, valid
JSONL, unique segment IDs, monotonic revisions, no decode across a gap, no
translation of a rejected segment. Those are real assertions on real data, and
they do not require knowing what the speaker actually said.

**Execution.** Almost all of category A runs on the H100 pod and therefore
behind a `SERVER_TEST_GATE`. Claude Code writes the script, validates it
statically, commits, pushes, prints the gate, and stops. It does not claim a
GPU test passed (TEST-060, `CLAUDE.md` Section 20).

---

## Category B — protocol, security, persistence negative vectors

**Permitted, and this is the only place purpose-built input is permitted.**
Section 25.15 B explicitly allows malformed headers, invalid UTF-8, oversized
input, stale revisions, duplicate IDs, queue overflow, path traversal, truncated
JSONL, timeouts and similar defensive branches.

**Labelling is mandatory.** Every fixture file carries one of:

```text
protocol_conformance_fixture
negative_test_vector
```

as a field inside the fixture and as part of its filename, so a reader of a test
report can tell at a glance that a passing result says nothing about meeting
quality. A category B fixture is never represented as meeting data (TEST-130).

**Coverage this project needs**, drawn from the normative text:

| Area | Vectors | Requirement |
|---|---|---|
| Binary header | truncated header, bad magic, unknown protocol version, impossible sample count, sequence wrap, oversized frame | PROT-100, PROT-110, PROT-330 |
| Control events | missing envelope field, unknown major version, unknown optional field, invalid UTF-8, oversized string | PROT-010, PROT-020, PROT-030, SEC-070 |
| Revisions | duplicate `event_id`, stale revision, equal revision with different payload, speaker-only revision arriving after newer content | PROT-280, PROT-290, PROT-380, UI-120, UI-130 |
| Gaps | gap event with inconsistent sequence range, decode attempt spanning an unreported gap | PROT-320, OPS-750 |
| Backpressure | queue overflow, client buffer limit exceeded, server overload response | PROT-120, PROT-130 |
| Persistence | truncated tail line, conflicting revision records, invalid path, permission failure, disk full | PERS-090 |
| Security | path traversal in a session-derived filename, oversized event, unvalidated worker message | SEC-060, SEC-070, SEC-080 |
| Isolation | a worker package importing another worker package | OPS-030 |

---

## Category C — real-capture replay

**Permitted input.** WebSocket traffic captured from an actual server run, with
capture provenance, protocol version, configuration hash and SHA-256
(TEST-140). Section 22.2 is explicit that this is not a mock: a replay fixture
reproducing an immutable captured exchange, with recorded provenance, is real
data.

**What it is for.** Client regression. The client projection logic — upsert by
`segment_id`, independent revisions, stale-event rejection, speaker merge
rewriting, translation pending and failure states — can be exercised
deterministically without a GPU once a real capture exists.

**Current status.** No capture exists yet, because no server exists yet. Every
category C test is therefore `blocked_real_fixture` until Phase 4 produces a
real server run. This is a coverage gap to report, not a gap to fill with an
invented capture (TEST-210).

---

## Evaluation split

Section 25.15 requires three non-overlapping time ranges over the real
recording:

```text
development set   threshold and parameter tuning
validation set    architecture and configuration selection
locked evaluation set   final unbiased assessment - never used for tuning
```

The split manifest lives in `tests/manifests/` and must balance the available
Japanese, Vietnamese, speakers, silence, low volume, clipping, filler,
hesitation, incomplete speech, noise, overlap and rapid turn changes
(TEST-150, TEST-160).

The split cannot be created before the recording is on the dev machine and has
been listened to, because a balanced split requires knowing what is in the
audio. This is Phase 12 work with a Phase 1 placeholder.

---

## Mandatory hallucination case categories

Section 22.6 lists sixteen categories that must have human-reviewed real cases
where they occur in the recording (TEST-110):

```text
silence                  low_volume_speech        clipped_beginning
clipped_end              filler                   hesitation
incomplete_sentence      background_noise         laughter
non_speech_sound         short_acknowledgement    overlap
rapid_speaker_change     japanese                 vietnamese
uncertain_language
```

A category that does not occur in the recording is reported as **unavailable**.
It is never synthesized. The availability report lives in `docs/test-data.md`
once the recording has been reviewed.

---

## What each test report must contain

Client test reports (TEST-200, `CLAUDE.md` Section 19):

- exact command
- environment summary
- fixture or capture ID and SHA-256
- passed / failed / skipped counts
- duration
- output artifact paths
- known limitations

Server test reports additionally require the raw stdout and stderr, structured
result files, transcript and translation artifacts, and `nvidia-smi`
observations returned by the user (`CLAUDE.md` Section 20). Until those arrive,
the affected requirements sit at `test_script_ready`, never at `tested`.

---

## Current classification of everything that exists

At the end of Phase 0 there is no source code, so there are no tests. The table
below is the plan, not a claim.

| Phase | Category A | Category B | Category C |
|---|---|---|---|
| 1 protocol | pure-function tests on codec and projection | full conformance vector set | blocked — no capture yet |
| 2 capture client | real device enumeration on the user machine | resampler and ring-buffer vectors | blocked |
| 3 UI and persistence | — | persistence negative vectors | first real captures usable once Phase 4 exists |
| 4 server ingestion | real recording streamed through the gateway | gap and backpressure vectors | capture produced here |
| 5 VAD | real recording segmentation | — | regression |
| 6 ASR | real recording transcription | — | regression |
| 7 language ID | real bilingual audio | — | regression |
| 8 speaker and overlap | real meeting diarization | — | regression |
| 9 hallucination | annotated real edge cases | retry-policy vectors | regression |
| 10 translation | real accepted finals | validator rejection vectors | regression |
| 11 end-to-end | full real session | — | full replay |
| 12 benchmark | soak test over the whole recording | — | — |
