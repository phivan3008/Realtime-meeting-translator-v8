"""Detecting an idle capture endpoint.

**A behavioural fact discovered on real hardware, 2026-09-08.** A WASAPI loopback
endpoint with nothing playing through it delivers **no callbacks at all**. It
does not deliver silence. Measured on the dev machine: three seconds on an idle
endpoint produced zero callbacks and zero bytes, while the stream reported
``is_active() == True`` and its clock advanced normally. Starting playback
produced 106 callbacks and 434,176 bytes in 2.26 s, exactly as expected.

That distinction matters more than it first appears. The canonical timeline
advances one sample per captured sample (Section 25.1). If an endpoint goes idle
for five minutes and produces nothing, the timeline does not advance, and
everything after it carries a ``start_sample`` five minutes too early - the same
class of failure PROT-180 forbids for gaps, arriving by a different route.

This module measures the deficit and classifies it. ADR-0015 D39 decided what
happens next: the client emits ``audio.idle`` carrying the span and advances its
own frame cursor, and the **server** decides whether to materialise silence for
model continuity. Nothing is fabricated on the wire - five minutes of idle costs
one small event rather than 9.6 MB of zeros.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from protocol.enums import IdleClass
from protocol.limits import CANONICAL_SAMPLE_RATE_HZ

#: How long an endpoint may deliver nothing before it is called idle rather than
#: merely between callbacks. Measured on the user machine: 468 callbacks in
#: 10.02 s is one roughly every 21 ms, so this is two orders of magnitude of
#: headroom. `benchmark_required` like every other timing constant.
DEFAULT_IDLE_THRESHOLD_S = 0.5

#: ADR-0015 D39 duration-class boundaries. Both are `benchmark_required` and
#: are decided at the Phase 5 gate, alongside the VAD parameters they interact
#: with. The values here are placeholders that make the classifier runnable, and
#: configuration validation refuses to start without real ones (OPS-600).
DEFAULT_LONG_IDLE_S = 3.0
DEFAULT_VERY_LONG_IDLE_S = 30.0


@dataclass(frozen=True, slots=True)
class IdlePeriod:
    """A stretch during which the endpoint produced nothing."""

    started_monotonic: float
    ended_monotonic: float
    #: How many canonical samples of media time passed with no audio to fill it.
    canonical_samples: int
    idle_class: IdleClass

    @property
    def duration_s(self) -> float:
        return self.ended_monotonic - self.started_monotonic


def classify_idle(
    duration_s: float,
    *,
    long_threshold_s: float = DEFAULT_LONG_IDLE_S,
    very_long_threshold_s: float = DEFAULT_VERY_LONG_IDLE_S,
) -> IdleClass:
    """Assign an idle span to an ADR-0015 duration class.

    Boundaries are half-open upward, matching the gap classes of Section 25.8, so
    a span exactly at a threshold takes the lighter treatment. The asymmetry is
    deliberate: escalating on an exact boundary would make the class depend on
    floating-point rounding of a wall-clock measurement.
    """
    if duration_s > very_long_threshold_s:
        return IdleClass.VERY_LONG
    if duration_s > long_threshold_s:
        return IdleClass.LONG
    return IdleClass.SHORT


@dataclass(slots=True)
class IdleTracker:
    """Compares elapsed wall time against audio actually received.

    Deliberately wall-clock based, and deliberately *not* an authority on media
    position. Section 25.1 makes the sample offset canonical and monotonic time
    an observability field; this uses monotonic time only to notice that samples
    are **absent**, which is the one question sample counting cannot answer -
    a counter that never advances looks identical whether one second passed or
    an hour.
    """

    idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S
    long_threshold_s: float = DEFAULT_LONG_IDLE_S
    very_long_threshold_s: float = DEFAULT_VERY_LONG_IDLE_S
    clock: Callable[[], float] = time.monotonic

    _last_audio_at: float | None = field(default=None, init=False)
    _idle_since: float | None = field(default=None, init=False)
    periods: list[IdlePeriod] = field(default_factory=list)

    def start(self) -> None:
        """Begin tracking. Called when capture starts."""
        now = self.clock()
        self._last_audio_at = now
        self._idle_since = None

    def note_audio(self, device_frames: int) -> IdlePeriod | None:
        """Record that audio arrived, closing any idle period it ends.

        Returns the period that just ended, or None if the endpoint was already
        producing. The caller turns a returned period into whatever the approved
        policy says - a timeline advance, a warning, or both.
        """
        if device_frames <= 0:
            return None

        now = self.clock()
        self._last_audio_at = now

        if self._idle_since is None:
            return None

        period = self._close(now)
        self.periods.append(period)
        return period

    def _close(self, now: float) -> IdlePeriod:
        assert self._idle_since is not None
        started = self._idle_since
        duration = now - started
        self._idle_since = None
        return IdlePeriod(
            started_monotonic=started,
            ended_monotonic=now,
            canonical_samples=int(duration * CANONICAL_SAMPLE_RATE_HZ),
            idle_class=classify_idle(
                duration,
                long_threshold_s=self.long_threshold_s,
                very_long_threshold_s=self.very_long_threshold_s,
            ),
        )

    def poll(self) -> bool:
        """Check whether the endpoint has gone idle. Returns True if it is idle now.

        Called from the consumer side on a timer. The callback cannot detect
        this, for the obvious reason that a callback which is not firing cannot
        observe that it is not firing.
        """
        if self._last_audio_at is None:
            return False

        now = self.clock()
        if now - self._last_audio_at <= self.idle_threshold_s:
            return False

        if self._idle_since is None:
            # The endpoint went quiet at the last audio, not at the moment the
            # threshold expired: attributing the whole gap is what keeps the
            # accounting exact.
            self._idle_since = self._last_audio_at
        return True

    @property
    def is_idle(self) -> bool:
        return self._idle_since is not None

    @property
    def idle_seconds(self) -> float:
        """How long the current idle period has run, or 0.0 when producing."""
        if self._idle_since is None:
            return 0.0
        return self.clock() - self._idle_since

    @property
    def total_idle_samples(self) -> int:
        """Canonical samples of media time that no audio ever covered.

        The number a fill policy would need, once one is approved.
        """
        return sum(period.canonical_samples for period in self.periods)

    def finish(self) -> IdlePeriod | None:
        """Close an open idle period at end of capture."""
        if self._idle_since is None:
            return None
        period = self._close(self.clock())
        self.periods.append(period)
        return period
