"""Build and review portable .termxpet character packs.

Run ``python -m termx.characters.cli --help`` without starting the TermX daemon.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .packs import (
    CharacterPackError,
    MAX_ATLAS_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_PACK_BYTES,
    _asset_name,
    _manifest,
    compile_sheet,
    export_pack,
    import_pack,
    starter_pack,
    validate_pack,
)


def _read(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        raw = source.read(limit + 1)
    if len(raw) > limit:
        raise CharacterPackError(f"File exceeds {limit} bytes: {path.name}")
    return raw


def _load(path: Path) -> tuple[dict, dict[str, bytes]]:
    if not path.is_dir():
        return import_pack(_read(path, MAX_PACK_BYTES))
    manifest = _manifest(_read(path / "manifest.json", MAX_MANIFEST_BYTES))
    if not isinstance(manifest, dict) or not isinstance(manifest.get("atlases"), list) or len(manifest["atlases"]) > 32:
        raise CharacterPackError("Directory manifest has an invalid atlas list")
    assets = {}
    for atlas in manifest["atlases"]:
        if not isinstance(atlas, dict):
            raise CharacterPackError("Directory manifest has an invalid atlas")
        filename = _asset_name(atlas.get("file"))
        assets[filename] = _read(path / filename, MAX_ATLAS_BYTES)
    return validate_pack(manifest, assets), assets


def _save(pack: tuple[dict, dict[str, bytes]], output: Path, directory: bool) -> None:
    manifest, assets = pack
    # Fully compile and validate before touching an existing output.
    raw = export_pack(manifest, assets)
    if directory:
        if output.exists():
            raise CharacterPackError("Directory output must not exist; use a new revision directory")
        output.mkdir(parents=True)
        (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        for filename, data in assets.items():
            (output / filename).write_bytes(data)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement leaves a previous usable pack intact after failure.
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(dir=output.parent, prefix=f".{output.name}.", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            try:
                temporary.write(raw)
                temporary.flush()
                os.fsync(temporary.fileno())
            except BaseException:
                temporary_path.unlink(missing_ok=True)
                raise
        try:
            temporary_path.replace(output)
        finally:
            temporary_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile and validate TermX character animation packs")
    subparsers = parser.add_subparsers(dest="command", required=True)
    starter = subparsers.add_parser("starter", help="Write the original Sprout robot pack")
    starter.add_argument("output", type=Path)
    starter.add_argument("--directory", action="store_true", help="Write editable manifest.json and PNG files")
    build = subparsers.add_parser("build", help="Normalize a transparent PNG grid into a character pack")
    build.add_argument("sheet", type=Path)
    build.add_argument("--output", "-o", type=Path, required=True)
    build.add_argument("--name", default="My character")
    build.add_argument("--id", dest="pack_id", default="my-character")
    build.add_argument("--clip", default="idle")
    build.add_argument("--columns", type=int, default=4)
    build.add_argument("--rows", type=int, default=2)
    build.add_argument("--duration-ms", type=int, default=140)
    build.add_argument("--base", type=Path, help="Preserve other animations from this pack or directory")
    build.add_argument("--remove-background", action="store_true", help="Explicitly flood-fill matching solid-color frame corners to transparency")
    build.add_argument("--directory", action="store_true")
    validate = subparsers.add_parser("validate", help="Validate a pack or editable pack directory")
    validate.add_argument("input", type=Path)
    export = subparsers.add_parser("export", help="Export an editable directory or canonicalize a pack")
    export.add_argument("input", type=Path)
    export.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "starter":
            pack = starter_pack()
            _save(pack, args.output, args.directory)
        elif args.command == "build":
            pack = compile_sheet(_read(args.sheet, MAX_ATLAS_BYTES), name=args.name, pack_id=args.pack_id,
                                 clip=args.clip, columns=args.columns, rows=args.rows, duration_ms=args.duration_ms,
                                 base=_load(args.base) if args.base else None,
                                 remove_background=args.remove_background)
            _save(pack, args.output, args.directory)
        elif args.command == "export":
            pack = _load(args.input)
            _save(pack, args.output, False)
        else:
            pack = _load(args.input)
        manifest, _ = pack
        print(json.dumps({"valid": True, "id": manifest["id"], "name": manifest["name"],
                          "revision": manifest["revision"], "clips": len(manifest["clips"]),
                          "fallback_clips": manifest.get("fallback_clips", [])}, sort_keys=True))
        return 0
    except (CharacterPackError, OSError) as exc:
        print(f"Character pack: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
