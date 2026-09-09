"""Replaying real captured WebSocket traffic.

Category C of `requirements.md` Section 25.15. A replay fixture is **not a
mock**: Section 22.2 says so explicitly, provided it reproduces an immutable
captured server exchange and records its provenance. That proviso is the whole
contract, and this module enforces it rather than trusting it.

TEST-140 requires every replay fixture to carry capture provenance, protocol
version, configuration hash and SHA-256. A capture whose bytes no longer match
its recorded hash is not a citation to a real server run - it is a file that once
was one, which is a different thing and a worse one.

**No capture exists yet, and none can.** A real capture requires a real server
run, which is Phase 4. This module is the harness; the fixtures it consumes
arrive later. Every category C requirement therefore stays at
``blocked_real_fixture``, reported rather than filled with an invented exchange
(TEST-210, Section 22.3).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from protocol.events import EVENT_MODELS, Envelope
from protocol.projection import ProjectionResult, SessionProjection
from protocol.version import PROTOCOL_VERSION

READ_CHUNK_BYTES = 1024 * 1024

#: Direction as recorded at capture time. A capture is taken from the client's
#: vantage point, so `inbound` is what the server sent.
DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"


class CaptureError(RuntimeError):
    """A capture that cannot be trusted as evidence of a real server run."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(READ_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class CaptureManifest:
    """Provenance for one captured exchange (TEST-140).

    Committed alongside the tests; the capture file itself lives under ``data/``
    and is never committed (PERS-120).
    """

    capture_id: str
    location: str
    sha256: str
    protocol_version: str
    config_hash: str
    captured_at: str
    captured_from: str
    session_id: str
    event_count: int
    note: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CaptureManifest:
        missing = [
            field_name
            for field_name in (
                "capture_id",
                "location",
                "sha256",
                "protocol_version",
                "config_hash",
                "captured_at",
                "captured_from",
                "session_id",
                "event_count",
            )
            if field_name not in data
        ]
        if missing:
            raise CaptureError(
                f"capture manifest is missing {missing}. TEST-140 requires capture "
                "provenance, protocol version, configuration hash and SHA-256 - a "
                "capture without them cannot be cited as a real server exchange"
            )
        return cls(
            capture_id=str(data["capture_id"]),
            location=str(data["location"]),
            sha256=str(data["sha256"]).lower(),
            protocol_version=str(data["protocol_version"]),
            config_hash=str(data["config_hash"]),
            captured_at=str(data["captured_at"]),
            captured_from=str(data["captured_from"]),
            session_id=str(data["session_id"]),
            event_count=int(data["event_count"]),
            note=str(data.get("note", "")),
        )


@dataclass(frozen=True, slots=True)
class CapturedEvent:
    """One event as it crossed the wire, with the direction it crossed in."""

    ordinal: int
    direction: str
    payload: dict[str, Any]

    @property
    def event_type(self) -> str:
        return str(self.payload.get("event_type", ""))


@dataclass(slots=True)
class Capture:
    """An immutable captured exchange, verified against its manifest."""

    manifest: CaptureManifest
    events: list[CapturedEvent] = field(default_factory=list)
    unknown_event_types: list[str] = field(default_factory=list)
    invalid_payloads: list[tuple[int, str]] = field(default_factory=list)

    @property
    def inbound(self) -> list[CapturedEvent]:
        """What the server sent. These are what a client projection folds."""
        return [event for event in self.events if event.direction == DIRECTION_INBOUND]


def verify_capture_file(path: Path, manifest: CaptureManifest) -> None:
    """Confirm the bytes on disk are the ones the manifest describes.

    Raises:
        CaptureError: if the file is missing or its hash differs. A capture whose
            bytes have changed is no longer evidence of the run it claims to
            record, and using it anyway would make every result derived from it
            unfounded.
    """
    if not path.is_file():
        raise CaptureError(
            f"{path} is missing. Captures live under data/ and are never committed "
            "(PERS-120); it has to be placed on this machine before the replay runs"
        )

    actual = sha256_of(path)
    if actual != manifest.sha256:
        raise CaptureError(
            f"capture hash mismatch for {manifest.capture_id}\n"
            f"  manifest {manifest.sha256}\n"
            f"  on disk  {actual}\n"
            "A capture that has changed is not the exchange it claims to be."
        )


def check_protocol_version(manifest: CaptureManifest) -> str | None:
    """Whether this build can interpret the capture, and why not if it cannot.

    Returns None when compatible, or a description of the mismatch. A capture
    from a different **major** version cannot be folded, because the meaning of
    its fields may differ; a different minor is fine, because minor versions are
    additive only (ADR-0010 D23).
    """
    captured_major = manifest.protocol_version.split(".")[0]
    current_major = PROTOCOL_VERSION.split(".")[0]
    if captured_major != current_major:
        return (
            f"capture speaks protocol major {captured_major}, this build speaks "
            f"{current_major}. Field meanings may differ, so the capture cannot be "
            "folded by this build"
        )
    return None


def load_capture(manifest: CaptureManifest, *, root: Path) -> Capture:
    """Load and verify a capture.

    The file is JSON Lines, one record per line:

    ```json
    {"ordinal": 0, "direction": "inbound", "payload": {"event_type": "..."}}
    ```

    Unknown event types and invalid payloads are collected rather than raised.
    A capture recorded by a newer build may contain event types this one does not
    know, and Section 9.1's forward compatibility applies to a stored exchange as
    much as to a live peer.

    Raises:
        CaptureError: if the file is missing, its hash differs, its major version
            is incompatible, or its event count disagrees with the manifest.
    """
    path = (root / manifest.location).resolve()
    verify_capture_file(path, manifest)

    mismatch = check_protocol_version(manifest)
    if mismatch is not None:
        raise CaptureError(mismatch)

    capture = Capture(manifest=manifest)
    with path.open("r", encoding="utf-8", newline="") as handle:
        for ordinal, raw in enumerate(handle):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise CaptureError(
                    f"{manifest.capture_id} line {ordinal + 1} is not valid JSON: "
                    f"{exc.msg}. A capture is immutable evidence; a malformed line "
                    "means the file is damaged rather than merely surprising"
                ) from exc

            capture.events.append(
                CapturedEvent(
                    ordinal=int(record.get("ordinal", ordinal)),
                    direction=str(record.get("direction", DIRECTION_INBOUND)),
                    payload=record["payload"],
                )
            )

    if len(capture.events) != manifest.event_count:
        raise CaptureError(
            f"{manifest.capture_id} holds {len(capture.events)} events but its "
            f"manifest records {manifest.event_count}. The manifest and the file "
            "disagree, so neither can be trusted"
        )

    return capture


def parse_events(capture: Capture) -> list[Envelope]:
    """Turn captured payloads into typed events, recording what could not be.

    Mutates ``capture`` to record unknown types and invalid payloads, so a caller
    can report coverage honestly: "the replay folded 412 of 419 events, and here
    are the seven it did not" is a usable statement, while a silent 412 is not.
    """
    events: list[Envelope] = []
    for captured in capture.inbound:
        model = EVENT_MODELS.get(captured.event_type)
        if model is None:
            capture.unknown_event_types.append(captured.event_type)
            continue
        try:
            events.append(model.model_validate(captured.payload))
        except ValidationError as exc:
            capture.invalid_payloads.append(
                (captured.ordinal, f"{exc.error_count()} validation error(s)")
            )
    return events


@dataclass(slots=True)
class ReplayResult:
    """What folding a capture produced."""

    projection: SessionProjection
    results: list[ProjectionResult]
    capture: Capture

    @property
    def applied(self) -> int:
        return sum(1 for result in self.results if result.applied)

    @property
    def refused(self) -> int:
        return sum(1 for result in self.results if not result.applied)

    @property
    def conflicts(self) -> int:
        return len(self.projection.conflicts)

    def summary(self) -> str:
        return (
            f"{self.capture.manifest.capture_id}: "
            f"{len(self.results)} events, {self.applied} applied, "
            f"{self.refused} refused, {self.conflicts} integrity conflicts, "
            f"{len(self.projection.segments)} segments"
        )


def replay(capture: Capture) -> ReplayResult:
    """Fold a capture through the shared reducer.

    The same reducer the UI, the history projection and the recovery rebuild use
    (ADR-0008 D13). That is what makes a replay meaningful: it exercises the code
    that will run in production, not a parallel implementation written for tests.
    """
    events = parse_events(capture)
    projection = SessionProjection(session_id=capture.manifest.session_id)
    results = [projection.apply(event) for event in events]
    return ReplayResult(projection=projection, results=results, capture=capture)


def load_manifests(path: Path) -> list[CaptureManifest]:
    """Read the committed capture manifest file.

    Returns an empty list when the file does not exist or holds no captures.
    That is the current state and it is not an error: no real capture can exist
    until a real server runs (Phase 4).
    """
    if not path.is_file():
        return []

    import yaml

    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise CaptureError(f"{path.name} did not parse as a mapping")

    return [CaptureManifest.from_dict(entry) for entry in (loaded.get("captures") or [])]
