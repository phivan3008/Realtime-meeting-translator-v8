"""Session-scoped identifiers and their allocators.

ADR-0008 D10. `requirements.md` Section 25.2 keeps four kinds of identifier
distinct and makes the session orchestrator the sole authority that creates
utterance and segment identifiers. Section 25.7 adds that anonymous speaker IDs
are session-scoped, monotonically allocated and **never reused**.

That last rule is why every allocator here widens rather than wraps. A wrapped
identifier is a reused identifier, and reuse would silently attach one speaker's
history to another. The formats are parsed as a prefix plus a decimal integer,
never as fixed-width strings, so widening is not a breaking change.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Final

from protocol.limits import MAX_IDENTIFIER_CHARS

STREAM_PREFIX: Final = "stream-"
UTTERANCE_PREFIX: Final = "utt-"
SEGMENT_PREFIX: Final = "seg-"
SPEAKER_TURN_PREFIX: Final = "spk-turn-"
SPEAKER_PREFIX: Final = "speaker-"

#: Minimum zero padding. Purely cosmetic: it keeps identifiers sorting
#: lexicographically for the first million, and widening past it is legal.
STREAM_PAD: Final = 4
ORDINAL_PAD: Final = 6

#: Reserved labels from Section 25.6. `speaker-unknown` means evidence was
#: insufficient; `speaker-multiple` is a presentation label only and must never
#: name an embedding cluster.
SPEAKER_UNKNOWN: Final = "speaker-unknown"
SPEAKER_MULTIPLE: Final = "speaker-multiple"

_ORDINAL_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    STREAM_PREFIX: re.compile(r"^stream-(\d+)$"),
    UTTERANCE_PREFIX: re.compile(r"^utt-(\d+)$"),
    SEGMENT_PREFIX: re.compile(r"^seg-(\d+)$"),
    SPEAKER_TURN_PREFIX: re.compile(r"^spk-turn-(\d+)$"),
    SPEAKER_PREFIX: re.compile(r"^speaker-(\d+)$"),
}

#: A session id is a uuid4 and is validated before it ever reaches a filesystem
#: path (PERS-100, SEC-060). A hostile or malformed id must not be able to
#: traverse out of the log directory.
SESSION_ID_PATTERN: Final = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


def new_session_id() -> str:
    return str(uuid.uuid4())


def new_event_id() -> str:
    return str(uuid.uuid4())


def new_server_epoch() -> str:
    """A fresh server epoch, generated once per server process.

    ADR-0009 D16: the client records this from ``session.started``, and a
    different value in any later response means the server restarted and the
    meeting is unrecoverable.
    """
    return str(uuid.uuid4())


def is_valid_session_id(value: str) -> bool:
    return bool(SESSION_ID_PATTERN.match(value))


def format_stream_id(ordinal: int) -> str:
    return f"{STREAM_PREFIX}{ordinal:0{STREAM_PAD}d}"


def format_utterance_id(ordinal: int) -> str:
    return f"{UTTERANCE_PREFIX}{ordinal:0{ORDINAL_PAD}d}"


def format_segment_id(ordinal: int) -> str:
    return f"{SEGMENT_PREFIX}{ordinal:0{ORDINAL_PAD}d}"


def format_speaker_turn_id(ordinal: int) -> str:
    return f"{SPEAKER_TURN_PREFIX}{ordinal:0{ORDINAL_PAD}d}"


def format_speaker_id(ordinal: int) -> str:
    """Anonymous speaker label.

    Note the asymmetry with the other formatters: Section 15.1 writes these as
    ``speaker-1``, not ``speaker-000001``, so this one is unpadded.
    """
    return f"{SPEAKER_PREFIX}{ordinal}"


def parse_ordinal(identifier: str, prefix: str) -> int:
    """Extract the numeric ordinal from an identifier.

    Raises:
        ValueError: if the identifier does not match the prefix's pattern.
    """
    pattern = _ORDINAL_PATTERNS.get(prefix)
    if pattern is None:
        raise ValueError(f"unknown identifier prefix: {prefix!r}")
    match = pattern.match(identifier)
    if match is None:
        raise ValueError(f"{identifier!r} is not a valid {prefix} identifier")
    return int(match.group(1))


def is_reserved_speaker_label(value: str) -> bool:
    """Whether a speaker label is one of the reserved non-cluster labels."""
    return value in (SPEAKER_UNKNOWN, SPEAKER_MULTIPLE)


def validate_identifier_length(value: str, *, field_name: str) -> None:
    """Raise if an identifier exceeds the SEC-070 length ceiling."""
    if len(value) > MAX_IDENTIFIER_CHARS:
        raise ValueError(
            f"{field_name} is {len(value)} characters, over the "
            f"{MAX_IDENTIFIER_CHARS} character limit"
        )


@dataclass(slots=True)
class OrdinalAllocator:
    """A monotonic, never-reusing ordinal source scoped to one session.

    Not thread-safe by design: ADR-0008 makes the orchestrator the single
    authority for utterance and segment identifiers, so an allocator that needed
    a lock would be evidence that something else had started allocating too.
    """

    prefix: str
    _next: int = field(default=0)

    def allocate(self) -> str:
        """Allocate the next identifier. Never returns the same value twice."""
        ordinal = self._next
        self._next += 1
        if self.prefix == SPEAKER_PREFIX:
            # Section 15.1 numbers speakers from 1: speaker-1, speaker-2.
            return format_speaker_id(ordinal + 1)
        return f"{self.prefix}{ordinal:0{self._pad}d}"

    @property
    def _pad(self) -> int:
        return STREAM_PAD if self.prefix == STREAM_PREFIX else ORDINAL_PAD

    @property
    def allocated_count(self) -> int:
        return self._next


@dataclass(slots=True)
class SessionIdentifiers:
    """Every allocator for one session, created together and never shared."""

    session_id: str
    streams: OrdinalAllocator = field(default_factory=lambda: OrdinalAllocator(STREAM_PREFIX))
    utterances: OrdinalAllocator = field(default_factory=lambda: OrdinalAllocator(UTTERANCE_PREFIX))
    segments: OrdinalAllocator = field(default_factory=lambda: OrdinalAllocator(SEGMENT_PREFIX))
    speaker_turns: OrdinalAllocator = field(
        default_factory=lambda: OrdinalAllocator(SPEAKER_TURN_PREFIX)
    )
    speakers: OrdinalAllocator = field(default_factory=lambda: OrdinalAllocator(SPEAKER_PREFIX))

    def __post_init__(self) -> None:
        if not is_valid_session_id(self.session_id):
            raise ValueError(f"session_id {self.session_id!r} is not a uuid4")
