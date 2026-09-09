"""Measure the resampler and the frame duration against real captured audio.

Two numbers were left `benchmark_required` by ADR-0014 and this measures both:
the libsamplerate converter quality (D35) and the wire frame duration (D36).

**What this does and does not claim.** It reports how much each converter
deviates from `sinc_best` and what each costs. It does not claim any converter
is correct, and it says nothing about transcription quality (TEST-130) - that
would need human references, which do not exist (TEST-070). `sinc_best` is used
as the comparison point because it is the most expensive setting available, not
because it is ground truth.

**Real audio only.** The recording this project owns is already 16 kHz mono - the
conversion target - so it cannot serve as a source for the 48 kHz path the real
devices produce. The only real source is live capture, so this tool captures
once and then replays that same buffer through every configuration. Nothing is
synthesised and nothing is written to disk (Section 25.14 keeps raw audio
persistence default-off).

Section 25.15 requires benchmarks to separate cold start from warm running and to
report distribution rather than an average, so the first chunks are discarded as
warm-up and every figure is reported as median, P95 and maximum.

Usage on the user machine, with the meeting recording playing::

    python tools/converter_benchmark.py --seconds 20
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from client.capture import LoopbackCapture  # noqa: E402
from client.devices import LoopbackDevice, select_device  # noqa: E402
from client.framing import FrameBuilder, samples_per_frame  # noqa: E402
from client.resampler import (  # noqa: E402
    CONVERTER_BEST,
    CONVERTER_FASTEST,
    CONVERTER_MEDIUM,
    AudioConverter,
    pcm16_to_float32,
)
from client.ringbuffer import CaptureRing  # noqa: E402
from protocol.limits import CANONICAL_SAMPLE_RATE_HZ  # noqa: E402

DEFAULT_SECONDS = 20.0
DEFAULT_CHUNK_MS = 20
#: Chunks discarded before timing starts. Section 25.15 requires cold start to be
#: separated from warm running; the first calls pay for allocation and for the
#: converter filling its filter history.
WARMUP_CHUNKS = 25
CANDIDATE_CONVERTERS = (CONVERTER_FASTEST, CONVERTER_MEDIUM, CONVERTER_BEST)
CANDIDATE_FRAME_MS = (20, 40)
POLL_INTERVAL_S = 0.05


class BenchmarkError(RuntimeError):
    """The benchmark could not produce a result worth reporting."""


@dataclass(frozen=True, slots=True)
class Distribution:
    """Section 25.15 asks for median, P95 and maximum, not an average."""

    samples: tuple[float, ...]

    @property
    def median_ms(self) -> float:
        return statistics.median(self.samples) * 1000

    @property
    def p95_ms(self) -> float:
        ordered = sorted(self.samples)
        index = min(len(ordered) - 1, int(len(ordered) * 0.95))
        return ordered[index] * 1000

    @property
    def max_ms(self) -> float:
        return max(self.samples) * 1000

    def render(self) -> str:
        return (
            f"median {self.median_ms:7.3f} ms   "
            f"P95 {self.p95_ms:7.3f} ms   "
            f"max {self.max_ms:7.3f} ms"
        )


@dataclass(frozen=True, slots=True)
class ConverterResult:
    converter_type: str
    cold_start_ms: float
    per_chunk: Distribution
    output_samples: int
    #: Realtime factor: converter cost divided by the media duration it covered.
    #: 0.01 means one hundredth of real time.
    realtime_factor: float
    #: Signal-to-noise against `sinc_best`, in dB. None for `sinc_best` itself.
    snr_vs_best_db: float | None


@dataclass(frozen=True, slots=True)
class FrameResult:
    frame_ms: int
    frames: int
    frames_per_second: float
    per_frame: Distribution
    header_overhead_fraction: float


def capture_real_audio(seconds: float) -> tuple[bytes, LoopbackDevice]:
    """Capture once. Every configuration is then measured on the same bytes.

    Replaying one buffer rather than capturing per configuration is what makes
    the comparison fair: two captures of a live meeting are two different pieces
    of audio, and the difference between converters would be buried under the
    difference between recordings.
    """
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        selection = select_device(audio)
        device = selection.device
        ring = CaptureRing(
            capacity_seconds=seconds + 5,
            sample_rate_hz=device.sample_rate_hz,
            channels=device.channels,
        )
        capture = LoopbackCapture(audio=audio, device=device, ring=ring)

        collected = bytearray()
        started = time.monotonic()
        with capture:
            while time.monotonic() - started < seconds:
                time.sleep(POLL_INTERVAL_S)
                collected.extend(ring.pop_all())
            collected.extend(ring.pop_all())

    if not collected:
        raise BenchmarkError(
            "the endpoint delivered nothing. A WASAPI loopback endpoint with "
            "nothing playing delivers no callbacks at all - start the meeting "
            "recording and run again"
        )

    audio_bytes = bytes(collected)
    samples = pcm16_to_float32(audio_bytes)
    if float(np.abs(samples).max()) <= 1e-4:
        raise BenchmarkError(
            "the captured audio is below the noise floor. Measuring a converter "
            "on silence says nothing about how it handles speech"
        )

    return audio_bytes, device


def chunk_bytes(payload: bytes, device: LoopbackDevice, chunk_ms: int) -> list[bytes]:
    frames_per_chunk = device.sample_rate_hz * chunk_ms // 1000
    size = frames_per_chunk * device.channels * 2
    return [payload[i : i + size] for i in range(0, len(payload) - size + 1, size)]


def run_converter(
    converter_type: str, chunks: list[bytes], device: LoopbackDevice
) -> tuple[ConverterResult, np.ndarray]:
    converter = AudioConverter(
        source_rate_hz=device.sample_rate_hz,
        source_channels=device.channels,
        converter_type=converter_type,
    )

    output = bytearray()
    timings: list[float] = []

    cold_started = time.perf_counter()
    output.extend(converter.convert(chunks[0]))
    cold_start_ms = (time.perf_counter() - cold_started) * 1000

    for index, chunk in enumerate(chunks[1:], start=1):
        started = time.perf_counter()
        output.extend(converter.convert(chunk))
        elapsed = time.perf_counter() - started
        if index >= WARMUP_CHUNKS:
            timings.append(elapsed)

    output.extend(converter.flush())

    if not timings:
        raise BenchmarkError(
            f"only {len(chunks)} chunks captured; more than {WARMUP_CHUNKS} are "
            "needed before timing begins. Capture for longer"
        )

    samples = pcm16_to_float32(bytes(output))
    media_seconds = samples.size / CANONICAL_SAMPLE_RATE_HZ
    total_cost = sum(timings) * len(chunks) / max(1, len(timings))

    return (
        ConverterResult(
            converter_type=converter_type,
            cold_start_ms=cold_start_ms,
            per_chunk=Distribution(tuple(timings)),
            output_samples=samples.size,
            realtime_factor=total_cost / media_seconds if media_seconds else 0.0,
            snr_vs_best_db=None,
        ),
        samples,
    )


def snr_db(candidate: np.ndarray, reference: np.ndarray) -> float:
    """Signal-to-noise of `candidate` against `reference`, in dB.

    Not a quality verdict. It answers one question: how far does this converter's
    output sit from the most expensive setting's output? A high figure means the
    cheaper setting is nearly indistinguishable from the dearer one on this
    audio.
    """
    length = min(candidate.size, reference.size)
    if length == 0:
        return float("nan")
    difference = candidate[:length] - reference[:length]
    noise = float(np.sum(difference**2))
    signal = float(np.sum(reference[:length] ** 2))
    if noise == 0:
        return float("inf")
    if signal == 0:
        return float("nan")
    return 10 * float(np.log10(signal / noise))


def run_framing(canonical: bytes, frame_ms: int) -> FrameResult:
    from protocol.frame import HEADER_SIZE_BYTES

    builder = FrameBuilder(stream_ordinal=0, frame_ms=frame_ms)
    per_frame_samples = samples_per_frame(frame_ms)
    step = per_frame_samples * 2

    timings: list[float] = []
    frames = 0
    for index in range(0, len(canonical) - step + 1, step):
        started = time.perf_counter()
        produced = builder.push(canonical[index : index + step])
        elapsed = time.perf_counter() - started
        frames += len(produced)
        if index // step >= WARMUP_CHUNKS:
            timings.append(elapsed)

    if not timings:
        raise BenchmarkError("not enough canonical audio to measure framing")

    payload_bytes = per_frame_samples * 2
    return FrameResult(
        frame_ms=frame_ms,
        frames=frames,
        frames_per_second=1000 / frame_ms,
        per_frame=Distribution(tuple(timings)),
        header_overhead_fraction=HEADER_SIZE_BYTES / (payload_bytes + HEADER_SIZE_BYTES),
    )


def render(
    device: LoopbackDevice,
    seconds: float,
    converters: list[ConverterResult],
    frames: list[FrameResult],
) -> str:
    lines = [
        "converter and frame-duration benchmark",
        "======================================",
        f"device        {device.describe()}",
        f"captured      {seconds:.1f} s of real audio, replayed through every configuration",
        f"warm-up       first {WARMUP_CHUNKS} chunks discarded (Section 25.15)",
        "",
        "converter (ADR-0014 D35)",
        "------------------------",
    ]
    for result in converters:
        snr = (
            "reference"
            if result.snr_vs_best_db is None
            else f"{result.snr_vs_best_db:6.1f} dB vs sinc_best"
        )
        lines += [
            f"  {result.converter_type}",
            f"    per chunk    {result.per_chunk.render()}",
            f"    cold start   {result.cold_start_ms:.3f} ms",
            f"    realtime     {result.realtime_factor * 100:.3f}% of real time",
            f"    deviation    {snr}",
            f"    output       {result.output_samples:,} samples",
        ]

    lines += [
        "",
        "frame duration (ADR-0014 D36)",
        "-----------------------------",
    ]
    for frame in frames:
        lines += [
            f"  {frame.frame_ms} ms",
            f"    per frame    {frame.per_frame.render()}",
            f"    rate         {frame.frames_per_second:.0f} frames/s",
            f"    header       {frame.header_overhead_fraction * 100:.2f}% of frame bytes",
            f"    frames       {frame.frames:,}",
        ]

    lines += [
        "",
        "This measures cost and deviation. It makes no claim about transcription",
        "quality, which needs human references that do not exist yet (TEST-070).",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--chunk-ms", type=int, default=DEFAULT_CHUNK_MS)
    args = parser.parse_args(argv)

    try:
        payload, device = capture_real_audio(args.seconds)
        chunks = chunk_bytes(payload, device, args.chunk_ms)

        results: list[ConverterResult] = []
        outputs: dict[str, np.ndarray] = {}
        for converter_type in CANDIDATE_CONVERTERS:
            result, samples = run_converter(converter_type, chunks, device)
            results.append(result)
            outputs[converter_type] = samples

        reference = outputs[CONVERTER_BEST]
        results = [
            result
            if result.converter_type == CONVERTER_BEST
            else ConverterResult(
                converter_type=result.converter_type,
                cold_start_ms=result.cold_start_ms,
                per_chunk=result.per_chunk,
                output_samples=result.output_samples,
                realtime_factor=result.realtime_factor,
                snr_vs_best_db=snr_db(outputs[result.converter_type], reference),
            )
            for result in results
        ]

        canonical = (np.clip(reference * 32768.0, -32768.0, 32767)).astype("<i2").tobytes()
        frame_results = [run_framing(canonical, frame_ms) for frame_ms in CANDIDATE_FRAME_MS]

    except ImportError:
        print("PyAudioWPatch is not installed, or this is not Windows.", file=sys.stderr)
        return 2
    except BenchmarkError as exc:
        print(f"benchmark not run: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"benchmark failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(render(device, args.seconds, results, frame_results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
