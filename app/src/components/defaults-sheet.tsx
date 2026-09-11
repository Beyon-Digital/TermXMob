import { useCallback, useEffect, useState } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { AppSheet, SheetInput } from "@/components/app-sheet";
import { useAppTheme } from "@/hooks/use-app-theme";
import { fetchPreferences, savePreferences } from "@/lib/api";
import type { Connection } from "@/lib/types";

type Props = {
  connection: Connection;
  isPresented: boolean;
  onDismiss: () => void;
  onApplied?: (shell: string) => void | Promise<void>;
};

export function DefaultsSheet({ connection, isPresented, onDismiss, onApplied }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const [shell, setShell] = useState("");
  const [shells, setShells] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const prefs = await fetchPreferences(connection);
      setShell(prefs.shell);
      setShells(prefs.shells);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load shells");
    }
  }, [connection]);

  useEffect(() => {
    if (isPresented) void reload();
  }, [isPresented, reload]);

  const save = async () => {
    setBusy(true);
    setError("");
    setStatus("");
    try {
      const next = await savePreferences(connection, { shell });
      setShell(next.shell);
      setStatus("Saved. Opening a new session with this shell.");
      await onApplied?.(next.shell);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppSheet title="Shell" isPresented={isPresented} onDismiss={onDismiss}>
      <Text style={[styles.hint, { color: ui.textMuted }]}>
        Stored on the host. New terminals use this shell. Working directories are under Folders.
      </Text>
      <Text style={[styles.label, { color: ui.textMuted }]}>Shell</Text>
      <View style={styles.chips}>
        {shells.map((item) => {
          const selected = item === shell;
          return (
            <Pressable
              key={item}
              onPress={() => setShell(item)}
              style={[
                styles.chip,
                { borderColor: selected ? ui.accent : ui.border, backgroundColor: selected ? ui.surfaceActive : ui.surfaceAlt },
              ]}>
              <Text style={{ color: selected ? ui.accent : ui.text, fontSize: 13 }}>{item.split("/").pop()}</Text>
            </Pressable>
          );
        })}
      </View>
      <SheetInput
        value={shell}
        onChangeText={setShell}
        autoCapitalize="none"
        autoCorrect={false}
        placeholder="/bin/zsh"
        placeholderTextColor={ui.textMuted}
        style={[styles.input, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
      />
      {error ? <Text style={{ color: ui.danger }}>{error}</Text> : null}
      {status ? <Text style={{ color: ui.accent }}>{status}</Text> : null}
      <Pressable
        style={[styles.primary, { backgroundColor: ui.accent, opacity: busy ? 0.6 : 1 }]}
        onPress={() => void save()}
        disabled={busy}>
        <Text style={[styles.primaryLabel, { color: ui.accentText }]}>Save and new session</Text>
      </Pressable>
    </AppSheet>
  );
}

const styles = StyleSheet.create({
  hint: { fontSize: 13, lineHeight: 18 },
  label: { fontSize: 12, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase" },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  primary: { borderRadius: 10, paddingVertical: 12, alignItems: "center" },
  primaryLabel: { fontWeight: "700" },
});
