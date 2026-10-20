# TermX character packs

Characters are data-only `.termxpet` ZIP archives containing `manifest.json` and static PNG atlases. They cannot contain scripts, external URLs, directory trees, or executable payloads. Sprout, the original mint robot bundled with TermX, includes every agent and movement animation and works without an image provider.

## Create and review

```sh
python -m termx.characters.cli starter /tmp/sprout.termxpet
python -m termx.characters.cli starter /tmp/sprout-edit --directory
python -m termx.characters.cli validate /tmp/sprout.termxpet
python -m termx.characters.cli build idle-sheet.png --output /tmp/my-pet.termxpet --name "My pet" --id my-pet --columns 4 --rows 2
python -m termx.characters.cli build wave-sheet.png --output /tmp/my-pet-v2.termxpet --name "My pet" --id my-pet --clip wave --base /tmp/my-pet.termxpet
python -m termx.characters.cli export /tmp/sprout-edit /tmp/sprout-export.termxpet
```

Each input sheet is a PNG with an evenly divisible grid of frames, ordered left to right, then top to bottom. Every cell must contain a character and a transparent margin. A shared scale preserves relative pose sizes, crops empty margins, and aligns feet to the pack's foot anchor. Regenerating a single clip preserves all other clips, the logical canvas, and the foot anchor. Unreferenced atlases are discarded. Compilation and export are deterministic, and existing archive outputs are replaced atomically only after validation succeeds.

A new pack uses its first sheet as a fallback for the remaining poses. The manifest's `fallback_clips` list identifies these poses so the editor can show which animations still need custom artwork. Regeneration removes the replaced pose from that list. Success, error, wave, hop, and fall play once; other default clips loop.

Opaque artwork is rejected by default. `build --remove-background` explicitly enables exact-color flood filling from matching frame corners. It is intended for a flat matte, never a photograph or gradient. Disconnected interior pixels remain intact. Always preview the transparent result and each animation before accepting a generated revision; compilation does not activate a pack.

## Version 1 manifest

```json
{
  "version": 1,
  "id": "my-pet",
  "name": "My pet",
  "revision": 1,
  "logical_size": {"width": 96, "height": 112},
  "foot_anchor": {"x": 48, "y": 104},
  "interaction_bounds": {"x": 15, "y": 10, "width": 70, "height": 94},
  "atlases": [{"file": "atlas.png", "width": 768, "height": 1568}],
  "clips": {
    "idle": {
      "frames": [{"atlas": 0, "x": 0, "y": 0, "width": 96, "height": 112, "duration_ms": 150}],
      "loop": true
    }
  },
  "states": {"idle": "idle"},
  "fallback_clips": []
}
```

The abbreviated example shows one clip and state. A valid pack must define `idle`, `thinking`, `working`, `waiting`, `success`, `error`, `paused`, `sleeping`, `walk`, `hop`, `fall`, `sit`, `drag`, and `wave`. The first eight names are required agent states; each state maps to an existing clip. Additional named clips are permitted. `fallback_clips` is optional on import and defaults to an empty list.

Frame `atlas` values are zero-based indexes into `atlases`. Every rectangle has exactly the logical width and height and must fit within its atlas. Coordinates and anchors use logical canvas pixels. The renderer places `foot_anchor` on the current walking surface. `interaction_bounds` records a conservative interaction footprint. Native hit testing uses rendered alpha, so empty image margins do not intercept desktop input.

`revision` is a positive integer. A new compilation starts at 1; replacing a clip advances the base revision by one. The library assigns its own next revision when saving an imported or generated draft, preserving the accepted revision until the user accepts the new preview.

Limits are enforced before allocation where possible: 32 MiB archive and expanded payload, 32 atlas files, 4096 pixels per atlas edge, 16 million cumulative decoded pixels, 512 pixels per logical edge, 64 clips, 256 frames per clip, 4096 frames total, and 16–10000 ms per frame. Imports reject duplicate names and JSON keys, unsafe paths, symlinks, unsupported ZIP compression, encrypted members, missing assets, animated PNGs, unknown versions, and invalid frame or state references. Import reads bounded data without extracting archive paths.

The Python API in `termx.characters.packs` exposes `starter_pack`, `validate_pack`, `compile_sheet`, `import_pack`, and `export_pack`. Invalid inputs raise `CharacterPackError`, a `ValueError` subclass. Returned manifests are independent copies; asset values are immutable bytes. `compile_sheet(..., base=existing_pack)` never changes the existing revision.
