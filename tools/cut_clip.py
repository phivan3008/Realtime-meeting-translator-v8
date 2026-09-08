"""Cut a clip from a real recording, with provenance.

`requirements.md` Section 22.2 permits "clips extracted from that recording with
provenance metadata" as a category A fixture, and requires every derived fixture
to record ``source_file_sha256``, ``start_ms``, ``end_ms``, ``clip_sha256``,
human-reviewed ``labels``, ``reviewed_by`` and ``reviewed_at`` (TEST-030).

Three rules are enforced here rather than left to discipline:

1. **The source hash is verified before a single sample is read.** A clip whose
   provenance points at a file that has changed is not provenance, it is a
   citation to something that no longer exists.
2. **Cutting is sample-exact.** Offsets are canonical 16 kHz sample offsets
   (Section 25.1, PROT-140). Milliseconds are accepted for convenience and
   converted, and both are recorded, but the samples are the authority.
3. **The locked evaluation set refuses to be cut without an explicit flag**, and
   every such access is appended to ``docs/evaluation-log.md``
   (ADR-0012 D31, TEST-160). A rule with no mechanism is a habit.

The clip lands in ``data/``, which `.gitignore` excludes. Only the metadata is
committed (PERS-120).

Usage::

    python tools/cut_clip.py --source meeting-001 \\
        --start-sample 1248000 --end-sample 1320000 \\
        --label filler --label japanese \\
        --reviewed-by "<name>"
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = REPO_ROOT / "tests" / "manifests" / "source-recordings.yaml"
CLIP_MANIFEST_PATH = REPO_ROOT / "tests" / "fixtures" / "clips.yaml"
EVALUATION_LOG_PATH = REPO_ROOT / "docs" / "evaluation-log.md"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "clips"

CANONICAL_SAMPLE_RATE_HZ = 16_000
READ_CHUNK_BYTES = 1024 * 1024

LOCKED_SPLIT = "locked"


class ClipError(RuntimeError):
    """Anything that would produce a fixture with untrustworthy provenance."""


@dataclass(frozen=True, slots=True)
class Recording:
    """One entry from the source manifest."""

    recording_id: str
    path: Path
    sha256: str
    sample_rate_hz: int
    channels: int
    frames: int

    @property
    def duration_seconds(self) -> float:
        return self.frames / self.sample_rate_hz


@dataclass(frozen=True, slots=True)
class SplitRange:
    """One assigned range from the evaluation split."""

    split: str
    start_sample: int
    end_sample: int

    def intersects(self, start_sample: int, end_sample: int) -> bool:
        return self.start_sample < end_sample and start_sample < self.end_sample


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise ClipError(f"missing source manifest {path.relative_to(REPO_ROOT)}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ClipError(f"{path.name} did not parse as a mapping")
    return loaded


def find_recording(manifest: dict[str, Any], recording_id: str) -> Recording:
    for entry in manifest.get("recordings", []):
        if entry.get("id") != recording_id:
            continue
        return Recording(
            recording_id=recording_id,
            path=REPO_ROOT / str(entry["location"]),
            sha256=str(entry["sha256"]).lower(),
            sample_rate_hz=int(entry["sample_rate_hz"]),
            channels=int(entry["channels"]),
            frames=int(entry["frames"]),
        )
    known = [entry.get("id") for entry in manifest.get("recordings", [])]
    raise ClipError(f"unknown recording {recording_id!r}; manifest has {known}")


def load_splits(manifest: dict[str, Any]) -> list[SplitRange]:
    """Read the evaluation split, if one has been assigned yet.

    Returns an empty list while ``splits`` is empty, which is its state until the
    condition survey is done (ADR-0012 D30). An empty split means the locked-set
    guard has nothing to protect *yet*, not that the guard is off.
    """
    splits = manifest.get("splits") or {}
    ranges: list[SplitRange] = []
    for split_name, entries in splits.items():
        for entry in entries or []:
            ranges.append(
                SplitRange(
                    split=str(split_name),
                    start_sample=int(entry["start_sample"]),
                    end_sample=int(entry["end_sample"]),
                )
            )
    return ranges


def verify_source(recording: Recording) -> None:
    """Confirm the file on disk is the one the manifest describes."""
    if not recording.path.is_file():
        raise ClipError(
            f"{recording.path} is missing. The recording is git-ignored by design; "
            "copy it to data/recordings/ before cutting clips"
        )

    actual = sha256_of(recording.path)
    if actual != recording.sha256:
        raise ClipError(
            f"source hash mismatch for {recording.recording_id}\n"
            f"  manifest {recording.sha256}\n"
            f"  on disk  {actual}\n"
            "Provenance pointing at a changed file is not provenance."
        )


def check_locked_set(
    splits: list[SplitRange],
    start_sample: int,
    end_sample: int,
    *,
    evaluation_run: bool,
    purpose: str,
) -> list[SplitRange]:
    """Refuse a locked-set cut unless it is an explicit evaluation run.

    ADR-0012 D31 and TEST-160. Returns the locked ranges touched, so the caller
    can log them.
    """
    touched = [
        span
        for span in splits
        if span.split == LOCKED_SPLIT and span.intersects(start_sample, end_sample)
    ]
    if not touched:
        return []

    if not evaluation_run:
        described = ", ".join(f"[{s.start_sample}, {s.end_sample})" for s in touched)
        raise ClipError(
            f"samples [{start_sample}, {end_sample}) intersect the locked evaluation "
            f"set at {described}.\n"
            "The locked set is for final unbiased assessment only (TEST-160). "
            "If this really is an evaluation run, pass --evaluation-run and a "
            "--purpose, and the access will be recorded in docs/evaluation-log.md."
        )

    if not purpose:
        raise ClipError("--evaluation-run requires --purpose, which is what gets logged")

    return touched


def record_locked_access(
    touched: list[SplitRange],
    start_sample: int,
    end_sample: int,
    purpose: str,
    *,
    log_path: Path = EVALUATION_LOG_PATH,
) -> None:
    """Append one line to the locked-set audit trail.

    The trail is the point, not just the guard: if the locked set turns out to
    have been touched thirty times, the final report has to say so rather than
    present a contaminated number.
    """
    if not log_path.is_file():
        log_path.write_text(
            "# Locked evaluation set access log\n"
            "\n"
            "Every read of the locked evaluation set, appended automatically by\n"
            "`tools/cut_clip.py` (ADR-0012 D31, TEST-160). A long list here means\n"
            "the locked set is no longer locked, and the final report must say so.\n"
            "\n"
            "| Date | Samples | Locked ranges | Purpose |\n"
            "|---|---|---|---|\n",
            encoding="utf-8",
            newline="\n",
        )

    ranges = " ".join(f"[{s.start_sample},{s.end_sample})" for s in touched)
    today = dt.datetime.now(dt.UTC).date().isoformat()
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"| {today} | [{start_sample},{end_sample}) | {ranges} | {purpose} |\n")


def cut_samples(
    recording: Recording, start_sample: int, end_sample: int, destination: Path
) -> None:
    """Write the requested sample range to a new WAV, byte-exactly.

    Uses the standard library only. No resampling, no downmix, no normalisation:
    a clip is a slice of the source, and anything else would make its hash a
    hash of a transformation rather than of a citation.
    """
    with wave.open(str(recording.path), "rb") as source:
        if source.getframerate() != recording.sample_rate_hz:
            raise ClipError(
                f"{recording.path.name} reports {source.getframerate()} Hz but the "
                f"manifest says {recording.sample_rate_hz}"
            )
        if source.getnchannels() != recording.channels:
            raise ClipError(
                f"{recording.path.name} reports {source.getnchannels()} channels but the "
                f"manifest says {recording.channels}"
            )

        source.setpos(start_sample)
        payload = source.readframes(end_sample - start_sample)

        destination.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(destination), "wb") as clip:
            clip.setnchannels(source.getnchannels())
            clip.setsampwidth(source.getsampwidth())
            clip.setframerate(source.getframerate())
            clip.writeframes(payload)


def samples_to_ms(sample_offset: int) -> int:
    """Section 25.1: floor(sample_offset * 1000 / 16000)."""
    return sample_offset * 1000 // CANONICAL_SAMPLE_RATE_HZ


def manifest_location(path: Path) -> str:
    """How a clip's path is written into the committed manifest.

    Repo-relative POSIX when the clip is inside the tree, which is where
    ``--out-dir`` puts it. A path outside the tree is recorded absolutely rather
    than raising: refusing to record provenance is worse than recording an
    awkward path.
    """
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def build_entry(
    recording: Recording,
    destination: Path,
    start_sample: int,
    end_sample: int,
    labels: list[str],
    reviewed_by: str,
    note: str,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "clip_id": destination.stem,
        "source_recording_id": recording.recording_id,
        "source_file_sha256": recording.sha256,
        "start_sample": start_sample,
        "end_sample": end_sample,
        "start_ms": samples_to_ms(start_sample),
        "end_ms": samples_to_ms(end_sample),
        "sample_count": end_sample - start_sample,
        "clip_sha256": sha256_of(destination),
        "location": manifest_location(destination),
        "labels": sorted(labels),
        "labels_reviewed": "human_reviewed",
        "reviewed_by": reviewed_by,
        "reviewed_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    if note:
        entry["note"] = note
    return entry


def append_clip_entry(entry: dict[str, Any], *, path: Path = CLIP_MANIFEST_PATH) -> None:
    """Append to the committed clip manifest.

    The clip audio is git-ignored; this metadata is not. That asymmetry is the
    whole provenance model (PERS-120).
    """
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else None
    document: dict[str, Any] = loaded if isinstance(loaded, dict) else {}
    document.setdefault("schema_version", 1)
    clips: list[dict[str, Any]] = list(document.get("clips") or [])

    existing_ids = {clip.get("clip_id") for clip in clips}
    if entry["clip_id"] in existing_ids:
        raise ClipError(
            f"clip_id {entry['clip_id']!r} is already in {path.name}. "
            "Clip identifiers are not reused; choose a different --name."
        )

    clips.append(entry)
    document["clips"] = clips

    header = (
        "# Derived clip fixtures.\n"
        "#\n"
        "# Metadata only. The audio lives under data/ and is never committed\n"
        "# (PERS-120). Every entry carries the Section 22.2 provenance fields,\n"
        "# and offsets are canonical 16 kHz sample offsets (PROT-140).\n"
        "#\n"
        "# Appended by tools/cut_clip.py. Verify a clip still matches its entry:\n"
        "#   python tools/hash_file.py <location> --expect-sha256 <clip_sha256>\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        header + yaml.safe_dump(document, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="meeting-001", help="recording id in the manifest")

    bounds = parser.add_argument_group("range, in canonical 16 kHz samples or milliseconds")
    bounds.add_argument("--start-sample", type=int)
    bounds.add_argument("--end-sample", type=int)
    bounds.add_argument("--start-ms", type=int)
    bounds.add_argument("--end-ms", type=int)

    parser.add_argument(
        "--label",
        action="append",
        default=[],
        dest="labels",
        help="human-reviewed label; repeatable. At least one is required",
    )
    parser.add_argument(
        "--reviewed-by",
        required=True,
        help="who reviewed the labels. Section 22.2 requires labels to be human-reviewed, "
        "so a clip cannot be cut anonymously",
    )
    parser.add_argument("--name", help="clip id; defaults to <source>-<start>-<end>")
    parser.add_argument("--note", default="", help="anything unusual about this clip")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--evaluation-run",
        action="store_true",
        help="permit cutting from the locked evaluation set (ADR-0012 D31)",
    )
    parser.add_argument("--purpose", default="", help="required with --evaluation-run")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and report without writing anything",
    )
    return parser.parse_args(argv)


def resolve_bounds(args: argparse.Namespace, recording: Recording) -> tuple[int, int]:
    if args.start_sample is not None and args.start_ms is not None:
        raise ClipError("give --start-sample or --start-ms, not both")
    if args.end_sample is not None and args.end_ms is not None:
        raise ClipError("give --end-sample or --end-ms, not both")

    start = args.start_sample
    if start is None and args.start_ms is not None:
        start = args.start_ms * CANONICAL_SAMPLE_RATE_HZ // 1000
    end = args.end_sample
    if end is None and args.end_ms is not None:
        end = args.end_ms * CANONICAL_SAMPLE_RATE_HZ // 1000

    if start is None or end is None:
        raise ClipError("a start and an end are required")
    if start < 0:
        raise ClipError(f"start {start} precedes the session start")
    if end <= start:
        raise ClipError(f"end {end} does not follow start {start}")
    if end > recording.frames:
        raise ClipError(f"end {end} runs past the recording, which is {recording.frames} samples")

    return start, end


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        if not args.labels:
            raise ClipError("at least one --label is required; an unlabelled clip is not a fixture")

        manifest = load_manifest()
        recording = find_recording(manifest, args.source)
        start_sample, end_sample = resolve_bounds(args, recording)

        touched = check_locked_set(
            load_splits(manifest),
            start_sample,
            end_sample,
            evaluation_run=args.evaluation_run,
            purpose=args.purpose,
        )

        verify_source(recording)

        clip_id = args.name or f"{recording.recording_id}-{start_sample}-{end_sample}"
        destination = args.out_dir / f"{clip_id}.wav"

        if args.dry_run:
            print(
                f"dry run: would cut {clip_id} "
                f"[{start_sample}, {end_sample}) "
                f"= {samples_to_ms(end_sample - start_sample)} ms -> {destination}"
            )
            return 0

        cut_samples(recording, start_sample, end_sample, destination)
        entry = build_entry(
            recording,
            destination,
            start_sample,
            end_sample,
            args.labels,
            args.reviewed_by,
            args.note,
        )
        append_clip_entry(entry)

        if touched:
            record_locked_access(touched, start_sample, end_sample, args.purpose)
            print(f"LOCKED SET ACCESS recorded in {EVALUATION_LOG_PATH.name}")

        print(f"wrote {destination.relative_to(REPO_ROOT)}")
        print(f"clip_sha256 {entry['clip_sha256']}")
        print(f"recorded in {CLIP_MANIFEST_PATH.relative_to(REPO_ROOT)}")
        return 0

    except ClipError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
