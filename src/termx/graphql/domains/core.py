"""Core host domain: health, connect info, machine, pairing, devices, audit,
notifications, permissions, launch-at-login, update, shutdown."""

from __future__ import annotations

import hmac
import os
from time import time
from typing import Any

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx import __version__, notify
from termx.audit import log_event, read_events
from termx.desktop.permissions import permission_snapshot, request_permissions
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.graphql.inputs import NotifyInput, PairInput, PermissionsInput
from termx.graphql import types as T
from termx.machine import machine_snapshot
from termx.net import connect_url, http_urls

Ctx = Info[TermxContext, None]


def _connect_target(state: Any) -> tuple[str, str | None]:
    tunnel = state.tunnels.status_public()
    urls = http_urls(state.port)
    remote = next(
        (
            url
            for url in urls
            if not url.startswith("http://127.") and not url.startswith("http://localhost")
        ),
        None,
    )
    target = tunnel.get("url") or remote or (
        urls[0] if urls else f"http://127.0.0.1:{state.port}"
    )
    return str(target), tunnel.get("url")


@strawberry.type
class CoreQueries:
    @strawberry.field
    @resolver
    def health(self, info: Ctx) -> T.Health:
        # Unauthenticated: keep the payload minimal. Clients gate features on
        # capabilities here (machine detail lives behind auth in /api/machine).
        state = info.context.state
        capabilities = dict(
            machine_snapshot(state.store, webrtc=state.rtc.available())["capabilities"]
        )
        capabilities["agent_credentials"] = state.credentials.available()
        return T.Health.wrap(
            {
                "app": "termx",
                "ok": True,
                "version": __version__,
                "passcode_required": state.auth.required,
                "capabilities": capabilities,
            }
        )

    @strawberry.field
    @resolver
    def connect_info(self, info: Ctx) -> T.ConnectInfo:
        ctx = info.context
        ctx.require("host-admin")
        state = ctx.state
        target, tunnel_url = _connect_target(state)
        return T.ConnectInfo.wrap(
            {
                "urls": http_urls(state.port),
                "tunnel_url": tunnel_url,
                "passcode": state.auth.passcode,
                "connect_url": connect_url(target, state.auth.passcode),
                "qr_svg": "/api/connect/qr.svg",
            }
        )

    @strawberry.field
    @resolver
    def machine(self, info: Ctx) -> T.MachineInfo:
        ctx = info.context
        ctx.require("machine-view")
        state = ctx.state
        return T.MachineInfo.wrap(
            machine_snapshot(
                state.store, state.tunnels.status_public(), webrtc=state.rtc.available()
            )
        )

    @strawberry.field
    @resolver
    def permissions(self, info: Ctx) -> JSON:
        info.context.require("machine-view")
        return permission_snapshot()

    @strawberry.field
    @resolver
    def launch_at_login(self, info: Ctx) -> T.LaunchAtLogin:
        info.context.require("machine-view")
        from termx.desktop import broker as desktop_broker

        details = desktop_broker.autostart()
        if details is None:
            return T.LaunchAtLogin.wrap({"managed": False, "enabled": None})
        return T.LaunchAtLogin.wrap({"managed": True, "enabled": bool(details.get("enabled"))})

    @strawberry.field
    @resolver
    def update_check(self, info: Ctx) -> T.UpdateInfo:
        info.context.require("host-admin")
        from termx.update import check_for_update

        return T.UpdateInfo.wrap(check_for_update(__version__))

    @strawberry.field
    @resolver
    def devices(self, info: Ctx) -> list[T.DeviceInfo]:
        info.context.require("host-admin")
        return T.DeviceInfo.wrap_all(info.context.state.tokens.list_public())

    @strawberry.field
    @resolver
    def audit(self, info: Ctx, limit: int = 100) -> list[JSON]:
        info.context.require("host-admin")
        return read_events(limit)

    @strawberry.field
    @resolver
    def notifications(self, info: Ctx, limit: int = 100, unread: bool = False) -> list[T.HostNotification]:
        info.context.require("machine-view")
        return T.HostNotification.wrap_all(
            info.context.state.notifications.list(limit=limit, unread=unread)
        )

    @strawberry.field
    @resolver
    def notification_unread_count(self, info: Ctx) -> int:
        info.context.require("machine-view")
        return info.context.state.notifications.unread_count()


@strawberry.type
class CoreMutations:
    @strawberry.mutation
    @resolver
    def notify(self, info: Ctx, input: NotifyInput) -> T.Ok:
        info.context.require("machine-view")
        notify.notify(input.title, input.body, kind="info", url=input.url)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def request_permissions(self, info: Ctx, input: PermissionsInput | None = None) -> JSON:
        ctx = info.context
        ctx.require("host-admin")
        which = (
            input.which
            if input is not None and input.which is not None
            else ["screen_recording", "accessibility"]
        )
        from termx.desktop import broker as desktop_broker

        if not desktop_broker.available():
            for item in which:
                if item in {"screen_recording", "accessibility", "open_settings"}:
                    notify.permission(item)
        log_event("permissions_request", which=",".join(which))
        return request_permissions(which)

    @strawberry.mutation
    @resolver
    def set_launch_at_login(self, info: Ctx, enabled: bool) -> T.LaunchAtLogin:
        info.context.require("host-admin")
        from termx.desktop import broker as desktop_broker

        details = desktop_broker.set_autostart(enabled)
        if details is None:
            raise HTTPException(
                status_code=503,
                detail="launch at login is managed by the Termx app on this machine",
            )
        log_event("launch_at_login", enabled=enabled)
        return T.LaunchAtLogin.wrap({"managed": True, "enabled": bool(details.get("enabled"))})

    @strawberry.mutation
    @resolver
    def apply_update(self, info: Ctx) -> T.UpdateInfo:
        info.context.require("host-admin")
        from termx.update import check_for_update, desktop_managed

        if not desktop_managed():
            details = check_for_update(__version__)
            return T.UpdateInfo.wrap(
                {
                    "started": False,
                    "reason": "this host runs the backend without the desktop app",
                    "instructions": "install the new release, or update the termx package",
                    **details,
                }
            )
        notify.update("install")
        log_event("update_apply", version=__version__)
        return T.UpdateInfo.wrap({"started": True})

    @strawberry.mutation
    @resolver
    def shutdown(self, info: Ctx) -> T.Ok:
        ctx = info.context
        ctx.require("host-admin")
        if os.environ.get("TERMX_DESKTOP") != "1":
            raise HTTPException(status_code=404, detail="not found")
        client = ctx.client_host()
        if client not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(status_code=403, detail="loopback only")
        callback = ctx.state.request_shutdown
        if not callable(callback):
            raise HTTPException(status_code=503, detail="shutdown unavailable")
        log_event("shutdown", source="desktop")
        callback()
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def pair(self, info: Ctx, input: PairInput | None = None) -> T.PairResult:
        ctx = info.context
        state = ctx.state
        if state.auth.passcode is not None and not hmac.compare_digest(
            ctx.secret or "", state.auth.passcode
        ):
            raise HTTPException(status_code=401, detail="invalid passcode")
        input = input or PairInput()
        expires_at = time() + input.expires_in_s if input.expires_in_s else None
        token = state.tokens.issue(
            input.scopes, device_name=input.device_name, expires_at=expires_at
        )
        scopes = state.tokens.check(token) or []
        log_event("pair", device_name=input.device_name, scopes=scopes)
        return T.PairResult.wrap(
            {"token": token, "scopes": scopes, "expires_at": expires_at}
        )

    @strawberry.mutation
    @resolver
    def revoke_device(self, info: Ctx, device_id: str) -> T.Ok:
        ctx = info.context
        ctx.require("host-admin")
        if not ctx.state.tokens.revoke(device_id):
            raise HTTPException(status_code=404, detail="device not found")
        log_event("device_revoke", device_id=device_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def set_device_scopes(self, info: Ctx, device_id: str, scopes: list[str]) -> T.DeviceInfo:
        ctx = info.context
        ctx.require("host-admin")
        device = ctx.state.tokens.update_scopes(device_id, scopes)
        if device is None:
            raise HTTPException(status_code=404, detail="device not found")
        log_event("device_scopes", device_id=device_id, scopes=device["scopes"])
        return T.DeviceInfo.wrap(device)

    @strawberry.mutation
    @resolver
    def publish_notification(self, info: Ctx, input: NotifyInput, kind: str = "info") -> T.HostNotification:
        ctx = info.context
        ctx.require("machine-view")
        item = ctx.state.notifications.publish(
            input.title, input.body, kind=kind, url=input.url, source="api"
        )
        return T.HostNotification.wrap(item)

    @strawberry.mutation
    @resolver
    def mark_notifications_read(self, info: Ctx, ids: list[str]) -> int:
        info.context.require("machine-view")
        return info.context.state.notifications.mark_read(ids)

    @strawberry.mutation
    @resolver
    def mark_all_notifications_read(self, info: Ctx) -> int:
        info.context.require("machine-view")
        return info.context.state.notifications.mark_all_read()
