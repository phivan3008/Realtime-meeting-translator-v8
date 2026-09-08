# ADR-0006: Qwen translation model identity

- **Status:** proposed — identity verified, runtime profile requires a server test gate
- **Date:** 2026-09-08
- **Design gate:** `requirements.md` Section 26, item 21
- **Requirement IDs:** TRN-010, TRN-020, TRN-030, TRN-040, OPS-320
- **Decided by:** pending user approval (decision D7, option B: verify at the gate, early)

## Context and constraints

`requirements.md` Section 4.2 fixes the translation model as "Qwen3.5-9B served
by vLLM" with thinking disabled. Section 25.13 requires that before
implementation the exact Hugging Face repository, instruct/base variant,
immutable revision where possible, tokenizer, chat template, license and access
requirements, compatible vLLM version, dtype and context length all be pinned.
Section 17.3 requires benchmarking the runtime configuration rather than
assuming it, and states: "Do not allocate the model's maximum possible context
merely because it is supported. Use the smallest context sufficient for live
translation."

`CLAUDE.md` Section 7 requires recording the model repository and immutable
revision where possible. Section 30 of `requirements.md` requires verifying the
model card during the design gate rather than assuming.

The user supplied the repository URL on 2026-09-08:
`https://huggingface.co/Qwen/Qwen3.5-9B`.

## Verification performed

Model card read on 2026-09-08 (`https://huggingface.co/Qwen/Qwen3.5-9B`):

| Property | Verified value |
|---|---|
| Repository | `Qwen/Qwen3.5-9B` — exists |
| Variant | Post-trained / instruct. The base model is a separate repository, `Qwen/Qwen3.5-9B-Base` |
| License | Apache-2.0 |
| Gated access | None. No conditions to accept |
| Parameters | 9B |
| Native context | 262,144 tokens, extensible to 1,010,000 |
| Tensor types | BF16, F32 |
| Thinking mode | **On by default.** Output is wrapped as `<think>\n...\n</think>\n\n` |
| Disabling thinking | `chat_template_kwargs: {"enable_thinking": false}` on the request |
| Chat template | Jinja2 template shipped in the repository. Card notes historical turns must contain only final output, never thinking content |
| Card's vLLM example | `vllm serve Qwen/Qwen3.5-9B --port 8000 --tensor-parallel-size 1 --max-model-len 262144 --reasoning-parser qwen3` |

Three findings matter more than the rest.

**Finding 1 — the instruct variant is the correct one and the card confirms it.**
Section 4.2 says "Qwen3.5-9B" without qualifying the variant. The repository the
user named is the post-trained model, not `-Base`. This is the right choice for
a translation task with an instruction-bearing prompt contract, and it is what
the pod is already running. Recorded so the choice is not silently re-made later.

**Finding 2 — thinking is on by default, which directly contradicts a fixed
requirement.** Section 4.2 and `CLAUDE.md` Section 2 both fix Qwen thinking as
disabled, and Section 25.13 requires the output validator to reject "reasoning
tags or analysis text". This is not a preference to be set once and forgotten:
if `enable_thinking` is ever omitted from a request, the model reverts to
emitting `<think>` blocks. Disabling it must therefore be enforced in two
independent places — in the request construction and again in the output
validator — so that a configuration slip becomes a visible validation failure
rather than a `<think>` block rendered as a translation.

The card's own vLLM example passes `--reasoning-parser qwen3`, which *parses*
reasoning output rather than suppressing it. That flag is appropriate for a
reasoning application and inappropriate here. The launch profile in this project
must not copy the card's example line unexamined.

**Finding 3 — the native context is 262,144 tokens and we must not use it.**
Section 17.3 forbids allocating maximum context merely because it is supported.
A live translation request carries one accepted final transcript segment plus,
only if the default-off context experiment of Section 25.13 is enabled, a small
bounded number of prior accepted segments. That is a few hundred tokens, not a
few hundred thousand. KV cache reservation scales with `max_model_len`, and GPU
memory is the binding constraint on this pod (see ADR-0007), so an oversized
context length is not a harmless default — it is the difference between fitting
alongside the ASR worker and not.

## Options considered

The identity itself is not a choice: Section 4.2 fixes the model and the user
supplied the repository. What remains open is the runtime profile.

### Option A — adopt the model card's launch line

`--max-model-len 262144 --reasoning-parser qwen3`.

| Dimension | Assessment |
|---|---|
| Correctness | Violates Section 4.2 (thinking disabled) in spirit by installing a reasoning parser, and Section 17.3 explicitly |
| Latency | Larger KV cache, worse batching headroom |
| Accuracy | No benefit for a translation-only workload |
| GPU / RAM usage | Reserves far more HBM than the workload needs, on a GPU already 45 GB occupied |
| Testability | Configuration would not match the documented requirement |
| Dependency isolation | n/a |
| Future maintenance | Encourages copying vendor defaults instead of measuring |

### Option B — pin a minimal profile now, and benchmark it at a server test gate

Start from the smallest plausible context and raise it only if a real transcript
segment overflows it.

| Dimension | Assessment |
|---|---|
| Correctness | Matches Sections 4.2, 17.3 and 25.13 |
| Latency | Smaller KV cache leaves more headroom for the ASR worker |
| Accuracy | Unaffected, provided no real segment is truncated — which is exactly what the gate measures |
| GPU / RAM usage | Minimised, which matters on this pod |
| Testability | Every number in the profile has a measurement behind it |
| Dependency isolation | vLLM stays in its own environment |
| Future maintenance | Profile is a config file with a recorded evidence trail |

## Decision

**Identity — proposed as pinned:**

```yaml
translation_model:
  repository: Qwen/Qwen3.5-9B
  variant: post-trained (instruct); NOT Qwen/Qwen3.5-9B-Base
  license: apache-2.0
  gated: false
  revision: PENDING - must be pinned to an immutable commit SHA before Phase 10
  dtype: bfloat16
  tokenizer: repository default
  chat_template: repository default, Jinja2
  thinking: false          # via chat_template_kwargs.enable_thinking = false
  reasoning_parser: none   # deliberately NOT --reasoning-parser qwen3
```

**Runtime profile — proposed, every value to be confirmed by measurement:**

```yaml
vllm:
  tensor_parallel_size: 1
  max_model_len: 4096            # candidate; smallest value that fits real segments
  gpu_memory_utilization: TBD    # constrained by ADR-0007; cannot be chosen in isolation
  max_output_tokens: TBD         # bounded; derived from observed source/target length ratio
  temperature: 0.0
  top_p: 1.0
  enforce_eager: TBD
```

**Enforcement rules that are not negotiable regardless of profile:**

1. Every request sets `chat_template_kwargs.enable_thinking = false`.
2. The output validator rejects any response containing `<think>`, a reasoning
   tag, a Markdown fence, or explanatory preamble, per Section 25.13. It does
   not strip them — Section 25.13 forbids silently rewriting model output.
3. The launch profile never includes `--reasoning-parser`.
4. Source and target language are always stated explicitly in the request, and
   Qwen is never asked to identify the language (Section 12.2, Section 17.2).

## Consequences

- The identity question raised at Phase 0 is closed with verified facts rather
  than an assumption, and Phase 10 inherits a pinned repository.
- Apache-2.0 with no gating means no license-acceptance action is required from
  the user for this model, unlike pyannote (see `docs/model-inventory.md`).
- The immutable revision is still unpinned. Pinning it requires reading the
  repository's commit SHA, which is best done on the pod at download time so the
  recorded SHA matches the bytes actually loaded. This is an obligation on the
  first Phase 10 server gate, tracked as TRN-030.
- `max_model_len: 4096` is a candidate, not a decision. If a real accepted final
  transcript segment plus the enabled context ever approaches it, the profile is
  wrong and the gate will show it.

## Rollback plan

The runtime profile is configuration; changing it is a restart of the
translation service with a different launch line, with no code change. The
identity is harder: switching repositories would invalidate every translation
benchmark artifact, because Section 13.5 and Section 24 require the model
revision to be recorded in each artifact. Rollback on identity stops being cheap
once Phase 10 benchmark results exist, so identity must be settled before that
phase begins — which is why it is being settled now rather than at Phase 10.

## Evidence required before `accepted`

At a Phase 10 server test gate on the H100 pod, with real accepted final
transcripts from the real meeting recording:

1. The immutable commit SHA of `Qwen/Qwen3.5-9B` as downloaded on the pod.
2. Token length distribution of real accepted final transcript segments in both
   Japanese and Vietnamese, to justify `max_model_len` and `max_output_tokens`.
3. Confirmation from raw output that no response contains a `<think>` block
   when `enable_thinking` is false.
4. Measured GPU memory footprint at the chosen `gpu_memory_utilization`,
   alongside the ASR worker, per ADR-0007.
5. Translation latency distribution (median, P95, maximum) per Section 25.15.

No number in the runtime profile may be marked accepted before these artifacts
are returned by the user.

## Open questions

- The immutable revision SHA. Pinned at the first Phase 10 server gate.
- Whether the pod's existing vLLM instance on port 8001 may be reused or must be
  replaced by a project-controlled instance. Deferred to ADR-0007, because it is
  a GPU-resource question as much as a model-identity one.
