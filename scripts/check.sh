#!/usr/bin/env bash
# Phase-gate checks, in the order requirements.md Section 28 requires.
# Run from the repository root. Windows: use scripts/check.ps1.
set -euo pipefail

PY="${PY:-.venv/Scripts/python.exe}"
[ -x "$PY" ] || PY=".venv/bin/python"

echo "== format =="
"$PY" -m ruff format .

echo "== lint =="
"$PY" -m ruff check .

echo "== type-check =="
"$PY" -m mypy

echo "== traceability =="
"$PY" tools/traceability_sync.py --check

echo "== tests =="
# pytest exits 5 when it collects nothing. Before Phase 1 there are no
# tests to collect, and that is a reported gap, not a check failure.
set +e
"$PY" -m pytest
pytest_status=$?
set -e
if [ "$pytest_status" -eq 5 ]; then
  echo "no tests collected - see docs/phase-checklists.md"
elif [ "$pytest_status" -ne 0 ]; then
  exit "$pytest_status"
fi

echo "all checks passed"
