import { useCallback, useEffect, useState } from "react";
import { Alert, Clipboard, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/app-icon";
import {
  AppSheet,
  SheetButton,
  SheetError,
  SheetSection,
  SheetStatusPill,
} from "@/components/app-sheet";
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
    <AppSheet
      title="Tunnels"
      subtitle={connected ? `${active?.provider} · connected` : "Expose this machine to the internet"}
      isPresented={isPresented}
      onDismiss={onDismiss}>
      <SheetSection label="Status">
        <View style={styles.hero}>
          <SheetStatusPill
            tone={connected ? "success" : "muted"}
            label={connected ? `${active?.provider} · connected` : "No tunnel running"}
          />
          {active?.url ? (
            <Text selectable style={[styles.url, { color: ui.accent }]} numberOfLines={2}>
              {active.url}
            </Text>
          ) : null}
          {uptimeS != null ? (
            <Text style={[styles.uptime, { color: ui.textMuted }]}>{`Uptime ${formatUptime(uptimeS)}`}</Text>
          ) : null}
          {active?.url || connected ? (
            <View style={styles.actionRow}>
              {active?.url ? (
                <View style={styles.actionFlex}>
                  <SheetButton
                    variant="secondary"
                    label="Copy URL"
                    onPress={() => void copyUrl(active.url!)}
                    disabled={busy !== null}
                  />
                </View>
              ) : null}
              {connected ? (
                <View style={styles.actionFlex}>
                  <SheetButton
                    variant="secondary"
                    label={busy === "restart" ? "Restarting…" : "Restart"}
                    onPress={() => void restart()}
                    disabled={busy !== null}
                  />
                </View>
              ) : null}
            </View>
          ) : null}
          {connected ? (
            <SheetButton
              variant="danger"
              label={busy === "stop" ? "Stopping…" : "Stop tunnel"}
              onPress={() => void stop()}
              disabled={busy !== null}
            />
          ) : null}
          {connected || logs.length > 0 ? (
            <Pressable
              onPress={() => setDetailsOpen((open) => !open)}
              hitSlop={8}
              style={styles.detailsToggle}
              accessibilityRole="button"
              accessibilityLabel={detailsOpen ? "Hide tunnel details" : "Show tunnel details"}
              accessibilityState={{ expanded: detailsOpen }}>
              <Text style={[styles.detailsLabel, { color: ui.textMuted }]}>
                {detailsOpen ? "Hide details" : "Details"}
              </Text>
              <AppIcon name="chevron" color={ui.textMuted} size={12} />
            </Pressable>
          ) : null}
          {detailsOpen ? (
            <View style={[styles.logBox, { backgroundColor: ui.surface, borderColor: ui.border }]}>
              <Text selectable style={[styles.log, { color: ui.textMuted }]}>
                {logs.length ? logs.join("\n") : "No log lines"}
              </Text>
            </View>
          ) : null}
        </View>
      </SheetSection>
      <SheetError message={error} />
      <SheetSection label="Start a tunnel">
        {(runtime?.providers ?? []).map((provider, index) => {
          const starting = busy === provider.id;
          return (
            <View
              key={provider.id}
              style={[
                styles.provider,
                index > 0 && { borderTopColor: ui.border, borderTopWidth: StyleSheet.hairlineWidth },
              ]}>
              <View style={[styles.tile, { backgroundColor: ui.surfaceActive }]}>
                <AppIcon name="tunnel" color={ui.text} size={16} />
              </View>
              <View style={styles.providerText}>
                <Text style={[styles.providerName, { color: ui.text }]}>{provider.name}</Text>
                <Text style={[styles.providerMeta, { color: ui.textMuted }]}>
                  {provider.available ? "Ready on this machine" : `Not installed. ${provider.install ?? ""}`}
                </Text>
              </View>
              <Pressable
                onPress={() => void start(provider)}
                disabled={!provider.available || busy !== null}
                hitSlop={4}
                style={[
                  styles.start,
                  {
                    backgroundColor: provider.available ? ui.accent : ui.surfaceActive,
                    opacity: !provider.available || busy !== null ? 0.5 : 1,
                  },
                ]}
                accessibilityRole="button"
                accessibilityLabel={`Start ${provider.name}`}
                accessibilityState={!provider.available || busy !== null ? { disabled: true } : undefined}>
                <Text
                  style={[
                    styles.startLabel,
                    { color: provider.available ? ui.accentText : ui.textMuted },
                  ]}>
                  {starting ? "Starting…" : "Start"}
                </Text>
              </Pressable>
            </View>
          );
        })}
      </SheetSection>
    </AppSheet>
  );
}

const styles = StyleSheet.create({
  hero: { gap: 10, paddingVertical: 12 },
  url: { fontSize: 13, lineHeight: 18 },
  uptime: { fontSize: 13 },
  actionRow: { flexDirection: "row", gap: 8 },
  actionFlex: { flex: 1 },
  detailsToggle: { flexDirection: "row", alignItems: "center", gap: 4, paddingVertical: 4 },
  detailsLabel: { fontSize: 13, fontWeight: "600" },
  logBox: { borderWidth: StyleSheet.hairlineWidth, borderRadius: 12, padding: 10 },
  log: { fontSize: 12, lineHeight: 17, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
  provider: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 12 },
  tile: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  providerText: { flex: 1, gap: 2, minWidth: 0 },
  providerName: { fontSize: 16, fontWeight: "700" },
  providerMeta: { fontSize: 13, lineHeight: 17 },
  start: { borderRadius: 999, paddingHorizontal: 16, paddingVertical: 10 },
  startLabel: { fontWeight: "700", fontSize: 14 },
});
