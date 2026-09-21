import json
import tomllib
from pathlib import Path

from termx import __version__


ROOT = Path(__file__).resolve().parents[1]


def test_release_versions_match() -> None:
    project_version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    tauri_version = json.loads(
        (ROOT / "desktop" / "src-tauri" / "tauri.conf.json").read_text()
    )["version"]
    cargo_version = tomllib.loads(
        (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text()
    )["package"]["version"]

    assert __version__ == project_version == tauri_version == cargo_version
