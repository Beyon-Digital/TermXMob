import subprocess
from pathlib import Path


def test_desktop_web_bundle_is_committed() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = root / "desktop" / "web"
    assert (bundle / "index.html").is_file()
    assert (bundle / "_expo").is_dir()


def test_desktop_web_assets_are_tracked() -> None:
    """Bundled fonts must be committed.

    The web UI renders icons as inline SVG, but its text fonts are woff2 files
    under fonts/ referenced by global.css @font-face rules; if they drop out of
    git the packaged app falls back to system fonts. Any icon .ttf assets under
    assets/ (which can collide with node_modules/ and build/ ignore rules) must
    also be tracked.
    """
    root = Path(__file__).resolve().parents[1]
    fonts = sorted((root / "desktop" / "web" / "fonts").rglob("*.woff2"))
    fonts += sorted((root / "desktop" / "web" / "assets").rglob("*.ttf"))
    assert fonts, "web export has no bundled fonts"
    tracked = subprocess.run(
        ["git", "ls-files", "--", "desktop/web"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for font in fonts:
        rel = font.relative_to(root).as_posix()
        assert rel in tracked, f"{rel} is not tracked in git"
