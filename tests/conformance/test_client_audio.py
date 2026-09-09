"""Client audio pipeline conformance vectors.

Category B (`requirements.md` Section 25.15 B). Purpose-built inputs exercising
conversion arithmetic, buffer bounds, timeline continuity across loss, and the
lifecycle graph.

**No claim about audio quality is made anywhere here** (TEST-130). Section 22.3
forbids synthetic audio for triggering edge cases in *quality* tests; what these
exercise is arithmetic and state, and the one test that needs real device
behaviour opens the machine's actual loopback endpoint and skips when there is
none.

The property worth the most attention is in `TestTimelineContinuity`: when audio
is lost, the canonical timeline must advance by exactly the lost duration
(PROT-180). Getting that wrong shifts every subsequent timestamp by the size of
the hole, silently, for the rest of the meeting.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable

import numpy as np
import pytest

from client.devices import (
    DeviceSelection,
    DeviceUnavailableError,
    LoopbackDevice,
    UnsupportedDeviceFormatError,
    enumerate_loopback_devices,
    select_device,
    validate_device,
)
from client.framing import (
    DEFAULT_FRAME_MS,
    FrameBuilder,
    samples_per_frame,
)
from client.idle import IdleTracker
from client.lifecycle import (
    ALLOWED_TRANSITIONS,
    CaptureLifecycle,
    CaptureState,
    IllegalTransitionError,
    ReasonCode,
    Transition,
)
from client.resampler import (
    AudioConverter,
    downmix_to_mono,
    float32_to_pcm16,
    pcm16_to_float32,
)
from client.ringbuffer import CaptureRing, SendRetention
from protocol.frame import FrameFlags, decode_frame
from protocol.limits import BYTES_PER_SAMPLE, CANONICAL_SAMPLE_RATE_HZ

pytestmark = pytest.mark.conformance

FIXTURE_KIND = "protocol_conformance_fixture"

DEVICE_RATE = 48_000
DEVICE_CHANNELS = 2


def silence_bytes(frames: int, channels: int = 1) -> bytes:
    return b"\x00\x00" * frames * channels


# ---------------------------------------------------------------------------
# Device selection - ADR-0014 D38
# ---------------------------------------------------------------------------


class FakeAudio:
    """The slice of PyAudioWPatch that `client.devices` uses.

    Selection rules are the behaviour worth protecting, and they are decided
    entirely by device metadata. Testing them against a fake keeps them
    verifiable on any machine, including one with no audio hardware at all.
    """

    def __init__(self, devices: list[dict[str, object]], default_index: int | None) -> None:
        self._devices = devices
        self._default_index = default_index

    def get_default_wasapi_loopback(self) -> dict[str, object]:
        if self._default_index is None:
            raise OSError("no default WASAPI loopback device")
        for info in self._devices:
            if info["index"] == self._default_index:
                return info
        raise OSError("default device index is not in the device list")

    def get_loopback_device_info_generator(self) -> object:
        return iter(self._devices)


def device_info(
    index: int, name: str, rate: float = 48000.0, channels: int = 2
) -> dict[str, object]:
    return {
        "index": index,
        "name": name,
        "defaultSampleRate": rate,
        "maxInputChannels": channels,
    }


class TestDeviceSelection:
    def _audio(self, default_index: int | None = 8) -> FakeAudio:
        return FakeAudio(
            [
                device_info(8, "SAMSUNG (NVIDIA High Definition Audio) [Loopback]"),
                device_info(9, "Realtek Digital Output (Realtek(R) Audio) [Loopback]"),
            ],
            default_index,
        )

    def test_no_preference_uses_the_system_default(self) -> None:
        """AUD-220: the platform answers this, so no heuristic is involved."""
        selection = select_device(self._audio())
        assert selection.device.index == 8
        assert selection.device.is_system_default
        assert not selection.fell_back

    def test_a_preference_that_exists_is_honoured(self) -> None:
        selection = select_device(
            self._audio(), preferred_name="Realtek Digital Output (Realtek(R) Audio) [Loopback]"
        )
        assert selection.device.index == 9
        assert not selection.fell_back

    def test_a_vanished_preference_falls_back_and_says_so(self) -> None:
        """ADR-0014 D38. Silently recording from a different endpoint is the
        failure mode this exists to prevent."""
        selection = select_device(self._audio(), preferred_name="Headset that was unplugged")

        assert selection.fell_back
        assert selection.device.index == 8
        message = selection.fallback_message
        assert message is not None
        assert "Headset that was unplugged" in message
        assert "SAMSUNG" in message

    def test_selection_is_by_name_not_index(self) -> None:
        """Indices shift across reboots; names do not.

        The same name at a different index must still resolve, which is the
        whole reason the preference is stored as a name.
        """
        moved = FakeAudio(
            [
                device_info(3, "Realtek Digital Output (Realtek(R) Audio) [Loopback]"),
                device_info(7, "SAMSUNG (NVIDIA High Definition Audio) [Loopback]"),
            ],
            default_index=7,
        )
        selection = select_device(
            moved, preferred_name="Realtek Digital Output (Realtek(R) Audio) [Loopback]"
        )
        assert selection.device.index == 3
        assert not selection.fell_back

    def test_no_default_and_no_match_is_an_error(self) -> None:
        audio = FakeAudio([], default_index=None)
        with pytest.raises(DeviceUnavailableError):
            select_device(audio)

    def test_enumeration_marks_the_default(self) -> None:
        devices = enumerate_loopback_devices(self._audio())
        assert [d.index for d in devices] == [8, 9]
        assert [d.is_system_default for d in devices] == [True, False]

    def test_enumeration_survives_having_no_default(self) -> None:
        """Listing devices is useful even when no default speaker is enabled."""
        devices = enumerate_loopback_devices(self._audio(default_index=None))
        assert len(devices) == 2
        assert not any(d.is_system_default for d in devices)

    def test_a_float_sample_rate_becomes_an_exact_integer(self) -> None:
        """The host API reports 48000.0; every downstream calculation needs an int."""
        selection = select_device(FakeAudio([device_info(0, "x", rate=48000.0)], 0))
        assert selection.device.sample_rate_hz == 48_000
        assert isinstance(selection.device.sample_rate_hz, int)

    @pytest.mark.parametrize(
        ("channels", "rate"),
        [(0, 48000.0), (99, 48000.0), (2, 4000.0), (2, 400000.0)],
    )
    def test_unsupported_formats_are_refused_before_capture(
        self, channels: int, rate: float
    ) -> None:
        """AUD-040: validation happens before the device is opened."""
        device = LoopbackDevice(index=0, name="odd", sample_rate_hz=int(rate), channels=channels)
        with pytest.raises(UnsupportedDeviceFormatError):
            validate_device(device)

    def test_a_selection_without_fallback_has_no_message(self) -> None:
        selection = DeviceSelection(
            device=LoopbackDevice(index=0, name="x", sample_rate_hz=48000, channels=2)
        )
        assert selection.fallback_message is None


# ---------------------------------------------------------------------------
# Conversion - ADR-0014 D35
# ---------------------------------------------------------------------------


class TestConversion:
    def test_int16_round_trip_is_exact(self) -> None:
        """Every representable int16 survives the float32 detour unchanged.

        If it did not, the conversion pipeline would add quantisation noise to
        audio that needed no conversion at all.
        """
        raw = np.array([0, 1, -1, 32767, -32768, 1234, -4321], dtype="<i2").tobytes()
        assert float32_to_pcm16(pcm16_to_float32(raw)) == raw

    def test_downmix_averages_channels(self) -> None:
        stereo = np.array([1.0, 3.0, 2.0, 4.0, -1.0, 1.0], dtype=np.float32)
        assert downmix_to_mono(stereo, 2).tolist() == [2.0, 3.0, 0.0]

    def test_mono_passes_through_untouched(self) -> None:
        mono = np.array([0.5, -0.5], dtype=np.float32)
        assert downmix_to_mono(mono, 1) is mono

    def test_a_partial_frame_is_refused(self) -> None:
        """Averaging across a misaligned boundary would mix the wrong samples."""
        with pytest.raises(ValueError, match="whole number"):
            downmix_to_mono(np.zeros(5, dtype=np.float32), 2)

    def test_odd_byte_count_is_refused(self) -> None:
        with pytest.raises(ValueError, match="whole number"):
            pcm16_to_float32(b"\x00")

    def test_clipping_saturates_rather_than_wrapping(self) -> None:
        """A wrapped sample becomes a loud value of the opposite sign - a click
        that no downstream check would attribute correctly."""
        hot = np.array([2.0, -2.0], dtype=np.float32)
        recovered = np.frombuffer(float32_to_pcm16(hot), dtype="<i2")
        assert recovered.tolist() == [32767, -32768]

    def test_the_real_device_format_converts_at_the_expected_ratio(self) -> None:
        """48 kHz stereo to 16 kHz mono, the format this project's devices report."""
        converter = AudioConverter(source_rate_hz=DEVICE_RATE, source_channels=DEVICE_CHANNELS)
        assert converter.ratio == pytest.approx(1 / 3)
        assert converter.needs_resampling
        assert converter.needs_downmix

        produced = 0
        for _ in range(50):  # one second of 20 ms chunks
            produced += len(converter.convert(silence_bytes(960, DEVICE_CHANNELS))) // 2
        produced += len(converter.flush()) // 2

        # One second in, one second out, allowing for the converter's filter
        # latency at the very start.
        assert abs(produced - CANONICAL_SAMPLE_RATE_HZ) < 100

    def test_a_matching_rate_skips_resampling_entirely(self) -> None:
        """The real recording is already 16 kHz mono, so this path exists."""
        converter = AudioConverter(source_rate_hz=16_000, source_channels=1)
        assert not converter.needs_resampling
        assert not converter.needs_downmix

        payload = np.array([100, -100, 200], dtype="<i2").tobytes()
        assert converter.convert(payload) == payload

    def test_state_persists_across_chunks(self) -> None:
        """AUD-060. Restarting per chunk produces a discontinuity fifty times a
        second, and those land in Whisper's input."""
        chunked = AudioConverter(source_rate_hz=DEVICE_RATE, source_channels=1)
        whole = AudioConverter(source_rate_hz=DEVICE_RATE, source_channels=1)

        ramp = np.linspace(-0.5, 0.5, 4800, dtype=np.float32)
        payload = float32_to_pcm16(ramp)

        piecewise = b"".join(
            chunked.convert(payload[i : i + 960 * 2]) for i in range(0, len(payload), 960 * 2)
        )
        at_once = whole.convert(payload)

        # A stateful converter fed in pieces produces the same leading samples as
        # one fed the whole buffer. A stateless one would not.
        common = min(len(piecewise), len(at_once))
        assert common > 0
        assert piecewise[:common] == at_once[:common]

    def test_empty_input_is_not_an_error(self) -> None:
        converter = AudioConverter(source_rate_hz=DEVICE_RATE, source_channels=1)
        assert converter.convert(b"") == b""

    def test_invalid_configuration_is_refused(self) -> None:
        with pytest.raises(ValueError):
            AudioConverter(source_rate_hz=0, source_channels=1)
        with pytest.raises(ValueError):
            AudioConverter(source_rate_hz=48_000, source_channels=0)


# ---------------------------------------------------------------------------
# Ring buffer - ADR-0013 D37, ADR-0010 D22
# ---------------------------------------------------------------------------


class TestCaptureRing:
    def _ring(self, seconds: float = 0.1) -> CaptureRing:
        return CaptureRing(
            capacity_seconds=seconds, sample_rate_hz=DEVICE_RATE, channels=DEVICE_CHANNELS
        )

    def test_capacity_is_derived_from_seconds_of_media_time(self) -> None:
        ring = self._ring(seconds=1.0)
        assert ring.capacity_bytes == DEVICE_RATE * DEVICE_CHANNELS * 2

    def test_chunks_come_back_in_order(self) -> None:
        ring = self._ring()
        for value in (b"\x01\x00\x01\x00", b"\x02\x00\x02\x00"):
            ring.push(value)
        assert ring.pop_all() == b"\x01\x00\x01\x00\x02\x00\x02\x00"

    def test_overflow_drops_the_oldest(self) -> None:
        """Dropping the newest would discard what is being said now."""
        ring = CaptureRing(capacity_seconds=0.001, sample_rate_hz=1000, channels=1)
        ring.push(b"\x01\x00" * 1)
        dropped = ring.push(b"\x02\x00" * 1)

        assert dropped.occurred
        assert ring.pop_all() == b"\x02\x00"

    def test_overflow_reports_what_was_lost_in_device_frames(self) -> None:
        """The consumer converts this into a timeline skip, so the unit matters."""
        ring = CaptureRing(capacity_seconds=0.01, sample_rate_hz=1000, channels=1)
        ring.push(b"\x00\x00" * 10)
        dropped = ring.push(b"\x00\x00" * 10)

        assert dropped.device_frames == 10
        assert ring.stats().device_frames_dropped == 10
        assert ring.stats().overflow_events == 1

    def test_a_misaligned_chunk_is_refused(self) -> None:
        ring = self._ring()
        with pytest.raises(ValueError, match="whole number"):
            ring.push(b"\x00\x00\x00")

    def test_an_empty_push_is_a_no_op(self) -> None:
        ring = self._ring()
        assert not ring.push(b"").occurred
        assert ring.buffered_bytes == 0

    def test_popping_an_empty_ring_yields_nothing(self) -> None:
        ring = self._ring()
        assert ring.pop_all() == b""
        assert ring.pop_one() is None

    def test_peak_occupancy_is_tracked(self) -> None:
        ring = self._ring(seconds=1.0)
        ring.push(b"\x00\x00\x00\x00" * 100)
        ring.pop_all()
        assert ring.stats().peak_bytes == 400
        assert ring.buffered_bytes == 0

    def test_a_zero_capacity_ring_is_refused(self) -> None:
        with pytest.raises(ValueError):
            CaptureRing(capacity_seconds=0, sample_rate_hz=DEVICE_RATE, channels=2)


class TestSendRetention:
    def test_frames_are_released_once_acknowledged(self) -> None:
        """ADR-0010 D21: the cumulative ack is what lets the client free memory."""
        retention = SendRetention(capacity_seconds=10.0)
        for index in range(4):
            retention.remember(index * 320, silence_bytes(320))

        released = retention.release_through(640)

        assert released == 2
        assert retention.oldest_retained_sample == 640

    def test_replay_returns_frames_the_server_still_needs(self) -> None:
        """ADR-0009 D17: resume_from_sample against what is still held."""
        retention = SendRetention(capacity_seconds=10.0)
        for index in range(4):
            retention.remember(index * 320, silence_bytes(320))

        replay = retention.replay_from(640)

        assert [start for start, _ in replay] == [640, 960]

    def test_capacity_evicts_the_oldest(self) -> None:
        retention = SendRetention(capacity_seconds=320 * 2 / CANONICAL_SAMPLE_RATE_HZ)
        for index in range(5):
            retention.remember(index * 320, silence_bytes(320))

        assert retention.buffered_samples <= retention.capacity_samples
        oldest = retention.oldest_retained_sample
        assert oldest is not None and oldest > 0

    def test_an_empty_retention_has_no_oldest_sample(self) -> None:
        assert SendRetention(capacity_seconds=1.0).oldest_retained_sample is None


# ---------------------------------------------------------------------------
# Framing and timeline continuity - PROT-180
# ---------------------------------------------------------------------------


class TestFraming:
    def test_default_frame_is_twenty_milliseconds(self) -> None:
        """ADR-0014 D36. The competing argument was worth 1.4 kB/s."""
        assert DEFAULT_FRAME_MS == 20
        assert samples_per_frame(20) == 320
        assert samples_per_frame(40) == 640

    def test_every_whole_millisecond_is_exact_at_16_khz(self) -> None:
        """16000 Hz is 16 samples per millisecond, so no integer duration is
        fractional. The guard below only bites at other rates - recorded here so
        nobody later assumes it is unreachable."""
        for frame_ms in range(1, 101):
            assert samples_per_frame(frame_ms) == frame_ms * 16

    def test_a_fractional_frame_duration_is_refused(self) -> None:
        """A fraction of a sample per frame becomes a real offset over 30 minutes.

        44100 Hz is 44.1 samples per millisecond, so a 1 ms frame there is not a
        whole number of samples.
        """
        with pytest.raises(ValueError, match="whole number"):
            samples_per_frame(1, sample_rate_hz=44_100)

    def test_frames_carry_increasing_sequence_and_contiguous_offsets(self) -> None:
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        frames = builder.push(silence_bytes(320 * 3))

        assert [f.header.sequence for f in frames] == [0, 1, 2]
        assert [f.start_sample for f in frames] == [0, 320, 640]
        assert all(f.header.sample_count == 320 for f in frames)

    def test_partial_audio_is_held_until_a_frame_is_complete(self) -> None:
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)

        assert builder.push(silence_bytes(100)) == []
        assert builder.pending_samples == 100
        assert len(builder.push(silence_bytes(220))) == 1
        assert builder.pending_samples == 0

    def test_emitted_frames_decode_back_to_what_went_in(self) -> None:
        builder = FrameBuilder(stream_ordinal=7, clock_ns=lambda: 12345)
        payload = np.arange(320, dtype="<i2").tobytes()

        frame = builder.push(payload)[0]
        header, recovered = decode_frame(frame.encoded)

        assert recovered == payload
        assert header.stream_ordinal == 7
        assert header.start_sample == 0

    def test_the_capture_timestamp_is_relative_and_monotonic(self) -> None:
        ticks = iter([1_000, 2_000, 3_000])
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: next(ticks))

        frames = builder.push(silence_bytes(320 * 3))

        assert [f.header.capture_monotonic_ns for f in frames] == [0, 1_000, 2_000]

    def test_flush_emits_a_short_final_frame_rather_than_padding(self) -> None:
        """A padded frame would claim samples that were never captured."""
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        builder.push(silence_bytes(100))

        frames = builder.flush()

        assert len(frames) == 1
        assert frames[0].header.sample_count == 100

    def test_flush_can_pad_when_asked(self) -> None:
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        builder.push(silence_bytes(100))
        assert builder.flush(pad=True)[0].header.sample_count == 320

    def test_synthetic_frames_are_flagged(self) -> None:
        """ADR-0009 D15: a raw capture is self-describing about inserted silence."""
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)

        frames = builder.emit_synthetic(500)

        assert [f.header.sample_count for f in frames] == [320, 180]
        assert all(f.header.is_synthetic for f in frames)
        assert all(f.header.flags & FrameFlags.SYNTHETIC_AUDIO for f in frames)


class TestTimelineContinuity:
    """PROT-180: the timeline is never compressed to hide missing audio."""

    def test_a_skip_advances_the_cursor_by_exactly_the_lost_duration(self) -> None:
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        builder.push(silence_bytes(320))

        span = builder.skip(1_600)

        assert span.start_sample == 320
        assert span.end_sample == 1_920
        assert span.sample_count == 1_600
        assert builder.next_sample == 1_920

    def test_frames_after_a_skip_carry_the_shifted_offset(self) -> None:
        """The failure this prevents: every timestamp after a gap being wrong by
        the size of the gap, silently, for the rest of the meeting."""
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        builder.push(silence_bytes(320))
        builder.skip(1_600)

        resumed = builder.push(silence_bytes(320))[0]

        assert resumed.start_sample == 1_920

    def test_a_skip_discards_the_partial_frame_beside_it(self) -> None:
        """Joining audio across a hole would fabricate continuity."""
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        builder.push(silence_bytes(100))
        assert builder.pending_samples == 100

        builder.skip(160)

        assert builder.pending_samples == 0

    def test_a_non_positive_skip_is_refused(self) -> None:
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        with pytest.raises(ValueError):
            builder.skip(0)

    def test_a_ring_overflow_maps_to_an_exact_timeline_skip(self) -> None:
        """End to end: device frames lost at 48 kHz become canonical samples lost.

        1000 device frames at 48 kHz is 20.833 ms, which is 333 samples at
        16 kHz. The conversion has to happen on the count, not on the audio.
        """
        device_frames_lost = 1_000
        canonical_lost = device_frames_lost * CANONICAL_SAMPLE_RATE_HZ // DEVICE_RATE

        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        span = builder.skip(canonical_lost)

        assert span.sample_count == 333
        assert builder.next_sample == 333

    def test_stats_separate_emitted_from_skipped(self) -> None:
        builder = FrameBuilder(stream_ordinal=1, clock_ns=lambda: 0)
        builder.push(silence_bytes(640))
        builder.skip(320)

        assert builder.stats.samples_emitted == 640
        assert builder.stats.samples_skipped == 320
        assert builder.stats.frames_emitted == 2
        assert builder.stats.skips == 1


# ---------------------------------------------------------------------------
# Lifecycle - Section 8.2
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_the_happy_path_of_section_8_2(self) -> None:
        life = CaptureLifecycle(clock_ns=lambda: 0, clock_utc=lambda: "t")

        life.move_to(CaptureState.CONNECTING, ReasonCode.USER_STARTED)
        life.move_to(CaptureState.READY, ReasonCode.SESSION_ACCEPTED)
        life.move_to(CaptureState.CAPTURING, ReasonCode.CAPTURE_STARTED)
        life.move_to(CaptureState.STOPPING, ReasonCode.USER_STOPPED)
        life.move_to(CaptureState.COMPLETED, ReasonCode.SESSION_STOPPED_ACK)

        assert life.is_terminal
        assert len(life.history) == 5

    def test_an_illegal_transition_raises_rather_than_being_ignored(self) -> None:
        """A client in a state its own UI cannot describe is worse than a crash."""
        life = CaptureLifecycle()
        with pytest.raises(IllegalTransitionError, match="cannot move from idle to capturing"):
            life.move_to(CaptureState.CAPTURING, ReasonCode.CAPTURE_STARTED)

    def test_completed_is_terminal(self) -> None:
        assert ALLOWED_TRANSITIONS[CaptureState.COMPLETED] == frozenset()

    def test_error_and_reconnecting_are_mutually_reachable(self) -> None:
        """The Section 8.2 diagram draws this edge in both directions."""
        assert CaptureState.RECONNECTING in ALLOWED_TRANSITIONS[CaptureState.ERROR]
        assert CaptureState.ERROR in ALLOWED_TRANSITIONS[CaptureState.RECONNECTING]

    def test_capturing_can_reconnect_without_passing_through_error(self) -> None:
        life = CaptureLifecycle(clock_ns=lambda: 0, clock_utc=lambda: "t")
        life.move_to(CaptureState.CONNECTING, ReasonCode.USER_STARTED)
        life.move_to(CaptureState.READY, ReasonCode.SESSION_ACCEPTED)
        life.move_to(CaptureState.CAPTURING, ReasonCode.CAPTURE_STARTED)

        life.move_to(CaptureState.RECONNECTING, ReasonCode.CONNECTION_LOST)
        life.move_to(CaptureState.CAPTURING, ReasonCode.RESUME_ACCEPTED)

        assert life.is_capturing

    def test_every_transition_records_a_reason_and_both_clocks(self) -> None:
        """AUD-130 requires a reason, not just a state."""
        life = CaptureLifecycle(
            clock_ns=lambda: 42, clock_utc=lambda: "2026-09-08T00:00:00.000+00:00"
        )
        transition = life.move_to(
            CaptureState.CONNECTING, ReasonCode.USER_STARTED, detail="clicked start"
        )

        assert transition.reason is ReasonCode.USER_STARTED
        assert transition.at_monotonic_ns == 42
        assert transition.at_utc.startswith("2026-09-08")
        assert "clicked start" in transition.describe()

    def test_transitions_are_published_to_the_observer(self) -> None:
        """The client wires this to the debug writer: transitions never cross the
        wire, so they reach the meeting record as `client_local` records."""
        seen: list[Transition] = []
        life = CaptureLifecycle(
            clock_ns=lambda: 0, clock_utc=lambda: "t", on_transition=seen.append
        )
        life.move_to(CaptureState.CONNECTING, ReasonCode.USER_STARTED)

        assert [t.to_state for t in seen] == [CaptureState.CONNECTING]

    def test_a_failure_from_capturing_reaches_error(self) -> None:
        life = CaptureLifecycle(clock_ns=lambda: 0, clock_utc=lambda: "t")
        life.move_to(CaptureState.CONNECTING, ReasonCode.USER_STARTED)
        life.move_to(CaptureState.READY, ReasonCode.SESSION_ACCEPTED)
        life.move_to(CaptureState.CAPTURING, ReasonCode.CAPTURE_STARTED)

        life.fail(ReasonCode.DEVICE_REMOVED, "the endpoint disappeared")

        assert life.state is CaptureState.ERROR
        assert not life.is_capturing

    def test_reason_counts_feed_the_session_summary(self) -> None:
        life = CaptureLifecycle(clock_ns=lambda: 0, clock_utc=lambda: "t")
        life.move_to(CaptureState.CONNECTING, ReasonCode.USER_STARTED)
        life.move_to(CaptureState.READY, ReasonCode.SESSION_ACCEPTED)
        life.move_to(CaptureState.CAPTURING, ReasonCode.CAPTURE_STARTED)
        life.move_to(CaptureState.RECONNECTING, ReasonCode.CONNECTION_LOST)
        life.move_to(CaptureState.CAPTURING, ReasonCode.RESUME_ACCEPTED)
        life.move_to(CaptureState.RECONNECTING, ReasonCode.CONNECTION_LOST)

        assert life.reason_counts()[ReasonCode.CONNECTION_LOST] == 2

    def test_the_transition_table_covers_every_state(self) -> None:
        """A state missing from the table would raise KeyError at the worst moment."""
        assert set(ALLOWED_TRANSITIONS) == set(CaptureState)


# ---------------------------------------------------------------------------
# Real device - structural assertions only
# ---------------------------------------------------------------------------


def _real_loopback_devices() -> list[LoopbackDevice]:
    try:
        import pyaudiowpatch as pyaudio
    except ImportError:  # pragma: no cover - non-Windows
        return []
    try:
        with pyaudio.PyAudio() as audio:
            return enumerate_loopback_devices(audio)
    except Exception:  # pragma: no cover - no audio subsystem
        return []


class TestRealLoopbackDevices:
    """Reads the machine's actual WASAPI endpoints.

    Structural assertions only: that devices are discoverable, that the default
    is among them, and that their reported format is one the pipeline accepts.
    **No audio is captured and no claim about audio is made.**
    """

    def test_the_machine_exposes_loopback_devices(self) -> None:
        devices = _real_loopback_devices()
        if not devices:
            pytest.skip("no WASAPI loopback device on this machine")
        assert all("[Loopback]" in d.name or d.channels >= 1 for d in devices)

    def test_every_real_device_passes_validation(self) -> None:
        """AUD-040 against reality rather than a fake."""
        devices = _real_loopback_devices()
        if not devices:
            pytest.skip("no WASAPI loopback device on this machine")
        for device in devices:
            validate_device(device)

    def test_a_converter_can_be_built_for_every_real_device(self) -> None:
        """Whatever format the machine reports, the pipeline must handle it."""
        devices = _real_loopback_devices()
        if not devices:
            pytest.skip("no WASAPI loopback device on this machine")
        for device in devices:
            converter = AudioConverter(
                source_rate_hz=device.sample_rate_hz, source_channels=device.channels
            )
            out = converter.convert(silence_bytes(device.sample_rate_hz // 50, device.channels))
            assert len(out) % BYTES_PER_SAMPLE == 0


# ---------------------------------------------------------------------------
# Idle endpoint detection
# ---------------------------------------------------------------------------


class TestIdleTracker:
    """A WASAPI endpoint with nothing playing delivers no callbacks at all.

    Verified on real hardware on 2026-09-08: three seconds on an idle endpoint
    produced zero callbacks while `is_active()` reported True, and starting
    playback produced 106 callbacks and 434,176 bytes in 2.26 s.

    That is why a sample counter alone cannot detect the condition - a counter
    that never advances looks the same whether one second passed or an hour. The
    tracker uses monotonic time for exactly that one question, and for nothing
    else: Section 25.1 keeps the sample offset as the only media authority.
    """

    def _clock(self, ticks: list[float]) -> Callable[[], float]:
        stream = iter(ticks)
        last = ticks[-1]

        def read() -> float:
            nonlocal last
            with contextlib.suppress(StopIteration):
                last = next(stream)
            return last

        return read

    def test_a_producing_endpoint_is_not_idle(self) -> None:
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 0.1, 0.2]))
        tracker.start()

        assert not tracker.poll()
        assert not tracker.is_idle

    def test_silence_past_the_threshold_is_idle(self) -> None:
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 2.0]))
        tracker.start()

        assert tracker.poll()
        assert tracker.is_idle

    def test_the_idle_period_starts_at_the_last_audio_not_the_threshold(self) -> None:
        """Attributing the whole quiet stretch is what keeps the accounting exact."""
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 2.0, 3.0]))
        tracker.start()
        tracker.poll()

        period = tracker.note_audio(device_frames=480)

        assert period is not None
        assert period.started_monotonic == 0.0
        assert period.duration_s == pytest.approx(3.0)

    def test_the_uncovered_media_time_is_measured_in_canonical_samples(self) -> None:
        """The number a fill policy would need, in the unit the timeline uses."""
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 2.0, 3.0]))
        tracker.start()
        tracker.poll()
        tracker.note_audio(device_frames=480)

        assert tracker.total_idle_samples == 3 * CANONICAL_SAMPLE_RATE_HZ

    def test_audio_arriving_without_an_idle_period_reports_nothing(self) -> None:
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 0.1]))
        tracker.start()

        assert tracker.note_audio(device_frames=480) is None

    def test_an_empty_chunk_does_not_end_an_idle_period(self) -> None:
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 2.0, 2.1]))
        tracker.start()
        tracker.poll()

        assert tracker.note_audio(device_frames=0) is None
        assert tracker.is_idle

    def test_finish_closes_an_open_period(self) -> None:
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 2.0, 4.0]))
        tracker.start()
        tracker.poll()

        period = tracker.finish()

        assert period is not None
        assert tracker.total_idle_samples == 4 * CANONICAL_SAMPLE_RATE_HZ
        assert not tracker.is_idle

    def test_finish_on_a_producing_endpoint_reports_nothing(self) -> None:
        tracker = IdleTracker(idle_threshold_s=0.5, clock=self._clock([0.0, 0.1]))
        tracker.start()

        assert tracker.finish() is None

    def test_polling_before_start_is_harmless(self) -> None:
        assert not IdleTracker().poll()
