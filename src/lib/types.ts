export type Connection = {
  protocol: "http" | "https";
  host: string;
  port: number;
  passcode: string;
};

export type SavedConnection = Connection & {
  id: string;
  savedAt: number;
};

export function connectionId(c: Connection): string {
  return `${c.protocol}://${c.host}:${c.port}`;
}

export type SessionInfo = {
  id: string;
  title: string;
  created_at: number;
  cols: number;
  rows: number;
  exited: boolean;
  exit_code: number | null;
  cwd?: string;
  shell?: string;
};

export type SavedDirectory = {
  id: string;
  name: string;
  path: string;
  order: number;
  created_at: number;
  updated_at: number;
};

export type FsListing = {
  path: string;
  parent: string | null;
  home: string;
  entries: { name: string; path: string }[];
};

export type Capabilities = {
  saved_commands: boolean;
  saved_directories?: boolean;
  session_defaults: boolean;
  dynamic_tunnels: boolean;
  remote_screen: boolean;
  virtual_display: boolean;
  webrtc: boolean;
  providers: Record<string, boolean>;
};

export type Health = {
  ok: boolean;
  version?: string;
  passcode_required: boolean;
  hostname?: string;
  os?: string;
  capabilities?: Capabilities;
  tunnel?: TunnelStatus;
};

export type TunnelStatus = {
  provider?: string | null;
  state?: string;
  url?: string | null;
  detail?: string | null;
};

export type SavedCommand = {
  id: string;
  name: string;
  command: string;
  confirm: boolean;
  order: number;
  created_at: number;
  updated_at: number;
};

export type TunnelProviderInfo = {
  id: string;
  name: string;
  available: boolean;
  binary?: string | null;
  kinds: string[];
  install?: string;
};

export type TunnelProfile = {
  id: string;
  provider: string;
  name: string;
  kind: string;
  extra: Record<string, unknown>;
};

export type TunnelRuntime = {
  providers: TunnelProviderInfo[];
  profiles: TunnelProfile[];
  active_profile_id: string | null;
  runtime: Record<string, TunnelStatus>;
  active: TunnelStatus | null;
};

export type DisplayInfo = {
  id: string;
  name: string;
  kind: "physical" | "virtual" | string;
  width: number;
  height: number;
  x?: number;
  y?: number;
  main?: boolean;
  backend?: string | null;
  output?: string | null;
  detail?: string | null;
  selected?: boolean;
};

export type MachineInfo = {
  hostname: string;
  os: string;
  arch?: string;
  shells: string[];
  terminal: { shell: string; cwd: string };
  capabilities: Capabilities;
  tunnel?: TunnelStatus | null;
};

export type WorkspaceMode = "terminal" | "desktop";

export function httpBase(c: Connection): string {
  const def = c.protocol === "https" ? 443 : 80;
  const port = c.port === def ? "" : `:${c.port}`;
  return `${c.protocol}://${c.host}${port}`;
}

export function wsBase(c: Connection): string {
  const proto = c.protocol === "https" ? "wss" : "ws";
  const def = c.protocol === "https" ? 443 : 80;
  const port = c.port === def ? "" : `:${c.port}`;
  return `${proto}://${c.host}${port}`;
}

let current: Connection | null = null;

export function setCurrentConnection(c: Connection | null): void {
  current = c;
}

export function getCurrentConnection(): Connection | null {
  return current;
}
