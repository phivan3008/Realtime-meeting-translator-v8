# Environment matrix

Recorded facts only. Nothing in this file is an assumption; every value came
from a command whose output is reproduced below. Values marked `UNKNOWN` are
genuinely not yet known and must not be guessed
(`requirements.md` Section 7, `CLAUDE.md` Section 5).

Last updated: 2026-09-08, Phase 0.

---

## 1. Machines

There are three, and they have different roles.

| Machine | Role | Gets code by |
|---|---|---|
| **dev machine** | Windows 11. All code is written here. Client tests that need no real device audio and no real recording run here. | this Git working copy |
| **user machine** | Windows. Holds the real meeting recording and real audio devices. Runs client tests that need WASAPI loopback. | downloading a branch archive from GitHub — **not** a Git clone |
| **H100 pod** | Ubuntu 22.04. All GPU work. | copy-paste through the VS Code SSH UI — **not** a Git clone |

The consequence for every runbook: instructions must work from an extracted
archive or a pasted directory, and must never assume `git` is available or that
the directory is a working copy.

---

## 2. Dev machine (Windows) — verified 2026-09-08

| Property | Value |
|---|---|
| OS | Microsoft Windows 11 Pro, NT 10.0.26100.0 |
| CPU | 12th Gen Intel Core i5-12400F |
| RAM | 31.8 GB |
| Working drive | `F:` — 355 GB free of 488 GB |
| Python | 3.11.9, `C:\Users\Admin\AppData\Local\Programs\Python\Python311\python.exe` |
| pip | 24.0 |
| Git | 2.55.0.windows.3 |
| `core.autocrlf` | `true` — mitigated by `.gitattributes` |
| SSH | OpenSSH_10.3p1, OpenSSL 3.5.7 |
| ffmpeg | **not installed** |
| Sound devices | NVIDIA Virtual Audio Device (Wave Extensible) (WDM); Realtek High Definition Audio; NVIDIA High Definition Audio — all `OK` |
| `pyaudiowpatch` | **not installed** |
| `PySide6` | **not installed** |
| Installed packages | `pip 24.0`, `setuptools 65.5.0` only — no project venv exists yet |

### Notes

- No virtual environment exists on the dev machine yet. One is created at the
  start of Phase 1, pinned per ADR-0003.
- `ffmpeg` is absent and is needed by `tools/` for cutting real clips with
  recorded provenance (`requirements.md` Section 22.2). Either install it or
  cut clips with a pure-Python `soundfile`/`numpy` path. Decided at the Phase 1
  tooling step; the pure-Python path is preferred because it removes an external
  binary from the fixture-provenance chain.
- The dev machine has real output devices, so WASAPI loopback *enumeration* and
  *capture of whatever is playing locally* can be exercised here. What it cannot
  provide is the real meeting recording. See `docs/test-data.md`.

---

## 3. H100 pod (Linux) — verified 2026-09-08

### 3.1 Summary

| Property | Value |
|---|---|
| Hostname | `vscode-vandp-self-serving` |
| OS | Ubuntu 22.04.5 LTS (jammy) |
| Kernel | 5.15.0-130-generic, x86_64 |
| GPU | 1 × NVIDIA H100 80GB HBM3 |
| GPU total memory | 81,559 MiB |
| Compute capability | 9.0 |
| Driver | 580.82.07 |
| Driver CUDA API | 13.0 |
| CUDA toolkit (`nvcc`) | release 12.8, V12.8.93 |
| gcc | 11.4.0 |
| glibc | 2.35 |
| Python | 3.11.13 |
| Interpreter | `/workspace/Realtime-meeting-translator-v8/.venv/bin/python3` |
| pip | 26.2.1 |
| Installed packages in that venv | **none** |
| Docker | 29.7.2 |
| Docker Compose | v5.4.0 |
| podman | not present |
| User | `uid=0(root)` |
| CPU cores | 128 |
| System RAM | 1.5 TiB total, 1.4 TiB available |
| Persistent storage | `/workspace` — NFS PVC `10.200.157.2`, 300 G total, **99 G available** |
| Root filesystem | 24 T overlay, 21 T available — **user instruction: use `/workspace` only** |
| `HF_HOME` | `/workspace/cache` |
| `TRANSFORMERS_CACHE` | unset |
| `~/.cache/huggingface` | does not exist |
| `ulimit -l` (max locked memory) | 64 KB |
| `ulimit -n` (open files) | 524,288 |
| `ulimit -u` (max user processes) | unlimited |
| Swap | none |

### 3.2 GPU occupancy at inspection time

```text
|   0  NVIDIA H100 80GB HBM3          On  |   00000000:00:0B.0 Off |         0 |
| N/A   32C    P0            122W /  700W |   45241MiB /  81559MiB |  0%       |

|    0   N/A  N/A     409752      C   VLLM::EngineCore              45232MiB   |
```

The owning process:

```text
root  409752  VLLM::EngineCore
root  409237  python -m vllm.entrypoints.openai.api_server \
                --model Qwen/Qwen3.5-9B --port 8001 --gpu-memory-utilization 0.55
```

**This belongs to a different project.** It leaves ~36,300 MiB free. See
ADR-0007 for why this is a blocking question rather than a footnote.

A second unrelated service is also running:

```text
root  2106613  /workspace/meeting-translator/.venv-asr/bin/python \
                 .../uvicorn server.app:app --host 127.0.0.1 --port 3000
```

It holds no GPU memory but occupies port 3000 and `/workspace` disk.

### 3.3 Topology

```text
        GPU0    CPU Affinity    NUMA Affinity   GPU NUMA ID
GPU0     X      0-127           0-1             N/A
```

Single GPU, so no NVLink or multi-GPU scheduling concerns. `tensor_parallel_size`
is 1 by necessity, not by choice.

### 3.4 Filesystem detail

```text
Filesystem                                              Size  Used Avail Use% Mounted on
overlay                                                  24T  1.7T   21T   8% /
10.200.157.2:/pvc-053aef1e-9814-4e04-800f-2092a95347bb  300G  202G   99G  68% /workspace
tmpfs                                                    64M     0   64M   0% /dev/shm
```

Two things to carry forward:

- `/workspace` is **NFS**. Model load times will reflect network storage, and
  cold-start figures in Section 25.15 benchmarks must be labelled accordingly.
- `/dev/shm` is **64 MB**. Some torch DataLoader and multiprocessing patterns
  assume a much larger shared-memory segment. Relevant if any worker uses
  `num_workers > 0` or shared-memory tensors across processes.

---

## 4. Still UNKNOWN — must be measured, never assumed

| # | Unknown | How it gets resolved | Blocks |
|---|---|---|---|
| U1 | Whether the other project's vLLM can be stopped during our server gates | user decision | ADR-0007, all GPU benchmarks, Phase 12 |
| U2 | Contents and size of `/workspace/cache` | `du -sh /workspace/cache; ls -1 /workspace/cache` | model download plan, disk budget |
| U3 | Which localhost ports are bindable | corrected bind script in ADR-0007 | Phase 1 protocol, Phase 4 |
| U4 | Whether `ss`/`netstat` exist on the pod | `command -v ss; command -v netstat` | runbook diagnostics |
| U5 | torch / CTranslate2 / faster-whisper versions | verify official guidance at Phase 6 gate | Phase 6 |
| U6 | SpeechBrain version and whether `speechbrain.pretrained` or `speechbrain.inference` is current | verify at Phase 7 gate | Phase 7 |
| U7 | pyannote.audio version | verify at Phase 8 gate; card requires `>=3.1` | Phase 8 |
| U8 | vLLM version and whether it can coexist with a CTranslate2 CUDA context | verify at Phase 10 gate | Phase 10 |
| U9 | onnxruntime viability for Silero VAD on CPU | verify at Phase 5 gate | Phase 5 |
| U10 | Real recording: path, format, sample rate, channels, duration, SHA-256 | user copies it to the dev machine; `tools/hash_file.py` | every category A test |
| U11 | Whether the SSH tunnel to the pod is already established by VS Code port forwarding, and on which local port | user confirmation | Phase 11 end-to-end |
| U12 | Whether ffmpeg may be installed on the dev machine, or clips are cut in pure Python | Phase 1 tooling decision | fixture creation |

---

## 5. Candidate version matrix — NOT PINNED

Reproduced from ADR-0007 for convenience. **Nothing here is installed or
committed to.** Each line is verified against official installation guidance
immediately before installation, at that phase's gate.

```yaml
python: "3.11.13 (pod) / 3.11.9 (dev)"
cuda_wheels: "cu128 family - matches nvcc 12.8; driver 580.82.07 supports it"
torch: UNKNOWN - verify at Phase 6 gate
ctranslate2: UNKNOWN - verify at Phase 6 gate
faster_whisper: UNKNOWN - verify at Phase 6 gate
speechbrain: UNKNOWN - verify at Phase 7 gate
pyannote_audio: ">=3.1 required by the model card; exact version UNKNOWN"
vllm: UNKNOWN - verify at Phase 10 gate
onnxruntime: UNKNOWN - verify at Phase 5 gate
```

---

## 6. Raw inspection output

Preserved verbatim so that later claims can be checked against what was actually
returned, rather than against a summary of it.

### 6.1 `uname -a`

```text
Linux vscode-vandp-self-serving 5.15.0-130-generic #140-Ubuntu SMP Wed Dec 18 17:59:53 UTC 2024 x86_64 x86_64 x86_64 GNU/Linux
```

### 6.2 `/etc/os-release`

```text
PRETTY_NAME="Ubuntu 22.04.5 LTS"
NAME="Ubuntu"
VERSION_ID="22.04"
VERSION="22.04.5 LTS (Jammy Jellyfish)"
VERSION_CODENAME=jammy
ID=ubuntu
ID_LIKE=debian
UBUNTU_CODENAME=jammy
```

### 6.3 `nvidia-smi --query-gpu=...`

```text
name, driver_version, memory.total [MiB], compute_cap
NVIDIA H100 80GB HBM3, 580.82.07, 81559 MiB, 9.0
```

### 6.4 Toolchain

```text
Python 3.11.13
/workspace/Realtime-meeting-translator-v8/.venv/bin/python3
venv OK
pip 26.2.1 from /workspace/Realtime-meeting-translator-v8/.venv/lib/python3.11/site-packages/pip (python 3.11)
nvcc: NVIDIA (R) Cuda compiler driver
Cuda compilation tools, release 12.8, V12.8.93
gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0
ldd (Ubuntu GLIBC 2.35-0ubuntu3.8) 2.35
Docker version 29.7.2, build a7dcaa6
Docker Compose version v5.4.0
podman not present
uid=0(root) gid=0(root) groups=0(root)
```

### 6.5 Memory

```text
               total        used        free      shared  buff/cache   available
Mem:           1.5Ti       117Gi       194Gi       2.0Gi       1.2Ti       1.4Ti
Swap:             0B          0B          0B
```

### 6.6 `ulimit -a` (selected)

```text
max locked memory           (kbytes, -l) 64
open files                          (-n) 524288
max user processes                  (-u) unlimited
stack size                  (kbytes, -s) 8192
cpu time                   (seconds, -t) unlimited
```

### 6.7 Checks that returned nothing or failed

- `ss -tlnp` produced empty output. Whether the tool is missing or simply
  reported nothing is not distinguishable from the output; see U4.
- The localhost port-bind script failed on a transcription error
  (`s.bind("127.0.0.1", p)` — two arguments instead of one tuple) and returned
  `TypeError: socket.bind() takes exactly one argument (2 given)`. No port
  information was obtained; see U3 and the corrected script in ADR-0007.
