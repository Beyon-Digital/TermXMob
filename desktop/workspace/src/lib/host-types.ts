// Host entities used by the settings surfaces. GraphQL shapes mirror schema.graphql
// (EngineDescriptor, AgentProvider, HostCustomAgent, CustomAgentInput); the REST
// shapes mirror the /auth and /api/workspace/memory responses.

export interface EngineAuthMethod { id: string; name?: string; label?: string }
export interface EngineProtocol { name?: string; auth_methods?: unknown }

/** schema.graphql `EngineDescriptor`. `protocol` and `capabilities` are JSON scalars. */
export interface EngineDescriptor {
  id: string; label: string; installed: boolean;
  executable?: string | null; version?: string | null; auth_state?: string | null; auth_detail?: string | null;
  location?: string | null; transport?: string | null; protocol?: EngineProtocol | null; error?: string | null;
  capabilities?: unknown;
}

/** schema.graphql `AgentProvider`. */
export interface AgentProvider {
  id: string; kind: string; name: string;
  base_url?: string | null; model?: string | null; models?: string[] | null; capabilities?: string[] | null;
  secret_configured?: boolean | null; created_at?: number | null; updated_at?: number | null;
}

/** Shape ProviderAccountForm edits; `AgentProviderInput` minus the secret. */
export interface ProviderAccount { id: string; name: string; kind: string; base_url: string; model: string; capabilities: string[] }

export function providerAccount(provider: AgentProvider): ProviderAccount {
  return { id: provider.id, name: provider.name, kind: provider.kind, base_url: provider.base_url ?? '', model: provider.model ?? '', capabilities: provider.capabilities ?? [] };
}

/** schema.graphql `HostCustomAgent`. JSON scalars are narrowed to what the editor reads. */
export interface AgentPreset {
  id: string; name: string;
  description?: string | null; instructions?: string | null; provider_id?: string | null; model?: string | null;
  engine?: string | null; enabled?: boolean | null; auto_use?: boolean | null;
  tools?: string[] | null; tools_mode?: string | null; toolsets?: string[] | null; deny_tools?: string[] | null;
  limits?: Partial<Record<string, number>> | null; mcp_connections?: { connection: string; tools: string[] }[] | null;
  approval_mode?: string | null; sandbox_profile?: string | null;
  source?: string | null; sync_state?: string | null; file_path?: string | null; file_revision?: string | null;
  created_at?: number | null; updated_at?: number | null;
}

/** Fields the preset editor submits; matches `CustomAgentInput` / `CustomAgentPatchInput`. */
export interface AgentPresetInput {
  name: string; description: string; instructions: string; engine: string; model: string;
  mcp_connections: { connection: string; tools: string[] }[]; toolsets: string[]; deny_tools: string[];
  limits: Record<string, number>; approval_mode: string; sandbox_profile: string; enabled: boolean;
  tools?: string[]; tools_mode?: 'all' | 'explicit'; revision?: string;
}

export interface MemoryRow {
  id: string; revision: number; content: string; provenance: string;
  project_id?: string | null; retention_days: number; excluded?: boolean; expires_at?: number;
}

export interface HostIdentity { principal?: { id: string; display_name: string; scopes?: string[] }; session_id?: string; host_id?: string }
export interface AuthSession { id: string; device_name?: string | null; strength?: string; expires?: number; revoked?: boolean }
export interface AdminPrincipal { id: string; display_name: string; role?: string | null; enabled?: boolean; trusted_execution?: boolean }
export interface AuthAdapter { id: string; label: string; enabled: boolean; health?: string; version?: string | number }
export interface AccessSummary { role?: string; runtime_boundary?: string }
export interface AdapterConfiguration { configuration?: unknown }

/** Everything the Managers surface may hold for the active section. */
export interface ManagerData {
  engines?: EngineDescriptor[]; agent_providers?: AgentProvider[]; acp_registry?: unknown;
  custom_agents?: AgentPreset[]; memory?: MemoryRow[];
  access?: AccessSummary; sessions?: AuthSession[]; methods?: unknown;
  principals?: AdminPrincipal[]; adapters?: AuthAdapter[]; configuration?: AdapterConfiguration;
  identityProjects?: { id: string; name: string }[];
}
