import type { Connection } from "@/lib/types";

export function parseConnectTarget(raw: string): Connection | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const normalized = trimmed.includes("://")
      ? trimmed.replace(/^termx:/i, "http:")
      : `http://${trimmed}`;
    const url = new URL(normalized);
    if (!url.hostname) return null;
    const protocol = url.protocol === "https:" ? "https" : "http";
    const port = url.port ? Number(url.port) : protocol === "https" ? 443 : 80;
    if (!Number.isFinite(port)) return null;
    return {
      protocol,
      host: url.hostname,
      port,
      passcode: url.searchParams.get("k") || url.searchParams.get("passcode") || "",
    };
  } catch {
    return null;
  }
}

export function connectionToHostInput(c: Connection): string {
  const def = c.protocol === "https" ? 443 : 80;
  const port = c.port === def ? "" : `:${c.port}`;
  return `${c.host}${port}`;
}
