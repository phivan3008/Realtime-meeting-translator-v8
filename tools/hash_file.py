"""Record the provenance of a real audio fixture source.

Prints the fields ``requirements.md`` Section 22.2 requires before a file may be
used as a fixture source: SHA-256, size, and — for WAV, which the standard
library can read without any third-party dependency — container, codec, sample
rate, channel count, sample width and duration.

The hash is computed over the file exactly as it sits on disk, so the value can
be compared against one produced by ``Get-FileHash`` or ``sha256sum`` on any
other machine.

Usage::

    python tools/hash_file.py data/recordings/meeting_record.wav
    python tools/hash_file.py data/recordings/meeting_record.wav --yaml

``--yaml`` emits the block that belongs in ``tests/manifests/``.

Audio files are never committed (PERS-120); this tool exists so that the
*metadata* can be, and so a later run can prove the bytes have not changed.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import wave
from dataclasses import dataclass
from pathlib import Path

READ_CHUNK_BYTES = 1024 * 1024

WAV_ENCODING_BY_WIDTH = {
    1: "pcm_u8",
    2: "pcm_s16le",
    3: "pcm_s24le",
    4: "pcm_s32le",
}


@dataclass
class AudioProvenance:
    """Everything recorded about a fixture source file."""

    path: Path
    sha256: str
    size_bytes: int
    container: str
    codec: str | None = None
    sample_rate_hz: int | None = None
    channels: int | None = None
    sample_width_bytes: int | None = None
    frames: int | None = None
    duration_seconds: float | None = None
    note: str | None = None

    @property
    def canonical_samples(self) -> int | None:
        """Length in 16 kHz samples, the project's canonical media unit.

        ``requirements.md`` Section 25.1 makes the integer sample offset at
        16 kHz the canonical timeline, so a fixture's length is recorded in that
        unit as well as in seconds.
        """
        if self.duration_seconds is None:
            return None
        return int(self.duration_seconds * 16000)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def read_wav_properties(path: Path) -> dict[str, object]:
    """Return WAV format fields, or an explanation of why they are unavailable."""
    try:
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            sample_rate = wav.getframerate()
            frames = wav.getnframes()
    except wave.Error as exc:
        return {"note": f"not a readable PCM WAV: {exc}"}

    duration = frames / sample_rate if sample_rate else None
    return {
        "codec": WAV_ENCODING_BY_WIDTH.get(sample_width, f"pcm_{sample_width * 8}bit"),
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "sample_width_bytes": sample_width,
        "frames": frames,
        "duration_seconds": duration,
    }


def inspect(path: Path) -> AudioProvenance:
    if not path.is_file():
        raise SystemExit(f"not a file: {path}")

    container = path.suffix.lstrip(".").lower() or "unknown"
    provenance = AudioProvenance(
        path=path,
        sha256=sha256_of(path),
        size_bytes=path.stat().st_size,
        container=container,
    )

    if container == "wav":
        for key, value in read_wav_properties(path).items():
            setattr(provenance, key, value)
    else:
        provenance.note = (
            f"{container} is not readable by the standard library; "
            "format fields must be filled in by hand or by a Phase 1 tool"
        )

    return provenance


def _format_duration(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60)
    return f"{int(minutes)}m {remainder:.2f}s"


def render_human(provenance: AudioProvenance) -> str:
    lines = [
        f"path                 {provenance.path}",
        f"sha256               {provenance.sha256}",
        f"size_bytes           {provenance.size_bytes:,}",
        f"container            {provenance.container}",
    ]
    if provenance.codec is not None:
        lines.append(f"codec                {provenance.codec}")
    if provenance.sample_rate_hz is not None:
        lines.append(f"sample_rate_hz       {provenance.sample_rate_hz:,}")
    if provenance.channels is not None:
        lines.append(f"channels             {provenance.channels}")
    if provenance.sample_width_bytes is not None:
        lines.append(f"sample_width_bytes   {provenance.sample_width_bytes}")
    if provenance.frames is not None:
        lines.append(f"frames               {provenance.frames:,}")
    if provenance.duration_seconds is not None:
        lines.append(
            f"duration_seconds     {provenance.duration_seconds:.3f}"
            f"  ({_format_duration(provenance.duration_seconds)})"
        )
    if provenance.canonical_samples is not None:
        lines.append(f"canonical_16k_samples {provenance.canonical_samples:,}")
    if provenance.note is not None:
        lines.append(f"note                 {provenance.note}")
    return "\n".join(lines)


def render_yaml(provenance: AudioProvenance) -> str:
    lines = [
        f"- filename: {provenance.path.name}",
        f"  sha256: {provenance.sha256}",
        f"  size_bytes: {provenance.size_bytes}",
        f"  container: {provenance.container}",
    ]
    if provenance.codec is not None:
        lines.append(f"  codec: {provenance.codec}")
    if provenance.sample_rate_hz is not None:
        lines.append(f"  sample_rate_hz: {provenance.sample_rate_hz}")
    if provenance.channels is not None:
        lines.append(f"  channels: {provenance.channels}")
    if provenance.sample_width_bytes is not None:
        lines.append(f"  sample_width_bytes: {provenance.sample_width_bytes}")
    if provenance.frames is not None:
        lines.append(f"  frames: {provenance.frames}")
    if provenance.duration_seconds is not None:
        lines.append(f"  duration_seconds: {provenance.duration_seconds:.3f}")
    if provenance.canonical_samples is not None:
        lines.append(f"  canonical_16k_samples: {provenance.canonical_samples}")
    if provenance.note is not None:
        lines.append(f"  note: {provenance.note}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="audio file to inspect")
    parser.add_argument(
        "--yaml",
        action="store_true",
        help="emit a manifest block for tests/manifests/",
    )
    parser.add_argument(
        "--expect-sha256",
        help="verify the file still hashes to this value; exit non-zero if not",
    )
    args = parser.parse_args(argv)

    provenance = inspect(args.path)

    if args.expect_sha256 and provenance.sha256.lower() != args.expect_sha256.lower():
        print(
            f"hash mismatch for {args.path}\n"
            f"  expected {args.expect_sha256.lower()}\n"
            f"  actual   {provenance.sha256}",
            file=sys.stderr,
        )
        return 1

    print(render_yaml(provenance) if args.yaml else render_human(provenance))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
