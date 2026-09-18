import subprocess
from pathlib import Path


def test_desktop_web_bundle_is_committed() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = root / "desktop" / "web"
    assert (bundle / "index.html").is_file()
    assert (bundle / "_expo").is_dir()


def test_desktop_web_assets_are_tracked() -> None:
    """Icon fonts under assets/ must be committed.

    The export nests them under assets/node_modules/*/build/, which collides
    with the node_modules/ and build/ ignore rules; if they drop out of git the
    packaged app renders every icon as a box.
    """
    root = Path(__file__).resolve().parents[1]
    fonts = sorted((root / "desktop" / "web" / "assets").rglob("*.ttf"))
    assert fonts, "web export has no bundled fonts"
    tracked = subprocess.run(
        ["git", "ls-files", "--", "desktop/web/assets"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    for font in fonts:
        rel = font.relative_to(root).as_posix()
        assert rel in tracked, f"{rel} is not tracked in git"
