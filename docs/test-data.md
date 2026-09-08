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
| Real meeting recording | **present and verified** | `data/recordings/meeting_record.wav` |
| Derived clips | none | — |
| Real WebSocket capture | none — no server exists yet | — |
| Human-reviewed annotations | none | — |
| Evaluation split manifest | structure only, no ranges | `tests/manifests/source-recordings.yaml` |

### The recording

Copied to the dev machine on 2026-09-08 and verified by
`python tools/hash_file.py data/recordings/meeting_record.wav`:

| Property | Value |
|---|---|
| SHA-256 | `9f4e36d146c442f926307a8ff0e6841594ca788505c2949eedb5bfe1a8007625` |
| Size | 58,278,700 bytes |
| Container / codec | WAV / `pcm_s16le` |
| Sample rate | **16,000 Hz** |
| Channels | **1 (mono)** |
| Frames | 29,139,328 |
| Duration | 1821.208 s = **30m 21.21s** |

The hash matches the `Get-FileHash` value produced independently on the source
machine, so the bytes survived the copy intact.

Two properties of this file matter to the design, not just to the test plan:

1. **It is already in the canonical wire format.** Mono `pcm_s16le` at 16 kHz is
   exactly what PROT-080 puts on the wire. A server-side replay can send the
   file's sample data verbatim as wire frames, with no resampling and no
   downmix in between. The fixture timeline *is* the canonical sample timeline
   of PROT-140, with sample offset 0 at the first frame and 29,139,328 samples
   in total. Nothing about the fixture can drift from what the server sees.
2. **It is long enough for the mandatory soak test.** Section 25.15 requires a
   full-recording soak test of more than 30 minutes (TEST-180). At 30m 21s this
   file satisfies that by about 21 seconds — enough, but with no room to trim.
   A soak test must therefore use the whole file, not a subset.

What is still missing is everything a human has to supply: which languages
occur where, how many speakers, and where the sixteen hallucination categories
of Section 22.6 appear. None of it may be inferred from a model (TEST-260).

---

## 2. Why the recording lives on the dev machine

Done on 2026-09-08. It is needed here, not only on the user machine, for four
reasons:

1. Clips cannot be cut and hashed without the source file. Every category A
   fixture derives from clips with recorded provenance (TEST-030).
2. The development / validation / locked evaluation split (TEST-150) requires
   listening to the recording to balance the conditions across the three ranges.
3. The annotation workflow (TEST-080) runs on the dev machine; annotating on the
   user machine would mean the annotations live where the code does not.
4. Client-side tests that consume audio — resampler correctness, ring-buffer
   behaviour under real input, gap handling — can then run on the dev machine
   without occupying the user machine.

**Location:** `data/recordings/`, which `.gitignore` excludes. Confirmed:

```text
$ git check-ignore -v data/recordings/meeting_record.wav
.gitignore:47:recordings/       data/recordings/meeting_record.wav
```

**Still to do:** the same recording needs to reach the pod for server-side
category A tests, by copy through the VS Code SSH UI into `/workspace/` — never
through Git. Its hash is re-verified on arrival, so a truncated or mangled
transfer is caught before it silently becomes a bad benchmark.

---

## 3. Recording the provenance

`tools/hash_file.py` reads the format from the file header and computes the
hash, using only the standard library:

```powershell
python tools/hash_file.py data\recordings\meeting_record.wav
python tools/hash_file.py data\recordings\meeting_record.wav --yaml
python tools/hash_file.py data\recordings\meeting_record.wav --expect-sha256 <sha>
```

`--yaml` emits the block for `tests/manifests/source-recordings.yaml`.
`--expect-sha256` exits non-zero on a mismatch, which is how the file is
re-verified after it is copied to another machine.

The recorded fields:

```yaml
filename: required
sha256: required
size_bytes: required
container: required          # wav, m4a, mp3, ...
codec: required
sample_rate_hz: required
channels: required
duration_seconds: required
canonical_16k_samples: required   # length on the PROT-140 timeline
languages_present: as observed by a human, never inferred by a model
speakers_estimated: as observed by a human
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
