import { useCallback, useEffect, useState } from "react";
import { Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/app-icon";
import {
  AppSheet,
  SheetButton,
  SheetError,
  SheetField,
  SheetSection,
} from "@/components/app-sheet";
import { useAppTheme } from "@/hooks/use-app-theme";
import { fetchPreferences, savePreferences } from "@/lib/api";
import type { Connection } from "@/lib/types";

type Props = {
  connection: Connection;
  isPresented: boolean;
  onDismiss: () => void;
  onApplied?: () => void | Promise<void>;
};

export function DefaultsSheet({ connection, isPresented, onDismiss, onApplied }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const [shell, setShell] = useState("");
  const [cwd, setCwd] = useState("");
  const [shells, setShells] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const prefs = await fetchPreferences(connection);
      setShell(prefs.shell);
      setCwd(prefs.cwd);
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
      const next = await savePreferences(connection, { shell, cwd });
      setShell(next.shell);
      setCwd(next.cwd);
      setStatus("Saved. Opening a new session with this shell.");
      await onApplied?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  return (
    <AppSheet
      title="Shell"
      subtitle="New sessions use these defaults"
      isPresented={isPresented}
      onDismiss={onDismiss}>
      {shells.length ? (
        <SheetSection label={`Available · ${shells.length}`}>
          {shells.map((item, index) => {
            const selected = item === shell;
            return (
              <Pressable
                key={item}
                onPress={() => setShell(item)}
                disabled={busy}
                accessibilityRole="radio"
                accessibilityLabel={`Use ${item}`}
                accessibilityState={{ selected, disabled: busy }}
                style={[
                  styles.row,
                  index > 0 && { borderTopColor: ui.border, borderTopWidth: StyleSheet.hairlineWidth },
                ]}>
                <View
                  style={[
                    styles.tile,
                    { backgroundColor: selected ? ui.accent : ui.surfaceActive },
                  ]}>
                  <AppIcon name="shell" color={selected ? ui.accentText : ui.text} size={16} />
                </View>
                <View style={styles.rowText}>
                  <Text
                    style={[
                      styles.name,
                      { color: selected ? ui.accent : ui.text },
                      selected && styles.nameSelected,
                    ]}>
                    {item.split("/").pop()}
                  </Text>
                  <Text style={[styles.supporting, { color: ui.textMuted }]} numberOfLines={1}>
                    {item}
                  </Text>
                </View>
                <View
                  style={[
                    styles.radio,
                    { borderColor: selected ? ui.accent : ui.border },
                  ]}>
                  {selected ? (
                    <View style={[styles.radioDot, { backgroundColor: ui.accent }]} />
                  ) : null}
                </View>
              </Pressable>
            );
          })}
        </SheetSection>
      ) : null}
      <SheetSection label="Defaults">
        <View style={styles.form}>
          <SheetField
            value={shell}
            onChangeText={setShell}
            placeholder="/bin/zsh"
            accessibilityLabel="Shell path"
            autoCapitalize="none"
            autoCorrect={false}
            style={{
              backgroundColor: ui.surface,
              fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }),
            }}
          />
          <SheetField
            value={cwd}
            onChangeText={setCwd}
            placeholder="/Users/you"
            accessibilityLabel="Working directory"
            autoCapitalize="none"
            autoCorrect={false}
            style={{
              backgroundColor: ui.surface,
              fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }),
            }}
          />
          <SheetError message={error} />
          {status ? <Text style={[styles.status, { color: ui.accent }]}>{status}</Text> : null}
          <SheetButton
            label={busy ? "Saving…" : "Save and new session"}
            onPress={() => void save()}
            disabled={busy}
          />
        </View>
      </SheetSection>
    </AppSheet>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 12 },
  tile: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  rowText: { flex: 1, gap: 2, minWidth: 0 },
  name: { fontSize: 16, fontWeight: "600" },
  nameSelected: { fontWeight: "700" },
  supporting: { fontSize: 12, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
  radio: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    alignItems: "center",
    justifyContent: "center",
  },
  radioDot: { width: 12, height: 12, borderRadius: 6 },
  form: { gap: 10, paddingVertical: 10 },
  status: { fontSize: 13, lineHeight: 18 },
});
