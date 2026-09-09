"""The capture lifecycle state machine.

`requirements.md` Section 8.2 fixes the states and the shape of the graph:

```text
IDLE -> CONNECTING -> READY -> CAPTURING -> STOPPING -> COMPLETED
                         |          |
                         v          v
                       ERROR <-> RECONNECTING
```

and requires that the UI make the current state visible (AUD-120, UI-050) and
that every transition be logged with a timestamp and a reason code (AUD-130).

Transitions are a table rather than scattered `if` statements, for two reasons.
An illegal transition raises instead of quietly leaving the client in a state
its own UI cannot describe; and the table is the thing the tests read, so the
graph in the requirements and the graph in the code can be compared directly.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum


class CaptureState(StrEnum):
    """Section 8.2."""

    IDLE = "idle"
    CONNECTING = "connecting"
    READY = "ready"
    CAPTURING = "capturing"
    STOPPING = "stopping"
    COMPLETED = "completed"
    ERROR = "error"
    RECONNECTING = "reconnecting"

    @property
    def is_terminal(self) -> bool:
        return self is CaptureState.COMPLETED

    @property
    def is_capturing_audio(self) -> bool:
        return self is CaptureState.CAPTURING


class ReasonCode(StrEnum):
    """Why a transition happened. AUD-130 requires a reason, not just a state."""

    USER_STARTED = "user_started"
    USER_STOPPED = "user_stopped"
    CONNECTED = "connected"
    SESSION_ACCEPTED = "session_accepted"
    CAPTURE_STARTED = "capture_started"
    DRAIN_COMPLETE = "drain_complete"
    SESSION_STOPPED_ACK = "session_stopped_ack"
    #: ADR-0009 D19: the client compacts and completes even when the server goes
    #: quiet, because it is the authoritative writer (ADR-0004).
    NO_SERVER_STOP_ACK = "no_server_stop_ack"
    CONNECTION_LOST = "connection_lost"
    RECONNECT_ATTEMPT = "reconnect_attempt"
    RESUME_ACCEPTED = "resume_accepted"
    RESUME_REFUSED = "resume_refused"
    SERVER_RESTARTED = "server_restarted"
    DEVICE_REMOVED = "device_removed"
    DEVICE_ERROR = "device_error"
    RESAMPLER_FAILURE = "resampler_failure"
    DEBUG_WRITER_FAILURE = "debug_writer_failure"
    PROTOCOL_ERROR = "protocol_error"
    UNRECOVERABLE = "unrecoverable"


#: The graph of Section 8.2, written once.
ALLOWED_TRANSITIONS: dict[CaptureState, frozenset[CaptureState]] = {
    CaptureState.IDLE: frozenset({CaptureState.CONNECTING}),
    CaptureState.CONNECTING: frozenset({CaptureState.READY, CaptureState.ERROR}),
    CaptureState.READY: frozenset(
        {CaptureState.CAPTURING, CaptureState.STOPPING, CaptureState.ERROR}
    ),
    CaptureState.CAPTURING: frozenset(
        {CaptureState.STOPPING, CaptureState.RECONNECTING, CaptureState.ERROR}
    ),
    CaptureState.STOPPING: frozenset({CaptureState.COMPLETED, CaptureState.ERROR}),
    CaptureState.RECONNECTING: frozenset(
        {CaptureState.CAPTURING, CaptureState.ERROR, CaptureState.STOPPING}
    ),
    CaptureState.ERROR: frozenset({CaptureState.RECONNECTING, CaptureState.STOPPING}),
    #: Terminal. A new meeting is a new state machine, which is what keeps a
    #: second session from inheriting the first one's counters.
    CaptureState.COMPLETED: frozenset(),
}


class IllegalTransitionError(RuntimeError):
    """A transition the Section 8.2 graph does not contain."""

    def __init__(self, current: CaptureState, requested: CaptureState) -> None:
        allowed = sorted(ALLOWED_TRANSITIONS[current])
        super().__init__(
            f"cannot move from {current} to {requested}; "
            f"allowed from {current}: {allowed or 'nothing, it is terminal'}"
        )
        self.current = current
        self.requested = requested


@dataclass(frozen=True, slots=True)
class Transition:
    """One recorded state change (AUD-130)."""

    from_state: CaptureState
    to_state: CaptureState
    reason: ReasonCode
    at_monotonic_ns: int
    at_utc: str
    detail: str = ""

    def describe(self) -> str:
        return f"{self.from_state} -> {self.to_state} ({self.reason})" + (
            f": {self.detail}" if self.detail else ""
        )


def _utc_now() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


@dataclass(slots=True)
class CaptureLifecycle:
    """Holds the current state and the history of how it got there.

    Both clocks are injected: monotonic for measurement, UTC for correlation
    (Section 20). Neither is an ordering authority for media - that is the
    sample timeline (PROT-170).
    """

    state: CaptureState = CaptureState.IDLE
    history: list[Transition] = field(default_factory=list)
    clock_ns: Callable[[], int] = time.monotonic_ns
    clock_utc: Callable[[], str] = _utc_now
    #: Called with each transition. The client wires this to the debug writer so
    #: transitions land in the meeting record as `client_local` records
    #: (ADR-0011 D25), since they never cross the wire.
    on_transition: Callable[[Transition], None] | None = None

    def can_move_to(self, target: CaptureState) -> bool:
        return target in ALLOWED_TRANSITIONS[self.state]

    def move_to(self, target: CaptureState, reason: ReasonCode, detail: str = "") -> Transition:
        """Perform a transition.

        Raises:
            IllegalTransitionError: if the graph does not permit it.
        """
        if not self.can_move_to(target):
            raise IllegalTransitionError(self.state, target)

        transition = Transition(
            from_state=self.state,
            to_state=target,
            reason=reason,
            at_monotonic_ns=self.clock_ns(),
            at_utc=self.clock_utc(),
            detail=detail,
        )
        self.state = target
        self.history.append(transition)
        if self.on_transition is not None:
            self.on_transition(transition)
        return transition

    def fail(self, reason: ReasonCode, detail: str = "") -> Transition:
        """Move to ERROR from wherever that is legal.

        A failure arriving in a state with no path to ERROR - only COMPLETED -
        is recorded as an illegal transition rather than silently ignored,
        because a client that reports `completed` after failing has told the
        user something untrue.
        """
        return self.move_to(CaptureState.ERROR, reason, detail)

    @property
    def is_capturing(self) -> bool:
        return self.state.is_capturing_audio

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal

    def reason_counts(self) -> dict[ReasonCode, int]:
        """How often each reason occurred. Feeds the session summary."""
        counts: dict[ReasonCode, int] = {}
        for transition in self.history:
            counts[transition.reason] = counts.get(transition.reason, 0) + 1
        return counts
