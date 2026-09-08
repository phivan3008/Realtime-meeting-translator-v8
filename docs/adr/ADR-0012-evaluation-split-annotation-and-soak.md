# ADR-0012: Evaluation split, locked-set enforcement, annotation format and soak test

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 22
- **Requirement IDs:** TEST-080, TEST-090, TEST-100, TEST-140, TEST-150, TEST-160, TEST-170, TEST-180, TEST-190, TEST-260, TEST-270
- **Decided by:** user on 2026-09-08 (decisions D30 through D33, all as recommended)

## Context and constraints

The project has exactly one real meeting: `meeting_record.wav`, 1821.208 s,
29,139,328 samples at 16 kHz, mono. Every category A claim the project will ever
make rests on it, and Section 25.15 requires that recording to be split into
three non-overlapping ranges whose roles do not mix.

Fixed by the normative text:

- Development tunes thresholds, validation selects architecture and
  configuration, and the locked evaluation set gives the final unbiased
  assessment. The locked set is never used for threshold tuning (25.15).
- The split manifest balances the available Japanese, Vietnamese, speakers,
  silence, low volume, clipping, filler, hesitation, incomplete speech, noise,
  overlap and rapid turn changes (25.15).
- An annotation guide exists before reference metrics, covering eleven listed
  areas including reviewer identity, version and adjudication (25.15).
- A full-recording soak test over 30 minutes is mandatory (25.15).
- Steady-state GPU benchmarks separate cold start, warm-up, repeated runs and
  concurrent workload, reporting median, P95 and maximum (25.15).
- Whisper, Qwen, SpeechBrain and pyannote output is never ground truth (18).

One constraint dominates and is not in the document: **there is only one
meeting.** With a corpus of many recordings, splitting by recording is obvious.
With one, every split decision is a decision about which parts of a single
conversation the project is allowed to learn from.

---

## D30 — How the recording is split

### The problem

All three sets need Japanese and Vietnamese, more than one speaker, and a
spread of acoustic conditions. Otherwise tuning on development and reporting on
locked compares two different things and the locked number means nothing.

### Options considered

#### Option A — three contiguous blocks: first, middle, last

| Dimension | Assessment |
|---|---|
| Correctness | Conversational context and overlap regions stay intact |
| Accuracy | A speaker who only talks in the second half is absent from development entirely, so nothing is tuned for their voice and the locked score is measuring an unseen condition |
| Testability | Simple manifest |
| Future maintenance | Simple, and wrong for a single recording |

#### Option B — interleaved blocks of roughly 60 s, round-robin

| Dimension | Assessment |
|---|---|
| Correctness | All three sets span the whole meeting and share acoustic conditions |
| Accuracy | Balanced |
| Testability | Simple manifest |
| Future maintenance | Block boundaries fall mid-sentence and mid-overlap, and ASR context (Section 13.4) is severed at every seam, which changes the thing being measured |

#### Option C — interleaved blocks whose boundaries sit at long silences

Boundaries chosen from the condition survey rather than from a stopwatch.

| Dimension | Assessment |
|---|---|
| Correctness | Gets Option B's balance without cutting through a sentence or an overlap region |
| Accuracy | Balanced, and each block is a coherent stretch of conversation |
| Testability | Manifest is the same shape; the boundaries are just chosen more carefully |
| Dependency isolation | n/a |
| Future maintenance | Requires the condition survey first — which TEST-110 requires anyway, so it adds no work that was not already owed |

### Decision

**Option C.**

Proposed proportions:

```text
development  45%   threshold and parameter tuning
validation   25%   architecture and configuration selection
locked       30%   final unbiased assessment
```

The locked share is large because it is the number that gets reported, and a
30-minute recording does not permit it to be much smaller while still covering
Japanese, Vietnamese, multiple speakers and the harder acoustic conditions.

The locked set is **assigned first**, before development and validation are
carved from what remains. Assigning it last would mean choosing it from
whatever the tuning work happened not to want.

**The actual boundaries are not fixed here.** They require the condition survey
of `docs/test-data.md`, which requires a human to listen to the recording. The
manifest structure exists now; the ranges are filled in when the survey is done.
Inventing boundaries before knowing where the silences and the language switches
are would produce exactly the unbalanced split this decision exists to avoid.

---

## D31 — Enforcing the locked set

### The problem

"Do not tune on the locked set" is a rule that gets broken by accident, late at
night, by someone who does not notice. The requirement (TEST-160) is absolute;
the enforcement was left open.

### Options considered

| Option | Assessment |
|---|---|
| A — rely on discipline | Free. And a rule with no mechanism is a habit, which is a different thing |
| B — the fixture loader refuses locked-set clips unless an explicit flag is passed, and every access is logged | **Selected** |

### Decision

**Option B.**

- The fixture loader raises unless called with an explicit `--evaluation-run`
  flag (or its programmatic equivalent).
- Every locked-set access appends a line to `docs/evaluation-log.md`: date,
  purpose, commit hash, and which requirement the run was evidence for.

The audit trail is the point, not just the guard. If the locked set turns out to
have been touched thirty times, it is no longer a locked set, and the final
report has to say so rather than quietly presenting a contaminated number.

---

## D32 — Annotation format

### Decision

One JSONL file per annotation session, one label per line, clips referenced by
`clip_sha256`. Each file opens with a header record:

```json
{
  "record_kind": "annotation_header",
  "reviewer": "...",
  "annotated_at": "2026-09-09T...",
  "annotation_version": 1,
  "guide_version": 1,
  "source_file_sha256": "9f4e36d1...7625"
}
```

Annotation files are **immutable**. A correction creates a new session file with
a higher `annotation_version`; it never edits an existing line. TEST-270
requires reviewer, timestamp, source hash, clip interval and annotation version
on every annotation, and immutability is what keeps that record meaningful.

JSONL per session rather than one large document, for two reasons: two reviewers
can annotate simultaneously without touching the same file, and the adjudication
that Section 25.15 requires becomes a comparison of two files rather than a
merge conflict.

The annotation guide itself (TEST-170) is written before the first annotation
and versioned alongside, so `guide_version` in the header identifies the rules
that were in force.

---

## D33 — What the soak test asserts

### The problem

Section 25.15 lists what the soak test must examine but sets no thresholds, and
Section 24 forbids inventing them. The soak test must therefore assert things
that are true or false rather than things that are good or bad.

### Decision

The soak test runs the **whole** recording — 1821.208 s, all 29,139,328 samples,
never a subset, since the file clears the 30-minute requirement by only 21
seconds — and asserts:

| Assertion | Section 25.15 item |
|---|---|
| RSS is not monotonically increasing across three consecutive 10-minute windows | memory growth |
| Every queue stays within its configured bound | queues |
| GPU memory peak per worker is recorded and does not trend upward across runs | GPU fragmentation |
| Every `segment_id`, `utterance_id` and `speaker_id` is unique within the session | ID uniqueness |
| Every revision is monotonic per segment, and no `integrity_conflict` occurred | revision integrity |
| Every line of all three JSONL files parses, and `log_seq` is contiguous | JSONL validity |
| Translation backlog reaches zero before the drain budget expires | translation backlog |
| No worker restarted and no deadlock occurred | liveness |
| `history.final.jsonl` rebuilt from the debug log is **byte-identical** to the one written live | PERS-070 |

The last assertion is the strongest thing the project can prove without ground
truth. It demonstrates that the shared reducer (ADR-0008 D13) is genuinely
deterministic, over thirty minutes of real data, without anyone needing to know
what was said.

The soak test asserts **nothing** about WER, CER, DER, BLEU, chrF or COMET.
Those require human references that do not exist (TEST-070), and a soak test
that quietly reported them would be exactly the fabricated evidence Section 22.3
forbids.

Steady-state GPU benchmarks are separate from the soak test and follow
Section 25.15's own structure: cold start, warm-up, repeated runs and concurrent
workload measured separately, each reporting median, P95 and maximum. Under
ADR-0007's option S2 they run on a clear card, and the artifacts include the
`nvidia-smi` capture proving it.

---

## Consequences

- The condition survey becomes a blocking prerequisite for the split, which
  makes it the first real work after `protocol/` — earlier than its nominal
  Phase 12 home. That is the right order: an unbalanced split poisons every
  number derived from it afterwards.
- The locked-set guard means the fixture loader needs a mode parameter from its
  first version, rather than gaining one later when it is already used
  everywhere.
- `docs/evaluation-log.md` becomes part of the evidence trail and belongs in
  every phase report that touched the locked set.
- Annotation immutability means storage grows with corrections. At the scale of
  one 30-minute meeting this is irrelevant.
- The byte-identical rebuild assertion imposes a real constraint on the writers:
  the live projection and the rebuild must produce identical serialisation,
  including key order and number formatting. That is a constraint worth having,
  and it needs to be honoured from the first line of persistence code rather
  than discovered at the soak test.

## Rollback plan

Every decision here is reversible: the split is a manifest, the guard is a
parameter, the annotation format is versioned, and the soak assertions are
tests. The one thing that is not cheaply reversible is contamination — once the
locked set has been used for tuning, no change of process restores it, which is
the entire reason for D31.

## Evidence required before `accepted`

None for the structural decisions, approved by the user on 2026-09-08.

Blocked on real work, and honestly recorded as such:

- The condition survey of `docs/test-data.md`, which needs a human to listen to
  the recording. All sixteen categories are currently UNKNOWN.
- The split boundaries, which need the survey.
- The annotation guide content, which needs the survey to know what conventions
  actually come up.

## Open questions

- Whether 45/25/30 survives contact with the survey. If Vietnamese turns out to
  occupy a small fraction of the meeting, the proportions have to serve language
  balance rather than convenience, and this ADR gets amended.
- Whether the recording contains enough overlap to evaluate SPK-110 at all. If
  not, that is a coverage gap to report, not to fill (TEST-210).
