"""Protocol error codes.

ADR-0010 D23. Namespaced strings rather than numbers, because a captured fixture
has to explain itself years after the capture. `requirements.md` Section 25.15
requires replay fixtures to carry provenance; a stored exchange whose failure
branch reads `PROT_MALFORMED_HEADER` needs no lookup table, while one reading
`4102` needs a table that may no longer exist. The cost is a few dozen bytes on
the error path, where bandwidth has stopped being the problem.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Every error this protocol can report.

    `fatal` in the comment means the connection is terminated: the framing or the
    session can no longer be trusted. Everything else rejects one event and lets
    the session continue.
    """

    # Framing and encoding. All fatal: if the framing is wrong, nothing after it
    # can be relied on.
    PROT_UNSUPPORTED_MAJOR_VERSION = "PROT_UNSUPPORTED_MAJOR_VERSION"
    PROT_MALFORMED_HEADER = "PROT_MALFORMED_HEADER"
    PROT_FRAME_TOO_LARGE = "PROT_FRAME_TOO_LARGE"
    PROT_EVENT_TOO_LARGE = "PROT_EVENT_TOO_LARGE"
    PROT_INVALID_UTF8 = "PROT_INVALID_UTF8"

    # Content. Reject the event, keep the session.
    PROT_SCHEMA_VIOLATION = "PROT_SCHEMA_VIOLATION"

    # Timeline integrity. Fatal: the sample timeline is the identity authority
    # (ADR-0008), so a contradiction in it is not something to continue past.
    PROT_SEQUENCE_REGRESSION = "PROT_SEQUENCE_REGRESSION"
    PROT_SAMPLE_OVERLAP = "PROT_SAMPLE_OVERLAP"

    # Session lifecycle.
    SESSION_UNKNOWN = "SESSION_UNKNOWN"
    SESSION_ALREADY_ACTIVE = "SESSION_ALREADY_ACTIVE"
    SESSION_RESUME_REFUSED = "SESSION_RESUME_REFUSED"
    SESSION_SERVER_RESTARTED = "SESSION_SERVER_RESTARTED"

    # Load and internal.
    OVERLOAD_SUSTAINED = "OVERLOAD_SUSTAINED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


#: Codes that terminate the connection. Everything else rejects one event.
FATAL_CODES: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.PROT_UNSUPPORTED_MAJOR_VERSION,
        ErrorCode.PROT_MALFORMED_HEADER,
        ErrorCode.PROT_FRAME_TOO_LARGE,
        ErrorCode.PROT_EVENT_TOO_LARGE,
        ErrorCode.PROT_INVALID_UTF8,
        ErrorCode.PROT_SEQUENCE_REGRESSION,
        ErrorCode.PROT_SAMPLE_OVERLAP,
        ErrorCode.SESSION_SERVER_RESTARTED,
        ErrorCode.OVERLOAD_SUSTAINED,
    }
)


def is_fatal(code: ErrorCode) -> bool:
    """Whether this code terminates the connection."""
    return code in FATAL_CODES


class ProtocolError(Exception):
    """A protocol violation, carrying the code that names it."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    @property
    def fatal(self) -> bool:
        return is_fatal(self.code)
