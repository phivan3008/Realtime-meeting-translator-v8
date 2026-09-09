"""Capture a few seconds of loopback audio and report what the pipeline did.

A diagnostic, not a test. It answers the questions that only a real device can
answer on a real machine: does the default loopback endpoint resolve, does the
callback fire steadily, does conversion produce the expected number of samples,
and is the frame sequence continuous?

**It makes no claim about audio quality** (TEST-130). The level figures it prints
exist for one purpose: to tell the operator whether audio was actually playing
during the run.

**Something must be playing.** A WASAPI loopback endpoint with nothing going
through it delivers no callbacks at all - it does not deliver silence. Verified
on the dev machine on 2026-09-08: three seconds on an idle endpoint produced zero
callbacks while the stream reported itself active, and starting playback produced
106 callbacks and 434,176 bytes in 2.26 s. A run with zero callbacks therefore
proves only that the device opened, and this tool exits non-zero to say so.

Usage on the user machine, with something playing::

    python tools/capture_selfcheck.py --seconds 5
    python tools/capture_selfcheck.py --list
    python tools/capture_selfcheck.py --device "Speakers (Realtek(R) Audio) [Loopback]"
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import numpy as np  # noqa: E402

from client.capture import LoopbackCapture  # noqa: E402
from client.devices import (  # noqa: E402
    LoopbackDevice,
    enumerate_loopback_devices,
    select_device,
)
from client.framing import DEFAULT_FRAME_MS, FrameBuilder  # noqa: E402
from client.idle import IdleTracker  # noqa: E402
from client.resampler import AudioConverter  # noqa: E402
from client.ringbuffer import CaptureRing  # noqa: E402
from protocol.limits import CANONICAL_SAMPLE_RATE_HZ  # noqa: E402

DEFAULT_SECONDS = 5.0
DEFAULT_RING_SECONDS = 30.0
POLL_INTERVAL_S = 0.05


@dataclass(slots=True)
class Report:
    device: LoopbackDevice
    requested_seconds: float
    elapsed_seconds: float
    callbacks: int
    device_frames: int
    canonical_samples: int
    frames_emitted: int
    samples_skipped: int
    input_overflows: int
    ring_dropped_device_frames: int
    ring_peak_bytes: int
    resampler_failures: int
    clipped_samples: int
    peak_amplitude: float
    rms_amplitude: float
    sequence_contiguous: bool
    offsets_contiguous: bool
    idle_periods: int
    idle_samples: int

    @property
    def expected_canonical_samples(self) -> int:
        return int(self.device_frames * CANONICAL_SAMPLE_RATE_HZ / self.device.sample_rate_hz)

    @property
    def canonical_shortfall(self) -> int:
        """Samples the conversion did not produce, after flushing.

        A few samples are normal at a chunk boundary. A growing figure would mean
        audio is being lost in conversion, which is why it is reported rather
        than absorbed.
        """
        return self.expected_canonical_samples - self.canonical_samples

    @property
    def captured_anything(self) -> bool:
        """Whether the endpoint delivered any audio at all.

        Discovered on real hardware: an idle WASAPI loopback endpoint delivers
        **no callbacks**, not silence. A run with zero callbacks exercises the
        setup path and nothing else, so it must not be reported as a pass.
        """
        return self.callbacks > 0

    @property
    def audio_was_flowing(self) -> bool:
        """Whether the captured audio contains anything above the noise floor.

        A level floor, not a quality judgement.
        """
        return self.peak_amplitude > 1e-4


def render(report: Report) -> str:
    lines = [
        "capture self-check",
        "==================",
        f"device                    {report.device.describe()}",
        f"requested                 {report.requested_seconds:.1f} s",
        f"elapsed                   {report.elapsed_seconds:.2f} s",
        "",
        "callback",
        f"  callbacks               {report.callbacks}",
        f"  device frames           {report.device_frames:,} "
        f"({report.device_frames / report.device.sample_rate_hz:.2f} s)",
        f"  PortAudio overflows     {report.input_overflows}",
        "",
        "ring buffer",
        f"  peak occupancy          {report.ring_peak_bytes:,} bytes",
        f"  device frames dropped   {report.ring_dropped_device_frames:,}",
        "",
        "conversion",
        f"  canonical samples       {report.canonical_samples:,} "
        f"({report.canonical_samples / CANONICAL_SAMPLE_RATE_HZ:.2f} s)",
        f"  expected                {report.expected_canonical_samples:,}",
        f"  shortfall               {report.canonical_shortfall:,} samples",
        f"  resampler failures      {report.resampler_failures}",
        f"  clipped samples         {report.clipped_samples:,}",
        "",
        "framing",
        f"  frames emitted          {report.frames_emitted:,}",
        f"  samples skipped         {report.samples_skipped:,}",
        f"  sequence contiguous     {report.sequence_contiguous}",
        f"  sample offsets exact    {report.offsets_contiguous}",
        "",
        "idle endpoint",
        f"  idle periods            {report.idle_periods}",
        f"  media time uncovered    {report.idle_samples:,} samples "
        f"({report.idle_samples / CANONICAL_SAMPLE_RATE_HZ:.2f} s)",
        "",
        "level (diagnostic only, never a quality claim)",
        f"  peak                    {report.peak_amplitude:.6f}",
        f"  rms                     {report.rms_amplitude:.6f}",
    ]

    if not report.captured_anything:
        lines += [
            "",
            "NOTHING WAS CAPTURED.",
            "",
            "A WASAPI loopback endpoint with nothing playing through it delivers no",
            "callbacks at all - it does not deliver silence. Zero callbacks therefore",
            "proves only that the device opened. The capture, conversion and framing",
            "paths did not run, so this result establishes nothing about them.",
            "",
            "Play audio through this endpoint and run again.",
        ]
    elif not report.audio_was_flowing:
        lines += [
            "",
            "NOTE: audio was delivered but is below the noise floor. The pipeline ran",
            "      end to end; whatever played was effectively silent.",
        ]

    return "\n".join(lines)


def run(
    *,
    seconds: float,
    preferred_device_name: str | None,
    frame_ms: int,
    ring_seconds: float,
) -> Report:
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        selection = select_device(audio, preferred_name=preferred_device_name)
        device = selection.device
        if selection.fallback_message:
            print(f"warning: {selection.fallback_message}", file=sys.stderr)

        ring = CaptureRing(
            capacity_seconds=ring_seconds,
            sample_rate_hz=device.sample_rate_hz,
            channels=device.channels,
        )
        converter = AudioConverter(
            source_rate_hz=device.sample_rate_hz, source_channels=device.channels
        )
        builder = FrameBuilder(stream_ordinal=0, frame_ms=frame_ms)
        capture = LoopbackCapture(audio=audio, device=device, ring=ring)
        idle = IdleTracker()

        sequences: list[int] = []
        offsets: list[tuple[int, int]] = []
        canonical = bytearray()

        started = time.monotonic()
        with capture:
            idle.start()
            while time.monotonic() - started < seconds:
                time.sleep(POLL_INTERVAL_S)
                chunk = ring.pop_all()
                if not chunk:
                    idle.poll()
                    continue
                idle.note_audio(len(chunk) // ring.bytes_per_frame)
                converted = converter.convert(chunk)
                canonical.extend(converted)
                for frame in builder.push(converted):
                    sequences.append(frame.header.sequence)
                    offsets.append((frame.start_sample, frame.header.sample_count))

            # Drain whatever the device produced after the loop condition failed.
            chunk = ring.pop_all()
            if chunk:
                converted = converter.convert(chunk)
                canonical.extend(converted)
                for frame in builder.push(converted):
                    sequences.append(frame.header.sequence)
                    offsets.append((frame.start_sample, frame.header.sample_count))

            # Drain the converter's filter state. Without this the report is
            # short by the converter's latency - about 46 samples at sinc_medium
            # on a 48 kHz source, which looks like a discrepancy and is not one.
            tail = converter.flush()
            if tail:
                canonical.extend(tail)
                for frame in builder.push(tail):
                    sequences.append(frame.header.sequence)
                    offsets.append((frame.start_sample, frame.header.sample_count))

            idle.finish()

        elapsed = time.monotonic() - started

    samples = np.frombuffer(bytes(canonical), dtype="<i2").astype(np.float32) / 32768.0
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0

    sequence_contiguous = sequences == list(range(len(sequences)))
    offsets_contiguous = all(
        offsets[i][0] + offsets[i][1] == offsets[i + 1][0] for i in range(len(offsets) - 1)
    )

    return Report(
        device=device,
        requested_seconds=seconds,
        elapsed_seconds=elapsed,
        callbacks=capture.stats.callbacks,
        device_frames=capture.stats.device_frames,
        canonical_samples=len(canonical) // 2,
        frames_emitted=builder.stats.frames_emitted,
        samples_skipped=builder.stats.samples_skipped,
        input_overflows=capture.stats.input_overflows,
        ring_dropped_device_frames=ring.stats().device_frames_dropped,
        ring_peak_bytes=ring.stats().peak_bytes,
        resampler_failures=converter.stats.failures,
        clipped_samples=converter.stats.clipped_samples,
        peak_amplitude=peak,
        rms_amplitude=rms,
        sequence_contiguous=sequence_contiguous,
        offsets_contiguous=offsets_contiguous,
        idle_periods=len(idle.periods),
        idle_samples=idle.total_idle_samples,
    )


def list_devices() -> int:
    import pyaudiowpatch as pyaudio

    with pyaudio.PyAudio() as audio:
        devices = enumerate_loopback_devices(audio)

    if not devices:
        print("no WASAPI loopback devices found", file=sys.stderr)
        return 1
    for device in devices:
        print(device.describe())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=DEFAULT_SECONDS)
    parser.add_argument("--device", default=None, help="loopback device name; default is automatic")
    parser.add_argument("--frame-ms", type=int, default=DEFAULT_FRAME_MS)
    parser.add_argument("--ring-seconds", type=float, default=DEFAULT_RING_SECONDS)
    parser.add_argument("--list", action="store_true", help="list loopback devices and exit")
    args = parser.parse_args(argv)

    if args.list:
        return list_devices()

    try:
        report = run(
            seconds=args.seconds,
            preferred_device_name=args.device,
            frame_ms=args.frame_ms,
            ring_seconds=args.ring_seconds,
        )
    except ImportError:
        print(
            "PyAudioWPatch is not installed, or this is not Windows. The client requires both.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(f"capture failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print(render(report))

    problems = [
        name
        for name, ok in (
            ("nothing was captured", report.captured_anything),
            ("sequence not contiguous", report.sequence_contiguous),
            ("sample offsets not contiguous", report.offsets_contiguous),
            ("resampler failed", report.resampler_failures == 0),
            ("ring dropped audio", report.ring_dropped_device_frames == 0),
        )
        if not ok
    ]
    if problems:
        print("\nFAILED: " + "; ".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
