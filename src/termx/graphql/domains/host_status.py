"""Host health domain: the authenticated ``host_status`` query, its change stream and the durability settings.

The unauthenticated ``health`` query is unchanged. Everything here needs ``machine-view`` (a managed
principal additionally needs ``host-admin``, like the other host-wide streams); changing a setting needs
``host-admin``. No scope was added.
"""

from __future__ import annotations

import strawberry
from fastapi import HTTPException
from strawberry.types import Info

from termx.graphql import types as T  # noqa: F401  (keeps DictBacked import order stable)
from termx.graphql.context import TermxContext
from termx.graphql.errors import fail, resolver
from termx.graphql.types import DictBacked

Ctx = Info[TermxContext, None]


@strawberry.type
class HostServiceState(DictBacked):
    """The per-user supervisor. ``state``: ``running_managed`` (this host is the service), ``installed_not_managing``
    (a service is installed but this host was started another way) or ``not_installed``."""

    backend: str
    supported: bool
    installed: bool
    managed: bool
    state: str


@strawberry.type
class HostHolderState(DictBacked):
    """The detached PTY holder. ``mode`` is ``held`` (terminals survive a restart) or ``direct`` (they do not);
    ``reason`` says why whenever direct was not asked for. ``sessions`` is live terminals the holder owns,
    ``direct_terminals`` live terminals that would end with this host."""

    mode: str
    requested: str
    reason: str | None
    running: bool
    pid: int | None
    version: str | None
    protocol: int | None
    sessions: int
    exited_sessions: int
    draining: bool
    direct_terminals: int


@strawberry.type
class HostRecoveryState(DictBacked):
    recovering_tasks: int
    running_tasks: int
    lost_cli_sessions: int
    resumable_cli_sessions: int
    live_cli_sessions: int
    lost_engine_sessions: int
    resumable_engine_sessions: int


@strawberry.type
class HostKeepAwakeState(DictBacked):
    """``enabled`` is the owner's setting; ``active`` is whether the OS inhibitor is held right now, for ``reasons``."""

    enabled: bool
    active: bool
    supported: bool
    backend: str | None
    reasons: list[str]
    count: int
    error: str | None


@strawberry.type
class HostHeartbeat(DictBacked):
    """The heartbeat file (``heartbeat.json`` in the config directory) is rewritten every ``interval_s`` seconds."""

    at: float | None
    age_s: float | None
    interval_s: float
    file: str
    error: str | None


@strawberry.type
class HostStatus(DictBacked):
    version: str
    pid: int
    started_at: float
    uptime_s: float

    @strawberry.field
    def service(self) -> HostServiceState:
        return HostServiceState.wrap(object.__getattribute__(self, "_d")["service"])  # type: ignore[return-value]

    @strawberry.field
    def holder(self) -> HostHolderState:
        return HostHolderState.wrap(object.__getattribute__(self, "_d")["holder"])  # type: ignore[return-value]

    @strawberry.field
    def recovery(self) -> HostRecoveryState:
        return HostRecoveryState.wrap(object.__getattribute__(self, "_d")["recovery"])  # type: ignore[return-value]

    @strawberry.field
    def keep_awake(self) -> HostKeepAwakeState:
        return HostKeepAwakeState.wrap(object.__getattribute__(self, "_d")["keep_awake"])  # type: ignore[return-value]

    @strawberry.field
    def heartbeat(self) -> HostHeartbeat:
        return HostHeartbeat.wrap(object.__getattribute__(self, "_d")["heartbeat"])  # type: ignore[return-value]


@strawberry.input
class HostSettingsInput:
    """Only the fields you send change. ``pty_holder`` is ``auto``, ``held`` or ``direct`` and applies to new terminals."""

    keep_awake: bool | None = None
    pty_holder: str | None = None


def host_status_service(ctx: TermxContext):
    service = getattr(ctx.state, "host_status", None)
    if service is None:
        fail(503, "Host status is unavailable")
    return service


@strawberry.type
class HostStatusQueries:
    @strawberry.field
    @resolver
    def host_status(self, info: Ctx) -> HostStatus:
        ctx = info.context
        ctx.require_host("machine-view")
        return HostStatus.wrap(host_status_service(ctx).snapshot())  # type: ignore[return-value]


@strawberry.type
class HostStatusMutations:
    @strawberry.mutation
    @resolver
    def update_host_settings(self, info: Ctx, input: HostSettingsInput) -> HostStatus:
        ctx = info.context
        ctx.require_host("host-admin")
        try:
            snapshot = host_status_service(ctx).update_settings(keep_awake=input.keep_awake, pty_holder=input.pty_holder)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return HostStatus.wrap(snapshot)  # type: ignore[return-value]
