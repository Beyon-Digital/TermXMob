"""Desktop control: is the accessibility-tree program available, install or remove the pinned copy, choose a program path.

Every operation needs ``host-admin``: the status holds a filesystem path and the machine's permission state, and install,
remove and the path setting change what runs with the desktop permission. There is no agent tool for any of this.
"""

from __future__ import annotations

import asyncio
from typing import Any

import strawberry
from strawberry.types import Info

from termx.audit import log_event
from termx.desktop import agent_desktop as ad
from termx.desktop import agent_desktop_install as installer
from termx.desktop.agent_desktop_setting import env_locked, save_setting, valid_path
from termx.graphql import types as T
from termx.graphql.context import TermxContext
from termx.graphql.errors import fail, resolver

Ctx = Info[TermxContext, None]

_INSTALL_STATUS = {"pin_incomplete": 409, "busy": 409}


@strawberry.type
class DesktopControlPermission(T.DictBacked):
    """`state`: granted, denied, unknown, not_required or unavailable. `guidance` is the next step in plain words."""

    state: str
    guidance: str
    grant_to: str | None


@strawberry.type
class DesktopControlIssue(T.DictBacked):
    code: str
    message: str
    action: str


@strawberry.type
class DesktopControlInstallInfo(T.DictBacked):
    """What a managed install would download. `note` says how the download is checked."""

    available: bool
    asset: str | None
    size_bytes: int | None
    host: str
    note: str


@strawberry.type
class DesktopControlStatus(T.DictBacked):
    installed: bool
    source: str | None
    path: str | None
    version: str | None
    pinned_version: str
    verified: bool
    helper_ok: bool | None
    platform: str
    arch: str
    platform_supported: bool
    platform_reason: str | None
    envelope_version: str | None
    ready: bool
    permission: DesktopControlPermission
    supported_surfaces: list[str]
    issues: list[DesktopControlIssue]
    install: DesktopControlInstallInfo
    configured_path: str | None
    setting_locked: bool


@strawberry.input
class DesktopControlInstallInput:
    """`version` must be the pinned version the status showed. `consent` is the owner's confirmation of the download."""

    version: str
    consent: bool


def _view(client: ad.AgentDesktopClient, status: ad.DesktopStatus) -> DesktopControlStatus:
    asset = client.asset
    can_install = bool(asset and client.pin_complete() and status.platform_supported and status.platform_reason is None)
    data: dict[str, Any] = {
        "installed": status.installed, "source": status.source, "path": status.path, "version": status.version,
        "pinned_version": status.pinned_version, "verified": status.verified, "helper_ok": status.helper_ok,
        "platform": status.platform, "arch": status.arch, "platform_supported": status.platform_supported,
        "platform_reason": status.platform_reason, "envelope_version": status.envelope_version, "ready": status.ready,
        "permission": DesktopControlPermission.wrap({
            "state": status.permission.get("state", "unknown"), "guidance": status.permission.get("guidance", ""),
            "grant_to": status.permission.get("grant_to"),
        }),
        "supported_surfaces": list(status.supported_surfaces),
        "issues": DesktopControlIssue.wrap_all(status.issues),
        "install": DesktopControlInstallInfo.wrap({
            "available": can_install, "asset": asset.get("asset") if can_install and asset else None,
            "size_bytes": int(asset["size"]) if can_install and asset else None, "host": "github.com",
            "note": installer.CHECKSUM_NOTE,
        }),
        "configured_path": client.setting, "setting_locked": env_locked(),
    }
    return DesktopControlStatus.wrap(data)  # type: ignore[return-value]


@strawberry.type
class DesktopControlQueries:
    @strawberry.field
    @resolver
    async def desktop_control_status(self, info: Ctx, refresh: bool = False) -> DesktopControlStatus:
        """Whether agents can control other applications here. `refresh` looks again instead of using the last result."""
        info.context.require("host-admin")
        client = ad.get_client()
        return _view(client, await client.status(refresh=refresh))


@strawberry.type
class DesktopControlMutations:
    @strawberry.mutation
    @resolver
    async def desktop_control_install(self, info: Ctx, input: DesktopControlInstallInput) -> DesktopControlStatus:
        """Download the pinned release into TermX's data directory and check it against the built-in checksums."""
        info.context.require("host-admin")
        client = ad.get_client()
        if not input.consent:
            fail(400, "Confirm the download before installing.")
        if input.version != client.version:
            fail(409, f"This version of TermX installs desktop control {client.version}, not {input.version}. Reload and try again.")
        try:
            await asyncio.to_thread(installer.make_installer(client).install)
        except installer.InstallError as exc:
            fail(_INSTALL_STATUS.get(exc.code, 502), {"message": str(exc), "issue": exc.code})
        log_event("desktop_control.install", version=client.version, source="managed")
        return _view(client, await client.status(refresh=True))

    @strawberry.mutation
    @resolver
    async def desktop_control_remove(self, info: Ctx) -> DesktopControlStatus:
        """Delete the managed copy and stop anything still running from it. Other sources are left alone."""
        info.context.require("host-admin")
        client = ad.get_client()
        try:
            removed = await asyncio.to_thread(installer.make_installer(client).remove)
        except installer.InstallError as exc:
            fail(500, {"message": str(exc), "issue": exc.code})
        log_event("desktop_control.remove", version=client.version, removed=removed)
        return _view(client, await client.status(refresh=True))

    @strawberry.mutation
    @resolver
    async def desktop_control_set_path(self, info: Ctx, path: str | None = None) -> DesktopControlStatus:
        """Use the program at an absolute path instead of the managed copy. It must answer the version and helper
        checks. It is not checked against the pinned hash, so it runs unverified with the desktop permission.
        An empty path clears the setting."""
        info.context.require("host-admin")
        if env_locked():
            fail(409, "The program path is set by the TERMX_AGENT_DESKTOP environment variable. Change it there.")
        client = ad.get_client()
        value = (path or "").strip() or None
        if value is not None:
            if valid_path(value) is None:
                fail(400, {"message": "The path is not usable.", "issue": "configured_invalid"})
            issues = await client.validate_setting(value)
            if issues:
                fail(400, {"message": f"{issues[0]['message']} {issues[0]['action']}".strip(), "issue": issues[0]["code"]})
        await asyncio.to_thread(save_setting, client.home_root, value)
        client.setting = value
        log_event("desktop_control.path_set", configured=value is not None)
        return _view(client, await client.status(refresh=True))
