import shutil
import subprocess
import time

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


def test_virtual_display_is_explicitly_unsupported() -> None:
    adapter = active_adapter()
    if adapter is not None and adapter.can_create():
        created = create_virtual_display(800, 600)
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
