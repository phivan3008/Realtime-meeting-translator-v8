"""Where meeting files live, and how their names are built.

ADR-0016 D42. `requirements.md` Section 19.1 fixes the filenames and says nothing
about the directory. Section 25.14 requires UTF-8 without BOM and sanitized
session-derived filenames; SEC-060 requires safe paths.

The default is the user's Documents folder because **a meeting record is the
user's document, not application data**. It contains what people said (SEC-090),
it is the artifact they are asked to send back during testing, and it is what
they will eventually keep or delete on purpose (SEC-050). Documents is where a
person looks for a document.

The trap this avoids: the user machine runs from an extracted ZIP folder, which
may be read-only and which anyone tidying their Downloads will delete without
thinking. Writing a meeting record there puts it in the single most deletable
location on the machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from protocol.identifiers import is_valid_session_id

DEFAULT_SUBDIRECTORY = "MeetingTranslator"

DEBUG_SUFFIX = "_debug.jsonl"
HISTORY_SUFFIX = "_history.jsonl"
HISTORY_FINAL_SUFFIX = "_history.final.jsonl"
FILENAME_PREFIX = "meeting_"

#: Written next to the final history, and only in the explicit diagnostic mode
#: Section 25.14 keeps default-off.
RAW_AUDIO_SUFFIX = "_audio.raw"


class UnsafeSessionIdError(ValueError):
    """A session identifier that must not be allowed near a filesystem path.

    Raised rather than sanitised. Silently rewriting a bad identifier would
    produce a file whose name no longer matches the session it belongs to, which
    is worse than refusing: the record would exist and be unfindable.
    """


def default_directory() -> Path:
    """The default meeting directory: Documents, or the home folder as a fallback.

    ``USERPROFILE`` is consulted before ``Path.home()`` because it is what
    Windows actually sets and what a person sees in Explorer. A machine with no
    Documents folder - which happens on some virtual desktops, and the user
    machine is one - falls back to the home folder rather than creating a
    Documents folder the platform chose not to have.
    """
    profile = os.environ.get("USERPROFILE")
    home = Path(profile) if profile else Path.home()
    documents = home / "Documents"
    base = documents if documents.is_dir() else home
    return base / DEFAULT_SUBDIRECTORY


@dataclass(frozen=True, slots=True)
class MeetingPaths:
    """The three files of ADR-0004, for one session."""

    directory: Path
    session_id: str

    def __post_init__(self) -> None:
        if not is_valid_session_id(self.session_id):
            raise UnsafeSessionIdError(
                f"session_id {self.session_id!r} is not a lowercase uuid4 and must "
                "not be used to build a path"
            )

    @property
    def stem(self) -> str:
        return f"{FILENAME_PREFIX}{self.session_id}"

    @property
    def debug(self) -> Path:
        """The authoritative append-only event log (ADR-0004)."""
        return self.directory / f"{self.stem}{DEBUG_SUFFIX}"

    @property
    def history(self) -> Path:
        """The live projection. No authority; a convenience view."""
        return self.directory / f"{self.stem}{HISTORY_SUFFIX}"

    @property
    def history_final(self) -> Path:
        """The compacted deliverable, written by atomic rename at graceful stop."""
        return self.directory / f"{self.stem}{HISTORY_FINAL_SUFFIX}"

    @property
    def history_final_temp(self) -> Path:
        """Where compaction writes before validating and renaming (Section 25.14)."""
        return self.directory / f"{self.stem}{HISTORY_FINAL_SUFFIX}.tmp"

    @property
    def raw_audio(self) -> Path:
        """Only written in the explicit diagnostic mode of Section 25.14."""
        return self.directory / f"{self.stem}{RAW_AUDIO_SUFFIX}"

    def all_files(self) -> list[Path]:
        """Every file belonging to this session, for purge and for reporting."""
        return [self.debug, self.history, self.history_final, self.raw_audio]

    def ensure_directory(self) -> None:
        """Create the meeting directory.

        Raises:
            OSError: if the directory cannot be created or is not writable. The
                caller stops the session (PERS-130): a session that cannot
                preserve its own event source should not be recording.
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        probe = self.directory / f".write-probe-{self.session_id}"
        try:
            probe.write_text("", encoding="utf-8")
        finally:
            probe.unlink(missing_ok=True)


def build_paths(session_id: str, *, directory: Path | None = None) -> MeetingPaths:
    """Resolve the meeting paths for a session.

    Raises:
        UnsafeSessionIdError: if the identifier is not a uuid4. Checked here, at
            the boundary, rather than trusted from the caller - this is the last
            point before a value from the wire becomes part of a filesystem path
            (PERS-100, SEC-060).
    """
    return MeetingPaths(
        directory=(directory or default_directory()).expanduser(),
        session_id=session_id,
    )


def find_sessions(directory: Path) -> list[str]:
    """Session identifiers with a debug log in this directory.

    Used by the recovery command (PERS-070) and by the purge tool. Files whose
    names do not parse are ignored rather than guessed at: a name that does not
    match the pattern was not written by this project.
    """
    if not directory.is_dir():
        return []

    sessions: list[str] = []
    for path in sorted(directory.glob(f"{FILENAME_PREFIX}*{DEBUG_SUFFIX}")):
        candidate = path.name[len(FILENAME_PREFIX) : -len(DEBUG_SUFFIX)]
        if is_valid_session_id(candidate):
            sessions.append(candidate)
    return sessions
