"""Dependency isolation, enforced rather than promised.

Category B (`requirements.md` Section 25.15 B). ADR-0001 made two rules the
whole layout rests on, and OPS-030 requires them to be a failing test rather
than a convention:

1. ``protocol/`` carries no ML dependency. It is imported by both the client and
   the server, so anything that pulls torch, CTranslate2, SpeechBrain, pyannote
   or vLLM into it defeats the process isolation Section 7 requires.
2. No worker package imports another worker package. Workers communicate through
   ``protocol/`` types and the orchestrator, never by direct import.

The check reads the source with ``ast`` instead of importing it. That matters:
the ML packages are installed in per-domain environments on the pod (ADR-0007)
and are absent from the dev machine, so an import-based check would pass here
for the wrong reason - the offending import would fail to resolve rather than be
detected.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "protocol_conformance_fixture"

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

#: Everything that must never appear in `protocol/`. ADR-0001 and Section 7.
ML_PACKAGES = frozenset(
    {
        "torch",
        "torchaudio",
        "ctranslate2",
        "faster_whisper",
        "whisper",
        "transformers",
        "speechbrain",
        "pyannote",
        "vllm",
        "onnxruntime",
        "numpy",
        "scipy",
        "librosa",
        "soundfile",
    }
)

#: The isolated server components of Section 7. Each is its own dependency
#: domain with its own lock file.
WORKER_PACKAGES = (
    "gateway",
    "orchestrator",
    "asr_worker",
    "speechbrain_worker",
    "diarization_worker",
    "translation_service",
)

#: What `protocol/` is allowed to import beyond the standard library.
PROTOCOL_ALLOWED_THIRD_PARTY = frozenset({"pydantic"})


def _python_files(package: str) -> list[Path]:
    directory = REPO_ROOT / package
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in p.parts)


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported by one file.

    A relative import (``from . import x``) has no top-level name to report and
    is skipped: it cannot reach outside its own package by construction.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if node.module:
                roots.add(node.module.split(".")[0])

    return roots


def _imported_dotted(path: Path) -> set[str]:
    """Full dotted module paths imported by one file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    dotted: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            dotted.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            dotted.add(node.module)

    return dotted


class TestProtocolIsClean:
    """Rule 1: `protocol/` has no ML dependency."""

    def test_protocol_has_source_to_check(self) -> None:
        """A vacuous pass is worse than a failure, so assert there is something."""
        assert len(_python_files("protocol")) >= 5

    @pytest.mark.parametrize("path", _python_files("protocol"), ids=lambda p: p.name)
    def test_no_ml_import(self, path: Path) -> None:
        offenders = _imported_roots(path) & ML_PACKAGES
        assert not offenders, (
            f"{path.relative_to(REPO_ROOT)} imports {sorted(offenders)}; "
            "protocol/ must stay free of ML dependencies (ADR-0001, OPS-020)"
        )

    @pytest.mark.parametrize("path", _python_files("protocol"), ids=lambda p: p.name)
    def test_no_server_or_client_import(self, path: Path) -> None:
        """`protocol/` is imported *by* both sides and must not import either back."""
        offenders = _imported_roots(path) & {"server", "client"}
        assert not offenders, (
            f"{path.relative_to(REPO_ROOT)} imports {sorted(offenders)}; "
            "protocol/ is the shared contract and depends on neither side"
        )

    @pytest.mark.parametrize("path", _python_files("protocol"), ids=lambda p: p.name)
    def test_third_party_imports_are_on_the_allowlist(self, path: Path) -> None:
        import sys

        stdlib = sys.stdlib_module_names
        roots = _imported_roots(path)
        third_party = {
            root
            for root in roots
            if root not in stdlib and root != "protocol" and not root.startswith("_")
        }
        unexpected = third_party - PROTOCOL_ALLOWED_THIRD_PARTY
        assert not unexpected, (
            f"{path.relative_to(REPO_ROOT)} imports {sorted(unexpected)}, which is not on "
            f"protocol/'s allowlist {sorted(PROTOCOL_ALLOWED_THIRD_PARTY)}. Adding one is a "
            "dependency-isolation decision and needs an ADR amendment"
        )


class TestWorkersDoNotImportEachOther:
    """Rule 2: workers talk through `protocol/` and the orchestrator, not directly."""

    @pytest.mark.parametrize("worker", WORKER_PACKAGES)
    def test_worker_imports_no_sibling(self, worker: str) -> None:
        siblings = {f"server.{other}" for other in WORKER_PACKAGES if other != worker}
        violations: list[str] = []

        for path in _python_files(f"server/{worker}"):
            for dotted in _imported_dotted(path):
                for sibling in siblings:
                    if dotted == sibling or dotted.startswith(f"{sibling}."):
                        violations.append(f"{path.relative_to(REPO_ROOT)} -> {dotted}")

        assert not violations, (
            "cross-worker import found:\n  "
            + "\n  ".join(violations)
            + "\nWorkers are separate dependency domains (Section 7, ADR-0001). "
            "Share types through protocol/ instead."
        )


class TestClientDoesNotImportServer:
    @pytest.mark.parametrize("path", _python_files("client"), ids=lambda p: p.name)
    def test_client_stays_out_of_server(self, path: Path) -> None:
        offenders = _imported_roots(path) & {"server"}
        assert not offenders, (
            f"{path.relative_to(REPO_ROOT)} imports server; the client reaches the "
            "server over the WebSocket protocol, never by import"
        )


class TestTheCheckerItself:
    """A test that cannot fail is not a test. These prove the detector works."""

    def test_detects_a_plain_import(self, tmp_path: Path) -> None:
        source = tmp_path / "sample.py"
        source.write_text("import torch\nimport json\n", encoding="utf-8")
        assert _imported_roots(source) & ML_PACKAGES == {"torch"}

    def test_detects_a_from_import(self, tmp_path: Path) -> None:
        source = tmp_path / "sample.py"
        source.write_text("from speechbrain.inference import EncoderClassifier\n", encoding="utf-8")
        assert _imported_roots(source) & ML_PACKAGES == {"speechbrain"}

    def test_detects_an_import_nested_in_a_function(self, tmp_path: Path) -> None:
        """A deferred import is still a dependency; ast.walk reaches it."""
        source = tmp_path / "sample.py"
        source.write_text("def load():\n    import pyannote.audio\n", encoding="utf-8")
        assert _imported_roots(source) & ML_PACKAGES == {"pyannote"}

    def test_relative_imports_are_ignored(self, tmp_path: Path) -> None:
        source = tmp_path / "sample.py"
        source.write_text("from . import sibling\nfrom .deep import thing\n", encoding="utf-8")
        assert _imported_roots(source) == set()

    def test_dotted_paths_are_reported_in_full(self, tmp_path: Path) -> None:
        source = tmp_path / "sample.py"
        source.write_text("from server.asr_worker.decode import run\n", encoding="utf-8")
        assert _imported_dotted(source) == {"server.asr_worker.decode"}
