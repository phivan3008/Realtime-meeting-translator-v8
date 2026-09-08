"""Size and count limits.

ADR-0010 D24, satisfying `requirements.md` Section 21 and SEC-070. Most of these
are safety ceilings reasoned from the shape of the workload rather than tuning
parameters measured against it, so they are constants here. The one genuine
tuning parameter - queue depth - is deliberately absent: it trades latency
against burst tolerance and belongs in validated configuration, measured at the
Phase 4 gate.
"""

from __future__ import annotations

from typing import Final

CANONICAL_SAMPLE_RATE_HZ: Final = 16_000
BYTES_PER_SAMPLE: Final = 2  # pcm_s16le

#: A 40 ms frame is 1308 bytes including the header. 16 KiB is over 12x
#: headroom for experiments while still rejecting a hostile frame outright.
MAX_FRAME_BYTES: Final = 16 * 1024

#: 500 ms. A frame longer than half a second defeats real-time endpointing
#: regardless of how much buffer exists to hold it.
MAX_SAMPLES_PER_FRAME: Final = 8_000

#: The largest realistic control event is a transcript.final carrying text,
#: speaker candidates and decode evidence, which is far below this.
MAX_EVENT_BYTES: Final = 64 * 1024

#: Thirty seconds of continuous speech does not approach 1000 characters in
#: either Japanese or Vietnamese.
MAX_TEXT_CHARS: Final = 8_192

#: "seg-000123" is 10 characters; a uuid4 is 36.
MAX_IDENTIFIER_CHARS: Final = 64

#: requirements.md Section 3.1 fixes one active meeting at a time.
MAX_CONCURRENT_SESSIONS: Final = 1


def max_payload_bytes() -> int:
    """Largest audio payload that can accompany a header inside MAX_FRAME_BYTES."""
    from protocol.frame import HEADER_SIZE_BYTES

    return MAX_FRAME_BYTES - HEADER_SIZE_BYTES
