"""Binary frame header conformance vectors.

Category B (`requirements.md` Section 25.15 B). Every fixture here is
purpose-built: hand-computed byte sequences and deliberately malformed headers,
exercising the defensive branches Section 25.15 B explicitly permits this
category to cover.

**These say nothing about ASR, language, speaker, overlap or translation
quality** and must never be cited as if they did (TEST-130). They prove the
codec parses what it should and refuses what it should not.

Section 9.2 additionally requires real-capture serialization tests before server
implementation. No real capture exists yet - none can, until Phase 4 runs a real
server - so PROT-110 stays short of `tested`. That gap is reported, not filled
with an invented capture (TEST-210).
"""

from __future__ import annotations

import struct

import pytest

from protocol import limits
from protocol.errors import ErrorCode, ProtocolError, is_fatal
from protocol.frame import (
    HEADER_SIZE_BYTES,
    FrameFlags,
    FrameHeader,
    decode_frame,
    decode_header,
    encode_frame,
    encode_header,
)
from protocol.version import PROTOCOL_MAJOR

pytestmark = pytest.mark.conformance

FIXTURE_KIND_VALID = "protocol_conformance_fixture"
FIXTURE_KIND_NEGATIVE = "negative_test_vector"

SAMPLES_20MS = 320
SAMPLES_40MS = 640


def make_header(**overrides: object) -> FrameHeader:
    fields: dict[str, object] = {
        "protocol_major": PROTOCOL_MAJOR,
        "stream_ordinal": 1,
        "sequence": 0,
        "start_sample": 0,
        "capture_monotonic_ns": 0,
        "sample_count": SAMPLES_20MS,
        "flags": FrameFlags.NONE,
    }
    fields.update(overrides)
    return FrameHeader(**fields)  # type: ignore[arg-type]


def silence(sample_count: int) -> bytes:
    return b"\x00\x00" * sample_count


class TestLayout:
    """The documented layout is a contract, not an implementation detail."""

    def test_header_is_28_bytes(self) -> None:
        assert HEADER_SIZE_BYTES == 28

    def test_field_offsets_match_the_adr(self) -> None:
        """ADR-0010 D20 fixes each field's offset. Decode them independently."""
        header = make_header(
            stream_ordinal=0x1234,
            sequence=0xDEADBEEF,
            start_sample=0x0102030405060708,
            capture_monotonic_ns=0x1122334455667788,
            sample_count=0x0140,
            flags=FrameFlags.SYNTHETIC_AUDIO,
        )
        raw = encode_header(header)

        assert raw[0] == PROTOCOL_MAJOR, "protocol_major at offset 0"
        assert raw[1] == HEADER_SIZE_BYTES, "header_size_bytes at offset 1"
        assert struct.unpack_from("<H", raw, 2)[0] == 0x1234, "stream_ordinal at 2"
        assert struct.unpack_from("<I", raw, 4)[0] == 0xDEADBEEF, "sequence at 4"
        assert struct.unpack_from("<q", raw, 8)[0] == 0x0102030405060708, "start_sample at 8"
        assert struct.unpack_from("<Q", raw, 16)[0] == 0x1122334455667788, "capture time at 16"
        assert struct.unpack_from("<H", raw, 24)[0] == 0x0140, "sample_count at 24"
        assert struct.unpack_from("<H", raw, 26)[0] == 1, "flags at 26"

    def test_encoding_is_little_endian(self) -> None:
        raw = encode_header(make_header(sequence=1))
        assert raw[4:8] == b"\x01\x00\x00\x00"

    @pytest.mark.parametrize(
        ("frame_ms", "sample_count", "expected_payload"),
        [(20, SAMPLES_20MS, 640), (40, SAMPLES_40MS, 1280)],
    )
    def test_documented_frame_sizes(
        self, frame_ms: int, sample_count: int, expected_payload: int
    ) -> None:
        """The overhead figures quoted in ADR-0010 D20 are reproducible."""
        frame = encode_frame(make_header(sample_count=sample_count), silence(sample_count))
        assert len(frame) == expected_payload + HEADER_SIZE_BYTES
        overhead = HEADER_SIZE_BYTES / len(frame)
        assert overhead == pytest.approx(0.042 if frame_ms == 20 else 0.021, abs=0.002)


class TestRoundTrip:
    @pytest.mark.parametrize("sample_count", [1, SAMPLES_20MS, SAMPLES_40MS, 8000])
    def test_frame_survives_round_trip(self, sample_count: int) -> None:
        header = make_header(sample_count=sample_count, start_sample=16000, sequence=50)
        payload = bytes(i % 251 for i in range(sample_count * 2))
        decoded, recovered = decode_frame(encode_frame(header, payload))
        assert decoded == header
        assert recovered == payload

    def test_synthetic_flag_survives(self) -> None:
        """ADR-0009 D15 gap fill must be identifiable from a raw capture alone."""
        header = make_header(flags=FrameFlags.SYNTHETIC_AUDIO)
        decoded, _ = decode_frame(encode_frame(header, silence(SAMPLES_20MS)))
        assert decoded.is_synthetic

    def test_span_is_half_open(self) -> None:
        header = make_header(start_sample=1000, sample_count=320)
        assert header.end_sample == 1320
        assert header.span.sample_count == 320
        assert header.span.contains_sample(1319)
        assert not header.span.contains_sample(1320)


class TestMalformedHeaders:
    """`negative_test_vector`. Purpose-built, and quality-mute by construction."""

    def test_truncated_header(self) -> None:
        raw = encode_header(make_header())[:-1]
        with pytest.raises(ProtocolError) as exc:
            decode_header(raw)
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_empty_input(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            decode_header(b"")
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_unknown_major_version_is_rejected(self) -> None:
        """Section 9.1, PROT-020. A different major may lay its fields out differently."""
        raw = bytearray(encode_header(make_header()))
        raw[0] = PROTOCOL_MAJOR + 1
        with pytest.raises(ProtocolError) as exc:
            decode_header(bytes(raw))
        assert exc.value.code is ErrorCode.PROT_UNSUPPORTED_MAJOR_VERSION
        assert is_fatal(exc.value.code)

    def test_header_size_smaller_than_the_minimum(self) -> None:
        raw = bytearray(encode_header(make_header()))
        raw[1] = HEADER_SIZE_BYTES - 1
        with pytest.raises(ProtocolError) as exc:
            decode_header(bytes(raw))
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_header_size_larger_than_the_data(self) -> None:
        raw = bytearray(encode_header(make_header()))
        raw[1] = HEADER_SIZE_BYTES + 8
        with pytest.raises(ProtocolError) as exc:
            decode_header(bytes(raw))
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_a_larger_header_from_a_newer_minor_is_skipped_correctly(self) -> None:
        """ADR-0010 D20: header_size_bytes is what buys forward compatibility.

        A newer minor version appends fields. An older reader must still find the
        payload rather than reading header bytes as audio.
        """
        extra = b"\xaa" * 8
        raw = bytearray(encode_header(make_header(sample_count=4)))
        raw[1] = HEADER_SIZE_BYTES + len(extra)
        payload = silence(4)
        frame = bytes(raw) + extra + payload

        header, recovered = decode_frame(frame)
        assert header.sample_count == 4
        assert recovered == payload, "the appended header bytes leaked into the payload"

    def test_negative_start_sample(self) -> None:
        raw = bytearray(encode_header(make_header()))
        struct.pack_into("<q", raw, 8, -1)
        with pytest.raises(ProtocolError) as exc:
            decode_header(bytes(raw))
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_sample_count_over_the_per_frame_limit(self) -> None:
        raw = bytearray(encode_header(make_header()))
        struct.pack_into("<H", raw, 24, limits.MAX_SAMPLES_PER_FRAME + 1)
        with pytest.raises(ProtocolError) as exc:
            decode_header(bytes(raw))
        assert exc.value.code is ErrorCode.PROT_FRAME_TOO_LARGE

    def test_payload_shorter_than_sample_count_claims(self) -> None:
        header = make_header(sample_count=320)
        frame = encode_header(header) + silence(319)
        with pytest.raises(ProtocolError) as exc:
            decode_frame(frame)
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_payload_longer_than_sample_count_claims(self) -> None:
        header = make_header(sample_count=320)
        frame = encode_header(header) + silence(321)
        with pytest.raises(ProtocolError) as exc:
            decode_frame(frame)
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_frame_over_the_byte_limit(self) -> None:
        oversized = b"\x00" * (limits.MAX_FRAME_BYTES + 1)
        with pytest.raises(ProtocolError) as exc:
            decode_frame(oversized)
        assert exc.value.code is ErrorCode.PROT_FRAME_TOO_LARGE


class TestEncodeValidatesToo:
    """Producing a frame no conforming receiver would accept is a bug worth
    catching on the side that can still fix it."""

    def test_sequence_beyond_u32(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            encode_header(make_header(sequence=2**32))
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_stream_ordinal_beyond_u16(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            encode_header(make_header(stream_ordinal=2**16))
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_negative_start_sample_is_refused_on_encode(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            encode_header(make_header(start_sample=-1))
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_payload_length_must_match_sample_count(self) -> None:
        with pytest.raises(ProtocolError) as exc:
            encode_frame(make_header(sample_count=320), silence(100))
        assert exc.value.code is ErrorCode.PROT_MALFORMED_HEADER

    def test_a_30_minute_meeting_fits_comfortably_in_the_sample_field(self) -> None:
        """The real recording is 29,139,328 samples. i64 is not close to strained."""
        header = make_header(start_sample=29_139_328 - 320)
        decoded, _ = decode_frame(encode_frame(header, silence(320)))
        assert decoded.end_sample == 29_139_328
