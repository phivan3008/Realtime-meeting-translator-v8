# Third-party model inventory, licenses and access conditions

Required by `CLAUDE.md` Section 25 ("third-party package/model inventory,
licenses, access conditions, and redistribution restrictions" and "immutable
model revision manifest").

Every row was read from the model card on the date shown. Nothing here is
assumed. Where a field says `PENDING`, it is not yet known and must not be
guessed.

Last verified: 2026-09-08 (Phase 0).

---

## 1. Inventory

| Model | Repository | License | Gated | Token needed | Role | Fixed by |
|---|---|---|---|---|---|---|
| Whisper large-v3 | `openai/whisper-large-v3` | Apache-2.0 | No | No | ASR, partial and final | `requirements.md` 4.2 |
| VoxLingua107 ECAPA | `speechbrain/lang-id-voxlingua107-ecapa` | Apache-2.0 | No | No | Primary acoustic language ID | `requirements.md` 4.2 |
| ECAPA VoxCeleb | `speechbrain/spkrec-ecapa-voxceleb` | Apache-2.0 | No | No | Speaker embeddings for online clustering | `requirements.md` 4.2 |
| pyannote segmentation | `pyannote/segmentation-3.0` | MIT | **Yes** | **Yes** | Overlap detection, speaker-change evidence | `requirements.md` 4.2, 15.3 |
| pyannote diarization | `pyannote/speaker-diarization-3.1` | MIT | **Yes** | **Yes** | Retrospective diarization refinement | `requirements.md` 4.2, 15.3 |
| Silero VAD | `snakers4/silero-vad` (GitHub) | PENDING — verify at Phase 5 gate | No | No | Live endpointing authority, CPU | `requirements.md` 4.2 |
| SepFormer WSJ0-2Mix | `speechbrain/sepformer-wsj02mix` | PENDING — verify only if the experiment is enabled | PENDING | PENDING | Experimental source separation, **disabled by default** | `requirements.md` 4.2, 16.2 |
| Qwen3.5-9B | `Qwen/Qwen3.5-9B` | Apache-2.0 | No | No | Final-only translation via vLLM | `requirements.md` 4.2 |

---

## 2. Action required from the user

**Two models are gated and will fail to load without prior acceptance.**

`pyannote/segmentation-3.0` and `pyannote/speaker-diarization-3.1` both display
"You have to accept the conditions to access its files and content". Accepting
is done once per Hugging Face account, in a browser, and cannot be done by a
token alone:

1. Sign in to Hugging Face with the account whose token the pod will use.
2. Open `https://huggingface.co/pyannote/segmentation-3.0` and accept the
   conditions.
3. Open `https://huggingface.co/pyannote/speaker-diarization-3.1` and accept the
   conditions.
4. Confirm the token has `read` scope.

This is needed before Phase 8, not before Phase 0. It is recorded now so it does
not become a surprise blocker at a server test gate.

Everything else in the inventory is ungated Apache-2.0 or MIT and downloads with
no acceptance step.

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
- Immutable revision: `PENDING` — pinned on the pod at the Phase 6 gate, from
  the commit actually downloaded.

### 3.2 `speechbrain/lang-id-voxlingua107-ecapa`

- License: Apache-2.0. Not gated.
- Input: 16 kHz, single channel — matches the project's canonical wire format
  exactly, so no resampling is needed between the wire and this model.
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

- License: Apache-2.0. Not gated.
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

- License: MIT. **Gated** — conditions must be accepted; token required at load.
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

- License: MIT. **Gated** — conditions must be accepted; token required at load.
- Requires accepting conditions on `pyannote/segmentation-3.0` as well, because
  the pipeline loads it.
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

- License: Apache-2.0. Not gated.
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

This is not run yet: `huggingface_hub` is not installed on the pod, and
`CLAUDE.md` Section 5 forbids installing dependencies before the environment
gate closes.
