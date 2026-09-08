"""Round-trip conformance vectors for the traceability matrix tooling.

Category B (`negative_test_vector`): purpose-built inputs exercising defensive
branches. These say nothing about ASR, language, speaker, overlap or
translation quality, and must never be cited as if they did
(`requirements.md` Section 25.15 B, TEST-130).

The bug these exist for: a Notes cell containing a literal ``|`` split one
Markdown row into thirteen cells. The row then failed the column-count check,
was skipped by the parser, and on the next sync the requirement looked new — so
its hand-entered ADR, module and status were silently reset. Nothing raised.
The matrix simply forgot.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import traceability_sync as ts  # noqa: E402

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "negative_test_vector"


def _row(**overrides: str) -> ts.Row:
    fields: dict[str, str] = {
        "req_id": "PROT-010",
        "requirement": "an ordinary requirement",
    }
    fields.update(overrides)
    return ts.Row(**fields)


def _round_trip(row: ts.Row) -> ts.Row:
    """Render one row to Markdown and parse it back."""
    rendered = ts._render_table([row])
    data_line = rendered[-1]
    cells = ts._split_markdown_row(data_line)
    assert len(cells) == len(ts.COLUMNS), (
        f"row split into {len(cells)} cells, expected {len(ts.COLUMNS)}: {data_line}"
    )
    return ts.Row(
        req_id=cells[0],
        requirement=cells[1],
        adr=cells[2],
        module=cells[3],
        test=cells[4],
        fixture=cells[5],
        evidence=cells[6],
        status=cells[7],
        notes=cells[8],
    )


class TestCellEscaping:
    """A cell must survive render-then-parse unchanged, whatever it contains."""

    @pytest.mark.parametrize(
        "notes",
        [
            pytest.param("operation: create | revise | split | merge | seal", id="pipes"),
            pytest.param("| leading pipe", id="leading-pipe"),
            pytest.param("trailing pipe |", id="trailing-pipe"),
            pytest.param("||", id="adjacent-pipes"),
            pytest.param(r"already \| escaped", id="pre-escaped"),
            pytest.param("no pipes at all", id="plain"),
            pytest.param("-", id="empty-marker"),
        ],
    )
    def test_notes_survive_round_trip(self, notes: str) -> None:
        original = _row(notes=notes)
        assert _round_trip(original).notes == notes

    def test_every_column_survives_a_pipe(self) -> None:
        pipey = "a | b"
        original = _row(
            requirement=pipey,
            adr=pipey,
            module=pipey,
            test=pipey,
            fixture=pipey,
            evidence=pipey,
            notes=pipey,
        )
        recovered = _round_trip(original)
        for column in ("requirement", "adr", "module", "test", "fixture", "evidence", "notes"):
            assert getattr(recovered, column) == pipey, f"{column} lost its pipe"


class TestSyncPreservesHandEnteredWork:
    """The regression the escaping bug actually caused."""

    def test_a_pipe_in_notes_does_not_reset_a_row(self) -> None:
        catalogue = {"PROT-240": "Split and merge operations carry lineage fields"}
        existing = {
            "PROT-240": _row(
                req_id="PROT-240",
                requirement="Split and merge operations carry lineage fields",
                adr="ADR-0008",
                module="protocol/",
                status="implemented",
                notes="operation: create | revise | split | merge | seal",
            )
        }

        rendered = ts.render(ts.sync(catalogue, existing))
        reparsed = {}
        for line in rendered.splitlines():
            cells = ts._split_markdown_row(line)
            if len(cells) == len(ts.COLUMNS) and ts.ID_PATTERN.match(cells[0]):
                reparsed[cells[0]] = cells

        assert "PROT-240" in reparsed, "the row vanished from its own rendered output"
        recovered = reparsed["PROT-240"]
        assert recovered[2] == "ADR-0008"
        assert recovered[3] == "protocol/"
        assert recovered[7] == "implemented"

        second_pass = ts.sync(catalogue, ts.parse_existing_matrix_from_text(rendered))
        assert second_pass.added == [], "a second sync treated an existing row as new"

    def test_catalogue_owns_the_requirement_text(self) -> None:
        catalogue = {"PROT-010": "the new wording"}
        existing = {"PROT-010": _row(requirement="the old wording", status="implemented")}

        result = ts.sync(catalogue, existing)

        assert result.rows[0].requirement == "the new wording"
        assert result.rows[0].status == "implemented", "status is not the catalogue's to change"

    def test_a_removed_id_is_orphaned_not_deleted(self) -> None:
        """Requirement IDs are permanent (Section 25.16); removal must be visible."""
        result = ts.sync({}, {"PROT-010": _row(status="implemented")})

        assert result.rows == []
        assert [row.req_id for row in result.orphaned] == ["PROT-010"]


class TestIdParsing:
    @pytest.mark.parametrize(
        "req_id",
        ["AUD-010", "PROT-400", "OPS-1080", "TEST-270"],
    )
    def test_accepts_valid_ids(self, req_id: str) -> None:
        assert ts.ID_PATTERN.match(req_id)

    @pytest.mark.parametrize(
        "req_id",
        [
            pytest.param("OPS-10", id="too-few-digits"),
            pytest.param("OPS-10800", id="too-many-digits"),
            pytest.param("NOPE-010", id="unknown-domain"),
            pytest.param("ops-010", id="lowercase"),
            pytest.param("OPS_010", id="underscore"),
            pytest.param("", id="empty"),
        ],
    )
    def test_rejects_invalid_ids(self, req_id: str) -> None:
        assert not ts.ID_PATTERN.match(req_id)


class TestRealCatalogue:
    """The committed catalogue and matrix must actually agree."""

    def test_catalogue_parses_and_has_no_duplicates(self) -> None:
        catalogue = ts.parse_requirement_ids(ts.IDS_PATH)
        assert len(catalogue) > 250, "catalogue looks truncated"
        for req_id in catalogue:
            assert ts.ID_PATTERN.match(req_id)

    def test_matrix_is_in_sync_with_the_catalogue(self) -> None:
        catalogue = ts.parse_requirement_ids(ts.IDS_PATH)
        existing = ts.parse_existing_matrix(ts.MATRIX_PATH)
        result = ts.sync(catalogue, existing)

        assert result.added == [], "run: python tools/traceability_sync.py"
        assert result.orphaned == [], "an ID disappeared from the catalogue"

    def test_no_requirement_is_marked_tested_without_evidence(self) -> None:
        """TEST-230. The whole point of the matrix."""
        for row in ts.parse_existing_matrix(ts.MATRIX_PATH).values():
            assert row.status in ts.ALLOWED_STATUSES, f"{row.req_id}: {row.status}"
            if row.status == "tested":
                assert row.evidence not in ("", ts.EMPTY), (
                    f"{row.req_id} is marked tested with no evidence artifact"
                )
