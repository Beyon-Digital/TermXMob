"""Strawberry object types for the termx GraphQL schema.

Field names intentionally stay snake_case (``auto_camel_case=False`` on the
schema) so GraphQL payloads match the REST payloads they replace — the
frontend's existing types map field-for-field.

Handlers already return plain dicts; ``DictBacked`` lets those dicts resolve
as typed GraphQL objects without per-shape converters. Nested dynamic
sub-objects ride as ``JSON`` scalars.
"""

from __future__ import annotations

from typing import Any, Self

import strawberry
from strawberry.scalars import JSON


class DictBacked:
    """Wrap a dict so strawberry's attribute-based field resolution works.

    Declare fields with bare annotations only (no defaults) — a dataclass
    default would become a class attribute and shadow ``__getattr__``.
    """

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return object.__getattribute__(self, "_d").get(name)

    @classmethod
    def wrap(cls, data: dict[str, Any] | None) -> Self | None:
        if data is None:
            return None
        self = cls.__new__(cls)
        object.__setattr__(self, "_d", data)
        return self

    @classmethod
    def wrap_all(cls, items: list[dict[str, Any]] | None) -> list[Self]:
        return [cls.wrap(item) for item in (items or [])]  # type: ignore[misc]


def wrap_list(cls: type[DictBacked], items: list[dict[str, Any]] | None) -> list[Any]:
    return cls.wrap_all(items)


# -- Core ------------------------------------------------------------------


@strawberry.type
class Health(DictBacked):
    app: str | None
    ok: bool
    version: str | None
    passcode_required: bool | None
    capabilities: JSON | None
    tunnel: JSON | None


@strawberry.type
class ConnectInfo(DictBacked):
    urls: list[str]
    tunnel_url: str | None
    passcode: str
    connect_url: str
    qr_svg: str | None


@strawberry.type
class MachineInfo(DictBacked):
    hostname: str
    os: str
    arch: str | None
    shells: list[str]
    terminal: JSON | None
    permissions: JSON | None
    helper: JSON | None
    capabilities: JSON | None
    tunnel: JSON | None
    user: str | None


@strawberry.type
class LaunchAtLogin(DictBacked):
    managed: bool
    enabled: bool | None


@strawberry.type
class UpdateInfo(DictBacked):
    started: bool | None
    reason: str | None
    instructions: str | None
    current: str | None
    latest: str | None
    tag: str | None
    available: bool | None
    status: str | None
    url: str | None
    notes: str | None
    published_at: str | None


@strawberry.type
class PairResult(DictBacked):
    token: str
    scopes: list[str]
    expires_at: float | None


@strawberry.type
class DeviceInfo(DictBacked):
    id: str
    scopes: list[str]
    device_name: str
    created_at: float | None
    last_seen: float | None
    expires_at: float | None


@strawberry.type
class AuditEvent(DictBacked):
    ts: float | None
    event: str | None
    fields: JSON | None


# -- Sessions / workspace ----------------------------------------------------


@strawberry.type
class SessionInfo(DictBacked):
    id: str
    title: str
    created_at: float
    cols: int
    rows: int
    exited: bool
    exit_code: int | None
    cwd: str | None
    live_cwd: str | None
    shell: str | None


@strawberry.type
class WorkspaceSession(DictBacked):
    title: str | None
    shell: str | None
    cwd: str | None


@strawberry.type
class Workspace(DictBacked):
    @strawberry.field
    def sessions(self) -> list[WorkspaceSession]:
        return WorkspaceSession.wrap_all(object.__getattribute__(self, '_d').get('sessions') or [])
    saved_at: float | None


@strawberry.type
class WorkspaceRestoreResult(DictBacked):
    @strawberry.field
    def sessions(self) -> list[SessionInfo]:
        return SessionInfo.wrap_all(object.__getattribute__(self, '_d').get('sessions') or [])
    restored: int
    already_running: int


@strawberry.type
class Preferences(DictBacked):
    shell: str | None
    cwd: str | None
    shells: list[str] | None


@strawberry.type
class SavedCommand(DictBacked):
    id: str
    name: str
    command: str
    confirm: bool | None
    order: int | None
    created_at: float | None
    updated_at: float | None


@strawberry.type
class SavedDirectory(DictBacked):
    id: str
    name: str
    path: str
    order: int | None
    created_at: float | None
    updated_at: float | None


@strawberry.type
class DirectoriesResult(DictBacked):
    cwd: str | None
    @strawberry.field
    def directories(self) -> list[SavedDirectory]:
        return SavedDirectory.wrap_all(object.__getattribute__(self, '_d').get('directories') or [])


@strawberry.type
class UseDirectoryResult(DictBacked):
    @strawberry.field
    def directory(self) -> SavedDirectory | None:
        return SavedDirectory.wrap(object.__getattribute__(self, '_d').get('directory'))
    cwd: str | None


# -- Files / projects / git ---------------------------------------------------


@strawberry.type
class FsListing(DictBacked):
    path: str
    parent: str | None
    home: str | None
    entries: JSON | None


@strawberry.type
class Project(DictBacked):
    id: str
    path: str
    name: str
    updated_at: float | None


@strawberry.type
class ProjectTree(DictBacked):
    path: str
    entries: JSON | None
    next_offset: int | None
    total: int | None


@strawberry.type
class ProjectFile(DictBacked):
    path: str
    size: int | None
    editable: bool | None
    content: str | None
    revision: str | None
    reason: str | None
    encoding: str | None
    line_ending: str | None


@strawberry.type
class FileActionResult(DictBacked):
    ok: bool | None
    path: str | None
    name: str | None
    size: int | None
    revision: str | None


@strawberry.type
class SearchResult(DictBacked):
    path: str
    line: int | None
    text: str | None
    revision: str | None


@strawberry.type
class SearchResults(DictBacked):
    @strawberry.field
    def results(self) -> list[SearchResult]:
        return SearchResult.wrap_all(object.__getattribute__(self, '_d').get('results') or [])
    truncated: bool | None


@strawberry.type
class GitFile(DictBacked):
    path: str
    index: str | None
    worktree: str | None
    staged: bool | None
    untracked: bool | None


@strawberry.type
class GitStatus(DictBacked):
    repo: bool
    branch: str | None
    @strawberry.field
    def files(self) -> list[GitFile]:
        return GitFile.wrap_all(object.__getattribute__(self, '_d').get('files') or [])
    ahead: int | None
    behind: int | None


@strawberry.type
class GitResult(DictBacked):
    ok: bool | None
    staged: list[str] | None
    unstaged: list[str] | None
    commit: str | None
    branch: str | None
    error: str | None
    output: str | None


@strawberry.type
class ProjectPreview(DictBacked):
    id: str
    name: str
    url: str


@strawberry.type
class PreviewFromPortResult(DictBacked):
    preview: JSON | None
    listener: JSON | None


# -- Ports / processes / activity ----------------------------------------------


@strawberry.type
class PortListener(DictBacked):
    port: int
    address: str | None
    pid: int | None
    name: str | None
    command: str | None
    url: str | None
    is_http: bool | None
    protocol: str | None
    project_id: str | None


@strawberry.type
class PortsResult(DictBacked):
    @strawberry.field
    def ports(self) -> list[PortListener]:
        return PortListener.wrap_all(object.__getattribute__(self, '_d').get('ports') or [])
    supported: bool


@strawberry.type
class ProcessInfo(DictBacked):
    pid: int
    name: str | None
    command: str | None
    cwd: str | None
    cpu: float | None
    mem: float | None
    project_id: str | None


@strawberry.type
class ProcessesResult(DictBacked):
    @strawberry.field
    def processes(self) -> list[ProcessInfo]:
        return ProcessInfo.wrap_all(object.__getattribute__(self, '_d').get('processes') or [])
    supported: bool


@strawberry.type
class ActivityItem(DictBacked):
    id: str
    kind: str
    project_id: str | None
    title: str
    state: str | None
    started_at: float | None
    updated_at: float | None
    actions: JSON | None

    @strawberry.field
    def extra(self) -> JSON | None:
        # activity_snapshot flattens per-kind extras (url, mode, shell, ...)
        # into top-level keys — regroup them so they stay reachable.
        d = object.__getattribute__(self, '_d')
        core = {"id", "kind", "project_id", "title", "state",
                "started_at", "updated_at", "actions"}
        extra = {k: v for k, v in d.items() if k not in core}
        return extra or None


@strawberry.type
class ActivitySnapshot(DictBacked):
    @strawberry.field
    def activity(self) -> list[ActivityItem]:
        return ActivityItem.wrap_all(object.__getattribute__(self, '_d').get('activity') or [])


# -- Network (tunnels / forwards) ----------------------------------------------


@strawberry.type
class TunnelProviderInfo(DictBacked):
    id: str
    name: str
    available: bool
    binary: str | None
    kinds: list[str] | None
    install: str | None


@strawberry.type
class TunnelProfile(DictBacked):
    id: str
    provider: str
    name: str
    kind: str
    extra: JSON | None


@strawberry.type
class TunnelStatus(DictBacked):
    provider: str | None
    state: str | None
    url: str | None
    detail: str | None
    log: list[str] | None
    started_at: float | None
    uptime_s: int | None


@strawberry.type
class TunnelRuntime(DictBacked):
    @strawberry.field
    def providers(self) -> list[TunnelProviderInfo]:
        return TunnelProviderInfo.wrap_all(object.__getattribute__(self, '_d').get('providers') or [])
    @strawberry.field
    def profiles(self) -> list[TunnelProfile]:
        return TunnelProfile.wrap_all(object.__getattribute__(self, '_d').get('profiles') or [])
    active_profile_id: str | None
    runtime: JSON | None
    active: JSON | None


@strawberry.type
class ForwardRule(DictBacked):
    id: str
    name: str
    kind: str
    listen_host: str
    listen_port: int
    target_host: str
    target_port: int
    ssh_host: str | None
    auto_start: bool | None
    created_at: float | None


@strawberry.type
class ForwardStatus(DictBacked):
    id: str
    state: str
    detail: str | None
    started_at: float | None
    uptime_s: float | None
    log: list[str] | None


@strawberry.type
class ForwardsResult(DictBacked):
    @strawberry.field
    def rules(self) -> list[ForwardRule]:
        return ForwardRule.wrap_all(object.__getattribute__(self, '_d').get('rules') or [])
    @strawberry.field
    def statuses(self) -> list[ForwardStatus]:
        return ForwardStatus.wrap_all(object.__getattribute__(self, '_d').get('statuses') or [])


# -- Desktop / displays ---------------------------------------------------------


@strawberry.type
class DisplayInfo(DictBacked):
    id: str
    name: str | None
    kind: str | None
    width: int | None
    height: int | None
    x: int | None
    y: int | None
    main: bool | None
    backend: str | None
    output: str | None
    detail: str | None
    selected: bool | None


@strawberry.type
class DisplaysResult(DictBacked):
    @strawberry.field
    def displays(self) -> list[DisplayInfo]:
        return DisplayInfo.wrap_all(object.__getattribute__(self, '_d').get('displays') or [])
    selected_display: str | None
    view_only: bool | None
    permissions: JSON | None
    viewer_count: int | None
    fps: int | None
    target_fps: int | None
    last_capture_ms: float | None
    virtual_display_reason: str | None


@strawberry.type
class RtcAnswer(DictBacked):
    session_id: str | None
    answer: JSON | None


# -- Agent ---------------------------------------------------------------------


@strawberry.type
class AgentProvider(DictBacked):
    id: str
    kind: str
    name: str
    base_url: str | None
    model: str | None
    models: list[str] | None
    capabilities: list[str] | None
    secret_configured: bool | None
    created_at: float | None
    updated_at: float | None


@strawberry.type
class ProviderTestResult(DictBacked):
    ok: bool | None
    detail: str | None


@strawberry.type
class EngineDescriptor(DictBacked):
    id: str
    label: str
    installed: bool
    executable: str | None
    version: str | None
    auth_state: str | None
    auth_detail: str | None
    location: str | None
    transport: str | None
    protocol: JSON | None
    error: str | None
    capabilities: JSON | None


@strawberry.type
class EngineDiagnostics(DictBacked):
    report: JSON


@strawberry.type
class AgentEvent(DictBacked):
    id: str | None
    task_id: str | None
    sequence: int | None
    type: str
    payload: JSON | None
    created_at: float | None


@strawberry.type
class AgentApproval(DictBacked):
    id: str
    task_id: str | None
    kind: str | None
    status: str
    payload: JSON | None
    created_at: float | None
    resolved_at: float | None


@strawberry.type
class AgentArtifact(DictBacked):
    id: str
    task_id: str | None
    kind: str | None
    mime: str | None
    size: int | None
    digest: str | None
    created_at: float | None


@strawberry.type
class AgentTask(DictBacked):
    id: str
    prompt: str
    cwd: str | None
    provider_id: str | None
    engine: str | None
    engine_session_id: str | None
    engine_native_id: str | None
    mode: str | None
    model: str | None
    status: str
    limits: JSON | None
    plan: JSON | None
    result: str | None
    error: str | None
    execution_mode: str | None
    conversation_id: str | None
    custom_agent_id: str | None
    parent_id: str | None
    runtime: JSON | None
    metrics: JSON | None
    created_at: float | None
    updated_at: float | None
    events: JSON | None
    approvals: JSON | None
    artifacts: JSON | None


@strawberry.type
class TaskWorktree(DictBacked):
    task_id: str
    mode: str | None
    base_repo: str | None
    base_ref: str | None
    base_branch: str | None
    worktree_path: str | None
    branch: str | None
    head_sha: str | None
    status: str | None
    dirty: list[str] | None
    diff: JSON | None
    created_at: float | None
    updated_at: float | None


@strawberry.type
class AgentStorageStatus(DictBacked):
    bytes: int | None
    artifact_bytes: int | None
    database_bytes: int | None
    task_count: int | None
    retention_days: int | None
    max_bytes: int | None


@strawberry.type
class AgentStoragePruneResult(DictBacked):
    removed: list[str] | None
    @strawberry.field
    def storage(self) -> AgentStorageStatus | None:
        return AgentStorageStatus.wrap(object.__getattribute__(self, '_d').get('storage'))


@strawberry.type
class PolicyRule(DictBacked):
    id: str
    scope_type: str | None
    scope_id: str | None
    effect: str | None
    matcher: JSON | None
    created_at: float | None
    expires_at: float | None
    revoked_at: float | None


@strawberry.type
class EffectivePolicies(DictBacked):
    @strawberry.field
    def rules(self) -> list[PolicyRule]:
        return PolicyRule.wrap_all(object.__getattribute__(self, '_d').get('rules') or [])
    approval_mode: str | None
    sandbox_profile: str | None
    sandbox_capabilities: list[str] | None


# -- Conversations / custom agents / extensions / MCP ---------------------------


@strawberry.type
class HostConversation(DictBacked):
    id: str
    title: str | None
    project_id: str | None
    cwd: str | None
    mode: str | None
    custom_agent_id: str | None
    provider_id: str | None
    model: str | None
    pinned: bool | None
    archived: bool | None
    draft: bool | None
    created_at: float | None
    updated_at: float | None
    turns: JSON | None


@strawberry.type
class ConversationTurn(DictBacked):
    id: str
    conversation_id: str | None
    sequence: int | None
    task_id: str | None
    prompt: str | None
    mode: str | None
    provider_id: str | None
    model: str | None
    context_refs: JSON | None
    attachment_refs: JSON | None
    created_at: float | None


@strawberry.type
class HostCustomAgent(DictBacked):
    id: str
    name: str
    description: str | None
    instructions: str | None
    provider_id: str | None
    model: str | None
    engine_mode: str | None
    config_options: JSON | None
    tools: list[str] | None
    limits: JSON | None
    engine: str | None
    enabled: bool | None
    auto_use: bool | None
    tools_mode: str | None
    toolsets: list[str] | None
    deny_tools: list[str] | None
    skills: JSON | None
    workflows: list[str] | None
    mcp_connections: JSON | None
    delegation: JSON | None
    source: str | None
    sync_state: str | None
    file_path: str | None
    file_revision: str | None
    approval_mode: str | None
    sandbox_profile: str | None
    created_at: float | None
    updated_at: float | None


@strawberry.type
class CustomAgentFile(DictBacked):
    id: str
    slug: str | None
    markdown: str | None


@strawberry.type
class ExtensionEntry(DictBacked):
    qid: str
    kind: str | None
    id: str | None
    slug: str | None
    name: str | None
    description: str | None
    path: str | None
    source: str | None
    trusted: bool | None
    compat: bool | None
    enabled: bool | None
    authorized: bool | None
    digest: str | None
    errors: list[str] | None
    body: str | None
    body_error: str | None


@strawberry.type
class ExtensionsResult(DictBacked):
    @strawberry.field
    def extensions(self) -> list[ExtensionEntry]:
        return ExtensionEntry.wrap_all(object.__getattribute__(self, '_d').get('extensions') or [])
    report: JSON | None


@strawberry.type
class ExtensionState(DictBacked):
    qid: str | None
    enabled: bool | None
    trusted: bool | None
    note: str | None


@strawberry.type
class McpConnectionInfo(DictBacked):
    id: str
    label: str | None
    transport: str | None
    command: list[str] | None
    url: str | None
    enabled: bool | None
    owner: str | None
    allowed_projects: list[str] | None
    definition: JSON | None
    definition_digest: str | None
    credential_configured: bool | None
    catalog: JSON | None
    auth: JSON | None
    secret_refs: list[str] | None
    approved_tools: list[str] | None
    trust: str | None
    lan: bool | None
    runtime: JSON | None
    auth_url_pending: str | None
    path: str | None


@strawberry.type
class McpConnectResult(DictBacked):
    connection: str | None
    status: str
    auth_url: str | None
    note: str | None
    catalog: JSON | None
    fingerprint: str | None


@strawberry.type
class McpToolResult(DictBacked):
    result: JSON | None


# -- Runbooks --------------------------------------------------------------------


@strawberry.type
class Runbook(DictBacked):
    id: str
    name: str
    project_id: str | None
    steps: JSON | None
    created_at: float | None
    updated_at: float | None


@strawberry.type
class RunbookRun(DictBacked):
    id: str
    runbook_id: str | None
    status: str
    step_index: int | None
    step: int | None
    error: str | None
    steps: JSON | None
    created_at: float | None
    updated_at: float | None


@strawberry.type
class RunbookDetail(DictBacked):
    @strawberry.field
    def runbook(self) -> Runbook | None:
        return Runbook.wrap(object.__getattribute__(self, '_d').get('runbook'))
    @strawberry.field
    def runs(self) -> list[RunbookRun]:
        return RunbookRun.wrap_all(object.__getattribute__(self, '_d').get('runs') or [])


# -- Notifications / events ------------------------------------------------------


@strawberry.type
class HostNotification(DictBacked):
    id: str
    kind: str
    title: str
    body: str | None
    url: str | None
    level: str | None
    source: str | None
    task_id: str | None
    read: bool
    created_at: float


@strawberry.type
class TaskEventEnvelope(DictBacked):
    """One agent-task event or a heartbeat, matching the old WS protocol."""

    sequence: int
    type: str
    task_id: str | None
    payload: JSON | None
    created_at: float | None


@strawberry.type
class ActivityEvent(DictBacked):
    """``activity.upsert`` (activity set) or ``activity.remove`` (id set)."""

    type: str
    id: str | None
    @strawberry.field
    def activity(self) -> ActivityItem | None:
        return ActivityItem.wrap(object.__getattribute__(self, '_d').get('activity'))


# -- Generic results --------------------------------------------------------------


@strawberry.type
class Ok(DictBacked):
    ok: bool


@strawberry.type
class Deleted(DictBacked):
    deleted: str | None
