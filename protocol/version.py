"""Protocol version constants and compatibility rules.

ADR-0010 D23. `requirements.md` Section 9.1 fixes the two rules that matter:
an unknown major version is rejected, and an unknown optional field within a
known major version is ignored safely.

ADR-0005 extends the second rule to unknown *event types*, and ADR-0010 adds the
constraint that makes both safe: **minor versions are additive only**. A minor
bump may add an optional field or an event type. It may never remove a field,
narrow a type, or change the meaning of an existing one. Without that guarantee,
"ignore what you do not recognise" would let an old reader silently apply a
stale interpretation to changed data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

PROTOCOL_MAJOR: Final = 1
PROTOCOL_MINOR: Final = 0
PROTOCOL_VERSION: Final = f"{PROTOCOL_MAJOR}.{PROTOCOL_MINOR}"


@dataclass(frozen=True, slots=True)
class Version:
    """A parsed `major.minor` protocol version."""

    major: int
    minor: int

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}"


def parse_version(text: str) -> Version:
    """Parse a `major.minor` string.

    Raises:
        ValueError: if the text is not exactly two non-negative decimal integers
            separated by a single dot. Deliberately strict: a lenient parser here
            would let "1.0.0" or " 1.0" through and defer the failure to a place
            with less context.
    """
    parts = text.split(".")
    if len(parts) != 2:
        raise ValueError(f"protocol version must be major.minor, got {text!r}")
    try:
        major, minor = (int(part) for part in parts)
    except ValueError as exc:
        raise ValueError(f"protocol version components must be integers: {text!r}") from exc
    if major < 0 or minor < 0:
        raise ValueError(f"protocol version components must be non-negative: {text!r}")
    if any(part != str(int(part)) for part in parts):
        # Rejects "01.0" and "1.+0": a version that round-trips differently is a
        # version two implementations can disagree about.
        raise ValueError(f"protocol version must be canonical decimal: {text!r}")
    return Version(major=major, minor=minor)


def is_major_compatible(other: Version) -> bool:
    """Whether a peer speaking `other` can be understood at all."""
    return other.major == PROTOCOL_MAJOR


def is_forward_minor(other: Version) -> bool:
    """Whether the peer speaks a newer minor of the same major.

    True means the peer may send fields and event types this build does not know.
    Both are ignored safely, and both are logged, because "ignored safely" is only
    acceptable when it is also visible.
    """
    return other.major == PROTOCOL_MAJOR and other.minor > PROTOCOL_MINOR
