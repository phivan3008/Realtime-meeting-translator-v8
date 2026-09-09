"""WASAPI loopback device discovery and selection.

ADR-0014 D38. `requirements.md` Section 8.1 requires enumerating loopback-capable
devices and letting the user select one (AUD-010, AUD-020), validating format,
channel count, sample rate and availability before capture (AUD-040). The user
added AUD-220 on 2026-09-08: the client must pick a correct loopback device
automatically on an arbitrary Windows machine.

The platform answers AUD-220 directly. PyAudioWPatch exposes
``get_default_wasapi_loopback()``, which returns the loopback counterpart of the
system's default speakers, so no heuristic is needed.

**Selection is remembered by name, never by index.** Device indices shift across
reboots and whenever anything is plugged in. Writing an index into configuration
is how an application ends up recording from the wrong endpoint with nobody
noticing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

#: WASAPI loopback endpoints are exposed as virtual *input* devices, at the
#: endpoint's shared-mode mix format - commonly 48000 Hz stereo. The client
#: therefore always downmixes and always resamples (ADR-0014).
MIN_CHANNELS = 1
MAX_SUPPORTED_CHANNELS = 8
MIN_SAMPLE_RATE_HZ = 8_000
MAX_SAMPLE_RATE_HZ = 192_000


class DeviceUnavailableError(RuntimeError):
    """No usable loopback device, or the requested one cannot be opened."""


class UnsupportedDeviceFormatError(RuntimeError):
    """The device reports a format the conversion pipeline cannot handle.

    Raised rather than opening the device and coping: AUD-040 requires
    validation *before* capture, and a device the client cannot convert from is
    refused loudly instead of mishandled quietly.
    """


@dataclass(frozen=True, slots=True)
class LoopbackDevice:
    """One WASAPI loopback endpoint, as reported by the host API."""

    index: int
    name: str
    sample_rate_hz: int
    channels: int
    is_system_default: bool = False

    @classmethod
    def from_info(cls, info: dict[str, Any], *, is_system_default: bool = False) -> LoopbackDevice:
        return cls(
            index=int(info["index"]),
            name=str(info["name"]),
            # The host API reports the rate as a float. Rounding to int keeps
            # every downstream sample-count calculation in exact integers.
            sample_rate_hz=round(float(info["defaultSampleRate"])),
            channels=int(info["maxInputChannels"]),
            is_system_default=is_system_default,
        )

    def describe(self) -> str:
        return (
            f"[{self.index}] {self.name} "
            f"({self.sample_rate_hz} Hz, {self.channels} ch"
            f"{', system default' if self.is_system_default else ''})"
        )


class PyAudioLike(Protocol):
    """The slice of the PyAudioWPatch API this module uses.

    Declared as a Protocol so the selection logic can be tested without an audio
    device. The tests exercise the *rules* - name resolution, fallback,
    validation - which is where the behaviour worth protecting lives; opening a
    real stream is tested separately against a real device.
    """

    def get_default_wasapi_loopback(self) -> dict[str, Any]: ...

    def get_loopback_device_info_generator(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class DeviceSelection:
    """The outcome of resolving a configured preference against reality."""

    device: LoopbackDevice
    #: True when the configured device was not found and the default was used
    #: instead. AUD-090 and ADR-0014 D38: this is surfaced in the UI, never
    #: applied silently.
    fell_back: bool = False
    requested_name: str | None = None

    @property
    def fallback_message(self) -> str | None:
        if not self.fell_back:
            return None
        return (
            f"Configured capture device {self.requested_name!r} was not found. "
            f"Using {self.device.name!r} instead."
        )


def validate_device(device: LoopbackDevice) -> None:
    """Check a device against what the conversion pipeline can handle.

    AUD-040. Raises:
        UnsupportedDeviceFormatError: if the reported format is out of range.
    """
    if not MIN_CHANNELS <= device.channels <= MAX_SUPPORTED_CHANNELS:
        raise UnsupportedDeviceFormatError(
            f"{device.name!r} reports {device.channels} channels; "
            f"supported range is {MIN_CHANNELS}-{MAX_SUPPORTED_CHANNELS}"
        )
    if not MIN_SAMPLE_RATE_HZ <= device.sample_rate_hz <= MAX_SAMPLE_RATE_HZ:
        raise UnsupportedDeviceFormatError(
            f"{device.name!r} reports {device.sample_rate_hz} Hz; "
            f"supported range is {MIN_SAMPLE_RATE_HZ}-{MAX_SAMPLE_RATE_HZ}"
        )


def enumerate_loopback_devices(audio: PyAudioLike) -> list[LoopbackDevice]:
    """Every loopback endpoint, with the system default marked (AUD-010)."""
    try:
        default_info = audio.get_default_wasapi_loopback()
        default_index = int(default_info["index"])
    except Exception:
        # No default speakers, or WASAPI is unavailable. Enumeration can still
        # succeed and is more useful than nothing, so this is not fatal here.
        default_index = -1

    devices = [
        LoopbackDevice.from_info(info, is_system_default=int(info["index"]) == default_index)
        for info in audio.get_loopback_device_info_generator()
    ]
    return sorted(devices, key=lambda d: d.index)


def default_loopback_device(audio: PyAudioLike) -> LoopbackDevice:
    """The loopback counterpart of the system's default speakers (AUD-220).

    Raises:
        DeviceUnavailableError: when the platform reports no default loopback.
    """
    try:
        info = audio.get_default_wasapi_loopback()
    except Exception as exc:
        raise DeviceUnavailableError(
            "no default WASAPI loopback device; is any playback endpoint enabled?"
        ) from exc
    return LoopbackDevice.from_info(info, is_system_default=True)


def select_device(audio: PyAudioLike, *, preferred_name: str | None = None) -> DeviceSelection:
    """Resolve a configured preference against the devices that actually exist.

    ADR-0014 D38, in three steps:

    1. No preference: use the system default loopback.
    2. Preference that still exists: use it.
    3. Preference that has vanished: fall back to the default **and say so**.
       Never silently record from a different endpoint than the one chosen.

    Raises:
        DeviceUnavailableError: when there is nothing usable at all.
        UnsupportedDeviceFormatError: when the chosen device's format is out of
            range.
    """
    if preferred_name is not None:
        for device in enumerate_loopback_devices(audio):
            if device.name == preferred_name:
                validate_device(device)
                return DeviceSelection(device=device, requested_name=preferred_name)

    device = default_loopback_device(audio)
    validate_device(device)
    return DeviceSelection(
        device=device,
        fell_back=preferred_name is not None,
        requested_name=preferred_name,
    )
