# Test data provenance

What real data this project has, where it lives, how its provenance is recorded,
and what is still missing.

Requirement IDs: TEST-010, TEST-020, TEST-030, TEST-090, TEST-100, TEST-110,
TEST-140, PERS-120.

Last updated: 2026-09-08 (Phase 0).

---

## 1. Current inventory

| Asset | Status | Location |
|---|---|---|
| Real meeting recording | **not yet on the dev machine** | user machine |
| Derived clips | none | — |
| Real WebSocket capture | none — no server exists yet | — |
| Human-reviewed annotations | none | — |
| Evaluation split manifest | none | — |

Nothing in this project has a real fixture yet. Every category A and category C
test is therefore `blocked_real_fixture`, and that is the honest status rather
than a gap to fill by invention (`requirements.md` Section 22.3).

---

## 2. The recording needs to be copied to the dev machine

**Yes — please copy it.** It is needed here, not only on the user machine, for
four reasons:

1. Clips cannot be cut and hashed without the source file. Every category A
   fixture derives from clips with recorded provenance (TEST-030).
2. The development / validation / locked evaluation split (TEST-150) requires
   listening to the recording to balance the conditions across the three ranges.
3. The annotation workflow (TEST-080) runs on the dev machine; annotating on the
   user machine would mean the annotations live where the code does not.
4. Client-side tests that consume audio — resampler correctness, ring-buffer
   behaviour under real input, gap handling — can then run on the dev machine
   without occupying the user machine.

**Where to put it:**

```text
F:\workspaces\fpt\projects\whalelm\Realtime-meeting-translator-v8\data\recordings\
```

`data/` is excluded from Git by `.gitignore`, so copying the recording there
cannot result in it being committed. Verify after copying:

```powershell
git status --short          # the recording must NOT appear
git check-ignore -v data/recordings/<filename>
```

The second command should print the `.gitignore` rule that excludes it. If it
prints nothing, stop and report it — that would mean the file is committable.

**What is also useful:** the same recording eventually needs to reach the pod
for server-side category A tests. It travels the same way the source does, by
copy through the VS Code SSH UI into `/workspace/` — never through Git.

---

## 3. Recording the provenance (answers "how do I get the SHA-256")

Once the file is in `data/recordings/`, run this on the dev machine. It needs no
dependencies beyond the Python standard library:

```powershell
python tools/hash_file.py data\recordings\<filename>
```

`tools/hash_file.py` does not exist yet — it is written at the start of Phase 1,
along with the clip cutter. Until then, the SHA-256 alone can be obtained with
either of these, which are built into Windows and Git Bash respectively:

```powershell
Get-FileHash -Algorithm SHA256 data\recordings\<filename>
```

```bash
sha256sum data/recordings/<filename>
```

Beyond the hash, the following must be recorded before the file is used as a
fixture source. Most of it comes from reading the file header, which is what
`tools/hash_file.py` will do:

```yaml
filename: required
sha256: required
bytes: required
container: required          # wav, m4a, mp3, ...
codec: required
sample_rate_hz: required
channels: required
duration_seconds: required
recorded_at: if known
languages_present: [ja, vi]  # as observed, not assumed
speakers_estimated: as observed
notes: anything unusual - clipping, level, background noise
```

This block goes into `tests/manifests/source-recordings.yaml`, which **is**
committed. The audio itself never is.

---

## 4. What the recording must be checked for

Section 22.6 lists sixteen categories that need human-reviewed real cases where
they occur. After the recording is available and has been listened to, this
table gets filled in. A category that genuinely does not occur is marked
**unavailable** and reported as a coverage gap — never synthesized (TEST-110).

| Category | Present? | Example interval | Notes |
|---|---|---|---|
| silence | UNKNOWN | | |
| low_volume_speech | UNKNOWN | | |
| clipped_beginning | UNKNOWN | | |
| clipped_end | UNKNOWN | | |
| filler | UNKNOWN | | |
| hesitation | UNKNOWN | | |
| incomplete_sentence | UNKNOWN | | |
| background_noise | UNKNOWN | | |
| laughter | UNKNOWN | | |
| non_speech_sound | UNKNOWN | | |
| short_acknowledgement | UNKNOWN | | |
| overlap | UNKNOWN | | |
| rapid_speaker_change | UNKNOWN | | |
| japanese | UNKNOWN | | |
| vietnamese | UNKNOWN | | |
| uncertain_language | UNKNOWN | | |

Two of these are not optional in a different sense: if the recording turns out
to contain no Vietnamese, or no overlap, then the requirements that depend on
them cannot be tested on real data and that must be surfaced immediately rather
than at Phase 8.

---

## 5. Audio that must NOT be used

The sibling directories on the dev machine
(`Realtime-meeting-translator-v2` and later) contain files named
`loopback_test.wav` and `mic_test.wav`. These are device-check captures from
earlier iterations of this project, not meeting recordings.

They may be used for exactly one thing: verifying that a WAV reader or a
resampler produces the expected bytes, as a pure-function check. They may
**never** be used to make any claim about ASR, language ID, speaker, overlap or
translation quality (TEST-040). They are not in this repository and will not be
copied into it.

---

## 6. Ground truth

There is none, and none may be manufactured.

- Whisper, Qwen, SpeechBrain and pyannote output is never ground truth
  (TEST-260, `CLAUDE.md` Section 18).
- References must be human-reviewed and user-approved.
- A representative subset is annotated before the full recording (TEST-090).
- Each annotation records reviewer, timestamp, source hash, clip interval and
  annotation version (TEST-270).
- The annotation guide (TEST-170) is written before any reference metric is
  computed, and covers filler and hesitation transcription, false starts,
  punctuation and casing, Japanese number representation and tokenization,
  Vietnamese orthography, English and technical terms, unintelligible markers,
  overlap and speaker-unknown notation, timestamp precision, literal-versus-
  natural translation principles, and the adjudication process.

Until that exists, no CER, WER, DER, BLEU, chrF or COMET number will appear in
any report from this project (TEST-070).
