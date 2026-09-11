import { useCallback, useEffect, useState } from "react";
import { Alert, Clipboard, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { AppSheet } from "@/components/app-sheet";
import { useAppTheme } from "@/hooks/use-app-theme";
import { fetchTunnels, startTunnel, stopTunnel } from "@/lib/api";
import { httpBase, type Connection, type TunnelProviderInfo, type TunnelRuntime } from "@/lib/types";

type Props = {
  connection: Connection;
  isPresented: boolean;
  onDismiss: () => void;
};

type SheetStatus = {
  provider?: string | null;
  state?: string;
  url?: string | null;
  detail?: string | null;
  log?: string[];
  started_at?: number | null;
  uptime_s?: number | null;
};

function formatUptime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const rem = s % 60;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${rem}s`;
  return `${rem}s`;
}

async function restartTunnel(connection: Connection): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (connection.passcode) headers["X-Termx-Passcode"] = connection.passcode;
  const res = await fetch(`${httpBase(connection)}/api/tunnels/restart`, {
    method: "POST",
    headers,
    body: "{}",
  });
  if (res.status === 401) throw new Error("Wrong passcode");
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (typeof body.detail === "string" && body.detail) detail = body.detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
}

export function TunnelSheet({ connection, isPresented, onDismiss }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const [runtime, setRuntime] = useState<TunnelRuntime | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const reload = useCallback(async () => {
    try {
      setRuntime(await fetchTunnels(connection));
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load tunnels");
    }
  }, [connection]);

  useEffect(() => {
    if (isPresented) void reload();
  }, [isPresented, reload]);

  const active = runtime?.active as SheetStatus | null | undefined;
  const connected = active?.state === "connected";
  const logs = Array.isArray(active?.log) ? active.log : [];
  const uptimeS = typeof active?.uptime_s === "number" ? active.uptime_s : null;

  const start = async (provider: TunnelProviderInfo) => {
    setBusy(provider.id);
    setError("");
    try {
      const kind = provider.id === "cloudflare" ? "quick" : provider.id === "ngrok" ? "http" : "funnel";
      await startTunnel(connection, { provider: provider.id, kind });
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Start failed");
    } finally {
      setBusy(null);
    }
  };

  const copyUrl = async (url: string) => {
    try {
      if (Platform.OS === "web") {
        await navigator.clipboard.writeText(url);
        return;
      }
      Clipboard.setString(url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not copy URL");
    }
  };

  const restart = async () => {
    setBusy("restart");
    setError("");
    try {
      await restartTunnel(connection);
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Restart failed");
    } finally {
      setBusy(null);
    }
  };

  const stop = async () => {
    const currentUrl = active?.url;
    const usingCurrent =
      currentUrl &&
      (httpBase(connection).startsWith(currentUrl) || currentUrl.includes(connection.host));
    const go = async () => {
      setBusy("stop");
      try {
        await stopTunnel(connection);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Stop failed");
      } finally {
        setBusy(null);
      }
    };
    if (usingCurrent) {
      const message = "This client may be using the tunnel. Stop it anyway?";
      if (Platform.OS === "web") {
        if (globalThis.confirm?.(message)) void go();
        return;
      }
      Alert.alert("Stop tunnel?", message, [
        { text: "Cancel", style: "cancel" },
        { text: "Stop", style: "destructive", onPress: () => void go() },
      ]);
      return;
    }
    await go();
  };

  return (
    <AppSheet title="Tunnels" isPresented={isPresented} onDismiss={onDismiss}>
      <View style={[styles.status, { backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}>
        <Text style={[styles.statusLabel, { color: ui.textMuted }]}>Current route</Text>
        <Text style={[styles.statusValue, { color: ui.text }]}>
          {connected ? `${active?.provider} · connected` : "No tunnel running"}
        </Text>
        {active?.url ? (
          <Text selectable style={[styles.url, { color: ui.accent }]}>
            {active.url}
          </Text>
        ) : null}
        {uptimeS != null ? (
          <Text style={[styles.uptime, { color: ui.textMuted }]}>Uptime {formatUptime(uptimeS)}</Text>
        ) : null}
        <View style={styles.actions}>
          {active?.url ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Copy URL"
              style={[styles.action, { borderColor: ui.border }]}
              onPress={() => void copyUrl(active.url!)}
              disabled={busy !== null}>
              <Text style={{ color: ui.accent, fontWeight: "700" }}>Copy URL</Text>
            </Pressable>
          ) : null}
          {connected ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Restart tunnel"
              style={[styles.action, { borderColor: ui.border }]}
              onPress={() => void restart()}
              disabled={busy !== null}>
              <Text style={{ color: ui.text, fontWeight: "700" }}>{busy === "restart" ? "Restarting…" : "Restart"}</Text>
            </Pressable>
          ) : null}
          {connected ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Stop tunnel"
              style={[styles.action, { borderColor: ui.danger }]}
              onPress={() => void stop()}
              disabled={busy !== null}>
              <Text style={{ color: ui.danger, fontWeight: "700" }}>{busy === "stop" ? "Stopping…" : "Stop tunnel"}</Text>
            </Pressable>
          ) : null}
        </View>
        {connected || logs.length > 0 ? (
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: detailsOpen }}
            onPress={() => setDetailsOpen((open) => !open)}>
            <Text style={{ color: ui.accent, fontWeight: "700" }}>{detailsOpen ? "Hide details" : "Details"}</Text>
          </Pressable>
        ) : null}
        {detailsOpen ? (
          <Text selectable style={[styles.log, { color: ui.textMuted }]}>
            {logs.length ? logs.join("\n") : "No log lines"}
          </Text>
        ) : null}
      </View>
      {(runtime?.providers ?? []).map((provider) => (
        <View key={provider.id} style={[styles.card, { borderColor: ui.border, backgroundColor: ui.surfaceAlt }]}>
          <Text style={[styles.name, { color: ui.text }]}>{provider.name}</Text>
          <Text style={{ color: ui.textMuted, fontSize: 13 }}>
            {provider.available ? "Ready on this machine" : `Not installed. ${provider.install ?? ""}`}
          </Text>
          <Pressable
            style={[
              styles.primary,
              { backgroundColor: provider.available ? ui.accent : ui.border, opacity: busy ? 0.6 : 1 },
            ]}
            disabled={!provider.available || busy !== null}
            onPress={() => void start(provider)}>
            <Text style={[styles.primaryLabel, { color: provider.available ? ui.accentText : ui.textMuted }]}>
              {busy === provider.id ? "Starting…" : `Start ${provider.name}`}
            </Text>
          </Pressable>
        </View>
      ))}
      {error ? <Text style={{ color: ui.danger }}>{error}</Text> : null}
    </AppSheet>
  );
}

const styles = StyleSheet.create({
  status: { borderWidth: 1, borderRadius: 14, padding: 14, gap: 6 },
  statusLabel: { fontSize: 12, fontWeight: "700", textTransform: "uppercase", letterSpacing: 0.6 },
  statusValue: { fontSize: 16, fontWeight: "700" },
  url: { fontSize: 13 },
  uptime: { fontSize: 13 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6 },
  action: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 12, paddingVertical: 8 },
  log: { fontSize: 12, marginTop: 4 },
  card: { borderWidth: 1, borderRadius: 14, padding: 14, gap: 8 },
  name: { fontSize: 16, fontWeight: "700" },
  primary: { borderRadius: 10, paddingVertical: 11, alignItems: "center" },
  primaryLabel: { fontWeight: "700" },
});
