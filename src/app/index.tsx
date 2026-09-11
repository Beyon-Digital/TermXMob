import { useRouter } from "expo-router";
import { useEffect, useState } from "react";
import {
  Alert,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAppTheme } from "@/hooks/use-app-theme";
import { fetchHealth } from "@/lib/api";
import { connectionToHostInput, parseConnectTarget } from "@/lib/parse-url";
import { forgetConnection, listConnections, saveConnection } from "@/lib/storage";
import { statusColors } from "@/lib/themes";
import {
  connectionId,
  setCurrentConnection,
  type Connection,
  type SavedConnection,
} from "@/lib/types";

function sameOriginConnection(): Connection | null {
  if (Platform.OS !== "web" || typeof window === "undefined") return null;
  const url = new URL(window.location.href);
  return {
    protocol: url.protocol === "https:" ? "https" : "http",
    host: url.hostname,
    port: url.port ? Number(url.port) : url.protocol === "https:" ? 443 : 80,
    passcode: url.searchParams.get("k") || "",
  };
}

function needsLanAddress(connection: Connection): boolean {
  return connection.host === "127.0.0.1" || connection.host === "localhost";
}

function timeAgo(ts: number): string {
  const seconds = Math.max(1, Math.round((Date.now() - ts) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function ConnectScreen() {
  const router = useRouter();
  const { theme } = useAppTheme();
  const { ui } = theme;
  const status = statusColors(ui, theme.kind);
  const [host, setHost] = useState(Platform.OS === "web" ? "127.0.0.1:8787" : "");
  const [passcode, setPasscode] = useState("");
  const [saved, setSaved] = useState<SavedConnection[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const origin = sameOriginConnection();
      if (origin) {
        try {
          await fetchHealth(origin);
          if (cancelled) return;
          setCurrentConnection(origin);
          await saveConnection(origin);
          router.replace("/workspace");
          return;
        } catch {
          /* metro or unrelated web host */
        }
      }
      const entries = await listConnections();
      if (cancelled) return;
      setSaved(entries);
      const latest = entries.find((entry) => Platform.OS === "web" || !needsLanAddress(entry));
      if (latest) {
        setHost(connectionToHostInput(latest));
        setPasscode(latest.passcode);
      }
      setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [router]);

  const connect = async (target: Connection, source: "form" | "saved") => {
    setError("");
    if (Platform.OS !== "web" && needsLanAddress(target)) {
      setError("Phone cannot reach 127.0.0.1. Scan the QR or enter the laptop LAN IP printed by termx.");
      return;
    }
    setBusy(source === "form" ? "form" : connectionId(target));
    try {
      const health = await fetchHealth(target);
      if (health.passcode_required && !target.passcode) {
        setHost(connectionToHostInput(target));
        setPasscode("");
        setError(`Passcode required for ${connectionToHostInput(target)}`);
        return;
      }
      setCurrentConnection(target);
      const entry = await saveConnection(target);
      setSaved((previous) => [entry, ...previous.filter((item) => item.id !== entry.id)]);
      router.replace("/workspace");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cannot connect");
    } finally {
      setBusy(null);
    }
  };

  const connectForm = () => {
    const parsed = parseConnectTarget(host);
    if (!parsed) {
      setError("Enter host:port");
      return;
    }
    parsed.passcode = passcode;
    void connect(parsed, "form");
  };

  const confirmForget = (entry: SavedConnection) => {
    const label = connectionToHostInput(entry);
    const remove = async () => {
      await forgetConnection(entry.id);
      setSaved((previous) => previous.filter((item) => item.id !== entry.id));
    };
    if (Platform.OS === "web") {
      if (globalThis.confirm?.(`Forget ${label}?`)) void remove();
      return;
    }
    Alert.alert("Forget server?", label, [
      { text: "Cancel", style: "cancel" },
      { text: "Forget", style: "destructive", onPress: () => void remove() },
    ]);
  };

  return (
    <View style={[styles.screen, { backgroundColor: ui.background }]}>
      <SafeAreaView style={styles.safe}>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.hero}>
            <Text style={[styles.kicker, { color: ui.accent }]}>Remote machine</Text>
            <Text style={[styles.brand, { color: ui.text }]}>Termx</Text>
            <Text style={[styles.lede, { color: ui.textMuted }]}>
              {Platform.OS === "web"
                ? "Resume a host or add its address to open a terminal, tunnels, and desktop control."
                : "Scan the QR from the laptop, or type its LAN IP — not 127.0.0.1."}
            </Text>
          </View>

          {saved.length === 0 && ready ? (
            <View style={[styles.empty, { borderColor: ui.border, backgroundColor: ui.surface }]}>
              <Text style={[styles.emptyTitle, { color: ui.text }]}>No machines yet</Text>
              <Text style={[styles.emptyCopy, { color: ui.textMuted }]}>
                Start `uv run termx` on the computer you want to use, then scan the QR or enter host:port.
              </Text>
            </View>
          ) : null}

          {saved.map((entry, index) => {
            const id = connectionId(entry);
            const connecting = busy === id;
            return (
              <View
                key={entry.id}
                style={[
                  styles.card,
                  { backgroundColor: ui.surface, borderColor: index === 0 ? ui.accent : ui.border },
                ]}>
                <View style={styles.cardHead}>
                  <View style={[styles.dot, { backgroundColor: index === 0 ? status.success : ui.textMuted }]} />
                  <View style={styles.cardText}>
                    <Text style={[styles.cardTitle, { color: ui.text }]}>{connectionToHostInput(entry)}</Text>
                    <Text style={[styles.cardMeta, { color: ui.textMuted }]}>
                      {entry.protocol.toUpperCase()} · {timeAgo(entry.savedAt)}
                    </Text>
                  </View>
                </View>
                <View style={styles.cardActions}>
                  <Pressable
                    style={[styles.primary, { backgroundColor: ui.accent, opacity: busy ? 0.6 : 1 }]}
                    disabled={busy !== null}
                    accessibilityRole="button"
                    accessibilityLabel={index === 0 ? "Resume" : "Connect"}
                    onPress={() => void connect(entry, "saved")}>
                    <Text style={[styles.primaryLabel, { color: ui.accentText }]}>
                      {connecting ? "Connecting…" : index === 0 ? "Resume" : "Connect"}
                    </Text>
                  </Pressable>
                  <Pressable
                    onPress={() => confirmForget(entry)}
                    disabled={busy !== null}
                    style={styles.ghost}
                    accessibilityRole="button"
                    accessibilityLabel="Forget">
                    <Text style={{ color: ui.danger, fontWeight: "600" }}>Forget</Text>
                  </Pressable>
                </View>
              </View>
            );
          })}

          {ready ? (
            <View style={[styles.card, { backgroundColor: ui.surface, borderColor: ui.border }]}>
              <Text style={[styles.section, { color: ui.textMuted }]}>Add machine</Text>
              <TextInput
                placeholder="192.168.1.12:8787"
                value={host}
                onChangeText={setHost}
                autoCapitalize="none"
                autoCorrect={false}
                placeholderTextColor={ui.textMuted}
                style={[styles.input, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
              />
              <TextInput
                placeholder="Passcode (optional)"
                value={passcode}
                onChangeText={setPasscode}
                secureTextEntry
                autoCapitalize="none"
                onSubmitEditing={connectForm}
                placeholderTextColor={ui.textMuted}
                style={[styles.input, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
              />
              <Pressable
                style={[styles.primary, { backgroundColor: ui.accent, opacity: busy ? 0.6 : 1 }]}
                disabled={busy !== null}
                accessibilityRole="button"
                accessibilityLabel="Connect"
                onPress={connectForm}>
                <Text style={[styles.primaryLabel, { color: ui.accentText }]}>
                  {busy === "form" ? "Connecting…" : "Connect"}
                </Text>
              </Pressable>
            </View>
          ) : null}

          <View style={styles.secondaryRow}>
            {Platform.OS !== "web" ? (
              <Pressable
                style={[styles.secondary, { borderColor: ui.border }]}
                disabled={busy !== null}
                accessibilityRole="button"
                accessibilityLabel="Scan"
                onPress={() => router.push("/scan")}>
                <Text style={[styles.secondaryLabel, { color: ui.text }]}>Scan QR</Text>
              </Pressable>
            ) : null}
            <Pressable
              style={[styles.secondary, { borderColor: ui.border }]}
              disabled={busy !== null}
              accessibilityRole="button"
              accessibilityLabel="Settings"
              onPress={() => router.push("/settings")}>
              <Text style={[styles.secondaryLabel, { color: ui.text }]}>Settings</Text>
            </Pressable>
          </View>
          {error ? <Text style={[styles.error, { color: ui.danger }]}>{error}</Text> : null}
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  safe: { flex: 1 },
  content: { padding: 20, gap: 14, width: "100%", maxWidth: 720, alignSelf: "center", paddingBottom: 40 },
  hero: { gap: 6, paddingTop: 8, paddingBottom: 4 },
  kicker: { fontSize: 12, fontWeight: "700", letterSpacing: 1.2, textTransform: "uppercase" },
  brand: { fontSize: 34, fontWeight: "800", letterSpacing: -0.6 },
  lede: { fontSize: 15, lineHeight: 22 },
  empty: { borderWidth: 1, borderRadius: 16, padding: 16, gap: 6 },
  emptyTitle: { fontSize: 17, fontWeight: "700" },
  emptyCopy: { fontSize: 14, lineHeight: 20 },
  card: { borderWidth: 1, borderRadius: 16, padding: 14, gap: 12 },
  cardHead: { flexDirection: "row", alignItems: "center", gap: 10 },
  dot: { width: 10, height: 10, borderRadius: 5 },
  cardText: { flex: 1, gap: 2 },
  cardTitle: { fontSize: 16, fontWeight: "700" },
  cardMeta: { fontSize: 12 },
  cardActions: { flexDirection: "row", alignItems: "center", gap: 10 },
  primary: { flex: 1, borderRadius: 12, paddingVertical: 12, alignItems: "center" },
  primaryLabel: { fontWeight: "700", fontSize: 15 },
  ghost: { paddingHorizontal: 8, paddingVertical: 8 },
  section: { fontSize: 12, fontWeight: "700", letterSpacing: 0.8, textTransform: "uppercase" },
  input: { borderWidth: 1, borderRadius: 12, paddingHorizontal: 12, paddingVertical: Platform.OS === "ios" ? 12 : 8, fontSize: 15 },
  secondaryRow: { flexDirection: "row", gap: 10 },
  secondary: { flex: 1, borderWidth: 1, borderRadius: 12, paddingVertical: 12, alignItems: "center" },
  secondaryLabel: { fontWeight: "700" },
  error: { fontSize: 14, lineHeight: 20 },
});
