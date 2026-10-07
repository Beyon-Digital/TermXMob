"""Strawberry input types — mirrors of the REST Pydantic body models.

Server-side Pydantic validation still applies where the resolver rebuilds a
body model; inputs here carry the same fields with the same defaults so the
wire contract is unchanged.
"""

from __future__ import annotations

import strawberry
from strawberry.scalars import JSON

from termx.sessions import DEFAULT_COLS, DEFAULT_ROWS


@strawberry.input
class CreateSessionInput:
    cols: int = DEFAULT_COLS
    rows: int = DEFAULT_ROWS
    title: str | None = None
    shell: str | None = None
    cwd: str | None = None
    sandbox_profile: str | None = None


@strawberry.input
class WorkspaceSessionInput:
    title: str = ""
    shell: str = ""
    cwd: str = ""


@strawberry.input
class ForwardInput:
    name: str = ""
    kind: str = "local"
    listen_host: str = "127.0.0.1"
    listen_port: int = 0
    target_host: str = "127.0.0.1"
    target_port: int = 0
    ssh_host: str = ""
    auto_start: bool = False


@strawberry.input
class ForwardPatchInput:
    name: str | None = None
    auto_start: bool | None = None


@strawberry.input
class PreferencesInput:
    shell: str | None = None
    cwd: str | None = None


@strawberry.input
class CommandInput:
    name: str
    command: str
    confirm: bool = False


@strawberry.input
class CommandPatchInput:
    name: str | None = None
    command: str | None = None
    confirm: bool | None = None


@strawberry.input
class DirectoryInput:
    name: str = ""
    path: str = ""


@strawberry.input
class TunnelProfileInput:
    provider: str
    name: str
    kind: str = "quick"
    extra: JSON | None = None


@strawberry.input
class TunnelStartInput:
    profile_id: str | None = None
    provider: str | None = None
    kind: str | None = None


@strawberry.input
class VirtualDisplayInput:
    width: int = 1170
    height: int = 2532
    dpr: float = 2.0
    refresh_hz: int = 60


@strawberry.input
class RtcOfferInput:
    offer: JSON
    session_id: str | None = None


@strawberry.input
class NotifyInput:
    title: str
    body: str = ""
    url: str | None = None


@strawberry.input
class AgentProviderInput:
    id: str
    kind: str = "openai"
    name: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = ""
    capabilities: list[str] | None = None
    api_key: str | None = None


@strawberry.input
class AgentImageInput:
    name: str = "image"
    mime: str = "image/png"
    data: str = ""


@strawberry.input
class AgentTaskInput:
    prompt: str
    cwd: str
    engine: str | None = None
    engine_mode: str | None = None
    config_options: JSON | None = None
    provider_id: str | None = None
    model: str | None = None
    attachments: list[AgentImageInput] | None = None
    limits: JSON | None = None
    mode: str = "agent"
    execution_mode: str | None = None
    conversation_id: str | None = None
    custom_agent_id: str | None = None
    turn_prompt: str | None = None
    context_refs: list[JSON] | None = None


@strawberry.input
class WorktreeActionInput:
    action: str
    confirm: bool = False


@strawberry.input
class AgentApprovalInput:
    decision: str
    remember: str | None = None
    content: JSON | None = None
    limits: JSON | None = None


@strawberry.input
class PolicyRuleInput:
    effect: str
    scope_type: str
    tool: str
    fingerprint: str
    scope_id: str | None = None
    action_type: str = "tool"
    fingerprint_kind: str = "exact"
    matcher: JSON | None = None
    capabilities: list[str] | None = None
    sandbox_profile: str | None = None
    display: str = ""
    task_id: str | None = None
    project_id: str | None = None
    expires_at: float | None = None


@strawberry.input
class PolicyRulePatchInput:
    effect: str | None = None
    expires_at: float | None = None
    display: str | None = None
    capabilities: list[str] | None = None


@strawberry.input
class AgentRetentionInput:
    retention_days: int = 30
    max_bytes: int = 500_000_000


@strawberry.input
class ConversationInput:
    title: str = ""
    project_id: str | None = None
    cwd: str | None = None
    mode: str = "ask"
    custom_agent_id: str | None = None
    provider_id: str | None = None
    model: str | None = None
    pinned: bool = False
    archived: bool = False
    draft: bool = False


@strawberry.input
class ConversationPatchInput:
    title: str | None = None
    project_id: str | None = None
    cwd: str | None = None
    mode: str | None = None
    custom_agent_id: str | None = None
    provider_id: str | None = None
    model: str | None = None
    pinned: bool | None = None
    archived: bool | None = None
    draft: bool | None = None


@strawberry.input
class ConversationTurnInput:
    prompt: str
    task_id: str | None = None
    mode: str | None = None
    provider_id: str | None = None
    model: str | None = None
    context_refs: list[JSON] | None = None
    attachment_refs: list[JSON] | None = None


@strawberry.input
class CustomAgentInput:
    name: str
    description: str = ""
    instructions: str = ""
    provider_id: str | None = None
    model: str | None = None
    engine_mode: str | None = None
    config_options: JSON | None = None
    tools: list[str] | None = None
    limits: JSON | None = None
    approval_mode: str = "standard"
    sandbox_profile: str = "agent"
    engine: str = "inherit"
    enabled: bool = True
    auto_use: bool = True
    toolsets: list[str] | None = None
    deny_tools: list[str] | None = None
    skills: JSON | None = None
    workflows: list[str] | None = None
    mcp_connections: list[JSON] | None = None
    delegation: JSON | None = None
    slug: str | None = None


@strawberry.input
class CustomAgentPatchInput:
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    provider_id: str | None = None
    model: str | None = None
    engine_mode: str | None = None
    config_options: JSON | None = None
    tools: list[str] | None = None
    tools_mode: str | None = None
    limits: JSON | None = None
    approval_mode: str | None = None
    sandbox_profile: str | None = None
    engine: str | None = None
    enabled: bool | None = None
    auto_use: bool | None = None
    toolsets: list[str] | None = None
    deny_tools: list[str] | None = None
    skills: JSON | None = None
    workflows: list[str] | None = None
    mcp_connections: list[JSON] | None = None
    delegation: JSON | None = None
    revision: str | None = None


@strawberry.input
class CustomAgentImportInput:
    markdown: str
    source: str = "import"


@strawberry.input
class McpConnectionInput:
    """Free-form MCP connection definition (validated by validate_connection)."""

    data: JSON


@strawberry.input
class ExtensionStateInput:
    enabled: bool | None = None
    trusted: bool | None = None
    note: str | None = None


@strawberry.input
class McpToolCallInput:
    arguments: JSON | None = None


@strawberry.input
class ProjectInput:
    path: str
    name: str = ""


@strawberry.input
class FileSaveInput:
    path: str
    content: str = ""
    revision: str = ""


@strawberry.input
class FileActionInput:
    action: str
    path: str
    destination: str = ""
    revision: str = ""


@strawberry.input
class FileSearchInput:
    query: str
    content: bool = True
    case_sensitive: bool = False


@strawberry.input
class GitStageInput:
    paths: list[str] | None = None
    stage: bool = True


@strawberry.input
class GitHunkInput:
    patch: str
    stage: bool = True


@strawberry.input
class GitBranchInput:
    name: str
    create: bool = False


@strawberry.input
class PreviewInput:
    name: str = ""
    url: str = ""


@strawberry.input
class PreviewFromPortInput:
    port: int
    name: str = ""


@strawberry.input
class RunbookInput:
    name: str
    steps: list[JSON]
    project_id: str | None = None


@strawberry.input
class RunbookPatchInput:
    name: str | None = None
    project_id: str | None = None
    steps: list[JSON] | None = None


@strawberry.input
class PairInput:
    device_name: str = ""
    scopes: list[str] | None = None
    expires_in_s: int | None = None


@strawberry.input
class PermissionsInput:
    which: list[str] | None = None
