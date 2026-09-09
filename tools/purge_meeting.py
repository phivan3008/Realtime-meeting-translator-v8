"""Delete every file belonging to one meeting, and record that it happened.

ADR-0011 D28. `requirements.md` Section 21 and SEC-050 require retention and
deletion behaviour to be **defined**, not automated. Nothing in this project
deletes a meeting on its own: a destructive default eventually runs against
something the user wanted to keep, and a meeting record is not something to lose
to a configuration nobody read.

So deletion is explicit, it names what it removed, and it leaves an audit line.
The audit line is the point. A deletion with no trace is indistinguishable from
a file that was never written, and "was there ever a recording of that meeting?"
is a question someone will eventually ask.

Usage::

    python tools/purge_meeting.py --list
    python tools/purge_meeting.py <session-id> --dry-run
    python tools/purge_meeting.py <session-id> --confirm
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from client.paths import (  # noqa: E402
    MeetingPaths,
    UnsafeSessionIdError,
    build_paths,
    default_directory,
    find_sessions,
)

AUDIT_FILENAME = "deletions.log"

AUDIT_HEADER = (
    "# Meeting deletion audit\n"
    "#\n"
    "# Appended by tools/purge_meeting.py (ADR-0011 D28, SEC-050). Nothing in\n"
    "# this project deletes a meeting automatically; every line below records a\n"
    "# deliberate deletion.\n"
    "#\n"
    "# deleted_at_utc | session_id | files | bytes\n"
)


def existing_files(paths: MeetingPaths) -> list[Path]:
    return [path for path in paths.all_files() if path.is_file()]


def append_audit(directory: Path, session_id: str, files: list[Path], total_bytes: int) -> Path:
    audit = directory / AUDIT_FILENAME
    if not audit.is_file():
        audit.write_text(AUDIT_HEADER, encoding="utf-8", newline="\n")

    stamp = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
    names = ",".join(sorted(path.name for path in files))
    with audit.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"{stamp} | {session_id} | {names} | {total_bytes}\n")
    return audit


def purge(session_id: str, directory: Path, *, confirm: bool) -> int:
    paths = build_paths(session_id, directory=directory)
    files = existing_files(paths)

    if not files:
        print(f"no files for session {session_id} in {directory}", file=sys.stderr)
        return 1

    total_bytes = sum(path.stat().st_size for path in files)

    print(f"session   {session_id}")
    print(f"directory {directory}")
    for path in files:
        print(f"  {path.name}  {path.stat().st_size:,} bytes")
    print(f"total     {total_bytes:,} bytes")

    if not confirm:
        print(
            "\nnothing deleted. This removes a meeting record permanently, so it "
            "requires --confirm."
        )
        return 0

    for path in files:
        path.unlink()

    audit = append_audit(directory, session_id, files, total_bytes)
    print(f"\ndeleted {len(files)} files, {total_bytes:,} bytes")
    print(f"recorded in {audit}")
    return 0


def list_sessions(directory: Path) -> int:
    sessions = find_sessions(directory)
    if not sessions:
        print(f"no meeting logs in {directory}", file=sys.stderr)
        return 1
    for session_id in sessions:
        paths = build_paths(session_id, directory=directory)
        files = existing_files(paths)
        total = sum(path.stat().st_size for path in files)
        print(f"{session_id}  {len(files)} files  {total:>12,} bytes")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id", nargs="?", help="session to delete")
    parser.add_argument("--directory", type=Path, default=None)
    parser.add_argument("--list", action="store_true", help="list sessions and exit")
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="actually delete. Without it nothing is removed",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="alias for omitting --confirm; accepted for symmetry with other tools",
    )
    args = parser.parse_args(argv)

    directory = (args.directory or default_directory()).expanduser()

    if args.list:
        return list_sessions(directory)
    if not args.session_id:
        parser.error("a session id is required unless --list is given")

    try:
        return purge(args.session_id, directory, confirm=args.confirm and not args.dry_run)
    except UnsafeSessionIdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"deletion failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
