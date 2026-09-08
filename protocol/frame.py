"""Binary audio frame header codec.

ADR-0010 D20. 28 bytes, little-endian, every field naturally aligned:

```text
offset  size  field                  type
0       1     protocol_major         u8
1       1     header_size_bytes      u8
2       2     stream_ordinal         u16
4       4     sequence               u32
8       8     start_sample           i64
16      8     capture_monotonic_ns   u64
24      2     sample_count           u16
26      2     flags                  u16
```

The payload follows immediately: ``sample_count * 2`` bytes of ``pcm_s16le``.

``header_size_bytes`` at offset 1 costs one byte and buys forward compatibility.
A later minor version that appends a field leaves this value larger, and an
older receiver still skips to the payload correctly instead of reading header
bytes as audio.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from enum import IntFlag
from typing import Final

from protocol import limits
from protocol.errors import ErrorCode, ProtocolError
from protocol.timeline import SampleSpan, validate_sample_offset
from protocol.version import PROTOCOL_MAJOR

#: ``<`` fixes little-endian and disables struct's alignment padding, so the
#: layout is exactly the one documented above rather than whatever the host
#: compiler would have chosen.
_HEADER_STRUCT: Final = struct.Struct("<BBHIqQHH")

HEADER_SIZE_BYTES: Final = _HEADER_STRUCT.size
assert HEADER_SIZE_BYTES == 28, "the documented header layout is 28 bytes"

_U8_MAX: Final = 2**8 - 1
_U16_MAX: Final = 2**16 - 1
_U32_MAX: Final = 2**32 - 1
_U64_MAX: Final = 2**64 - 1


class FrameFlags(IntFlag):
    """Header flag bits.

    Bit 0 marks frames whose payload contains gap-filling silence inserted under
    ADR-0009 D15. It exists so that a raw capture is self-describing about
    inserted samples without needing the event log alongside it - which matters
    because a category C fixture may outlive the session that produced it.
    """

    NONE = 0
    SYNTHETIC_AUDIO = 1 << 0


@dataclass(frozen=True, slots=True)
class FrameHeader:
    """A decoded audio frame header."""

    protocol_major: int
    stream_ordinal: int
    sequence: int
    start_sample: int
    capture_monotonic_ns: int
    sample_count: int
    flags: FrameFlags = FrameFlags.NONE

    @property
    def end_sample(self) -> int:
        """First sample offset *after* this frame. The span is half-open."""
        return self.start_sample + self.sample_count

    @property
    def span(self) -> SampleSpan:
        return SampleSpan(start_sample=self.start_sample, end_sample=self.end_sample)

    @property
    def payload_bytes(self) -> int:
        return self.sample_count * limits.BYTES_PER_SAMPLE

    @property
    def is_synthetic(self) -> bool:
        """Whether this frame carries inserted gap-filling silence."""
        return bool(self.flags & FrameFlags.SYNTHETIC_AUDIO)


def _require(condition: bool, code: ErrorCode, detail: str) -> None:
    if not condition:
        raise ProtocolError(code, detail)


def _check_unsigned(value: int, maximum: int, field: str) -> None:
    _require(
        0 <= value <= maximum,
        ErrorCode.PROT_MALFORMED_HEADER,
        f"{field} {value} does not fit its unsigned field (max {maximum})",
    )


def encode_header(header: FrameHeader) -> bytes:
    """Serialise a header.

    Raises:
        ProtocolError: with ``PROT_MALFORMED_HEADER`` if any field is out of
            range. Encoding validates as strictly as decoding: producing a frame
            no conforming receiver would accept is a bug worth catching on the
            side that can still fix it.
    """
    _check_unsigned(header.protocol_major, _U8_MAX, "protocol_major")
    _check_unsigned(header.stream_ordinal, _U16_MAX, "stream_ordinal")
    _check_unsigned(header.sequence, _U32_MAX, "sequence")
    _check_unsigned(header.capture_monotonic_ns, _U64_MAX, "capture_monotonic_ns")
    _check_unsigned(header.sample_count, _U16_MAX, "sample_count")
    _check_unsigned(int(header.flags), _U16_MAX, "flags")
    validate_sample_offset(header.start_sample)

    _require(
        header.sample_count <= limits.MAX_SAMPLES_PER_FRAME,
        ErrorCode.PROT_FRAME_TOO_LARGE,
        f"sample_count {header.sample_count} exceeds the "
        f"{limits.MAX_SAMPLES_PER_FRAME} sample per-frame limit",
    )

    return _HEADER_STRUCT.pack(
        header.protocol_major,
        HEADER_SIZE_BYTES,
        header.stream_ordinal,
        header.sequence,
        header.start_sample,
        header.capture_monotonic_ns,
        header.sample_count,
        int(header.flags),
    )


def decode_header(data: bytes | bytearray | memoryview) -> tuple[FrameHeader, int]:
    """Decode a header from the front of ``data``.

    Returns:
        The header and the offset at which its payload begins. The offset comes
        from the frame's own ``header_size_bytes`` rather than from this build's
        constant, so a frame written by a newer minor version with extra header
        fields still yields the right payload boundary.

    Raises:
        ProtocolError: with ``PROT_MALFORMED_HEADER`` for a truncated or
            self-inconsistent header, or ``PROT_UNSUPPORTED_MAJOR_VERSION`` for a
            major this build cannot speak.
    """
    view = memoryview(data)

    _require(
        len(view) >= HEADER_SIZE_BYTES,
        ErrorCode.PROT_MALFORMED_HEADER,
        f"frame is {len(view)} bytes, shorter than the {HEADER_SIZE_BYTES} byte header",
    )

    (
        protocol_major,
        header_size_bytes,
        stream_ordinal,
        sequence,
        start_sample,
        capture_monotonic_ns,
        sample_count,
        raw_flags,
    ) = _HEADER_STRUCT.unpack_from(view, 0)

    # Version is checked before anything else is trusted: a different major may
    # lay its fields out differently, so the values just unpacked are only
    # meaningful once the major matches.
    _require(
        protocol_major == PROTOCOL_MAJOR,
        ErrorCode.PROT_UNSUPPORTED_MAJOR_VERSION,
        f"frame declares protocol major {protocol_major}, this build speaks {PROTOCOL_MAJOR}",
    )

    _require(
        header_size_bytes >= HEADER_SIZE_BYTES,
        ErrorCode.PROT_MALFORMED_HEADER,
        f"header_size_bytes {header_size_bytes} is smaller than the "
        f"{HEADER_SIZE_BYTES} byte minimum for major {PROTOCOL_MAJOR}",
    )
    _require(
        len(view) >= header_size_bytes,
        ErrorCode.PROT_MALFORMED_HEADER,
        f"frame is {len(view)} bytes but declares a {header_size_bytes} byte header",
    )

    _require(
        sample_count <= limits.MAX_SAMPLES_PER_FRAME,
        ErrorCode.PROT_FRAME_TOO_LARGE,
        f"sample_count {sample_count} exceeds the "
        f"{limits.MAX_SAMPLES_PER_FRAME} sample per-frame limit",
    )

    validate_sample_offset(start_sample)

    header = FrameHeader(
        protocol_major=protocol_major,
        stream_ordinal=stream_ordinal,
        sequence=sequence,
        start_sample=start_sample,
        capture_monotonic_ns=capture_monotonic_ns,
        sample_count=sample_count,
        flags=FrameFlags(raw_flags),
    )
    return header, header_size_bytes


def encode_frame(header: FrameHeader, payload: bytes) -> bytes:
    """Serialise a complete frame.

    Raises:
        ProtocolError: if the payload length disagrees with ``sample_count``, or
            the frame exceeds the size limit.
    """
    expected = header.payload_bytes
    _require(
        len(payload) == expected,
        ErrorCode.PROT_MALFORMED_HEADER,
        f"payload is {len(payload)} bytes but sample_count {header.sample_count} "
        f"requires {expected}",
    )

    encoded = encode_header(header) + payload
    _require(
        len(encoded) <= limits.MAX_FRAME_BYTES,
        ErrorCode.PROT_FRAME_TOO_LARGE,
        f"frame is {len(encoded)} bytes, over the {limits.MAX_FRAME_BYTES} byte limit",
    )
    return encoded


def decode_frame(data: bytes | bytearray | memoryview) -> tuple[FrameHeader, bytes]:
    """Decode a complete frame into its header and audio payload.

    Raises:
        ProtocolError: ``PROT_FRAME_TOO_LARGE`` when the frame exceeds the size
            limit, ``PROT_MALFORMED_HEADER`` when the payload length disagrees
            with ``sample_count``, plus anything :func:`decode_header` raises.
    """
    view = memoryview(data)

    _require(
        len(view) <= limits.MAX_FRAME_BYTES,
        ErrorCode.PROT_FRAME_TOO_LARGE,
        f"frame is {len(view)} bytes, over the {limits.MAX_FRAME_BYTES} byte limit",
    )

    header, payload_offset = decode_header(view)
    payload = bytes(view[payload_offset:])

    expected = header.payload_bytes
    _require(
        len(payload) == expected,
        ErrorCode.PROT_MALFORMED_HEADER,
        f"payload is {len(payload)} bytes but sample_count {header.sample_count} "
        f"requires {expected}",
    )

    return header, payload
