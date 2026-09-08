# Phase-gate checks, in the order requirements.md Section 28 requires.
# Run from the repository root.
$ErrorActionPreference = "Stop"

$py = ".venv\Scripts\python.exe"

Write-Output "== format =="
& $py -m ruff format .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "== lint =="
& $py -m ruff check .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "== type-check =="
& $py -m mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "== traceability =="
& $py tools\traceability_sync.py --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Output "== tests =="
# pytest exits 5 when it collects nothing. Before Phase 1 there are no
# tests to collect, and that is a reported gap, not a check failure.
& $py -m pytest
if ($LASTEXITCODE -eq 5) {
    Write-Output "no tests collected - see docs/phase-checklists.md"
} elseif ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output "all checks passed"
