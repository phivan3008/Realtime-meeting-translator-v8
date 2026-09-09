"""Rebuild a meeting's final history from its debug log.

`requirements.md` Section 19.2 requires "a deterministic command" that rebuilds
the history file from the debug event log, and Section 25.14 requires recovery to
ignore or quarantine an incomplete final line and to validate event IDs and
revisions (PERS-070, PERS-080).

Deterministic in a specific sense: it feeds the same reducer the UI uses
(ADR-0008 D13), reading records whose payload is the wire event verbatim
(ADR-0011 D25). Running it against a log written by a completed session produces
a file byte-identical to the one that session wrote itself.

Usage::

    python tools/rebuild_history.py --list
    python tools/rebuild_history.py <session-id>
    python tools/rebuild_history.py <session-id> --directory "D:/logs" --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from client.paths import (  # noqa: E402
    UnsafeSessionIdError,
    build_paths,
    default_directory,
    find_sessions,
)
from client.persistence import (  # noqa: E402
    PersistenceError,
    compact,
    rebuild_projection,
    summarise,
)


def list_sessions(directory: Path) -> int:
    sessions = find_sessions(directory)
    if not sessions:
        print(f"no meeting logs in {directory}", file=sys.stderr)
        return 1
    for session_id in sessions:
        paths = build_paths(session_id, directory=directory)
        size = paths.debug.stat().st_size if paths.debug.is_file() else 0
        final_exists = "final present" if paths.history_final.is_file() else "NO FINAL"
        print(f"{session_id}  {size:>12,} bytes  {final_exists}")
    return 0


def rebuild(session_id: str, directory: Path, *, dry_run: bool) -> int:
    paths = build_paths(session_id, directory=directory)

    if not paths.debug.is_file():
        print(f"no debug log at {paths.debug}", file=sys.stderr)
        return 1

    projection, read = rebuild_projection(paths.debug)
    counts = summarise(projection)
    compactable = len(projection.compactable_segments())

    print(f"session          {session_id}")
    print(f"debug log        {paths.debug}")
    print(f"records read     {len(read.records):,}")
    print(f"log_seq contiguous {read.log_seq_contiguous}")
    print(f"quarantined      {len(read.quarantined)}")
    for line in read.quarantined:
        print(f"  line {line.line_number} (log_seq {line.log_seq}): {line.reason}")
    print(f"segments         {counts['segment_count']}")
    print(f"  sealed         {counts['sealed_count']}")
    print(f"  rejected       {counts['rejected_count']} (excluded from the final history)")
    print(f"  low confidence {counts['low_confidence_count']}")
    print(f"  translation failed {counts['translation_failed_count']}")
    print(f"integrity conflicts {counts['integrity_conflict_count']}")
    print(f"would write      {compactable} segments")

    if not read.log_seq_contiguous:
        # Reported rather than fatal. A hole means the log is missing records,
        # and rebuilding from what remains is still the best available outcome -
        # but the operator has to know the result is partial.
        print(
            "\nWARNING: log_seq is not contiguous. Records are missing from the "
            "log, so this rebuild covers less than the session recorded.",
            file=sys.stderr,
        )

    if dry_run:
        print("\ndry run: nothing written")
        return 0

    try:
        written = compact(paths, projection)
    except PersistenceError as exc:
        print(f"\nrebuild failed: {exc}", file=sys.stderr)
        return 1

    print(f"\nwrote {written} segments to {paths.history_final}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id", nargs="?", help="session to rebuild")
    parser.add_argument(
        "--directory",
        type=Path,
        default=None,
        help="meeting directory; defaults to the configured location",
    )
    parser.add_argument("--list", action="store_true", help="list sessions and exit")
    parser.add_argument(
        "--dry-run", action="store_true", help="report what would be written, write nothing"
    )
    args = parser.parse_args(argv)

    directory = (args.directory or default_directory()).expanduser()

    if args.list:
        return list_sessions(directory)
    if not args.session_id:
        parser.error("a session id is required unless --list is given")

    try:
        return rebuild(args.session_id, directory, dry_run=args.dry_run)
    except UnsafeSessionIdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
