from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import stat
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from PIL import Image, ImageDraw
import pytest

from termx.characters.cli import main
from termx.characters.packs import (
    CharacterPackError,
    MAX_DECODED_PIXELS,
    REQUIRED_CLIPS,
    REQUIRED_STATES,
    compile_sheet,
    export_pack,
    import_pack,
    starter_pack,
    validate_pack,
)


def png(image: Image.Image) -> bytes:
    target = BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()


def sheet(*, opaque: bool = False, differing_corners: bool = False) -> bytes:
    image = Image.new("RGBA", (64 * 4, 64 * 2), "white" if opaque else None)
    draw = ImageDraw.Draw(image)
    for index in range(8):
        x, y = index % 4 * 64, index // 4 * 64
        draw.rectangle((x + 15, y + 10, x + 45, y + 50 - index), fill="teal")
    if differing_corners:
        draw.point((0, 0), fill="red")
    return png(image)


def zip_members(members: list[tuple[str | ZipInfo, bytes]]) -> bytes:
    target = BytesIO()
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for name, raw in members:
            archive.writestr(name, raw)
    return target.getvalue()


def test_starter_is_complete_original_transparent_art() -> None:
    manifest, assets = starter_pack()
    assert manifest["name"] == "Sprout"
    assert manifest["id"] == "termx-robot"
    assert set(manifest["clips"]) == set(REQUIRED_CLIPS)
    assert set(manifest["states"]) == set(REQUIRED_STATES)
    assert manifest["foot_anchor"] == {"x": 48, "y": 104}
    assert manifest["fallback_clips"] == []
    atlas = Image.open(BytesIO(assets["atlas.png"]))
    assert atlas.mode == "RGBA"
    assert atlas.getpixel((0, 0))[3] == 0
    state_pixels = set()
    for clip in manifest["clips"].values():
        frame = clip["frames"][0]
        crop = atlas.crop((frame["x"], frame["y"], frame["x"] + frame["width"], frame["y"] + frame["height"]))
        assert crop.getbbox() is not None
        state_pixels.add(crop.tobytes())
    assert len(state_pixels) >= 8


def test_starter_returns_independent_manifests() -> None:
    manifest, assets = starter_pack()
    manifest["clips"]["idle"]["frames"][0]["duration_ms"] = 999
    assets.clear()
    next_manifest, next_assets = starter_pack()
    assert next_manifest["clips"]["idle"]["frames"][0]["duration_ms"] != 999
    assert next_assets


def test_export_roundtrip_is_byte_deterministic() -> None:
    pack = starter_pack()
    first, second = export_pack(*pack), export_pack(*pack)
    assert first == second
    assert export_pack(*import_pack(first)) == first
    assert import_pack(first) == pack
    with ZipFile(BytesIO(first)) as archive:
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


def test_compile_complete_pack_uses_foot_alignment_and_shared_scale() -> None:
    manifest, assets = compile_sheet(sheet(), name="Custom", pack_id="custom")
    assert set(manifest["clips"]) == set(REQUIRED_CLIPS)
    assert manifest["fallback_clips"] == sorted(set(REQUIRED_CLIPS) - {"idle"})
    assert manifest["clips"]["idle"]["loop"] is True
    assert manifest["clips"]["success"]["loop"] is False
    atlas = Image.open(BytesIO(next(iter(assets.values()))))
    heights = []
    for frame in manifest["clips"]["idle"]["frames"]:
        cell = atlas.crop((frame["x"], frame["y"], frame["x"] + frame["width"], frame["y"] + frame["height"]))
        bbox = cell.getchannel("A").getbbox()
        assert bbox is not None
        assert bbox[3] == manifest["foot_anchor"]["y"]
        assert abs((bbox[0] + bbox[2]) / 2 - manifest["foot_anchor"]["x"]) <= 0.5
        heights.append(bbox[3] - bbox[1])
    assert heights[0] > heights[-1], "Per-frame scaling must not erase relative pose size"


def test_replacing_clip_preserves_base_and_other_animations() -> None:
    original = starter_pack()
    before = deepcopy(original)
    manifest, assets = compile_sheet(sheet(), name="Sprout custom", pack_id="sprout-custom", clip="wave", base=original)
    assert original == before
    assert manifest["logical_size"] == before[0]["logical_size"]
    assert manifest["foot_anchor"] == before[0]["foot_anchor"]
    assert manifest["clips"]["idle"] == before[0]["clips"]["idle"]
    assert manifest["clips"]["wave"]["frames"] != before[0]["clips"]["wave"]["frames"]
    assert len(assets) == 2
    assert manifest["revision"] == 2


def test_regenerated_pose_clears_fallback_and_reuses_identical_atlas() -> None:
    base = compile_sheet(sheet(), name="Custom", pack_id="custom")
    replacement = compile_sheet(sheet(), name="Custom", pack_id="custom", clip="wave", base=base)
    assert "wave" not in replacement[0]["fallback_clips"]
    assert "working" in replacement[0]["fallback_clips"]
    assert len(replacement[1]) == 1
    assert replacement == compile_sheet(sheet(), name="Custom", pack_id="custom", clip="wave", base=base)
    assert compile_sheet(sheet(), name="Custom", pack_id="custom", clip="wave", base=replacement)[0]["revision"] == 3


def test_new_clip_replacement_discards_unreachable_atlas() -> None:
    base = compile_sheet(sheet(), name="Custom", pack_id="custom")
    other = Image.open(BytesIO(sheet()))
    draw = ImageDraw.Draw(other)
    draw.rectangle((16, 11, 44, 39), fill="red")
    first = compile_sheet(png(other), name="Custom", pack_id="custom", clip="wave", base=base)
    second = compile_sheet(sheet(), name="Custom", pack_id="custom", clip="wave", base=first)
    assert len(first[1]) == 2
    assert len(second[1]) == 1


@pytest.mark.parametrize("changes", [
    {"columns": 0}, {"columns": True}, {"rows": 33}, {"rows": 32, "columns": 32},
    {"columns": 3}, {"duration_ms": 0}, {"duration_ms": 10001}, {"pack_id": "../escape"},
    {"clip": "../../walk"}, {"name": ""},
])
def test_sheet_rejects_invalid_grid_or_metadata(changes: dict) -> None:
    arguments = {"name": "Custom", "pack_id": "custom", **changes}
    with pytest.raises(CharacterPackError):
        compile_sheet(sheet(), **arguments)


def test_sheet_rejects_invisible_frame() -> None:
    image = Image.open(BytesIO(sheet()))
    image.paste((0, 0, 0, 0), (0, 0, 64, 64))
    with pytest.raises(CharacterPackError, match="completely transparent"):
        compile_sheet(png(image), name="Custom", pack_id="custom")


def test_sheet_requires_explicit_removal_of_flat_matte() -> None:
    with pytest.raises(CharacterPackError, match="transparent margin"):
        compile_sheet(sheet(opaque=True), name="Custom", pack_id="custom")
    manifest, assets = compile_sheet(sheet(opaque=True), name="Custom", pack_id="custom", remove_background=True)
    image = Image.open(BytesIO(next(iter(assets.values()))))
    assert image.getpixel((0, 0))[3] == 0
    assert manifest["fallback_clips"]
    with pytest.raises(CharacterPackError, match="matching solid-color corners"):
        compile_sheet(sheet(opaque=True, differing_corners=True), name="Custom", pack_id="custom", remove_background=True)
    image = Image.open(BytesIO(sheet(opaque=True)))
    image.putpixel((10, 0), (254, 254, 254, 255))
    with pytest.raises(CharacterPackError, match="solid-color border"):
        compile_sheet(png(image), name="Custom", pack_id="custom", remove_background=True)


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("id", "bad/name"), ("name", "\x00bad"),
    ("revision", ""), ("revision", "1"), ("revision", 0), ("revision", True),
    ("logical_size", {"width": 1, "height": 1}),
    ("foot_anchor", {"x": float("nan"), "y": 104}),
    ("interaction_bounds", {"x": 95, "y": 0, "width": 2, "height": 10}),
    ("states", {"idle": "idle"}), ("fallback_clips", ["missing"]),
    ("fallback_clips", ["idle", "idle"]),
])
def test_invalid_manifests_are_rejected(field: str, value: object) -> None:
    manifest, assets = starter_pack()
    manifest[field] = value
    with pytest.raises(CharacterPackError):
        validate_pack(manifest, assets)


@pytest.mark.parametrize("field,value", [("atlas", 8), ("x", 99999), ("width", 95), ("duration_ms", 15)])
def test_invalid_frames_are_rejected(field: str, value: object) -> None:
    manifest, assets = starter_pack()
    manifest["clips"]["idle"]["frames"][0][field] = value
    with pytest.raises(CharacterPackError):
        validate_pack(manifest, assets)


def test_missing_clips_unknown_states_and_extra_assets_rejected() -> None:
    manifest, assets = starter_pack()
    del manifest["clips"]["walk"]
    with pytest.raises(CharacterPackError, match="required animation"):
        validate_pack(manifest, assets)
    manifest, assets = starter_pack()
    manifest["states"]["idle"] = "missing"
    with pytest.raises(CharacterPackError, match="unknown clip"):
        validate_pack(manifest, assets)
    manifest, assets = starter_pack()
    assets["evil.py"] = b"print('hello')"
    with pytest.raises(CharacterPackError, match="exactly"):
        validate_pack(manifest, assets)


@pytest.mark.parametrize("name", ["../atlas.png", "/atlas.png", "folder/atlas.png", "folder\\atlas.png", "script.py"])
def test_archive_rejects_unsafe_or_executable_paths(name: str) -> None:
    raw = zip_members([("manifest.json", b"{}"), (name, b"bad")])
    with pytest.raises(CharacterPackError):
        import_pack(raw)


def test_archive_rejects_symlink_duplicate_and_missing_manifest() -> None:
    symlink = ZipInfo("atlas.png")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with pytest.raises(CharacterPackError, match="regular data files"):
        import_pack(zip_members([("manifest.json", b"{}"), (symlink, b"/etc/passwd")]))
    with pytest.raises(CharacterPackError, match="duplicate"):
        import_pack(zip_members([("manifest.json", b"{}"), ("atlas.png", b"a"), ("ATLAS.png", b"b")]))
    with pytest.raises(CharacterPackError, match="missing manifest"):
        import_pack(zip_members([("a.png", b"a"), ("b.png", b"b")]))


def test_archive_rejects_duplicate_json_keys_invalid_png_and_truncation() -> None:
    with pytest.raises(CharacterPackError, match="Duplicate manifest key"):
        import_pack(zip_members([("manifest.json", b'{"version":1,"version":1}'), ("atlas.png", b"x")]))
    manifest, _ = starter_pack()
    with pytest.raises(CharacterPackError, match="PNG"):
        import_pack(zip_members([("manifest.json", json.dumps(manifest).encode()), ("atlas.png", b"bad")]))
    with pytest.raises(CharacterPackError):
        import_pack(export_pack(*starter_pack())[:50])


def test_pixel_budget_is_checked_before_decoding(monkeypatch) -> None:
    manifest, assets = starter_pack()
    manifest["atlases"] = [
        {"file": "atlas.png", "width": 4096, "height": 4096},
        {"file": "extra.png", "width": 1, "height": 1},
    ]
    from types import SimpleNamespace
    calls = []
    def decoder(raw: bytes):
        calls.append(raw)
        return SimpleNamespace(size=(4096, 4096))
    monkeypatch.setattr("termx.characters.packs._png", decoder)
    with pytest.raises(CharacterPackError, match="cumulative decoded pixel limit"):
        validate_pack(manifest, {**assets, "extra.png": assets["atlas.png"]})
    assert len(calls) == 1, "An over-budget second atlas must not be decoded"
    assert MAX_DECODED_PIXELS <= 16 * 1024 * 1024


def test_cli_starter_edit_export_and_failure_preserve_previous_revision(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "sprout.termxpet"
    directory = tmp_path / "editable"
    assert main(["starter", str(archive)]) == 0
    assert main(["validate", str(archive)]) == 0
    before = archive.read_bytes()
    assert main(["starter", str(directory), "--directory"]) == 0
    assert main(["export", str(directory), str(tmp_path / "exported.termxpet")]) == 0
    assert (tmp_path / "exported.termxpet").read_bytes() == before
    invalid = tmp_path / "bad.png"
    invalid.write_bytes(b"bad png")
    assert main(["build", str(invalid), "--output", str(archive)]) == 2
    assert archive.read_bytes() == before
    assert "Character pack:" in capsys.readouterr().err


def test_cli_compile_and_validate_directory(tmp_path: Path) -> None:
    source = tmp_path / "sheet.png"
    source.write_bytes(sheet())
    output = tmp_path / "custom"
    assert main(["build", str(source), "--output", str(output), "--directory"]) == 0
    assert main(["validate", str(output)]) == 0
    assert main(["build", str(source), "--output", str(output), "--directory"]) == 2
