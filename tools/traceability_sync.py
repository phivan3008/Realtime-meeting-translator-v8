"""Keep docs/traceability.md in sync with docs/requirement-ids.md.

Reads the requirement tables in ``docs/requirement-ids.md``, then rewrites
``docs/traceability.md`` so that every requirement ID has exactly one row.

Rows that already exist keep every value a human or an earlier phase put in
them.  New IDs are appended with status ``planned``.  An ID that has disappeared
from ``requirement-ids.md`` is never deleted silently: it is moved to an
"orphaned" section so the removal is visible in review, because
``requirements.md`` Section 25.16 makes requirement IDs permanent.

Usage::

    python tools/traceability_sync.py            # rewrite the matrix
    python tools/traceability_sync.py --check    # fail if it would change

``--check`` is what a phase gate runs: it proves the matrix is current without
modifying anything.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IDS_PATH = REPO_ROOT / "docs" / "requirement-ids.md"
MATRIX_PATH = REPO_ROOT / "docs" / "traceability.md"

ID_PATTERN = re.compile(r"^(AUD|UI|PROT|VAD|ASR|LID|SPK|TRN|PERS|OPS|SEC|TEST)-\d{3,4}$")

ALLOWED_STATUSES = (
    "planned",
    "implemented",
    "tested",
    "test_script_ready",
    "blocked_environment",
    "blocked_real_fixture",
    "failed",
    "accepted_exception",
)

DOMAIN_ORDER = (
    "AUD",
    "UI",
    "PROT",
    "VAD",
    "ASR",
    "LID",
    "SPK",
    "TRN",
    "PERS",
    "OPS",
    "SEC",
    "TEST",
)

DOMAIN_TITLES = {
    "AUD": "AUD - audio capture and client runtime",
    "UI": "UI - client user interface",
    "PROT": "PROT - protocol, timeline, identity, revisions",
    "VAD": "VAD - preprocessing and segmentation",
    "ASR": "ASR - transcription and hallucination control",
    "LID": "LID - language identification and routing",
    "SPK": "SPK - speaker, clustering, overlap, diarization",
    "TRN": "TRN - translation",
    "PERS": "PERS - persistence",
    "OPS": "OPS - deployment, operations, resources, process",
    "SEC": "SEC - security and privacy",
    "TEST": "TEST - testing, fixtures, evaluation",
}

COLUMNS = (
    "ID",
    "Requirement",
    "ADR",
    "Module",
    "Test / script",
    "Fixture / vector",
    "Evidence",
    "Status",
    "Notes",
)

EMPTY = "-"


@dataclass
class Row:
    """One requirement's traceability record."""

    req_id: str
    requirement: str
    adr: str = EMPTY
    module: str = EMPTY
    test: str = EMPTY
    fixture: str = EMPTY
    evidence: str = EMPTY
    status: str = "planned"
    notes: str = EMPTY

    @property
    def domain(self) -> str:
        return self.req_id.split("-", 1)[0]

    @property
    def number(self) -> int:
        return int(self.req_id.split("-", 1)[1])

    def cells(self) -> list[str]:
        return [
            self.req_id,
            self.requirement,
            self.adr,
            self.module,
            self.test,
            self.fixture,
            self.evidence,
            self.status,
            self.notes,
        ]


@dataclass
class SyncResult:
    rows: list[Row] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    orphaned: list[Row] = field(default_factory=list)


CELL_SEPARATOR = re.compile(r"(?<!\\)\|")


def _escape_cell(value: str) -> str:
    """Escape pipes so a cell cannot silently split a row into extra columns."""
    return value.replace("|", r"\|")


def _unescape_cell(value: str) -> str:
    return value.replace(r"\|", "|")


def _split_markdown_row(line: str) -> list[str]:
    """Split a Markdown table row into stripped cell values.

    Splits on unescaped pipes only. A note that legitimately contains a pipe -
    "create | revise | split" - would otherwise turn one row into thirteen
    cells, and the row would then fail the column-count check and be silently
    treated as a new requirement on the next sync.
    """
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    # Drop the leading and trailing pipe before splitting so empty edge cells
    # do not appear.
    body = stripped[1:]
    if body.endswith("|") and not body.endswith(r"\|"):
        body = body[:-1]
    return [_unescape_cell(cell.strip()) for cell in CELL_SEPARATOR.split(body)]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(set(cell) <= set("-: ") and cell for cell in cells)


def parse_requirement_ids(path: Path) -> dict[str, str]:
    """Return ``{requirement_id: requirement_text}`` from the ID catalogue."""
    if not path.is_file():
        raise SystemExit(f"missing {path.relative_to(REPO_ROOT)}")

    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        cells = _split_markdown_row(line)
        if len(cells) < 2 or _is_separator_row(cells):
            continue
        req_id = cells[0]
        if not ID_PATTERN.match(req_id):
            continue
        if req_id in found:
            raise SystemExit(f"duplicate requirement ID in catalogue: {req_id}")
        found[req_id] = cells[1]
    if not found:
        raise SystemExit(f"no requirement IDs parsed from {path.name}")
    return found


def parse_existing_matrix(path: Path) -> dict[str, Row]:
    """Return existing traceability rows keyed by requirement ID."""
    if not path.is_file():
        return {}
    return parse_existing_matrix_from_text(path.read_text(encoding="utf-8"))


def parse_existing_matrix_from_text(text: str) -> dict[str, Row]:
    """Parse a rendered matrix. Separated from the file read so a round trip is testable."""
    existing: dict[str, Row] = {}
    for line in text.splitlines():
        cells = _split_markdown_row(line)
        if len(cells) != len(COLUMNS) or _is_separator_row(cells):
            continue
        req_id = cells[0]
        if not ID_PATTERN.match(req_id):
            continue
        existing[req_id] = Row(
            req_id=req_id,
            requirement=cells[1],
            adr=cells[2],
            module=cells[3],
            test=cells[4],
            fixture=cells[5],
            evidence=cells[6],
            status=cells[7],
            notes=cells[8],
        )
    return existing


def sync(catalogue: dict[str, str], existing: dict[str, Row]) -> SyncResult:
    result = SyncResult()

    for req_id, requirement in catalogue.items():
        previous = existing.get(req_id)
        if previous is None:
            result.rows.append(Row(req_id=req_id, requirement=requirement))
            result.added.append(req_id)
            continue
        # The catalogue owns the requirement text; everything else is owned by
        # whoever filled the matrix in.
        previous.requirement = requirement
        result.rows.append(previous)

    for req_id, row in existing.items():
        if req_id not in catalogue:
            result.orphaned.append(row)

    result.rows.sort(key=lambda r: (DOMAIN_ORDER.index(r.domain), r.number))
    result.orphaned.sort(key=lambda r: (r.domain, r.number))
    return result


def _render_table(rows: list[Row]) -> list[str]:
    lines = ["| " + " | ".join(COLUMNS) + " |"]
    lines.append("|" + "|".join(["---"] * len(COLUMNS)) + "|")
    lines.extend(
        "| " + " | ".join(_escape_cell(cell) for cell in row.cells()) + " |" for row in rows
    )
    return lines


def render(result: SyncResult) -> str:
    counts: dict[str, int] = {}
    for row in result.rows:
        counts[row.status] = counts.get(row.status, 0) + 1

    out: list[str] = []
    out.append("# Requirement traceability matrix")
    out.append("")
    out.append(
        "Generated by `tools/traceability_sync.py` from `docs/requirement-ids.md`. "
        "Run `python tools/traceability_sync.py` after adding a requirement ID, and "
        "`python tools/traceability_sync.py --check` at every phase gate."
    )
    out.append("")
    out.append(
        "Edit the ADR, Module, Test, Fixture, Evidence, Status and Notes columns by "
        "hand; they are preserved across regeneration. The Requirement column is "
        "owned by `docs/requirement-ids.md` and is overwritten."
    )
    out.append("")
    out.append("## Status rules")
    out.append("")
    out.append("```text")
    for status in ALLOWED_STATUSES:
        out.append(status)
    out.append("```")
    out.append("")
    out.append(
        "`tested` requires a real evidence artifact path in the Evidence column. "
        "A server-side requirement may not be marked `tested` until the user has "
        "returned the raw run artifacts (`requirements.md` Section 22.4, "
        "`CLAUDE.md` Section 20). `accepted_exception` requires explicit user "
        "approval, a rationale, a risk statement and a tracked follow-up."
    )
    out.append("")
    out.append("## Coverage summary")
    out.append("")
    out.append("| Status | Count |")
    out.append("|---|---|")
    for status in ALLOWED_STATUSES:
        out.append(f"| {status} | {counts.get(status, 0)} |")
    out.append(f"| **total** | **{len(result.rows)}** |")
    out.append("")

    unmapped = [row.req_id for row in result.rows if row.status == "planned"]
    out.append(
        f"{len(unmapped)} of {len(result.rows)} requirements are still `planned`. "
        "A phase cannot be declared complete while a requirement it claims to "
        "deliver is unmapped or falsely marked tested (TEST-250)."
    )
    out.append("")

    for domain in DOMAIN_ORDER:
        domain_rows = [row for row in result.rows if row.domain == domain]
        if not domain_rows:
            continue
        out.append(f"## {DOMAIN_TITLES[domain]}")
        out.append("")
        out.extend(_render_table(domain_rows))
        out.append("")

    if result.orphaned:
        out.append("## Orphaned rows")
        out.append("")
        out.append(
            "These IDs are present in the matrix but no longer in "
            "`docs/requirement-ids.md`. Requirement IDs are permanent, so removal "
            "must be deliberate: either restore the ID to the catalogue or record "
            "why it was withdrawn."
        )
        out.append("")
        out.extend(_render_table(result.orphaned))
        out.append("")

    out.append("## Accepted exceptions")
    out.append("")
    accepted = [row for row in result.rows if row.status == "accepted_exception"]
    if accepted:
        out.extend(_render_table(accepted))
    else:
        out.append("None.")
    out.append("")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the matrix is out of date, without writing",
    )
    args = parser.parse_args(argv)

    catalogue = parse_requirement_ids(IDS_PATH)
    existing = parse_existing_matrix(MATRIX_PATH)
    result = sync(catalogue, existing)

    for row in result.rows:
        if row.status not in ALLOWED_STATUSES:
            print(
                f"invalid status {row.status!r} for {row.req_id}; "
                f"allowed: {', '.join(ALLOWED_STATUSES)}",
                file=sys.stderr,
            )
            return 2
        if row.status == "tested" and row.evidence in ("", EMPTY):
            print(
                f"{row.req_id} is marked tested with no evidence artifact (TEST-230)",
                file=sys.stderr,
            )
            return 2

    rendered = render(result)
    current = MATRIX_PATH.read_text(encoding="utf-8") if MATRIX_PATH.is_file() else ""

    if args.check:
        if rendered != current:
            print(
                "docs/traceability.md is out of date; run: python tools/traceability_sync.py",
                file=sys.stderr,
            )
            return 1
        print(f"traceability matrix current: {len(result.rows)} requirements")
        return 0

    MATRIX_PATH.write_text(rendered, encoding="utf-8", newline="\n")
    print(f"wrote {MATRIX_PATH.relative_to(REPO_ROOT)}: {len(result.rows)} requirements")
    if result.added:
        print(f"added {len(result.added)}: {', '.join(result.added)}")
    if result.orphaned:
        print(f"orphaned {len(result.orphaned)}: " + ", ".join(r.req_id for r in result.orphaned))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
