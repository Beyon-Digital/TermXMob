import {
  httpBase,
  wsBase,
  type Connection,
  type DisplayInfo,
  type Health,
  type MachineInfo,
  type SavedCommand,
  type SessionInfo,
  type TunnelRuntime,
  type TunnelStatus,
} from "@/lib/types";

function headers(connection: Connection): Record<string, string> {
  const next: Record<string, string> = { "Content-Type": "application/json" };
  if (connection.passcode) next["X-Termx-Passcode"] = connection.passcode;
  return next;
}

function connectHint(connection: Connection): string {
  const base = httpBase(connection);
  if (connection.host === "127.0.0.1" || connection.host === "localhost") {
    return `${base} — on a phone use the laptop LAN IP from the Termx QR, not 127.0.0.1`;
  }
  return base;
}

async function pull(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    throw new Error(`Could not connect to ${url}. Same Wi‑Fi as the laptop? ${detail}`);
  }
}

async function readError(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: string };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    /* ignore */
  }
  return `Request failed (${res.status})`;
}

async function request<T>(connection: Connection, path: string, init?: RequestInit): Promise<T> {
  const res = await pull(`${httpBase(connection)}${path}`, {
    ...init,
    headers: { ...headers(connection), ...(init?.headers as Record<string, string> | undefined) },
  });
  if (res.status === 401) throw new Error("Wrong passcode");
  if (!res.ok) throw new Error(await readError(res));
  return (await res.json()) as T;
}

export async function fetchHealth(connection: Connection): Promise<Health> {
  const res = await pull(`${httpBase(connection)}/api/health`);
  if (!res.ok) throw new Error(`Cannot reach ${connectHint(connection)}`);
  return (await res.json()) as Health;
}

export async function fetchMachine(connection: Connection): Promise<MachineInfo> {
  return request<MachineInfo>(connection, "/api/machine");
}

export async function listSessions(connection: Connection): Promise<SessionInfo[]> {
  const body = await request<{ sessions: SessionInfo[] }>(connection, "/api/sessions");
  return body.sessions;
}

export async function createSession(
  connection: Connection,
  cols: number,
  rows: number,
): Promise<SessionInfo> {
  return request<SessionInfo>(connection, "/api/sessions", {
    method: "POST",
    body: JSON.stringify({ cols, rows }),
  });
}

export async function killSession(connection: Connection, id: string): Promise<void> {
  await request(connection, `/api/sessions/${id}`, { method: "DELETE" });
}

export async function fetchPreferences(
  connection: Connection,
): Promise<{ shell: string; cwd: string; shells: string[] }> {
  return request(connection, "/api/preferences");
}

export async function savePreferences(
  connection: Connection,
  body: { shell?: string; cwd?: string },
): Promise<{ shell: string; cwd: string }> {
  return request(connection, "/api/preferences", { method: "PUT", body: JSON.stringify(body) });
}

export async function listCommands(connection: Connection): Promise<SavedCommand[]> {
  const body = await request<{ commands: SavedCommand[] }>(connection, "/api/commands");
  return body.commands;
}

export async function createCommand(
  connection: Connection,
  body: { name: string; command: string; confirm?: boolean },
): Promise<SavedCommand> {
  return request(connection, "/api/commands", { method: "POST", body: JSON.stringify(body) });
}

export async function deleteCommand(connection: Connection, id: string): Promise<void> {
  await request(connection, `/api/commands/${id}`, { method: "DELETE" });
}

export async function fetchTunnels(connection: Connection): Promise<TunnelRuntime> {
  return request(connection, "/api/tunnels");
}

export async function startTunnel(
  connection: Connection,
  body: { profile_id?: string; provider?: string; kind?: string } = {},
): Promise<TunnelStatus> {
  return request(connection, "/api/tunnels/start", { method: "POST", body: JSON.stringify(body) });
}

export async function stopTunnel(connection: Connection): Promise<TunnelStatus> {
  return request(connection, "/api/tunnels/stop", { method: "POST", body: "{}" });
}

export async function fetchDisplays(
  connection: Connection,
): Promise<{ displays: DisplayInfo[]; view_only: boolean; virtual_display_reason?: string }> {
  return request(connection, "/api/displays");
}

export async function createVirtualDisplay(
  connection: Connection,
  body: { width: number; height: number; dpr?: number },
): Promise<DisplayInfo> {
  return request(connection, "/api/displays/virtual", { method: "POST", body: JSON.stringify(body) });
}

export function ptySocketUrl(connection: Connection, sessionId: string): string {
  const k = connection.passcode ? `?k=${encodeURIComponent(connection.passcode)}` : "";
  return `${wsBase(connection)}/api/sessions/${sessionId}/pty${k}`;
}

export function desktopSocketUrl(connection: Connection): string {
  const k = connection.passcode ? `?k=${encodeURIComponent(connection.passcode)}` : "";
  return `${wsBase(connection)}/api/desktop/session${k}`;
}

export async function rtcOffer(
  connection: Connection,
  body: { offer: object; session_id?: string },
): Promise<{ answer: object; session_id: string }> {
  return request(connection, "/api/desktop/rtc/offer", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function rtcIce(
  connection: Connection,
  body: { session_id: string; candidate: object },
): Promise<void> {
  await request(connection, "/api/desktop/rtc/ice", {
    method: "POST",
    body: JSON.stringify(body),
  });
}
