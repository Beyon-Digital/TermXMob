"""Computer Observation v2 (AG2-015/016).

Structured frame metadata layered over the existing capture/input backends:
frame identity (perceptual aHash where the host can decode JPEG, exact digest
otherwise), duplicate suppression for model context, configurable model-bound
image scaling, region capture, and control-ownership tracking.
"""
from __future__ import annotations

import hashlib
import io
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

try:  # Pillow is a host-optional dependency (declared for Windows only)
    from PIL import Image
except ImportError:  # pragma: no cover - exercised on hosts without Pillow
    Image = None

MODEL_IMAGE_MAX_PX = 1568
MODEL_IMAGE_QUALITY = 80
_AHASH_UNCHANGED_THRESHOLD = 10


def jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Read pixel dimensions straight from the JPEG SOF marker."""
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in {0xC0, 0xC1, 0xC2, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        if index + 4 > len(data):
            break
        index += 2 + max(2, int.from_bytes(data[index + 2 : index + 4], "big"))
    return None


def frame_identity(frame: bytes) -> tuple[str, str]:
    """(kind, digest) — aHash perceptual identity when decodable, else sha256."""
    if Image is not None:
        try:
            with Image.open(io.BytesIO(frame)) as image:
                pixels = list(image.convert("L").resize((8, 8)).getdata())
            average = sum(pixels) / max(1, len(pixels))
            bits = 0
            for pixel in pixels:
                bits = (bits << 1) | int(pixel >= average)
            return "ahash", f"{bits:016x}"
        except Exception:
            pass
    return "sha256", hashlib.sha256(frame).hexdigest()[:16]


def _same_frame(previous: tuple[str, str] | None, current: tuple[str, str]) -> bool:
    if previous is None:
        return False
    if previous[0] != current[0]:
        return False
    if current[0] == "ahash":
        return bin(int(previous[1], 16) ^ int(current[1], 16)).count("1") <= _AHASH_UNCHANGED_THRESHOLD
    return previous[1] == current[1]


def scale_for_model(
    frame: bytes,
    *,
    max_px: int | None = None,
    quality: int = MODEL_IMAGE_QUALITY,
) -> bytes:
    """Downscale the model-bound frame; full-resolution stays in artifacts.

    max_px comes from TERMX_MODEL_IMAGE_MAX_PX (default 1568). No-op without
    Pillow or when the frame already fits.
    """
    limit = max_px if max_px is not None else _model_image_max_px()
    if Image is None or limit <= 0:
        return frame
    try:
        with Image.open(io.BytesIO(frame)) as image:
            width, height = image.size
            if max(width, height) <= limit:
                return frame
            ratio = limit / max(width, height)
            resized = image.resize((max(1, int(width * ratio)), max(1, int(height * ratio))))
            buffer = io.BytesIO()
            resized.convert("RGB").save(buffer, format="JPEG", quality=quality)
            return buffer.getvalue()
    except Exception:
        return frame


def crop_region(frame: bytes, region: dict[str, Any]) -> bytes | None:
    """Crop {x,y,width,height} (display pixel space) out of a full frame.

    Returns None when the host cannot decode JPEG (Pillow absent) — the caller
    then serves the full frame with region_cropped=False on the observation.
    """
    if Image is None:
        return None
    try:
        with Image.open(io.BytesIO(frame)) as image:
            width, height = image.size
            x = min(max(0, int(region.get("x") or 0)), width)
            y = min(max(0, int(region.get("y") or 0)), height)
            right = min(x + max(1, int(region.get("width") or width - x)), width)
            bottom = min(y + max(1, int(region.get("height") or height - y)), height)
            cropped = image.crop((x, y, right, bottom))
            buffer = io.BytesIO()
            cropped.convert("RGB").save(buffer, format="JPEG", quality=MODEL_IMAGE_QUALITY)
            return buffer.getvalue()
    except Exception:
        return None


def _model_image_max_px() -> int:
    try:
        return int(os.environ.get("TERMX_MODEL_IMAGE_MAX_PX") or MODEL_IMAGE_MAX_PX)
    except ValueError:
        return MODEL_IMAGE_MAX_PX


@dataclass
class Observation:
    id: str
    display_id: str | None
    width: int
    height: int
    dpr: float
    captured_at: float
    artifact_id: str | None
    frame_hash: str
    hash_kind: str
    changed: bool
    control_owner: str
    capture_backend: str
    region: dict[str, int] | None = None
    region_cropped: bool = False
    pointer: dict[str, float] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "display_id": self.display_id,
            "width": self.width,
            "height": self.height,
            "dpr": self.dpr,
            "captured_at": self.captured_at,
            "age_ms": int((time.time() - self.captured_at) * 1000),
            "artifact_id": self.artifact_id,
            "frame_hash": self.frame_hash,
            "hash_kind": self.hash_kind,
            "changed": self.changed,
            "control_owner": self.control_owner,
            "capture_backend": self.capture_backend,
        }
        if self.region is not None:
            data["region"] = self.region
            data["region_cropped"] = self.region_cropped
        if self.pointer is not None:
            data["pointer"] = self.pointer
        data.update(self.extra)
        return data


class ObservationTracker:
    """Per-stream dedup + shared control-ownership state.

    ``changed`` compares a frame only against the same observation stream —
    same scope (agent task), same display, same region — so a task's first
    capture always reports changed=True and ``previous_id`` never points into
    another task's stream. ``control_owner`` stays screen-global by design.
    """

    def __init__(self) -> None:
        self._last: Observation | None = None
        # (scope, display_id, region-key) -> (observation id, frame identity)
        self._streams: dict[tuple[str, str, str], tuple[str, tuple[str, str]]] = {}
        self.control_owner = "agent"

    @property
    def last(self) -> Observation | None:
        return self._last

    @staticmethod
    def _region_key(region: dict[str, int] | None) -> str:
        if region is None:
            return "full"
        return "{x},{y},{w},{h}".format(
            x=region.get("x", 0),
            y=region.get("y", 0),
            w=region.get("width", region.get("w", 0)),
            h=region.get("height", region.get("h", 0)),
        )

    def record(
        self,
        *,
        frame: bytes,
        display_id: str | None,
        width: int,
        height: int,
        dpr: float,
        backend: str,
        scope: str = "",
        region: dict[str, int] | None = None,
        region_cropped: bool = False,
        pointer: dict[str, float] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> Observation:
        kind, digest = frame_identity(frame)
        identity = (kind, digest)
        key = (scope, display_id or "", self._region_key(region))
        previous_id, last_identity = self._streams.get(key, (None, None))
        observation = Observation(
            id=uuid.uuid4().hex[:12],
            display_id=display_id,
            width=width,
            height=height,
            dpr=dpr,
            captured_at=time.time(),
            artifact_id=None,
            frame_hash=digest,
            hash_kind=kind,
            changed=not _same_frame(last_identity, identity),
            control_owner=self.control_owner,
            capture_backend=backend,
            region=region,
            region_cropped=region_cropped,
            pointer=pointer,
            extra=dict(extra or {}),
        )
        if previous_id is not None:
            observation.extra["previous_id"] = previous_id
        self._streams[key] = (observation.id, identity)
        self._last = observation
        return observation
