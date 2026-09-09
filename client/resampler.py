"""Downmix and resample device audio to the canonical wire format.

ADR-0014 D35. `requirements.md` Section 8.1 requires conversion to mono PCM
signed 16-bit little-endian at 16 kHz (AUD-050) using a **streaming-quality
resampler with persistent state across chunks** (AUD-060).

The persistent state is the whole point. A resampler restarted per chunk
produces a discontinuity at every boundary - fifty per second at a 20 ms frame -
and those discontinuities are broadband clicks that land directly in Whisper's
input. Section 14 spends nine defensive layers on hallucination; feeding the
model self-inflicted artefacts would undermine all of them.

Order: **downmix, then resample.** Half as many samples reach the filter, and
averaging channels before band-limiting is correct rather than merely cheaper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Protocol

import numpy as np
import samplerate

CANONICAL_SAMPLE_RATE_HZ: Final = 16_000

#: libsamplerate converter names, best first. The choice between them is
#: `benchmark_required` (ADR-0014) and is measured against real captured audio
#: at the Phase 2 gate, not decided here.
CONVERTER_BEST: Final = "sinc_best"
CONVERTER_MEDIUM: Final = "sinc_medium"
CONVERTER_FASTEST: Final = "sinc_fastest"

#: int16 full scale. Conversion divides by this on the way in and multiplies on
#: the way out, so a round trip through float32 is exact for every input value.
INT16_SCALE: Final = 32768.0
INT16_MAX: Final = 32767


class ResamplerFailure(RuntimeError):
    """The converter produced something unusable.

    AUD-090 lists resampler failure among the conditions the client must detect,
    so it is a named error rather than a silently empty buffer.
    """


class ResamplerBackend(Protocol):
    """The replaceable part.

    An interface sits in front of the backend for the same reason Section 4.3
    requires one for ``NoiseReducer`` and Section 16.2 for ``SourceSeparator``:
    so that changing the implementation touches one module rather than every
    call site. ADR-0014's rollback plan depends on this.
    """

    def process(
        self, block: np.ndarray, ratio: float, end_of_input: bool = False
    ) -> np.ndarray: ...

    def reset(self) -> None: ...


def downmix_to_mono(frames: np.ndarray, channels: int) -> np.ndarray:
    """Average interleaved channels into mono.

    Args:
        frames: interleaved float32 samples, length a multiple of ``channels``.
        channels: channel count of the source.

    Raises:
        ValueError: if the buffer is not a whole number of frames. A partial
            frame means the caller lost byte alignment, and averaging across a
            misaligned boundary would silently mix the wrong samples together.
    """
    if channels < 1:
        raise ValueError(f"channels must be positive, got {channels}")
    if channels == 1:
        return frames
    if frames.size % channels:
        raise ValueError(
            f"buffer of {frames.size} samples is not a whole number of {channels}-channel frames"
        )
    mono: np.ndarray = frames.reshape(-1, channels).mean(axis=1)
    return mono


def pcm16_to_float32(payload: bytes) -> np.ndarray:
    """Decode little-endian int16 PCM into float32 in [-1, 1)."""
    if len(payload) % 2:
        raise ValueError(f"{len(payload)} bytes is not a whole number of int16 samples")
    return np.frombuffer(payload, dtype="<i2").astype(np.float32) / INT16_SCALE


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """Encode float32 into little-endian int16 PCM, clipping to full scale.

    Clipping rather than wrapping: a sample driven past full scale by the
    resampler's ringing should saturate, which sounds like clipping and is
    reported as clipping by the quality features (VAD-020). Wrapping would turn
    a loud sample into a loud sample of the opposite sign, which is a click that
    no downstream check would attribute correctly.
    """
    scaled = np.clip(samples * INT16_SCALE, -INT16_SCALE, INT16_MAX)
    return scaled.astype("<i2").tobytes()


@dataclass(slots=True)
class ConversionStats:
    """Counters the client surfaces as diagnostics (AUD-210)."""

    input_frames: int = 0
    output_samples: int = 0
    clipped_samples: int = 0
    failures: int = 0


@dataclass(slots=True)
class AudioConverter:
    """Device audio in, canonical wire audio out, with state that persists.

    One instance per capture session. Feeding it successive chunks produces a
    continuous stream: the converter's filter history carries across calls, so
    there is no boundary artefact between chunks.
    """

    source_rate_hz: int
    source_channels: int
    converter_type: str = CONVERTER_MEDIUM
    target_rate_hz: int = CANONICAL_SAMPLE_RATE_HZ
    stats: ConversionStats = field(default_factory=ConversionStats)
    _backend: samplerate.Resampler | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.source_rate_hz <= 0:
            raise ValueError(f"source_rate_hz must be positive, got {self.source_rate_hz}")
        if self.source_channels < 1:
            raise ValueError(f"source_channels must be positive, got {self.source_channels}")
        self._backend = samplerate.Resampler(self.converter_type, channels=1)

    @property
    def ratio(self) -> float:
        return self.target_rate_hz / self.source_rate_hz

    @property
    def needs_resampling(self) -> bool:
        return self.source_rate_hz != self.target_rate_hz

    @property
    def needs_downmix(self) -> bool:
        return self.source_channels > 1

    def reset(self) -> None:
        """Discard filter state.

        Called at a stream discontinuity, never between chunks of a continuous
        stream - resetting mid-stream reintroduces exactly the boundary artefact
        the persistent state exists to avoid.
        """
        if self._backend is not None:
            self._backend.reset()

    def convert(self, payload: bytes, *, end_of_input: bool = False) -> bytes:
        """Convert one chunk of device audio to canonical wire bytes.

        Args:
            payload: interleaved int16 PCM at ``source_rate_hz``.
            end_of_input: flush the converter's remaining state. Set only on the
                final chunk of a stream.

        Returns:
            Mono ``pcm_s16le`` at 16 kHz. May be empty when the input was
            shorter than the converter needs to produce an output sample - that
            is normal, not a failure.

        Raises:
            ResamplerFailure: if the converter raises or returns something
                unusable (AUD-090).
        """
        if not payload:
            return b""

        interleaved = pcm16_to_float32(payload)
        mono = downmix_to_mono(interleaved, self.source_channels)
        self.stats.input_frames += int(mono.size)

        converted: np.ndarray
        if not self.needs_resampling:
            converted = mono
        else:
            assert self._backend is not None
            try:
                # Deliberately untyped: the backend ships no stubs, so its return
                # value is narrowed below rather than trusted.
                raw: object = self._backend.process(mono, self.ratio, end_of_input=end_of_input)
            except Exception as exc:
                self.stats.failures += 1
                raise ResamplerFailure(
                    f"resampling {self.source_rate_hz} Hz to {self.target_rate_hz} Hz failed: {exc}"
                ) from exc

            if not isinstance(raw, np.ndarray):
                self.stats.failures += 1
                raise ResamplerFailure(
                    f"resampler returned {type(raw).__name__}, expected an array"
                )
            converted = raw
            if not np.isfinite(converted).all():
                # AUD-090 and ASR-260: invalid numeric values after conversion
                # are an audio-validity failure, and letting a NaN through would
                # poison every quality feature computed downstream.
                self.stats.failures += 1
                raise ResamplerFailure("resampler produced non-finite samples")

        self.stats.clipped_samples += int(np.count_nonzero(np.abs(converted) >= 1.0))
        self.stats.output_samples += int(converted.size)
        return float32_to_pcm16(converted)

    def flush(self) -> bytes:
        """Drain whatever the converter still holds, at end of stream."""
        if not self.needs_resampling:
            return b""
        assert self._backend is not None
        try:
            raw: object = self._backend.process(
                np.zeros(0, dtype=np.float32), self.ratio, end_of_input=True
            )
        except Exception as exc:
            self.stats.failures += 1
            raise ResamplerFailure(f"flushing the resampler failed: {exc}") from exc
        if not isinstance(raw, np.ndarray):
            self.stats.failures += 1
            raise ResamplerFailure(f"resampler returned {type(raw).__name__}, expected an array")
        self.stats.output_samples += int(raw.size)
        return float32_to_pcm16(raw)
