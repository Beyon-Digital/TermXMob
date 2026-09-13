from pathlib import Path


def test_desktop_web_bundle_is_committed() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = root / "desktop" / "web"
    assert (bundle / "index.html").is_file()
    assert (bundle / "_expo").is_dir()
