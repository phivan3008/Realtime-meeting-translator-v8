"""Coalescing projection updates for the UI, without Qt.

ADR-0016 D41. Fifty partials a second produce fifty projections; repainting a
500-row timeline fifty times a second for one changed row is waste that grows
with the length of the meeting.

So the asyncio side accumulates **which segments changed**, and the Qt side
drains that set on a timer. Two properties make it worth having its own module:

- Carrying identifiers rather than payloads means the drain is O(changed), not
  O(timeline). A partial at the end of a long meeting touches one row.
- It is pure Python, so the batching policy is testable without a display -
  which is the same argument ADR-0013 made for keeping the network off Qt.

The accumulator is written from the asyncio thread and drained from the Qt
thread, so it takes a lock. The lock is held across a set operation and nothing
else.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

#: How long changes accumulate before the UI is told. Below the threshold at
#: which a person notices, so partial text still reads as live.
#: `benchmark_required` (ADR-0016 D41).
DEFAULT_COALESCE_INTERVAL_S = 0.1


@dataclass(slots=True)
class UpdateStats:
    """What the coalescing actually saved. Reported as a client diagnostic."""

    changes_recorded: int = 0
    drains: int = 0
    segments_published: int = 0
    #: Changes that were superseded before the UI ever saw them. This is the
    #: number that justifies the mechanism, so it is measured rather than assumed.
    coalesced_away: int = 0

    @property
    def coalescing_ratio(self) -> float:
        if not self.changes_recorded:
            return 0.0
        return self.coalesced_away / self.changes_recorded


class ChangeAccumulator:
    """Collects changed segment identifiers between drains.

    Thread-safe by design: recorded from the asyncio thread, drained from the Qt
    thread. Nothing but a set is touched under the lock.
    """

    def __init__(self) -> None:
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self.stats = UpdateStats()

    def record(self, segment_id: str) -> None:
        """Note that a segment changed. Cheap enough to call per event."""
        with self._lock:
            self.stats.changes_recorded += 1
            if segment_id in self._pending:
                # Already pending: this change replaces one the UI never saw.
                self.stats.coalesced_away += 1
            self._pending.add(segment_id)

    def record_many(self, segment_ids: list[str]) -> None:
        for segment_id in segment_ids:
            self.record(segment_id)

    def drain(self) -> set[str]:
        """Take everything pending, leaving the accumulator empty.

        Returns an empty set when nothing changed, which the caller uses to skip
        the repaint entirely rather than repainting nothing.
        """
        with self._lock:
            if not self._pending:
                return set()
            pending, self._pending = self._pending, set()
            self.stats.drains += 1
            self.stats.segments_published += len(pending)
            return pending

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()


@dataclass(slots=True)
class CoalescingScheduler:
    """Decides when a drain is due, without owning a timer.

    Separated from any timer so the policy can be tested by advancing an
    injected clock instead of sleeping. A test that sleeps is a test that is
    slow and occasionally wrong.
    """

    interval_s: float = DEFAULT_COALESCE_INTERVAL_S
    clock: Callable[[], float] = time.monotonic
    _last_drain: float = field(default=0.0, init=False)
    _started: bool = field(default=False, init=False)

    def start(self) -> None:
        self._last_drain = self.clock()
        self._started = True

    def due(self, *, has_pending: bool) -> bool:
        """Whether the UI should be updated now.

        An empty accumulator is never due: publishing nothing still costs a
        signal, a slot call and a repaint decision on the GUI thread.
        """
        if not self._started:
            self.start()
        if not has_pending:
            return False
        return self.clock() - self._last_drain >= self.interval_s

    def mark_drained(self) -> None:
        self._last_drain = self.clock()

    @property
    def seconds_since_drain(self) -> float:
        return self.clock() - self._last_drain
