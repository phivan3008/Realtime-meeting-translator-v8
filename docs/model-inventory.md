# Third-party model inventory, licenses and access conditions

Required by `CLAUDE.md` Section 25 ("third-party package/model inventory,
licenses, access conditions, and redistribution restrictions" and "immutable
model revision manifest").

Every row was read from the model card on the date shown. Nothing here is
assumed. Where a field says `PENDING`, it is not yet known and must not be
guessed.

Last verified: 2026-09-08 (Phase 0, second pass after inspecting the pod cache).

---

## 1. Inventory

| Model | Repository | License | Gated | Cached on pod | Role | Fixed by |
|---|---|---|---|---|---|---|
| Whisper large-v3 | `openai/whisper-large-v3` | Apache-2.0 | No | ❌ | ASR, partial and final | `requirements.md` 4.2 |
| Whisper large-v3, CT2 build | `Systran/faster-whisper-large-v3` | Apache-2.0 (derived) | No | ✅ | candidate production ASR artifact — Phase 6 gate | see §3.1 |
| VoxLingua107 ECAPA | `speechbrain/lang-id-voxlingua107-ecapa` | Apache-2.0 | No | ✅ | Primary acoustic language ID | `requirements.md` 4.2 |
| ECAPA VoxCeleb | `speechbrain/spkrec-ecapa-voxceleb` | Apache-2.0 | No | ✅ | Speaker embeddings for online clustering | `requirements.md` 4.2 |
| pyannote segmentation | `pyannote/segmentation-3.0` | MIT | **Yes — accepted 2026-09-08** | ❌ | Overlap detection, speaker-change evidence | `requirements.md` 4.2, 15.3 |
| pyannote diarization | `pyannote/speaker-diarization-3.1` | MIT | **Yes — accepted 2026-09-08** | ❌ | Retrospective diarization refinement | `requirements.md` 4.2, 15.3 |
| Silero VAD | `snakers4/silero-vad` (GitHub) | PENDING — verify at Phase 5 gate | No | ❌ | Live endpointing authority, CPU | `requirements.md` 4.2 |
| SepFormer WSJ0-2Mix | `speechbrain/sepformer-wsj02mix` | PENDING — verify only if the experiment is enabled | PENDING | ❌ | Experimental source separation, **disabled by default** | `requirements.md` 4.2, 16.2 |
| Qwen3.5-9B | `Qwen/Qwen3.5-9B` | Apache-2.0 | No | ✅ | Final-only translation via vLLM | `requirements.md` 4.2 |

All gated models require a Hugging Face token at load time. A token is already
configured on the pod (`/workspace/cache/token`); it has not been read, printed
or committed (SEC-030).

---

## 2. Gating status

**Resolved 2026-09-08.** `pyannote/segmentation-3.0` and
`pyannote/speaker-diarization-3.1` are gated behind "You have to accept the
conditions to access its files and content". The user accepted the conditions
for both on 2026-09-08 with the account whose token the pod uses.

That acceptance is not verified by this project until the models are actually
downloaded at the Phase 8 gate — a browser acceptance and a working token load
are two different things, and only the download proves both. Until then the
pyannote requirements stay `planned`, not `implemented`.

Everything else in the inventory is ungated Apache-2.0 or MIT.

## 2a. What is already on the pod

`/workspace/cache` holds 44 G, and four of the six models this project needs are
in it, left by earlier work on the pod:

```text
models--Qwen--Qwen3.5-9B
models--Systran--faster-whisper-large-v3
models--speechbrain--lang-id-voxlingua107-ecapa
models--speechbrain--spkrec-ecapa-voxceleb
```

This removes roughly 20 GB of downloads from later gates. It also introduces an
obligation: **the cached revision was chosen by someone else.** For each of
these four, the resolved commit SHA must be read out of the cache and recorded
before any benchmark uses it (`CLAUDE.md` Section 7, ASR-240, TRN-030). A cache
hit is not a pin.

Still to download: pyannote (both), and Silero VAD.

---

## 3. Per-model detail

### 3.1 `openai/whisper-large-v3`

- License: Apache-2.0. Commercial use and redistribution permitted.
- Not gated.
- The model card carries an advisory, not a legal restriction: it cautions
  against transcribing recordings of individuals taken without consent, and
  against deployment in high-risk decision-making contexts. Relevant to this
  project's privacy posture (`requirements.md` Section 21) and worth reflecting
  in the retention notes.
- Production backend is faster-whisper / CTranslate2, which requires a converted
  model. The conversion output is a derived artifact and is subject to the same
  license.
- **Not currently cached on the pod.** What *is* cached is
  `Systran/faster-whisper-large-v3`, the pre-converted CTranslate2 build.
- Immutable revision: `PENDING` — pinned on the pod at the Phase 6 gate, from
  the commit actually downloaded.

### 3.1a `Systran/faster-whisper-large-v3` — a Phase 6 design-gate question

Already in `/workspace/cache/hub`. It is the CTranslate2 conversion of
`openai/whisper-large-v3`, which is precisely the production artifact
Section 4.2 calls for — but produced by a third party rather than by us.

Two paths, decided at the Phase 6 gate, not here:

- **Use the cached Systran build.** Saves ~6 GB and a conversion step. The
  recorded provenance becomes "Systran/faster-whisper-large-v3 at commit X",
  which satisfies Section 13.5 as long as the commit is pinned.
- **Download `openai/whisper-large-v3` and convert it in-project.** The
  conversion parameters — compute type in particular — become ours and are
  recorded alongside the benchmark. Costs disk and one conversion run.

Neither is obviously right. The deciding evidence is whether the Systran build's
compute type matches what the benchmark wants; if it does not, converting
ourselves is the only way to control it.

### 3.2 `speechbrain/lang-id-voxlingua107-ecapa`

- License: Apache-2.0. Not gated. **Already cached on the pod.**
- Input: 16 kHz, single channel — matches the project's canonical wire format
  exactly, so no resampling is needed between the wire and this model. The real
  meeting recording is also mono 16 kHz, so the whole chain from fixture to
  model runs at one sample rate with no conversion anywhere.
- Both **Japanese and Vietnamese are among the 107 languages**, confirmed on the
  card. This is the precondition for Section 12.2 treating SpeechBrain as the
  primary acoustic signal.
- Loading class: `EncoderClassifier`. The card shows
  `from speechbrain.pretrained import EncoderClassifier`. Newer SpeechBrain
  releases moved inference classes to `speechbrain.inference`; which import path
  is correct depends on the installed version and must be checked at the
  Phase 7 gate rather than copied from the card.
- Immutable revision: `PENDING` — Phase 7 gate.

### 3.3 `speechbrain/spkrec-ecapa-voxceleb`

- License: Apache-2.0. Not gated. **Already cached on the pod.**
- Input: 16 kHz single channel. The card notes the model will resample and
  downmix automatically; the project feeds it 16 kHz mono already, so that path
  should never trigger. Worth asserting in a test, because silent resampling
  would mean the embedding was computed on audio that differs from the audio the
  ASR saw.
- Loading class: `EncoderClassifier` (embeddings) or `SpeakerRecognition`
  (verification). This project needs embeddings only; clustering is a separately
  designed layer per Section 15.2.
- Embedding dimension: not stated on the card. Determined empirically at the
  Phase 7 gate and recorded then, not assumed.
- Immutable revision: `PENDING` — Phase 7 gate.

### 3.4 `pyannote/segmentation-3.0`

- License: MIT. **Gated** — conditions accepted by the user 2026-09-08; token
  required at load. Not yet cached on the pod.
- Output: a matrix of shape `(num_frames, num_classes)` over **7 powerset
  classes**: non-speech, speaker #1, speaker #2, speaker #3, speakers #1+#2,
  speakers #1+#3, speakers #2+#3.
- Supports overlapped speech detection through the `OverlappedSpeechDetection`
  pipeline. This is the concrete mechanism behind the Section 16.1 requirement
  to mark `overlap=true` and preserve overlap intervals.
- Input: 16 kHz mono, **10-second windows**, tensor shape
  `(batch_size, num_channels, duration * sample_rate)`.
- The 10-second native window is a hard constraint on the Pyannote Scheduling
  ADR (Section 25.7): the rolling-window length and overlap decided there must
  be compatible with a model that reasons over 10 seconds at a time, and the
  powerset formulation caps it at 3 simultaneous speakers per window. Section
  15.4 forbids assuming a fixed speaker count for the *system*; this is a
  per-window model property, not a system-level assumption, and the distinction
  must be preserved in the design.
- Immutable revision: `PENDING` — Phase 8 gate.

### 3.5 `pyannote/speaker-diarization-3.1`

- License: MIT. **Gated** — conditions accepted by the user 2026-09-08; token
  required at load. Not yet cached on the pod.
- Requires accepting conditions on `pyannote/segmentation-3.0` as well, because
  the pipeline loads it. Both were accepted.
- Requires `pyannote.audio >= 3.1`.
- Role in this project is strictly retrospective refinement and comparison
  (Section 15.3). It does **not** replace Silero as the live endpointing
  authority and does **not** open or close ASR utterances (Section 25.2).
- Immutable revision: `PENDING` — Phase 8 gate.

### 3.6 Silero VAD

- Source: `https://github.com/snakers4/silero-vad`, not a Hugging Face
  repository.
- License: `PENDING` — read at the Phase 5 gate.
- Runs on CPU per Section 25.11.
- ADR-0007 proposes the ONNX build so that torch stays out of the core
  gateway/orchestrator environment. Whether the ONNX build is equivalent in
  behaviour to the torch build is a Phase 5 question, not an assumption.
- Immutable revision: `PENDING` — pinned to a Git tag or commit at the Phase 5
  gate.

### 3.7 `speechbrain/sepformer-wsj02mix`

- Not verified, deliberately. Source separation is disabled in the MVP
  production path (Section 4.2, Section 16.2), and this model is benchmarked
  only behind a feature flag.
- Section 16.2 already records the decisive caveat: the model is trained for a
  different language, domain and sample rate, so no quality assumption is
  permitted.
- License and revision are verified only if and when the experiment is enabled.

### 3.8 `Qwen/Qwen3.5-9B`

- License: Apache-2.0. Not gated. **Already cached on the pod**, in
  `models--Qwen--Qwen3.5-9B`, so the ~18 GB download is already done. The cached
  revision was chosen by other work on the pod and must be read out and pinned
  before any benchmark.
- Post-trained (instruct) variant. The base model is a separate repository,
  `Qwen/Qwen3.5-9B-Base`, and is **not** what this project uses.
- Native context 262,144 tokens, extensible to 1,010,000. The project uses a far
  smaller `max_model_len`; see ADR-0006 for why.
- **Thinking mode is on by default** and must be disabled per request via
  `chat_template_kwargs: {"enable_thinking": false}`.
- The card's example vLLM launch line includes `--reasoning-parser qwen3`.
  This project does **not** use that flag. See ADR-0006.
- Immutable revision: `PENDING` — Phase 10 gate.

---

## 4. Immutable revision manifest

`CLAUDE.md` Section 25 requires an immutable model revision manifest. It is
populated from the pod, at each model's download gate, by recording the commit
actually resolved — not by copying a value from a web page, which can move.

```yaml
# configs/model-revisions.yaml  - to be created, one entry per model
# Every field below is PENDING until the model is downloaded on the pod and
# its resolved commit is read back.
models: {}
```

The recording command for a Hugging Face model, run on the pod after download:

```bash
python3 - <<'PY'
from huggingface_hub import HfApi
for repo in [
    "openai/whisper-large-v3",
    "speechbrain/lang-id-voxlingua107-ecapa",
    "speechbrain/spkrec-ecapa-voxceleb",
    "pyannote/segmentation-3.0",
    "pyannote/speaker-diarization-3.1",
    "Qwen/Qwen3.5-9B",
]:
    try:
        info = HfApi().model_info(repo)
        print(f"{repo}\t{info.sha}")
    except Exception as e:
        print(f"{repo}\tERROR: {e}")
PY
```

This is not run yet: `huggingface_hub` is not installed on the pod. It runs at
the first gate that installs a worker environment.

For the four models already in the cache, the resolved commit can also be read
straight off disk without any network access or installed package, because the
Hugging Face cache layout stores it as a directory name:

```bash
for d in /workspace/cache/hub/models--*/snapshots/*; do
  echo "$(basename "$(dirname "$(dirname "$d")")")  $(basename "$d")"
done
```

That is the value to record: it is the revision whose bytes are actually on
disk, which is not necessarily the current `main` of the repository.
