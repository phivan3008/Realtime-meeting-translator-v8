# ADR-0002: Git workflow, branching and code transport

- **Status:** accepted
- **Date:** 2026-09-08
- **Design gate:** none — process decision supporting `CLAUDE.md` Section 21
- **Requirement IDs:** OPS-100, OPS-110, OPS-120
- **Decided by:** user on 2026-09-08 (decision D1, option A)

## Context and constraints

`CLAUDE.md` Section 21 requires a commit and push after every approved phase and
before every server test gate, and requires pushing to a configured
non-protected working branch. Section 21 also forbids force push, shared-history
rewriting, unrequested rebase, destructive reset/clean/checkout, and pushing to
a protected branch without explicit instruction.

The repository began Phase 0 with a single branch `main`, tracking
`origin/main` on a public GitHub repository with no branch protection
configured.

The physical topology is unusual and shapes the workflow. There are three
machines:

- **dev machine** (Windows 11, this workspace) — where all code is written and
  where client-side tests that need no real capture device audio are run.
- **user machine** (Windows) — holds the real meeting recording and the real
  audio devices. The user downloads the source from a GitHub branch as an
  archive; it is not a Git working copy and cannot clone or check out.
- **H100 pod** (Ubuntu 22.04) — reached over VS Code SSH. Source arrives by
  copy-paste through the VS Code UI, not by `git clone`.

So the GitHub branch is not only version control, it is the transport mechanism
by which code reaches the two machines that can actually run it. A branch that
is not pushed is a branch the user cannot test.

## Options considered

### Option A — One branch per phase, merged to `main` after the phase report is approved

| Dimension | Assessment |
|---|---|
| Correctness | Branch boundaries coincide with design gates and phase reports |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | The user always downloads a branch that corresponds to one named phase, so test instructions can name it unambiguously |
| Dependency isolation | n/a |
| Future maintenance | Many branches; each is short-lived and self-describing |

### Option B — A single long-lived `develop`, merged to `main` at milestones

| Dimension | Assessment |
|---|---|
| Correctness | Works, but a phase boundary becomes just a commit |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | The user must be told a commit hash rather than a branch name; error-prone when downloading an archive |
| Dependency isolation | n/a |
| Future maintenance | Fewer branches; harder to roll a single phase back |

### Option C — Push directly to `main`

| Dimension | Assessment |
|---|---|
| Correctness | Simplest |
| Latency | n/a |
| Accuracy | n/a |
| GPU / RAM usage | n/a |
| Testability | No way to hand the user a phase-scoped snapshot |
| Dependency isolation | n/a |
| Future maintenance | No per-phase rollback point; conflicts with the spirit of `CLAUDE.md` Section 21 |

## Decision

Option A.

- Branch name: `phase/NN-<kebab-slug>`, for example `phase/00-repository-audit`,
  `phase/01-architecture-protocol`.
- The branch is pushed as soon as it carries something the user can test, and
  again at every server test gate.
- `main` is updated only by merging an approved phase branch, and only after the
  user has approved that phase report. Claude Code does not merge to `main`
  on its own initiative.
- Commit message form: `<type>(phase-NN): <what changed>`, following the example
  in `CLAUDE.md` Section 21.
- Before every commit, run the inspection set from `CLAUDE.md` Section 21:
  `git status --short --branch`, `git diff`, `git diff --cached`,
  `git diff --check`, `git ls-files -o --exclude-standard`,
  `find . -type f -size +50M -print`.
- Forbidden without an explicit instruction naming the operation:
  `git push --force`, `--force-with-lease`, `git reset --hard`, `git clean`,
  rebase of a pushed branch, amending a pushed commit.
- If a push fails, the local commit is left intact and the exact error is
  reported verbatim.

Branch protection on `main` is currently absent. It is not required for the
workflow above to be safe, because nothing pushes to `main` directly. If the
user wants it, the setting is: GitHub repository → Settings → Branches → Add
branch ruleset → target `main` → enable "Restrict deletions", "Block force
pushes" and "Require a pull request before merging". Adding it would change
nothing about how Claude Code operates.

## Consequences

- Every phase yields one branch, one phase report, one reviewable diff.
- Test instructions given to the user can always name a branch, which is what
  the GitHub "Download ZIP" flow needs.
- `main` stays a chain of approved phases, so rolling back one phase is a
  revert of one merge.
- Slightly more ceremony than pushing to `main`, and branch count grows to
  roughly fourteen over the project.

## Rollback plan

Abandoning per-phase branches costs nothing: start committing to `main` or to a
long-lived `develop` from any point forward. Existing phase branches remain in
history as snapshots. No rewriting is needed, so there is no point after which
rollback becomes expensive.

## Evidence required before `accepted`

None — process decision with no measurable quantity.

## Open questions

- Whether merges to `main` are fast-forward, merge-commit or squash is left to
  the user's preference at the first merge. Merge commit is the default
  assumption because it keeps the phase boundary visible in `main`'s history.
