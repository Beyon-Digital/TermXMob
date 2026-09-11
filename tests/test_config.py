from pathlib import Path

import pytest

from termx.config import ConfigStore, validate_cwd, validate_shell


def test_command_crud_and_reorder(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    first = store.add_command("Status", "git status")
    second = store.add_command("Diff", "git diff", confirm=True)
    assert [item.id for item in store.list_commands()] == [first.id, second.id]
    store.reorder_commands([second.id, first.id])
    assert [item.id for item in store.list_commands()] == [second.id, first.id]
    updated = store.patch_command(first.id, name="Git status")
    assert updated is not None
    assert updated.name == "Git status"
    assert store.delete_command(second.id) is True
    assert [item.id for item in store.list_commands()] == [first.id]


def test_terminal_prefs_validate(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "config.json")
    prefs = store.update_terminal(cwd=str(Path.home()))
    assert Path(prefs.cwd).is_dir()
    with pytest.raises(ValueError):
        store.update_terminal(cwd=str(tmp_path / "missing-dir"))
    shell = validate_shell(prefs.shell)
    assert Path(shell).is_file()
    validate_cwd(prefs.cwd)
