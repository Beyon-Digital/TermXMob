import json
import os
import shutil
import subprocess
import sys
import time

import pytest

import termx.desktop.capabilities as desktop_capabilities
import termx.desktop.virtual as vmod
from termx.desktop.capabilities import probe_desktop
from termx.desktop.virtual import (
    LEASE_GRACE_S,
    VirtualAdapter,
    VirtualDisplayError,
    active_adapter,
    create_virtual_display,
    destroy_all_virtual_displays,
    destroy_virtual_display,
    expire_leases,
    list_virtual_displays,
    touch_lease,
)


class FakeAdapter(VirtualAdapter):
    id = "fake"

    def __init__(self) -> None:
        self.created: list[str] = []
        self.destroyed: list[str] = []

    def available(self) -> bool:
        return True

    def can_create(self) -> bool:
        return True

    def create(self, display_id: str, width: int, height: int, dpr: float, refresh_hz: int) -> str:
        name = f"termx-{display_id}"
        self.created.append(name)
        return name

    def destroy(self, name: str) -> None:
        self.destroyed.append(name)


def test_probe_does_not_crash() -> None:
    probe = probe_desktop()
    assert probe.helper["installed"] is True
    assert probe.remote_screen in {True, False}
    adapter = active_adapter()
    assert probe.virtual_display is bool(adapter and adapter.can_create())
    assert probe.virtual_backend == (adapter.id if adapter else None)


def test_probe_does_not_advertise_unimplemented_ydotool_backend(monkeypatch) -> None:
    monkeypatch.setattr(desktop_capabilities.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(desktop_capabilities, "active_adapter", lambda: None)
    monkeypatch.setattr(desktop_capabilities, "_select_capture_backend", lambda: "grim")
    monkeypatch.setattr(desktop_capabilities, "_xtest_available", lambda: False)
    monkeypatch.setattr(desktop_capabilities, "permission_snapshot", lambda: {})
    monkeypatch.setattr(
        desktop_capabilities.shutil,
        "which",
        lambda name: "/usr/bin/ydotool" if name == "ydotool" else None,
    )

    probe = desktop_capabilities.probe_desktop()

    assert probe.input_backend is None
    assert probe.remote_screen is False
    assert probe.reason == (
        "No supported input backend: on X11 install libXtst or xdotool; "
        "native Wayland input is not yet supported"
    )


def test_virtual_display_is_explicitly_unsupported() -> None:
    adapter = active_adapter()
    if adapter is not None and adapter.can_create():
        try:
            created = create_virtual_display(800, 600)
        except VirtualDisplayError as exc:
            # can_create() is optimistic; headless hosts fail with a clear reason.
            assert str(exc)
            return
        try:
            assert created["kind"] == "virtual"
            assert created["adapter"] == adapter.id
        finally:
            destroy_virtual_display(str(created["id"]))
        return
    try:
        create_virtual_display(800, 600)
        raise AssertionError("expected VirtualDisplayError")
    except VirtualDisplayError as exc:
        assert str(exc)


def test_registry_create_destroy_with_fake_adapter() -> None:
    destroy_all_virtual_displays()
    fake = FakeAdapter()
    created = create_virtual_display(800, 600, dpr=2.0, refresh_hz=60, force_adapter=fake)
    assert created["kind"] == "virtual"
    assert created["width"] == 800
    assert created["height"] == 600
    assert created["selected"] is True
    assert created["adapter"] == "fake"
    assert created["name"] in fake.created
    listed = list_virtual_displays()
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]
    assert listed[0]["owner"] is None
    assert isinstance(listed[0]["lease_until"], float)
    assert listed[0]["lease_until"] > time.time()
    destroy_virtual_display(str(created["id"]))
    assert list_virtual_displays() == []
    assert fake.destroyed == [created["name"]]


def test_destroy_unknown_id_raises() -> None:
    try:
        destroy_virtual_display("missing")
        raise AssertionError("expected VirtualDisplayError")
    except VirtualDisplayError as exc:
        assert "missing" in str(exc)


def test_destroy_all_virtual_displays_clears_registry() -> None:
    fake = FakeAdapter()
    create_virtual_display(800, 600, force_adapter=fake)
    create_virtual_display(1024, 768, force_adapter=fake)
    assert len(list_virtual_displays()) == 2
    destroy_all_virtual_displays()
    assert list_virtual_displays() == []
    assert len(fake.destroyed) == 2


def test_destroy_all_sweeps_helpers_owned_by_this_process(monkeypatch) -> None:
    """Helpers that drifted out of the registry are still killed at shutdown."""
    destroy_all_virtual_displays()
    helper = vmod._adapter_by_id("helper")
    destroyed: list[str] = []
    monkeypatch.setattr(
        helper,
        "list_existing",
        lambda: [
            {"display_id": 111, "pid": 999999, "parent_pid": os.getpid()},
            {"display_id": 222, "pid": 999998, "parent_pid": os.getpid() + 1},
        ],
    )
    monkeypatch.setattr(helper, "destroy", lambda name: destroyed.append(name))
    destroy_all_virtual_displays()
    assert destroyed == ["111"]


def test_probe_virtual_true_only_when_adapter_can_create(monkeypatch) -> None:
    import termx.desktop.virtual as vmod

    class Capable(VirtualAdapter):
        id = "xrandr"

        def available(self) -> bool:
            return True

        def can_create(self) -> bool:
            return True

    monkeypatch.setattr(vmod, "_ADAPTERS", [Capable()])
    probe = probe_desktop()
    assert probe.virtual_display is True
    assert probe.virtual_backend == "xrandr"

    class PresentButUseless(VirtualAdapter):
        id = "gdctl"

        def available(self) -> bool:
            return True

        def can_create(self) -> bool:
            return False

    monkeypatch.setattr(vmod, "_ADAPTERS", [PresentButUseless()])
    probe = probe_desktop()
    assert probe.virtual_display is False
    assert probe.virtual_backend == "gdctl"


def test_create_virtual_display_sets_owner_and_lease() -> None:
    destroy_all_virtual_displays()
    fake = FakeAdapter()
    before = time.time()
    created = create_virtual_display(800, 600, force_adapter=fake, owner="client-a")
    listed = list_virtual_displays()
    assert listed[0]["owner"] == "client-a"
    assert listed[0]["lease_until"] >= before + 60
    rec = vmod._active[str(created["id"])]
    assert rec["owner"] == "client-a"
    assert isinstance(rec["created_at"], float)
    destroy_all_virtual_displays()


def test_touch_lease_updates_owner_and_ttl() -> None:
    destroy_all_virtual_displays()
    fake = FakeAdapter()
    created = create_virtual_display(800, 600, force_adapter=fake)
    display_id = str(created["id"])
    before = time.time()
    touch_lease(display_id, "client-b", ttl=120)
    listed = list_virtual_displays()[0]
    assert listed["owner"] == "client-b"
    assert listed["lease_until"] >= before + 120
    destroy_all_virtual_displays()


def test_expire_leases_destroys_after_grace() -> None:
    destroy_all_virtual_displays()
    fake = FakeAdapter()
    created = create_virtual_display(800, 600, force_adapter=fake, owner="client-a")
    display_id = str(created["id"])
    vmod._active[display_id]["lease_until"] = 1.0
    kept = expire_leases(now=1.0 + LEASE_GRACE_S)
    assert kept == []
    assert len(list_virtual_displays()) == 1
    expired = expire_leases(now=1.0 + LEASE_GRACE_S + 0.01)
    assert expired == [display_id]
    assert list_virtual_displays() == []
    assert fake.destroyed == [created["name"]]


def test_expire_leases_skips_unset_lease() -> None:
    destroy_all_virtual_displays()
    fake = FakeAdapter()
    created = create_virtual_display(800, 600, force_adapter=fake)
    display_id = str(created["id"])
    vmod._active[display_id]["lease_until"] = None
    assert expire_leases(now=time.time() + 10_000) == []
    assert len(list_virtual_displays()) == 1
    destroy_all_virtual_displays()


def test_adapter_registry_order() -> None:
    assert [adapter.id for adapter in vmod._ADAPTERS] == [
        "helper",
        "hyprland",
        "sway",
        "xrandr",
        "gdctl",
        "kscreen-doctor",
        "windows-idd",
    ]


def test_hyprland_adapter_unavailable_without_hyprctl() -> None:
    adapter = vmod.HyprlandAdapter()
    if shutil.which("hyprctl") is None:
        assert adapter.available() is False
        assert adapter.can_create() is False


def test_hyprland_adapter_requires_wayland_and_hyprctl(monkeypatch) -> None:
    monkeypatch.setattr(vmod.sys, "platform", "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setattr(vmod.shutil, "which", lambda name: None)
    adapter = vmod.HyprlandAdapter()
    assert adapter.available() is False
    assert adapter.can_create() is False


def test_hyprland_create_destroy_commands(monkeypatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

    monkeypatch.setattr(vmod, "_run", fake_run)
    adapter = vmod.HyprlandAdapter()
    name = adapter.create("abcd", 800, 600, 1.0, 60)
    assert name == "termx-abcd"
    adapter.destroy(name)
    assert calls == [
        ["hyprctl", "output", "create", "headless", "termx-abcd"],
        ["hyprctl", "output", "remove", "termx-abcd"],
    ]


def test_hyprland_create_error_uses_stderr(monkeypatch) -> None:
    def fake_run(argv: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom\n")

    monkeypatch.setattr(vmod, "_run", fake_run)
    try:
        vmod.HyprlandAdapter().create("x", 1, 1, 1.0, 60)
        raise AssertionError("expected VirtualDisplayError")
    except VirtualDisplayError as exc:
        assert "boom" in str(exc)


def test_active_adapter_uses_first_available(monkeypatch) -> None:
    class Missing(VirtualAdapter):
        id = "missing"

        def available(self) -> bool:
            return False

    first = FakeAdapter()
    first.id = "first"
    second = FakeAdapter()
    second.id = "second"
    monkeypatch.setattr(vmod, "_ADAPTERS", [Missing(), first, second])
    assert active_adapter() is first
    destroy_all_virtual_displays()
    created = create_virtual_display(640, 480, force_adapter=first)
    assert created["adapter"] == "first"
    destroy_all_virtual_displays()


def test_gnome_can_create_requires_virtual_help(monkeypatch) -> None:
    monkeypatch.setattr(vmod.sys, "platform", "linux")

    def no_virtual(binary: str) -> str | None:
        return "Usage: gdctl set\n"

    adapter = vmod.GnomeAdapter()
    monkeypatch.setattr(adapter, "_bin", lambda: "/usr/bin/gdctl")
    monkeypatch.setattr(vmod, "_help_text", no_virtual)
    assert adapter.available() is True
    assert adapter.can_create() is False

    def has_virtual(binary: str) -> str | None:
        return "create a virtual monitor\n"

    capable = vmod.GnomeAdapter()
    monkeypatch.setattr(capable, "_bin", lambda: "/usr/bin/gdctl")
    monkeypatch.setattr(vmod, "_help_text", has_virtual)
    assert capable.can_create() is True


def test_view_only_preference_persists(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    from termx.config import ConfigStore
    from termx.desktop.session import DesktopManager

    store = ConfigStore()
    assert DesktopManager(store).view_only is True
    store.set_view_only(False)
    assert ConfigStore().get().desktop.view_only_default is False
    assert DesktopManager(ConfigStore()).view_only is False


def test_control_message_updates_preference(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    from fastapi.testclient import TestClient

    from termx.app import AppState, create_app

    state = AppState(passcode="secret")
    client = TestClient(create_app(state, web_dir=None))
    with client.websocket_connect("/api/desktop/session?k=secret") as ws:
        ws.receive_json()
        ws.send_json({"type": "control", "view_only": False})
        assert ws.receive_json()["view_only"] is False
    state.store.__class__  # keep reference; preference is written synchronously
    from termx.config import ConfigStore

    assert ConfigStore(state.store.path).get().desktop.view_only_default is False


def test_capture_blocked_without_screen_recording(monkeypatch) -> None:
    from termx.desktop import capture

    monkeypatch.setattr(capture, "screen_recording_denied", lambda: True)
    try:
        capture.grab_jpeg(None)
    except capture.CaptureError as exc:
        assert "Screen Recording" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected CaptureError")


def test_list_displays_deduplicates_virtual_entries(monkeypatch) -> None:
    from termx.desktop import capture

    physical = [
        {"id": "111", "name": "Display 1", "kind": "physical", "width": 1920, "height": 1080, "main": True},
        {"id": "222", "name": "Display 2", "kind": "physical", "width": 1280, "height": 800},
    ]
    virtual = [
        {"id": "abc123", "name": "222", "width": 1280, "height": 800, "adapter": "helper", "x": 0, "y": 0}
    ]
    monkeypatch.setattr(capture, "list_physical_displays", lambda: physical)
    import termx.desktop.virtual as virtual_module

    monkeypatch.setattr(virtual_module, "list_virtual_displays", lambda adopt=False: virtual)
    displays = capture.list_displays()
    ids = [item["id"] for item in displays]
    assert ids == ["111", "abc123"]
    assert displays[1]["kind"] == "virtual"


def test_mac_pointer_routes_wheel_to_scroll(monkeypatch) -> None:
    from termx.desktop import input as input_module

    calls: list[object] = []
    monkeypatch.setattr(input_module, "_cg_scroll", lambda event: calls.append(event))
    monkeypatch.setattr(input_module, "probe_desktop", lambda: type("P", (), {"input_backend": "cgevent"})())
    input_module._mac_pointer(0.5, 0.5, "wheel", 1, {"dy": 3, "dx": 0}, None)
    assert calls and calls[0]["dy"] == 3


_REAL_ADOPT = vmod._adopt_helper_displays


def _write_helper_store(tmp_path, entries: list[dict]) -> None:
    store = tmp_path / "termx-virtual-displays"
    store.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (store / f"{entry['display_id']}.json").write_text(json.dumps(entry), encoding="utf-8")


def _fake_virtual_helper(tmp_path, output: str) -> str:
    script = tmp_path / "termx-virtual-display-fake"
    script.write_text(f"#!/bin/sh\nif [ \"$1\" = list ]; then\ncat <<'JSON'\n{output}\nJSON\nfi\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


# The helper is a POSIX executable (and macOS-only in production), so these
# tests only run where such a script can be executed.
needs_posix_helper = pytest.mark.skipif(
    sys.platform == "win32", reason="the virtual-display helper is a POSIX executable"
)


@needs_posix_helper
def test_virtual_displays_adopted_and_orphans_pruned(tmp_path, monkeypatch) -> None:
    import json as json_module

    from termx.desktop import virtual as virtual_module

    live = {
        "id": "live0001",
        "display_id": 424242,
        "width": 1170,
        "height": 2532,
        "refresh": 60,
        "pid": os.getpid(),
        "parent_pid": os.getpid(),
    }
    orphan = {
        "id": "orphan01",
        "display_id": 434343,
        "width": 800,
        "height": 600,
        "refresh": 60,
        "pid": 2**30,
        "parent_pid": 2**30,
    }
    outputs = "\n".join(json_module.dumps(entry) for entry in (live, orphan))
    binary = _fake_virtual_helper(tmp_path, outputs)
    monkeypatch.setenv("TERMX_VIRTUAL_DISPLAY_BIN", binary)
    monkeypatch.setattr(virtual_module, "_pid_alive", lambda pid: pid == os.getpid())
    # conftest disables adoption so real displays never leak into tests; this
    # test exercises it on purpose.
    monkeypatch.setattr(virtual_module, "_adopt_helper_displays", _REAL_ADOPT)

    adapter = virtual_module.HelperAdapter()
    existing = adapter.list_existing()
    assert [item["id"] for item in existing] == ["live0001"]

    virtual_module._active.clear()
    virtual_module._impls.clear()
    virtual_module._adopt_helper_displays(force=True)
    adopted = virtual_module.list_virtual_displays()
    assert [item["id"] for item in adopted] == ["424242"]
    assert adopted[0]["adopted"] is True
    assert adopted[0]["width"] == 1170


@needs_posix_helper
def test_virtual_display_destroy_uses_helper_cli(tmp_path, monkeypatch) -> None:
    from termx.desktop import virtual as virtual_module

    calls: list[list[str]] = []

    def fake_run(argv, timeout=20):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, "", "")

    binary = _fake_virtual_helper(tmp_path, "")
    monkeypatch.setenv("TERMX_VIRTUAL_DISPLAY_BIN", binary)
    monkeypatch.setattr(virtual_module, "_run", fake_run)
    adapter = virtual_module.HelperAdapter()
    virtual_module._active.clear()
    virtual_module._impls.clear()
    virtual_module._active["424242"] = {
        "name": "424242",
        "width": 1,
        "height": 1,
        "adapter": "helper",
    }
    virtual_module._impls["424242"] = adapter
    virtual_module.destroy_virtual_display("424242")
    assert calls and calls[0][1:] == ["destroy", "424242"]
    assert "424242" not in virtual_module._active


def test_destroy_all_skips_foreign_displays(monkeypatch) -> None:
    from termx.desktop import virtual as virtual_module

    destroyed: list[str] = []

    class Recorder(VirtualAdapter):
        id = "recorder"

        def destroy(self, name: str) -> None:
            destroyed.append(name)

    adapter = Recorder()
    virtual_module._active.clear()
    virtual_module._impls.clear()
    virtual_module._active["mine"] = {"name": "mine", "adapter": "recorder", "owner_pid": os.getpid()}
    virtual_module._active["theirs"] = {"name": "theirs", "adapter": "recorder", "owner_pid": 999999}
    virtual_module._impls["mine"] = adapter
    virtual_module._impls["theirs"] = adapter

    virtual_module.destroy_all_virtual_displays()
    assert destroyed == ["mine"]
    assert "theirs" in virtual_module._active
    virtual_module._active.clear()
    virtual_module._impls.clear()


def test_ws_display_create_and_delete(tmp_path, monkeypatch) -> None:
    """Displays are managed over the session socket, not HTTP."""
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    from fastapi.testclient import TestClient

    from termx.app import AppState, create_app
    from termx.desktop import virtual as virtual_module

    created = {"value": None}
    destroyed: list[str] = []

    class FakeAdapter(VirtualAdapter):
        id = "helper"

        def available(self) -> bool:
            return True

        def can_create(self) -> bool:
            return True

        def create(self, display_id, width, height, dpr, refresh_hz) -> str:
            created["value"] = {"id": display_id, "width": width, "height": height}
            return "424242"

        def destroy(self, name: str) -> None:
            destroyed.append(name)

    adapter = FakeAdapter()
    monkeypatch.setattr(virtual_module, "_adapter_by_id", lambda _id: adapter)
    # Patch the resolver: on machines without a real display adapter the create
    # path would otherwise fail before reaching the fake.
    monkeypatch.setattr(virtual_module, "_resolve_adapter", lambda *_args, **_kwargs: adapter)
    monkeypatch.setattr(virtual_module, "active_adapter", lambda: adapter)

    from termx.desktop import session as session_module

    def _no_capture(*_args: object, **_kwargs: object) -> bytes:
        raise RuntimeError("no capture backend in this test")

    # The frame pump reports capture failures over the socket; on headless hosts
    # that error races the control replies asserted below. A non-CaptureError
    # failure exits the pump silently instead.
    monkeypatch.setattr(session_module, "grab_jpeg", _no_capture)
    state = AppState(passcode="secret")
    client = TestClient(create_app(state, web_dir=None))
    with client.websocket_connect("/api/desktop/session?k=secret") as ws:
        ws.receive_json()
        ws.send_json({"type": "display_create", "width": 1170, "height": 2532})
        created_message = ws.receive_json()
        assert created_message["type"] == "displays"
        assert created_message["created"]["kind"] == "virtual"
        assert created_message["created"]["width"] == 1170
        display_id = created_message["created"]["id"]
        assert created["value"]["width"] == 1170

        ws.send_json({"type": "stream", "fps": 24})
        stream = ws.receive_json()
        assert stream == {"type": "stream", "fps": 24}

        ws.send_json({"type": "display_delete", "id": display_id})
        after = ws.receive_json()
        assert after["type"] == "displays"
        assert all(item["id"] != display_id for item in after["displays"])
        assert after["selected_display"] != display_id
    assert destroyed == ["424242"]


def test_ws_rtc_signalling_reports_availability(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    from fastapi.testclient import TestClient

    from termx.app import AppState, create_app

    state = AppState(passcode="secret")
    client = TestClient(create_app(state, web_dir=None))
    with client.websocket_connect("/api/desktop/session?k=secret") as ws:
        ws.receive_json()
        ws.send_json({"type": "rtc", "action": "offer", "session_id": "abc", "offer": {"type": "offer", "sdp": "v=0\r\n"}})
        reply = ws.receive_json()
        assert reply["type"] == "rtc"
        if state.rtc.available():
            assert reply["action"] == "answer"
            assert reply["answer"]["type"] == "answer"
        else:
            assert reply["ok"] is False
            assert "unavailable" in reply["error"]


def test_update_version_ordering() -> None:
    from termx.update import is_newer, parse_version

    assert parse_version("v0.1.7") == (0, 1, 7)
    assert parse_version("1.2.3") == (1, 2, 3)
    assert parse_version("0.10") > parse_version("0.9")
    assert is_newer("0.1.7", "0.1.6") is True
    assert is_newer("0.1.6", "0.1.7") is False
    assert is_newer("0.1.6", "0.1.6") is False


def test_update_check_reports_release(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    from fastapi.testclient import TestClient

    import termx.update as update_module
    from termx.app import AppState, create_app

    monkeypatch.setattr(
        update_module,
        "fetch_latest_release",
        lambda timeout=8.0: {"tag_name": "v9.9.9", "html_url": "https://example.test/v9.9.9", "body": "notes", "published_at": "2026-01-01T00:00:00Z"},
    )
    state = AppState(passcode="secret")
    client = TestClient(create_app(state, web_dir=None))
    body = client.get("/api/update/check?k=secret").json()
    assert body["available"] is True
    assert body["latest"] == "9.9.9"
    assert body["url"].endswith("v9.9.9")


def test_update_apply_without_desktop_shell(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TERMX_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.delenv("TERMX_DESKTOP", raising=False)
    from fastapi.testclient import TestClient

    import termx.update as update_module
    from termx.app import AppState, create_app

    monkeypatch.setattr(update_module, "fetch_latest_release", lambda timeout=8.0: None)
    state = AppState(passcode="secret")
    client = TestClient(create_app(state, web_dir=None))
    body = client.post("/api/update/apply?k=secret").json()
    assert body["started"] is False
    assert "instructions" in body
