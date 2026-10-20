"""Deterministic, data-only character packs shared by the daemon and native host.

The archive format deliberately has no scripts, URLs, or platform-specific code.
Never extract an imported archive: validation works on bounded bytes in memory.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files
from io import BytesIO
import json
import math
import re
import stat
import warnings
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from PIL import Image, ImageDraw, UnidentifiedImageError


PACK_VERSION = 1
REQUIRED_STATES = (
    "idle", "thinking", "working", "waiting", "success", "error", "paused", "sleeping",
)
REQUIRED_CLIPS = REQUIRED_STATES + ("walk", "hop", "fall", "sit", "drag", "wave")
MAX_PACK_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_ATLASES = 32
MAX_ATLAS_BYTES = 16 * 1024 * 1024
MAX_ATLAS_EDGE = 4096
MAX_DECODED_PIXELS = 16 * 1024 * 1024
MAX_LOGICAL_EDGE = 512
MAX_FRAMES_PER_CLIP = 256
MAX_TOTAL_FRAMES = 4096
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\.png\Z")
_MANIFEST_KEYS = {
    "version", "id", "name", "revision", "logical_size", "foot_anchor",
    "interaction_bounds", "atlases", "clips", "states",
}
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CharacterPackError(ValueError):
    """A pack is invalid, exceeds a resource limit, or cannot be decoded."""


def _object(value: object, keys: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise CharacterPackError(f"{label} must contain exactly: {', '.join(sorted(keys))}")
    return value


def _integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise CharacterPackError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def _number(value: object, minimum: float, maximum: float, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise CharacterPackError(f"{label} must be a finite number from {minimum} to {maximum}")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise CharacterPackError(f"{label} must be a lowercase identifier of at most 64 characters")
    return value


def _asset_name(value: object) -> str:
    if not isinstance(value, str) or not _ASSET_NAME.fullmatch(value) or ".." in value:
        raise CharacterPackError("Atlas files must be simple PNG filenames")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 80 or any(ord(c) < 32 for c in value):
        raise CharacterPackError(f"{label} must be nonempty text of at most 80 characters")
    return value.strip()


def _png(raw: bytes) -> Image.Image:
    if not isinstance(raw, bytes) or not raw.startswith(_PNG_SIGNATURE) or len(raw) > MAX_ATLAS_BYTES:
        raise CharacterPackError("Atlas must be a PNG of at most 16 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw)) as source:
                if source.format != "PNG" or getattr(source, "n_frames", 1) != 1:
                    raise CharacterPackError("Atlases must be static PNG images")
                width, height = source.size
                if not (1 <= width <= MAX_ATLAS_EDGE and 1 <= height <= MAX_ATLAS_EDGE):
                    raise CharacterPackError("Atlas dimensions exceed 4096 pixels")
                if width * height > MAX_DECODED_PIXELS:
                    raise CharacterPackError("Atlas exceeds decoded pixel limit")
                source.load()
                return source.convert("RGBA")
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise CharacterPackError("Could not decode PNG atlas") from exc


def _encode(image: Image.Image) -> bytes:
    target = BytesIO()
    image.save(target, format="PNG", optimize=False, compress_level=9)
    return target.getvalue()


def validate_pack(manifest: dict, assets: dict[str, bytes]) -> dict:
    """Validate the complete pack and return an independent normalized manifest.

    Pixel limits apply cumulatively, including duplicate atlas references. Each
    frame occupies one logical canvas, so renderers need no implicit scaling.
    """
    if not isinstance(manifest, dict):
        raise CharacterPackError("Manifest must be an object")
    value = _object({key: item for key, item in manifest.items() if key != "fallback_clips"}, _MANIFEST_KEYS, "Manifest")
    _integer(value["version"], PACK_VERSION, PACK_VERSION, "version")
    pack_id = _identifier(value["id"], "id")
    name = _text(value["name"], "name")
    revision = _integer(value["revision"], 1, 2**31 - 1, "revision")
    size = _object(value["logical_size"], {"width", "height"}, "logical_size")
    width = _integer(size["width"], 16, MAX_LOGICAL_EDGE, "logical width")
    height = _integer(size["height"], 16, MAX_LOGICAL_EDGE, "logical height")
    anchor = _object(value["foot_anchor"], {"x", "y"}, "foot_anchor")
    anchor = {"x": _number(anchor["x"], 0, width, "foot x"), "y": _number(anchor["y"], 0, height, "foot y")}
    bounds = _object(value["interaction_bounds"], {"x", "y", "width", "height"}, "interaction_bounds")
    bounds = {
        "x": _number(bounds["x"], 0, width - 1, "interaction x"),
        "y": _number(bounds["y"], 0, height - 1, "interaction y"),
        "width": _number(bounds["width"], 1, width, "interaction width"),
        "height": _number(bounds["height"], 1, height, "interaction height"),
    }
    if bounds["x"] + bounds["width"] > width or bounds["y"] + bounds["height"] > height:
        raise CharacterPackError("Interaction bounds exceed the logical canvas")
    atlas_values = value["atlases"]
    if not isinstance(atlas_values, list) or not 1 <= len(atlas_values) <= MAX_ATLASES:
        raise CharacterPackError("Pack must contain between 1 and 32 atlases")
    if not isinstance(assets, dict):
        raise CharacterPackError("Assets must be a mapping of filenames to PNG bytes")
    atlases, filenames, pixels = [], set(), 0
    for atlas_value in atlas_values:
        atlas = _object(atlas_value, {"file", "width", "height"}, "Atlas")
        filename = _asset_name(atlas["file"])
        if filename.casefold() in filenames:
            raise CharacterPackError("Atlas filenames must be unique")
        filenames.add(filename.casefold())
        aw = _integer(atlas["width"], 1, MAX_ATLAS_EDGE, "atlas width")
        ah = _integer(atlas["height"], 1, MAX_ATLAS_EDGE, "atlas height")
        pixels += aw * ah
        if pixels > MAX_DECODED_PIXELS:
            raise CharacterPackError("Pack exceeds cumulative decoded pixel limit")
        if filename not in assets or _png(assets[filename]).size != (aw, ah):
            raise CharacterPackError(f"Atlas dimensions or asset missing: {filename}")
        atlases.append({"file": filename, "width": aw, "height": ah})
    if set(assets) != {atlas["file"] for atlas in atlases}:
        raise CharacterPackError("Assets must contain exactly the declared PNG atlases")
    if sum(len(raw) for raw in assets.values()) > MAX_PACK_BYTES:
        raise CharacterPackError("Pack assets exceed 32 MiB")
    clip_values = value["clips"]
    if not isinstance(clip_values, dict) or not set(REQUIRED_CLIPS).issubset(clip_values) or len(clip_values) > 64:
        raise CharacterPackError("Pack must define all required animation clips (at most 64 clips)")
    for clip_name in clip_values:
        _identifier(clip_name, "clip name")
    clips, total_frames = {}, 0
    for clip_name, clip_value in sorted(clip_values.items()):
        clip = _object(clip_value, {"frames", "loop"}, f"Clip {clip_name}")
        if type(clip["loop"]) is not bool:
            raise CharacterPackError(f"Clip {clip_name} loop must be a boolean")
        frame_values = clip["frames"]
        if not isinstance(frame_values, list) or not 1 <= len(frame_values) <= MAX_FRAMES_PER_CLIP:
            raise CharacterPackError(f"Clip {clip_name} must have between 1 and 256 frames")
        total_frames += len(frame_values)
        if total_frames > MAX_TOTAL_FRAMES:
            raise CharacterPackError("Pack exceeds 4096 animation frames")
        frames = []
        for frame_value in frame_values:
            frame = _object(frame_value, {"atlas", "x", "y", "width", "height", "duration_ms"}, "Frame")
            ai = _integer(frame["atlas"], 0, len(atlases) - 1, "frame atlas")
            atlas = atlases[ai]
            fw = _integer(frame["width"], width, width, "frame width")
            fh = _integer(frame["height"], height, height, "frame height")
            x = _integer(frame["x"], 0, atlas["width"], "frame x")
            y = _integer(frame["y"], 0, atlas["height"], "frame y")
            if x + fw > atlas["width"] or y + fh > atlas["height"]:
                raise CharacterPackError("Frame rectangle exceeds its atlas")
            frames.append({"atlas": ai, "x": x, "y": y, "width": fw, "height": fh,
                           "duration_ms": _integer(frame["duration_ms"], 16, 10000, "frame duration_ms")})
        clips[clip_name] = {"frames": frames, "loop": clip["loop"]}
    states = value["states"]
    if not isinstance(states, dict) or set(states) != set(REQUIRED_STATES):
        raise CharacterPackError("States must map each required agent state to a clip")
    if any(not isinstance(target, str) or target not in clips for target in states.values()):
        raise CharacterPackError("State refers to an unknown clip")
    fallbacks = manifest.get("fallback_clips", [])
    if not isinstance(fallbacks, list) or any(not isinstance(item, str) or item not in clips for item in fallbacks):
        raise CharacterPackError("fallback_clips must list known clip names")
    if len(set(fallbacks)) != len(fallbacks):
        raise CharacterPackError("fallback_clips must not contain duplicates")
    return {"version": PACK_VERSION, "id": pack_id, "name": name, "revision": revision,
            "logical_size": {"width": width, "height": height}, "foot_anchor": anchor,
            "interaction_bounds": bounds, "atlases": atlases, "clips": clips,
            "states": {state: states[state] for state in REQUIRED_STATES}, "fallback_clips": sorted(fallbacks)}


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CharacterPackError(f"Duplicate manifest key: {key}")
        result[key] = value
    return result


def _manifest(raw: bytes) -> dict:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise CharacterPackError("Manifest exceeds 512 KiB")
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(CharacterPackError("Invalid JSON number")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise CharacterPackError("Manifest is not valid JSON") from exc


def import_pack(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    """Import a bounded .termxpet ZIP without writing any archive paths."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_PACK_BYTES:
        raise CharacterPackError("Character pack must be at most 32 MiB")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            members = archive.infolist()
            if not 2 <= len(members) <= MAX_ATLASES + 1:
                raise CharacterPackError("Archive has an invalid member count")
            seen, total = set(), 0
            for info in members:
                filename = info.filename
                if filename != "manifest.json":
                    _asset_name(filename)
                if filename.casefold() in seen:
                    raise CharacterPackError("Archive contains duplicate filenames")
                seen.add(filename.casefold())
                mode = info.external_attr >> 16
                if info.is_dir() or (stat.S_IFMT(mode) not in (0, stat.S_IFREG)):
                    raise CharacterPackError("Archive must contain regular data files")
                if info.flag_bits & 1 or info.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                    raise CharacterPackError("Encrypted or unsupported archive member")
                limit = MAX_MANIFEST_BYTES if filename == "manifest.json" else MAX_ATLAS_BYTES
                if info.file_size > limit:
                    raise CharacterPackError("Archive member exceeds size limit")
                total += info.file_size
                if total > MAX_PACK_BYTES:
                    raise CharacterPackError("Archive exceeds uncompressed size limit")
            if "manifest.json" not in archive.namelist():
                raise CharacterPackError("Archive is missing manifest.json")
            manifest = _manifest(archive.read("manifest.json"))
            assets = {info.filename: archive.read(info) for info in members if info.filename != "manifest.json"}
    except (BadZipFile, RuntimeError, OSError, EOFError, NotImplementedError) as exc:
        raise CharacterPackError("Could not read character pack archive") from exc
    return validate_pack(manifest, assets), assets


def export_pack(manifest: dict, assets: dict[str, bytes]) -> bytes:
    """Export stable bytes: sorted members, canonical JSON, and fixed timestamps."""
    normalized = validate_pack(manifest, assets)
    members = {"manifest.json": (json.dumps(normalized, sort_keys=True, indent=2) + "\n").encode(), **assets}
    if len(members["manifest.json"]) > MAX_MANIFEST_BYTES:
        raise CharacterPackError("Manifest exceeds 512 KiB")
    target = BytesIO()
    with ZipFile(target, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for filename, raw in sorted(members.items()):
            info = ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, raw, compresslevel=9)
    raw = target.getvalue()
    if len(raw) > MAX_PACK_BYTES:
        raise CharacterPackError("Export exceeds 32 MiB")
    return raw


def compile_sheet(raw: bytes, *, name: str, pack_id: str, clip: str = "idle", columns: int = 4,
                  rows: int = 2, duration_ms: int = 140,
                  base: tuple[dict, dict[str, bytes]] | None = None,
                  remove_background: bool = False) -> tuple[dict, dict[str, bytes]]:
    """Normalize a transparent grid into a complete pack or replace one base clip.

    New packs use the submitted animation as the fallback for every state. Later
    builds replace one clip without changing other animations. All cells share
    one scale and align to the same foot anchor; no input object is mutated.
    """
    _identifier(pack_id, "id")
    _text(name, "name")
    _identifier(clip, "clip name")
    _integer(columns, 1, 32, "columns")
    _integer(rows, 1, 32, "rows")
    if columns * rows > MAX_FRAMES_PER_CLIP:
        raise CharacterPackError("Sheet must contain at most 256 frames")
    _integer(duration_ms, 16, 10000, "duration_ms")
    if type(remove_background) is not bool:
        raise CharacterPackError("remove_background must be a boolean")
    sheet = _png(raw)
    if sheet.width % columns or sheet.height % rows:
        raise CharacterPackError("Sheet dimensions must be divisible by its grid")
    cell_width, cell_height = sheet.width // columns, sheet.height // rows
    if cell_width < 1 or cell_height < 1:
        raise CharacterPackError("Sheet cells must be nonempty")
    if base is not None:
        manifest = validate_pack(*base)
        assets = dict(base[1])
        width, height = manifest["logical_size"]["width"], manifest["logical_size"]["height"]
    else:
        width, height = min(MAX_LOGICAL_EDGE, max(16, cell_width)), min(MAX_LOGICAL_EDGE, max(16, cell_height))
        manifest = {"version": PACK_VERSION, "id": pack_id, "name": name.strip(), "revision": 1,
                    "logical_size": {"width": width, "height": height},
                    "foot_anchor": {"x": width // 2, "y": height - 4},
                    "interaction_bounds": {"x": 0, "y": 0, "width": width, "height": height},
                    "atlases": [], "clips": {}, "states": {state: state for state in REQUIRED_STATES},
                    "fallback_clips": sorted(set(REQUIRED_CLIPS) - {clip})}
        assets = {}
    manifest["id"], manifest["name"] = pack_id, name.strip()
    anchor = manifest["foot_anchor"]
    cells = []
    for index in range(columns * rows):
        x, y = (index % columns) * cell_width, (index // columns) * cell_height
        cell = sheet.crop((x, y, x + cell_width, y + cell_height))
        if remove_background and cell.getchannel("A").getextrema()[0] != 0:
            corners = ((0, 0), (cell.width - 1, 0), (0, cell.height - 1), (cell.width - 1, cell.height - 1))
            matte = cell.getpixel(corners[0])
            if any(cell.getpixel(point) != matte for point in corners):
                raise CharacterPackError("Background removal requires matching solid-color corners in every frame")
            border = [(bx, by) for bx in range(cell.width) for by in (0, cell.height - 1)]
            border.extend((bx, by) for by in range(cell.height) for bx in (0, cell.width - 1))
            if any(cell.getpixel(point) != matte for point in border):
                raise CharacterPackError("Background removal requires a solid-color border in every frame")
            for point in corners:
                if cell.getpixel(point) == matte:
                    ImageDraw.floodfill(cell, point, (0, 0, 0, 0), thresh=0)
        alpha = cell.getchannel("A")
        edge_alpha = [alpha.crop(bounds).getextrema()[0] for bounds in
                      ((0, 0, cell.width, 1), (0, 0, 1, cell.height),
                       (0, cell.height - 1, cell.width, cell.height), (cell.width - 1, 0, cell.width, cell.height))]
        if min(edge_alpha) != 0:
            raise CharacterPackError(f"Sheet frame {index + 1} needs a transparent margin; use explicit solid background removal")
        bbox = cell.getchannel("A").getbbox()
        if bbox is None:
            raise CharacterPackError(f"Sheet frame {index + 1} is completely transparent")
        cells.append(cell.crop(bbox))
    available_width = min(anchor["x"], width - anchor["x"]) * 2 - 4
    available_height = anchor["y"] - 2
    if available_width < 1 or available_height < 1:
        raise CharacterPackError("Foot anchor leaves no room to normalize frames")
    scale = min(available_width / max(cell.width for cell in cells),
                available_height / max(cell.height for cell in cells))
    output_columns = min(columns, MAX_ATLAS_EDGE // width)
    output_rows = math.ceil(len(cells) / output_columns)
    if output_rows * height > MAX_ATLAS_EDGE:
        raise CharacterPackError("Normalized sheet exceeds atlas dimensions")
    atlas = Image.new("RGBA", (output_columns * width, output_rows * height))
    frame_values = []
    for index, cell in enumerate(cells):
        cell = cell.resize((max(1, round(cell.width * scale)), max(1, round(cell.height * scale))), Image.Resampling.LANCZOS)
        x, y = index % output_columns * width, index // output_columns * height
        atlas.alpha_composite(cell, (x + round(anchor["x"] - cell.width / 2), y + round(anchor["y"] - cell.height)))
        frame_values.append({"atlas": len(manifest["atlases"]), "x": x, "y": y,
                             "width": width, "height": height, "duration_ms": duration_ms})
    encoded = _encode(atlas)
    filename = f"atlas-{sha256(encoded).hexdigest()[:24]}.png"
    # Identical rebuilds reuse their atlas and do not exhaust the atlas limit.
    prior = next((i for i, item in enumerate(manifest["atlases"]) if item["file"] == filename), None)
    if prior is None:
        manifest["atlases"].append({"file": filename, "width": atlas.width, "height": atlas.height})
        assets[filename] = encoded
    else:
        for frame in frame_values:
            frame["atlas"] = prior
    definition = {"frames": frame_values, "loop": clip not in ("success", "error", "hop", "fall", "wave")}
    if base is None:
        manifest["clips"] = {key: {"frames": deepcopy(frame_values),
                                   "loop": key not in ("success", "error", "hop", "fall", "wave")}
                             for key in REQUIRED_CLIPS}
    manifest["clips"][clip] = definition
    manifest["fallback_clips"] = [key for key in manifest.get("fallback_clips", []) if key != clip]
    # Replacement removes unreachable atlases while preserving all live frames.
    used = sorted({frame["atlas"] for item in manifest["clips"].values() for frame in item["frames"]})
    remap = {old: new for new, old in enumerate(used)}
    manifest["atlases"] = [manifest["atlases"][index] for index in used]
    assets = {item["file"]: assets[item["file"]] for item in manifest["atlases"]}
    for item in manifest["clips"].values():
        for frame in item["frames"]:
            frame["atlas"] = remap[frame["atlas"]]
    manifest["revision"] = manifest["revision"] + 1 if base is not None else 1
    return validate_pack(manifest, assets), assets


def _robot_frame(clip: str, index: int) -> Image.Image:
    """Original TermX sprout robot, drawn at 3x for crisp soft contours."""
    scale = 3
    image = Image.new("RGBA", (96 * scale, 112 * scale))
    draw = ImageDraw.Draw(image)
    phase = index * math.tau / 8
    bounce = round(math.sin(phase) * (2 if clip in ("idle", "thinking", "working", "waiting") else 1))
    if clip == "hop":
        bounce = -round(math.sin(index * math.pi / 7) * 13)
    if clip == "success":
        bounce = -round(abs(math.sin(phase)) * 7)
    if clip in ("sleeping", "paused", "sit"):
        bounce = 9
    if clip == "fall":
        bounce = min(3, index)
    outline, shell, highlight, screen, teal = "#17364a", "#8fe1dd", "#dbffed", "#183b50", "#5df8cc"
    def box(coords, fill, radius=0, stroke=None, line_width=2):
        coords = tuple(round(v * scale) for v in coords)
        if radius:
            draw.rounded_rectangle(coords, radius=radius * scale, fill=fill, outline=stroke, width=line_width * scale)
        else:
            draw.rectangle(coords, fill=fill, outline=stroke, width=line_width * scale)
    def line(points, fill, width=2):
        draw.line([(round(x * scale), round(y * scale)) for x, y in points], fill=fill, width=width * scale, joint="curve")
    def ellipse(coords, fill, stroke=None):
        draw.ellipse(tuple(round(v * scale) for v in coords), fill=fill, outline=stroke, width=2 * scale)
    oy = bounce
    # Grounding shadow remains in the logical frame and never catches input.
    ellipse((25, 101, 71, 106), (14, 45, 54, 30))
    leg = round(math.sin(phase) * 5) if clip == "walk" else 0
    box((29 + leg, 91 + oy, 43 + leg, 102 + oy), outline, 4)
    box((53 - leg, 91 + oy, 67 - leg, 102 + oy), outline, 4)
    box((26 + leg, 98 + min(oy, 0), 44 + leg, 104 + min(oy, 0)), shell, 3, outline)
    box((52 - leg, 98 + min(oy, 0), 70 - leg, 104 + min(oy, 0)), shell, 3, outline)
    arm = round(math.sin(phase) * 4)
    if clip in ("wave", "success", "drag", "fall"):
        line([(72, 74 + oy), (82, 60 + oy), (80 + arm, 43 + oy)], outline, 7)
        ellipse((75 + arm, 37 + oy, 85 + arm, 48 + oy), shell, outline)
    else:
        line([(72, 73 + oy), (80, 81 + oy + arm / 2), (77, 89 + oy + arm / 2)], outline, 7)
        ellipse((72, 85 + oy + arm / 2, 82, 95 + oy + arm / 2), shell, outline)
    if clip == "working":
        line([(24, 75 + oy), (17, 82 + oy), (24 + arm, 84 + oy)], outline, 7)
        box((10, 88 + oy, 28, 92 + oy), teal, 2)
    else:
        line([(24, 73 + oy), (16, 82 + oy - arm / 2), (19, 90 + oy - arm / 2)], outline, 7)
        ellipse((14, 86 + oy - arm / 2, 24, 96 + oy - arm / 2), shell, outline)
    box((25, 63 + oy, 71, 95 + oy), shell, 11, outline)
    box((32, 66 + oy, 64, 71 + oy), highlight, 2)
    ellipse((43, 78 + oy, 53, 88 + oy), outline)
    ellipse((46, 81 + oy, 50, 85 + oy), teal)
    line([(48, 27 + oy), (48, 18 + oy)], outline, 3)
    ellipse((43, 10 + oy, 53, 20 + oy), teal, outline)
    box((17, 27 + oy, 79, 68 + oy), shell, 14, outline)
    box((22, 30 + oy, 74, 35 + oy), highlight, 3)
    box((24, 36 + oy, 72, 61 + oy), screen, 8)
    if clip in ("sleeping", "paused"):
        line([(31, 48 + oy), (39, 48 + oy)], teal, 2)
        line([(56, 48 + oy), (64, 48 + oy)], teal, 2)
    elif clip == "error":
        line([(31, 43 + oy), (38, 50 + oy)], "#ffbc92", 3)
        line([(38, 43 + oy), (31, 50 + oy)], "#ffbc92", 3)
        line([(57, 43 + oy), (64, 50 + oy)], "#ffbc92", 3)
        line([(64, 43 + oy), (57, 50 + oy)], "#ffbc92", 3)
    elif clip == "success":
        line([(30, 49 + oy), (35, 43 + oy), (40, 49 + oy)], teal, 2)
        line([(56, 49 + oy), (61, 43 + oy), (66, 49 + oy)], teal, 2)
    elif index == 6 and clip in ("idle", "sit", "waiting"):
        line([(31, 47 + oy), (39, 47 + oy)], teal, 2)
        line([(56, 47 + oy), (64, 47 + oy)], teal, 2)
    else:
        gaze = round(math.sin(phase) * 2) if clip == "thinking" else 0
        box((32 + gaze, 42 + oy, 39 + gaze, 52 + oy), teal, 3)
        box((57 + gaze, 42 + oy, 64 + gaze, 52 + oy), teal, 3)
    if clip == "waiting":
        for dot in range(3):
            ellipse((39 + dot * 7, 55 + oy, 42 + dot * 7, 58 + oy), teal if index % 3 == dot else "#38766f")
    else:
        line([(44, 56 + oy), (51, 56 + oy)], teal, 1)
    if clip == "thinking":
        ellipse((76, 17, 81, 22), (93, 248, 204, 180))
        ellipse((83, 8, 90, 15), (93, 248, 204, 100 + index * 15))
    if clip == "sleeping":
        z_y = 14 - index % 4
        line([(77, z_y), (85, z_y), (77, z_y + 8), (85, z_y + 8)], teal, 2)
    if clip == "success":
        ellipse((9, 18 + index % 3 * 3, 12, 21 + index % 3 * 3), "#ffdb80")
        ellipse((82, 24 - index % 3 * 2, 85, 27 - index % 3 * 2), "#ffdb80")
    return image.resize((96, 112), Image.Resampling.LANCZOS)


@lru_cache(maxsize=1)
def _starter() -> tuple[dict, dict[str, bytes]]:
    bundled = files("termx.characters").joinpath("assets", "sprout")
    if bundled.joinpath("manifest.json").is_file():
        manifest = _manifest(bundled.joinpath("manifest.json").read_bytes())
        assets = {"atlas.png": bundled.joinpath("atlas.png").read_bytes()}
        return validate_pack(manifest, assets), assets
    # Source-only development distributions can reproduce the bundled artwork.
    width, height, count = 96, 112, 8
    atlas = Image.new("RGBA", (width * count, height * len(REQUIRED_CLIPS)))
    clips = {}
    for row, clip in enumerate(REQUIRED_CLIPS):
        frames = []
        for index in range(count):
            atlas.alpha_composite(_robot_frame(clip, index), (index * width, row * height))
            frames.append({"atlas": 0, "x": index * width, "y": row * height, "width": width,
                           "height": height, "duration_ms": 120 if clip == "walk" else 150})
        clips[clip] = {"frames": frames, "loop": clip not in ("success", "error", "hop", "fall", "wave")}
    manifest = {"version": PACK_VERSION, "id": "termx-robot", "name": "Sprout", "revision": 1,
                "logical_size": {"width": width, "height": height}, "foot_anchor": {"x": 48, "y": 104},
                "interaction_bounds": {"x": 15, "y": 10, "width": 70, "height": 94},
                "atlases": [{"file": "atlas.png", "width": atlas.width, "height": atlas.height}],
                "clips": clips, "states": {state: state for state in REQUIRED_STATES}}
    assets = {"atlas.png": _encode(atlas)}
    return validate_pack(manifest, assets), assets


def starter_pack() -> tuple[dict, dict[str, bytes]]:
    """Return the original bundled Sprout robot; callers may safely edit copies."""
    manifest, assets = _starter()
    return deepcopy(manifest), dict(assets)
