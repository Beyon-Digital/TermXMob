"""Network + desktop domain: tunnels, forwards, displays, RTC signaling,
activity snapshot."""

from __future__ import annotations

import uuid

import strawberry
from fastapi import HTTPException
from strawberry.scalars import JSON
from strawberry.types import Info

from termx import notify
from termx.audit import log_event
from termx.desktop.capture import virtual_display_reason
from termx.desktop.virtual import (
    VirtualDisplayError,
    create_virtual_display,
    destroy_virtual_display,
)
from termx.desktop.webrtc import RtcError
from termx.graphql.context import TermxContext
from termx.graphql.errors import resolver
from termx.graphql.inputs import (
    ForwardInput,
    ForwardPatchInput,
    RtcOfferInput,
    TunnelProfileInput,
    TunnelStartInput,
    VirtualDisplayInput,
)
from termx.graphql import types as T

Ctx = Info[TermxContext, None]


@strawberry.type
class NetworkQueries:
    @strawberry.field
    @resolver
    def tunnels(self, info: Ctx) -> T.TunnelRuntime:
        info.context.require("network-manage")
        return T.TunnelRuntime.wrap(info.context.state.tunnels.runtime())

    @strawberry.field
    @resolver
    def forwards(self, info: Ctx) -> T.ForwardsResult:
        ctx = info.context
        ctx.require("network-manage")
        return T.ForwardsResult.wrap(
            {
                "rules": [rule.public() for rule in ctx.state.store.list_rules()],
                "statuses": ctx.state.forwards.statuses(),
            }
        )

    @strawberry.field
    @resolver
    def displays(self, info: Ctx) -> T.DisplaysResult:
        ctx = info.context
        ctx.require("desktop-view")
        return T.DisplaysResult.wrap(
            {
                **ctx.state.desktop.snapshot(),
                "virtual_display_reason": virtual_display_reason(),
            }
        )

    @strawberry.field
    @resolver
    def activity(self, info: Ctx) -> T.ActivitySnapshot:
        info.context.require("machine-view")
        from termx.activity import activity_snapshot

        return T.ActivitySnapshot.wrap(activity_snapshot(info.context.state))


@strawberry.type
class NetworkMutations:
    @strawberry.mutation
    @resolver
    def create_tunnel_profile(self, info: Ctx, input: TunnelProfileInput) -> T.TunnelProfile:
        ctx = info.context
        ctx.require("network-manage")
        if input.provider not in {"cloudflare", "ngrok", "tailscale"}:
            raise HTTPException(status_code=400, detail="unknown provider")
        profile = ctx.state.store.add_profile(
            input.provider, input.name, input.kind, input.extra
        )
        return T.TunnelProfile.wrap(profile.public())

    @strawberry.mutation
    @resolver
    def delete_tunnel_profile(self, info: Ctx, profile_id: str) -> T.Ok:
        ctx = info.context
        ctx.require("network-manage")
        if not ctx.state.store.delete_profile(profile_id):
            raise HTTPException(status_code=404, detail="profile not found")
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    async def start_tunnel(self, info: Ctx, input: TunnelStartInput | None = None) -> T.TunnelStatus:
        ctx = info.context
        ctx.require("network-manage")
        input = input or TunnelStartInput()
        profile = None
        if input.profile_id:
            profile = ctx.state.store.get_profile(input.profile_id)
            if profile is None:
                raise HTTPException(status_code=404, detail="profile not found")
        elif input.provider:
            profile = ctx.state.store.add_profile(
                input.provider,
                input.provider,
                input.kind
                or (
                    "quick"
                    if input.provider == "cloudflare"
                    else "http"
                    if input.provider == "ngrok"
                    else "funnel"
                ),
            )
        if profile is None:
            status = await ctx.state.tunnels.start_quick_cloudflare()
        else:
            status = await ctx.state.tunnels.start(profile)
        if status.state == "error":
            notify.notify("Tunnel failed", status.detail or "tunnel failed", kind="error")
            ctx.state.notifications.publish(
                "Tunnel failed", status.detail or "tunnel failed", kind="error", source="tunnel"
            )
            raise HTTPException(status_code=400, detail=status.detail or "tunnel failed")
        log_event("tunnel_start", provider=status.provider, state=status.state)
        if status.state == "connected" and status.url:
            notify.notify("Tunnel connected", status.url, kind="success", url=status.url)
            ctx.state.notifications.publish(
                "Tunnel connected", status.url, kind="success", url=status.url, source="tunnel"
            )
        return T.TunnelStatus.wrap(status.public())

    @strawberry.mutation
    @resolver
    async def stop_tunnel(self, info: Ctx) -> T.TunnelStatus:
        ctx = info.context
        ctx.require("network-manage")
        status = await ctx.state.tunnels.stop()
        log_event("tunnel_stop", state=status.state)
        return T.TunnelStatus.wrap(status.public())

    @strawberry.mutation
    @resolver
    async def restart_tunnel(self, info: Ctx) -> T.TunnelStatus:
        ctx = info.context
        ctx.require("network-manage")
        status = await ctx.state.tunnels.restart()
        if status.state == "error":
            notify.notify("Tunnel failed", status.detail or "tunnel failed", kind="error")
            ctx.state.notifications.publish(
                "Tunnel failed", status.detail or "tunnel failed", kind="error", source="tunnel"
            )
            raise HTTPException(status_code=400, detail=status.detail or "tunnel failed")
        log_event("tunnel_restart", provider=status.provider, state=status.state)
        if status.state == "connected" and status.url:
            notify.notify("Tunnel connected", status.url, kind="success", url=status.url)
            ctx.state.notifications.publish(
                "Tunnel connected", status.url, kind="success", url=status.url, source="tunnel"
            )
        return T.TunnelStatus.wrap(status.public())

    @strawberry.mutation
    @resolver
    def create_forward(self, info: Ctx, input: ForwardInput) -> T.ForwardRule:
        ctx = info.context
        ctx.require("network-manage")
        try:
            rule = ctx.state.store.add_rule(
                name=input.name,
                kind=input.kind,
                listen_port=input.listen_port,
                target_port=input.target_port,
                ssh_host=input.ssh_host,
                listen_host=input.listen_host,
                target_host=input.target_host,
                auto_start=input.auto_start,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        log_event(
            "forward_create",
            rule_id=rule.id,
            forward_kind=rule.kind,
            listen_port=rule.listen_port,
        )
        return T.ForwardRule.wrap(rule.public())

    @strawberry.mutation
    @resolver
    def patch_forward(self, info: Ctx, rule_id: str, input: ForwardPatchInput) -> T.ForwardRule:
        ctx = info.context
        ctx.require("network-manage")
        rule = ctx.state.store.patch_rule(
            rule_id, auto_start=input.auto_start, name=input.name
        )
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        return T.ForwardRule.wrap(rule.public())

    @strawberry.mutation
    @resolver
    def delete_forward(self, info: Ctx, rule_id: str) -> T.Ok:
        ctx = info.context
        ctx.require("network-manage")
        ctx.state.forwards.stop(rule_id)
        if not ctx.state.store.delete_rule(rule_id):
            raise HTTPException(status_code=404, detail="rule not found")
        log_event("forward_delete", rule_id=rule_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def start_forward(self, info: Ctx, rule_id: str) -> T.ForwardStatus:
        ctx = info.context
        ctx.require("network-manage")
        rule = ctx.state.store.get_rule(rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="rule not found")
        status = ctx.state.forwards.start(rule)
        log_event("forward_start", rule_id=rule_id, state=status.state)
        return T.ForwardStatus.wrap(status.public())

    @strawberry.mutation
    @resolver
    def stop_forward(self, info: Ctx, rule_id: str) -> T.ForwardStatus:
        ctx = info.context
        ctx.require("network-manage")
        if ctx.state.store.get_rule(rule_id) is None:
            raise HTTPException(status_code=404, detail="rule not found")
        ctx.state.forwards.stop(rule_id)
        log_event("forward_stop", rule_id=rule_id)
        return T.ForwardStatus.wrap(ctx.state.forwards.status_for(rule_id).public())

    @strawberry.mutation
    @resolver
    def create_virtual_display(self, info: Ctx, input: VirtualDisplayInput) -> JSON:
        ctx = info.context
        ctx.require("desktop-control")
        try:
            created = create_virtual_display(
                input.width, input.height, input.dpr, input.refresh_hz
            )
        except VirtualDisplayError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        log_event("display_create", display_id=created.get("id"), adapter=created.get("adapter"))
        return created

    @strawberry.mutation
    @resolver
    def delete_display(self, info: Ctx, display_id: str) -> T.Ok:
        ctx = info.context
        ctx.require("desktop-control")
        try:
            destroy_virtual_display(display_id)
        except VirtualDisplayError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        log_event("display_destroy", display_id=display_id)
        return T.Ok.wrap({"ok": True})

    @strawberry.mutation
    @resolver
    def rtc_offer(self, info: Ctx, input: RtcOfferInput) -> JSON:
        ctx = info.context
        ctx.require("desktop-view")
        session_id = input.session_id or uuid.uuid4().hex[:12]
        try:
            return ctx.state.rtc.handle_offer(session_id, input.offer)
        except RtcError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @strawberry.mutation
    @resolver
    def rtc_ice(self, info: Ctx, session_id: str, candidate: JSON) -> T.Ok:
        ctx = info.context
        ctx.require("desktop-view")
        ctx.state.rtc.add_ice(session_id, candidate)
        return T.Ok.wrap({"ok": True})
