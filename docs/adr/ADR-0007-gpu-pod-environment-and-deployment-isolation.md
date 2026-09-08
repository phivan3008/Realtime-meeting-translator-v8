# ADR-0007: GPU pod environment and deployment isolation

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 2
- **Requirement IDs:** OPS-300, OPS-310, OPS-320, OPS-330, OPS-340, OPS-350
- **Decided by:** user on 2026-09-08 (GPU sharing: option S2)

## Context and constraints

`requirements.md` Section 7 requires the server not to load all ML frameworks
into one Python process, and lists six components to isolate. It offers two
deployment options and states that the choice may be made only after inspecting
the pod: Docker Compose if Docker is available and allowed, otherwise multiple
pinned virtual environments plus supervised subprocesses. Section 7 calls the
environment inspection a mandatory design gate and forbids locking CUDA,
PyTorch, CTranslate2, SpeechBrain, pyannote, vLLM and driver versions before the
returned output is in hand.

The user ran the inspection commands on 2026-09-08 and returned raw output.
Recorded in full in `docs/environment-matrix.md`. The load-bearing facts:

| Fact | Value |
|---|---|
| OS | Ubuntu 22.04.5 LTS (jammy), kernel 5.15.0-130-generic, x86_64 |
| GPU | 1 × NVIDIA H100 80GB HBM3, compute capability 9.0, 81,559 MiB total |
| Driver | 580.82.07, driver CUDA API 13.0 |
| CUDA toolkit | nvcc release 12.8, V12.8.93 |
| Compiler | gcc 11.4.0, glibc 2.35 |
| Python | 3.11.13, venv already created at `/workspace/Realtime-meeting-translator-v8/.venv`, pip 26.2.1 |
| Installed packages | none — the venv is empty |
| Container runtime | Docker 29.7.2, Docker Compose v5.4.0, running as root |
| CPU | 128 cores |
| System RAM | 1.5 TiB total, 1.4 TiB available |
| Persistent storage | `/workspace` — NFS PVC, 300 G total, **99 G available** |
| Root filesystem | 24 T overlay, but the user instructs: use `/workspace` only |
| HF cache | `HF_HOME=/workspace/cache`, `TRANSFORMERS_CACHE` unset |
| `ulimit -l` | 64 KB max locked memory |
| `ulimit -n` | 524,288 open files |

Two facts about the pod are not about our project at all, and they dominate this
decision.

**The GPU is already occupied.** `nvidia-smi` shows PID 409752,
`VLLM::EngineCore`, holding **45,232 MiB**. Its parent is:

```text
python -m vllm.entrypoints.openai.api_server --model Qwen/Qwen3.5-9B \
  --port 8001 --gpu-memory-utilization 0.55
```

That is a different project's experiment. It leaves roughly **36,300 MiB free**
of the 81,559 MiB card.

**A second unrelated service is running.** PID 2106613,
`/workspace/meeting-translator/.venv-asr/bin/uvicorn server.app:app --host
127.0.0.1 --port 3000`. It holds no GPU memory but occupies port 3000 and
`/workspace` disk.

`requirements.md` Section 25.11 states: "No benchmark/reference/separation
workload shall run concurrently with a production meeting unless a dedicated
concurrency test is being performed", and requires a GPU Resource ADR and a
user-run server gate before loading all models concurrently. The pod as observed
violates the spirit of that clause before our first line of server code exists.

## The GPU memory budget

Estimated steady-state footprint of this project's stack, weights plus CUDA
context, excluding KV cache:

| Component | Estimate | Basis |
|---|---|---|
| Qwen3.5-9B, bfloat16 weights | ~18 GB | 9B parameters × 2 bytes |
| Qwen KV cache | 4-8 GB | depends on `max_model_len`, see ADR-0006 |
| whisper-large-v3, CTranslate2 float16 | ~3.1 GB | 1.55B parameters × 2 bytes |
| SpeechBrain VoxLingua107 ECAPA + spkrec ECAPA | <1 GB | both are small ECAPA models |
| pyannote segmentation + diarization | <1 GB | small models |
| CUDA context per process, 4-5 processes | 1.5-2.5 GB | ~300-500 MB each |
| **Total** | **~29-34 GB** | |

Against ~36.3 GB free, that fits — with between 2 and 7 GB of margin, and only
if the other project's vLLM neither grows nor restarts with a larger
`gpu-memory-utilization`. It is not a margin to plan a benchmark around, because
`requirements.md` Section 25.15 requires steady-state benchmarks that separate
cold start, warm-up, repeated runs and concurrent workload, and Section 18
requires reporting GPU memory idle and peak per worker. Peak measurements taken
next to an uncontrolled 45 GB neighbour are not reproducible measurements.

There is a second, subtler hazard. vLLM's `--gpu-memory-utilization` is a
fraction of **total** GPU memory, not of free memory. Launching a second vLLM
with a naively chosen value on a card that is already 55% committed is the
standard way to produce an out-of-memory failure at engine start.

## Options considered

### Deployment mechanism

#### Option A — Docker Compose

| Dimension | Assessment |
|---|---|
| Correctness | Strongest isolation; each service gets its own CUDA userspace |
| Latency | Negligible runtime overhead |
| Accuracy | n/a |
| GPU / RAM usage | No difference at runtime |
| Testability | Reproducible; but image builds must be reproduced on the pod |
| Dependency isolation | Enforced at the image level, the strongest form available |
| Future maintenance | 5 CUDA base images on a 99 GB NFS volume is 30-60 GB of image layers before a single model is downloaded. Build times on NFS are poor. Docker-in-Kubernetes-pod GPU passthrough works here (the pod has `nvidia-ctk` hooks) but adds a failure surface the MVP does not need |

#### Option B — Multiple pinned virtual environments plus supervised subprocesses

| Dimension | Assessment |
|---|---|
| Correctness | Isolation is per-process and per-`site-packages`, which is what Section 7 actually requires |
| Latency | No overhead |
| Accuracy | n/a |
| GPU / RAM usage | No difference at runtime |
| Testability | The user can inspect and edit files directly in the VS Code UI, which matches how code reaches this pod |
| Dependency isolation | Enforced by separate `site-packages` and separate `requirements/<domain>.lock.txt` |
| Future maintenance | Roughly 3-4 GB per torch-bearing venv; far less disk than images. No container layer to debug |

#### Option C — Option B, with venvs created only as each phase needs them

As B, plus: no environment is created before the phase that uses it.

| Dimension | Assessment |
|---|---|
| Correctness | Same as B |
| Latency | Same as B |
| Accuracy | n/a |
| GPU / RAM usage | Same as B; but disk pressure arrives gradually and stays visible |
| Testability | Each phase's server gate installs exactly one new environment, so a dependency conflict is attributable to one change |
| Dependency isolation | Same as B |
| Future maintenance | Matches the user's instruction to keep the number of environments low for the MVP, without violating the Section 7 isolation requirement |

### GPU sharing

#### Option S1 — run alongside the existing vLLM, sized to the remaining ~36 GB

Cheapest. But peak-memory and latency numbers are contaminated by a neighbour we
do not control, and Section 25.15 benchmark requirements cannot be honestly met.

#### Option S2 — the user stops the other vLLM during our server test gates — SELECTED

Gives a clean card for the duration of a gate. Costs the other project's
availability for the length of a test run. With the full 81,559 MiB available,
the memory budget below stops constraining the design and becomes just a number
to measure.

#### Option S3 — reuse the already-running vLLM on port 8001 as our translation backend

Tempting: it is already serving `Qwen/Qwen3.5-9B`, the exact model Section 4.2
fixes. But it was launched by someone else with `--gpu-memory-utilization 0.55`
and no control over `max_model_len`, sampling defaults, or `enable_thinking`.
Section 24 requires a configuration hash in every benchmark artifact, and
Section 13.5 requires recording backend version and decode parameters. We cannot
record what we do not control, and the instance can be restarted or
reconfigured without notice. Reusing it would make every translation benchmark
unreproducible.

## Decision

**Proposed, pending the GPU-sharing answer:**

### 1. Deployment mechanism — Option C

Multiple pinned virtual environments under `/workspace/Realtime-meeting-translator-v8/`,
created lazily, one per dependency domain, supervised as subprocesses.

| Environment | Domain | Created at | Heavy dependencies |
|---|---|---|---|
| `.venv` (exists) | gateway + orchestrator + protocol | Phase 4 | none — pure Python, `pydantic`, WebSocket. **No torch.** |
| `.venv-asr` | faster-whisper / CTranslate2 | Phase 6 | `faster-whisper`, CTranslate2, cuDNN |
| `.venv-sb` | SpeechBrain LID + ECAPA | Phase 7 | torch, `speechbrain` |
| `.venv-pyannote` | pyannote | Phase 8 | torch, `pyannote.audio` |
| `.venv-vllm` | vLLM + Qwen | Phase 10 | vLLM, torch |

Five environments is the minimum that satisfies Section 7 without merging a
domain the requirements forbid merging. In particular, SpeechBrain and pyannote
are not combined despite both needing torch, because Section 4.2 and
`CLAUDE.md` Section 2 both require pyannote to run "in an isolated
worker/environment". Gateway and orchestrator *are* combined, because Section 7
lists them as separate components but nothing requires them to be separate
*environments*, and neither carries an ML dependency.

Silero VAD runs in the core `.venv` via **ONNX Runtime on CPU**, not via
`torch.hub`. Section 25.11 requires Silero on CPU; using the ONNX build keeps
torch out of the core environment entirely, which is what makes the "no ML
dependency in gateway/orchestrator" boundary of ADR-0001 real rather than
nominal. This is a candidate, to be confirmed against the Silero repository at
the Phase 5 gate.

Docker is available and would work; it is declined for the MVP on disk and
build-time grounds, and because the code-transport path to this pod is VS Code
copy-paste, which suits a plain directory far better than an image build.

### 2. GPU sharing — Option S2, selected by the user on 2026-09-08

**The other project's vLLM is stopped for the duration of each of our server
test gates, and restarted afterwards.**

Reasoning: Section 25.11 makes final ASR starvation the thing to prevent, and
Section 25.15 requires distribution metrics (median, P95, maximum) that mean
nothing when an uncontrolled process holds 55% of the card. A gate typically
runs for minutes, not hours, so the cost to the other project is bounded.

Two operational consequences follow, and both belong in the server runbook:

- Every server test gate's instructions begin with stopping the other vLLM and
  end with restarting it, and the gate's artifacts include an `nvidia-smi`
  capture taken **after** the stop, proving the card was clear when the run
  started. A benchmark whose artifacts do not show a clear card is labelled as
  measured under contention, and no acceptance threshold may be derived from it
  (OPS-340).
- Between gates the card returns to the other project, so this project must not
  leave a model resident. Worker startup and shutdown are part of the gate
  procedure, not a persistent service.

Option S3 is rejected outright: an instance we do not control cannot produce a
reproducible benchmark artifact.

### 3. Candidate version matrix — NOT pinned

The driver (580.82.07, CUDA 13.0 API) is newer than the toolkit (nvcc 12.8), so
CUDA 12.x builds run fine under driver forward-compatibility. Python is 3.11 on
both the pod and the dev machine.

```yaml
# CANDIDATE ONLY. Verified against official install guidance at each
# phase's environment gate, immediately before installation.
python: "3.11.13"
cuda_wheels: "cu128 family"       # matches nvcc 12.8; driver supports it
torch: "verify at Phase 6 gate"
ctranslate2: "verify at Phase 6 gate"
faster_whisper: "verify at Phase 6 gate"
speechbrain: "verify at Phase 7 gate"
pyannote_audio: ">=3.1 (required by pyannote/speaker-diarization-3.1)"
vllm: "verify at Phase 10 gate"
onnxruntime: "verify at Phase 5 gate"
```

Nothing above is installed until its phase's gate, per Section 7 and
`CLAUDE.md` Section 5.

### 4. Storage plan — revised 2026-09-08 after inspecting the cache

`/workspace` has 99 G available and is NFS-mounted. `/workspace/cache` already
holds 44 G, and **four of the six models this project needs are in it**, left by
earlier work on the pod: `Qwen/Qwen3.5-9B`, `Systran/faster-whisper-large-v3`,
`speechbrain/lang-id-voxlingua107-ecapa` and `speechbrain/spkrec-ecapa-voxceleb`.

Remaining projected consumption:

| Item | Estimate | Note |
|---|---|---|
| Qwen3.5-9B weights | **0 GB** | already cached |
| ASR model | **0 GB** using the cached Systran CT2 build; ~6 GB if `openai/whisper-large-v3` is downloaded and converted in-project | Phase 6 gate decides |
| SpeechBrain models | **0 GB** | both already cached |
| pyannote models | ~1 GB | conditions accepted 2026-09-08; not yet downloaded |
| Silero VAD | <0.1 GB | |
| 4 torch-bearing venvs | ~12-16 GB | |
| vLLM installation | ~8-10 GB | |
| **Total** | **~21-27 GB**, or ~27-33 GB if the ASR model is converted in-project | |

Against 99 G available this is comfortable. The earlier disk-pressure concern is
resolved. Lazy environment creation is kept for the dependency-attribution
reason, not the disk reason.

`HF_HOME=/workspace/cache` is already set correctly and must not be changed —
it keeps weights on the persistent volume and off the ephemeral root filesystem.
Note that model loading from NFS is slower than from local disk; cold-start
figures in Section 25.15 benchmarks will reflect NFS latency and should be
labelled as such.

## Consequences

- The MVP runs as five supervised processes with five `site-packages` trees,
  which is auditable by listing directories and needs no container tooling.
- The core environment stays free of torch, so the gateway and orchestrator can
  be developed and tested on the Windows dev machine as well as the pod.
- Lazy environment creation means a dependency conflict surfaces at the gate of
  the phase that introduced it, attributable to one change, which is what
  `CLAUDE.md` Section 7 asks for.
- If the GPU stays shared, every server gate result carries a contention caveat
  and no numeric acceptance threshold can be derived from it. This blocks
  Phase 12, whose entire purpose is establishing those thresholds.
- `ulimit -l` is 64 KB, which is low. With `tensor_parallel_size: 1` there is no
  NCCL requirement, so this is unlikely to matter, but it is recorded because it
  is the kind of limit that produces a confusing failure if a library tries to
  pin host memory.

## Rollback plan

Moving from venvs to Docker Compose later is additive: the per-domain
`requirements/<domain>.lock.txt` files are exactly the input a Dockerfile needs,
so the lock files are the portable artifact and the venvs are disposable.
Rollback stays cheap indefinitely, provided service startup is expressed as a
command plus an environment path rather than as a hard-coded interpreter
location. Runbooks therefore parameterise the interpreter path.

### 5. Port allocation

Verified bindable on 2026-09-08: `127.0.0.1:8760`, `8761`, `8762`, `8000`.
Port 8001 is the other project's vLLM and port 3000 its uvicorn service; both
are avoided permanently, not only during gates.

Proposed allocation, to be confirmed at the Phase 1 protocol gate:

```yaml
gateway_websocket: 8760      # the only port the SSH tunnel forwards
worker_ipc_base:   8761      # 8761, 8762, ... as workers are added
vllm_openai_api:   8000      # this project's own vLLM, not the one on 8001
```

All bind to `127.0.0.1` only (SEC-010, SEC-020).

Neither `ss` nor `netstat` exists on the pod. Runbook diagnostics use
`cat /proc/net/tcp` or a Python socket probe, never those tools.

## Evidence required before `accepted`

All satisfied on 2026-09-08:

1. ~~GPU sharing answer~~ - option S2 selected.
2. ~~Cache contents~~ - 44 G, four of six models present. See
   `docs/environment-matrix.md` section 3.4.
3. ~~Port bind check~~ - 8760, 8761, 8762, 8000 bindable. See section 3.5.
   The first attempt had failed on a transcription error
   (`s.bind("127.0.0.1", p)` instead of `s.bind(("127.0.0.1", p))`).
4. ~~`ss` / `netstat` availability~~ - both missing.

## Open questions

- ~~GPU sharing policy.~~ Resolved: S2.
- Whether Silero VAD via ONNX Runtime is viable at the accuracy the project
  needs, versus the torch build. Phase 5 gate.
- Whether the cached `Systran/faster-whisper-large-v3` CT2 build is used, or
  `openai/whisper-large-v3` is downloaded and converted in-project. Phase 6
  gate; recorded as U13 in the environment matrix.
- Final port allocation. Phase 1 protocol gate.
