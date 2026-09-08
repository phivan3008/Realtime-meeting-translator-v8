"""Clip provenance and locked-set guard vectors.

Category B (`requirements.md` Section 25.15 B). The subject is the provenance
machinery, not audio: bounds arithmetic, the locked-set refusal, the audit
trail, and the manifest entry's required fields.

**No audio is fabricated anywhere in this module.** Section 22.3 forbids
synthetic audio, so the guard logic is tested as pure functions with no file at
all, and the one test that needs real samples reads the real recording and skips
when it is absent. Nothing here claims anything about ASR, language, speaker,
overlap or translation quality (TEST-130).

The rule under test that matters most is ADR-0012 D31: a locked-set cut is
refused without an explicit flag, and every permitted access is logged. That is
the difference between TEST-160 being a rule and being a habit.
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path
from typing import Any, ClassVar

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import cut_clip  # noqa: E402

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "negative_test_vector"

MEETING_FRAMES = 29_139_328
MEETING_SHA256 = "9f4e36d146c442f926307a8ff0e6841594ca788505c2949eedb5bfe1a8007625"
RECORDING_PATH = REPO_ROOT / "data" / "recordings" / "meeting_record.wav"

recording_present = pytest.mark.skipif(
    not RECORDING_PATH.is_file(),
    reason=(
        "the real recording is git-ignored by design; this test reads it and is "
        "skipped on a machine that does not have it"
    ),
)


def manifest_with_splits(splits: dict[str, list[dict[str, int]]]) -> dict[str, Any]:
    return {
        "recordings": [
            {
                "id": "meeting-001",
                "location": "data/recordings/meeting_record.wav",
                "sha256": MEETING_SHA256,
                "sample_rate_hz": 16_000,
                "channels": 1,
                "frames": MEETING_FRAMES,
            }
        ],
        "splits": splits,
    }


def args(**overrides: Any) -> Any:
    import argparse

    namespace = argparse.Namespace(
        start_sample=None,
        end_sample=None,
        start_ms=None,
        end_ms=None,
    )
    for key, value in overrides.items():
        setattr(namespace, key, value)
    return namespace


@pytest.fixture
def recording() -> cut_clip.Recording:
    return cut_clip.find_recording(manifest_with_splits({}), "meeting-001")


class TestBounds:
    def test_samples_are_taken_as_given(self, recording: cut_clip.Recording) -> None:
        start, end = cut_clip.resolve_bounds(args(start_sample=1000, end_sample=2000), recording)
        assert (start, end) == (1000, 2000)

    def test_milliseconds_convert_to_samples(self, recording: cut_clip.Recording) -> None:
        start, end = cut_clip.resolve_bounds(args(start_ms=1000, end_ms=2000), recording)
        assert (start, end) == (16_000, 32_000)

    def test_mixing_units_on_one_end_is_refused(self, recording: cut_clip.Recording) -> None:
        """Ambiguity about which unit won is exactly how a fixture ends up citing
        the wrong interval."""
        with pytest.raises(cut_clip.ClipError, match="not both"):
            cut_clip.resolve_bounds(
                args(start_sample=1000, start_ms=100, end_sample=2000), recording
            )

    def test_an_empty_range_is_refused(self, recording: cut_clip.Recording) -> None:
        with pytest.raises(cut_clip.ClipError, match="does not follow"):
            cut_clip.resolve_bounds(args(start_sample=1000, end_sample=1000), recording)

    def test_a_reversed_range_is_refused(self, recording: cut_clip.Recording) -> None:
        with pytest.raises(cut_clip.ClipError, match="does not follow"):
            cut_clip.resolve_bounds(args(start_sample=2000, end_sample=1000), recording)

    def test_a_negative_start_is_refused(self, recording: cut_clip.Recording) -> None:
        with pytest.raises(cut_clip.ClipError, match="precedes the session start"):
            cut_clip.resolve_bounds(args(start_sample=-1, end_sample=1000), recording)

    def test_running_past_the_recording_is_refused(self, recording: cut_clip.Recording) -> None:
        with pytest.raises(cut_clip.ClipError, match="runs past the recording"):
            cut_clip.resolve_bounds(
                args(start_sample=MEETING_FRAMES - 10, end_sample=MEETING_FRAMES + 1), recording
            )

    def test_the_last_sample_is_reachable(self, recording: cut_clip.Recording) -> None:
        """Half-open intervals: end == frames is the whole file, not one past it."""
        _, end = cut_clip.resolve_bounds(args(start_sample=0, end_sample=MEETING_FRAMES), recording)
        assert end == MEETING_FRAMES


class TestLockedSetGuard:
    """ADR-0012 D31, TEST-160."""

    LOCKED: ClassVar[list[dict[str, int]]] = [{"start_sample": 1_000_000, "end_sample": 2_000_000}]

    def splits(self) -> list[cut_clip.SplitRange]:
        return cut_clip.load_splits(manifest_with_splits({"locked": self.LOCKED}))

    def test_an_unassigned_split_protects_nothing_yet(self) -> None:
        """While `splits` is empty the guard has nothing to protect, which is not
        the same as the guard being off."""
        assert cut_clip.load_splits(manifest_with_splits({})) == []

    def test_a_locked_range_is_refused_without_the_flag(self) -> None:
        with pytest.raises(cut_clip.ClipError, match="locked evaluation"):
            cut_clip.check_locked_set(
                self.splits(), 1_500_000, 1_600_000, evaluation_run=False, purpose=""
            )

    def test_even_a_one_sample_overlap_is_refused(self) -> None:
        """Contamination is not proportional to how much of the locked set was read."""
        with pytest.raises(cut_clip.ClipError, match="locked evaluation"):
            cut_clip.check_locked_set(
                self.splits(), 999_999, 1_000_001, evaluation_run=False, purpose=""
            )

    def test_an_adjacent_range_is_allowed(self) -> None:
        """Half-open intervals again: ending exactly at the locked start is outside."""
        assert (
            cut_clip.check_locked_set(
                self.splits(), 900_000, 1_000_000, evaluation_run=False, purpose=""
            )
            == []
        )

    def test_the_flag_alone_is_not_enough(self) -> None:
        """--evaluation-run without a purpose would leave an audit line saying nothing."""
        with pytest.raises(cut_clip.ClipError, match="requires --purpose"):
            cut_clip.check_locked_set(
                self.splits(), 1_500_000, 1_600_000, evaluation_run=True, purpose=""
            )

    def test_an_explicit_evaluation_run_is_permitted_and_returns_what_it_touched(self) -> None:
        touched = cut_clip.check_locked_set(
            self.splits(),
            1_500_000,
            1_600_000,
            evaluation_run=True,
            purpose="final ASR assessment",
        )
        assert [(s.start_sample, s.end_sample) for s in touched] == [(1_000_000, 2_000_000)]

    def test_a_development_range_is_not_guarded(self) -> None:
        splits = cut_clip.load_splits(
            manifest_with_splits({"development": [{"start_sample": 0, "end_sample": 500_000}]})
        )
        assert cut_clip.check_locked_set(splits, 0, 100, evaluation_run=False, purpose="") == []


class TestAuditTrail:
    def test_the_first_access_creates_the_log_with_a_header(self, tmp_path: Path) -> None:
        log = tmp_path / "evaluation-log.md"
        touched = [cut_clip.SplitRange("locked", 1_000_000, 2_000_000)]

        cut_clip.record_locked_access(touched, 1_500_000, 1_600_000, "baseline", log_path=log)

        text = log.read_text(encoding="utf-8")
        assert "Locked evaluation set access log" in text
        assert "| Date | Samples | Locked ranges | Purpose |" in text
        assert "baseline" in text

    def test_accesses_accumulate(self, tmp_path: Path) -> None:
        """The count is the signal: thirty lines means the set is no longer locked."""
        log = tmp_path / "evaluation-log.md"
        touched = [cut_clip.SplitRange("locked", 1_000_000, 2_000_000)]

        for index in range(3):
            cut_clip.record_locked_access(
                touched, 1_500_000, 1_600_000, f"run {index}", log_path=log
            )

        rows = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.startswith("| 2")]
        assert len(rows) == 3


class TestSourceVerification:
    def test_a_missing_source_is_reported_clearly(self, tmp_path: Path) -> None:
        recording = cut_clip.Recording(
            recording_id="meeting-001",
            path=tmp_path / "absent.wav",
            sha256=MEETING_SHA256,
            sample_rate_hz=16_000,
            channels=1,
            frames=MEETING_FRAMES,
        )
        with pytest.raises(cut_clip.ClipError, match="git-ignored by design"):
            cut_clip.verify_source(recording)

    def test_an_unknown_recording_id_lists_what_is_known(self) -> None:
        with pytest.raises(cut_clip.ClipError, match="meeting-001"):
            cut_clip.find_recording(manifest_with_splits({}), "meeting-999")


class TestManifestEntry:
    def test_every_required_provenance_field_is_present(self, tmp_path: Path) -> None:
        """TEST-030 lists exactly what Section 22.2 requires."""
        clip = tmp_path / "sample.wav"
        clip.write_bytes(b"not audio, only bytes to hash")
        recording = cut_clip.find_recording(manifest_with_splits({}), "meeting-001")

        entry = cut_clip.build_entry(
            recording, clip, 16_000, 32_000, ["filler", "japanese"], "reviewer", ""
        )

        assert entry["location"].endswith("sample.wav")
        for required in (
            "source_file_sha256",
            "start_ms",
            "end_ms",
            "clip_sha256",
            "labels",
            "reviewed_by",
            "reviewed_at",
        ):
            assert required in entry, f"Section 22.2 requires {required}"

    def test_sample_offsets_are_recorded_alongside_milliseconds(self, tmp_path: Path) -> None:
        """PROT-140: samples are the authority, milliseconds are for reading."""
        clip = tmp_path / "sample.wav"
        clip.write_bytes(b"x")
        recording = cut_clip.find_recording(manifest_with_splits({}), "meeting-001")

        entry = cut_clip.build_entry(recording, clip, 31, 16_031, ["x"], "reviewer", "")

        assert entry["start_sample"] == 31
        assert entry["start_ms"] == 1, "floor(31 * 1000 / 16000)"
        assert entry["sample_count"] == 16_000

    def test_a_duplicate_clip_id_is_refused(self, tmp_path: Path) -> None:
        """Fixture identifiers are citations; reusing one silently redirects it."""
        manifest = tmp_path / "clips.yaml"
        entry = {"clip_id": "meeting-001-0-16000", "clip_sha256": "abc"}

        cut_clip.append_clip_entry(dict(entry), path=manifest)
        with pytest.raises(cut_clip.ClipError, match="already in"):
            cut_clip.append_clip_entry(dict(entry), path=manifest)


class TestRealRecordingCut:
    """Reads the real recording to prove the cut is byte-exact.

    This makes **no claim about audio quality or model behaviour**. It asserts
    that the bytes written are the bytes read, which is what makes a clip a
    citation rather than a transformation.
    """

    @recording_present
    def test_the_cut_is_byte_identical_to_the_source_range(self, tmp_path: Path) -> None:
        recording = cut_clip.find_recording(manifest_with_splits({}), "meeting-001")
        start, end = 16_000, 48_000
        destination = tmp_path / "clip.wav"

        cut_clip.cut_samples(recording, start, end, destination)

        with wave.open(str(recording.path), "rb") as source:
            source.setpos(start)
            expected = source.readframes(end - start)
        with wave.open(str(destination), "rb") as clip:
            assert clip.getnframes() == end - start
            assert clip.getframerate() == 16_000
            assert clip.getnchannels() == 1
            assert clip.readframes(end - start) == expected

    @recording_present
    def test_the_manifest_hash_still_matches_the_file_on_disk(self) -> None:
        """The provenance chain is only as good as this check."""
        recording = cut_clip.find_recording(manifest_with_splits({}), "meeting-001")
        cut_clip.verify_source(recording)
