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

/** What discovery learned about one model. Unknown values are `null` and are never shown as zero. */
export interface ModelInfo {
  context_window?: number | null; max_output?: number | null;
  /** USD per million tokens. */
  price_in?: number | null; price_out?: number | null;
  capabilities?: string[] | null; source?: string | null; fetched_at?: number | null;
}
/** Values the person set by hand; they win over discovered ones. */
export type ModelOverride = Partial<Pick<ModelInfo, 'context_window' | 'max_output' | 'price_in' | 'price_out'>>;
export type ModelOverrides = Record<string, ModelOverride>;

/** schema.graphql `AgentProvider`. The catalog fields are JSON scalars written by model discovery. */
export interface AgentProvider {
  id: string; kind: string; name: string;
  base_url?: string | null; model?: string | null; models?: string[] | null; capabilities?: string[] | null;
  secret_configured?: boolean | null; created_at?: number | null; updated_at?: number | null;
  model_catalog?: Record<string, ModelInfo> | null; model_overrides?: ModelOverrides | null;
  catalog_refreshed_at?: number | null; catalog_error?: string | null; wire?: string | null;
}

/** What ProviderAccountForm shows: the saved provider without its secret. */
export interface ProviderAccount {
  id: string; name: string; kind: string; base_url: string; model: string; capabilities: string[];
  model_catalog: Record<string, ModelInfo>; model_overrides: ModelOverrides;
  catalog_refreshed_at: number | null; catalog_error: string | null; wire?: string | null;
}
/** What ProviderAccountForm submits; matches `AgentProviderInput`. `model` lists the included models, empty meaning all. */
export interface ProviderAccountInput {
  id: string; name: string; kind: string; base_url: string; model: string; capabilities: string[]; api_key: string | null;
  model_overrides?: ModelOverrides; wire?: 'responses' | 'chat';
}

export function providerAccount(provider: AgentProvider): ProviderAccount {
  return {
    id: provider.id, name: provider.name, kind: provider.kind, base_url: provider.base_url ?? '', model: provider.model ?? '', capabilities: provider.capabilities ?? [],
    model_catalog: provider.model_catalog ?? {}, model_overrides: provider.model_overrides ?? {},
    catalog_refreshed_at: provider.catalog_refreshed_at ?? null, catalog_error: provider.catalog_error ?? null, wire: provider.wire ?? null,
  };
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
  /** Who the note applies to. Rows saved before scopes existed omit these and count as active notes the user wrote. */
  scope?: 'user' | 'project' | 'agent'; agent_id?: string | null; author?: 'user' | 'agent'; state?: 'active' | 'proposed';
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
