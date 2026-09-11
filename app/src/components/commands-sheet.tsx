import { useCallback, useEffect, useState } from "react";
import { Alert, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { AppSheet, SheetInput } from "@/components/app-sheet";
import { useAppTheme } from "@/hooks/use-app-theme";
import { createCommand, deleteCommand, listCommands } from "@/lib/api";
import type { Connection, SavedCommand } from "@/lib/types";

type Props = {
  connection: Connection;
  isPresented: boolean;
  onDismiss: () => void;
  onRun: (command: string) => void;
};

export function CommandsSheet({ connection, isPresented, onDismiss, onRun }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const [items, setItems] = useState<SavedCommand[]>([]);
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [command, setCommand] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      setItems(await listCommands(connection));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load commands");
    }
  }, [connection]);

  useEffect(() => {
    if (isPresented) void reload();
  }, [isPresented, reload]);

  const filtered = items.filter((item) => {
    const q = query.trim().toLowerCase();
    if (!q) return true;
    return item.name.toLowerCase().includes(q) || item.command.toLowerCase().includes(q);
  });

  const run = (item: SavedCommand) => {
    const fire = () => {
      onRun(item.command.endsWith("\n") ? item.command : `${item.command}\n`);
      onDismiss();
    };
    if (!item.confirm) {
      fire();
      return;
    }
    if (Platform.OS === "web") {
      if (globalThis.confirm?.(`Run ${item.name}?\n${item.command}`)) fire();
      return;
    }
    Alert.alert("Run command?", item.command, [
      { text: "Cancel", style: "cancel" },
      { text: "Run", onPress: fire },
    ]);
  };

  const save = async () => {
    setError("");
    setBusy(true);
    try {
      await createCommand(connection, { name, command, confirm: false });
      setName("");
      setCommand("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const remove = (item: SavedCommand) => {
    const go = async () => {
      await deleteCommand(connection, item.id);
      await reload();
    };
    if (Platform.OS === "web") {
      if (globalThis.confirm?.(`Delete ${item.name}?`)) void go();
      return;
    }
    Alert.alert("Delete command?", item.name, [
      { text: "Cancel", style: "cancel" },
      { text: "Delete", style: "destructive", onPress: () => void go() },
    ]);
  };

  return (
    <AppSheet title="Commands" isPresented={isPresented} onDismiss={onDismiss}>
      <Text style={[styles.hint, { color: ui.textMuted }]}>
        Saved on this machine. A tap runs the command immediately.
      </Text>
      <SheetInput
        value={query}
        onChangeText={setQuery}
        placeholder="Search"
        placeholderTextColor={ui.textMuted}
        autoCapitalize="none"
        style={[styles.input, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
      />
      {filtered.length === 0 ? (
        <Text style={{ color: ui.textMuted }}>No saved commands yet.</Text>
      ) : (
        filtered.map((item) => (
          <View key={item.id} style={[styles.row, { borderColor: ui.border, backgroundColor: ui.surfaceAlt }]}>
            <Pressable style={styles.rowMain} onPress={() => run(item)} accessibilityRole="button">
              <Text style={[styles.name, { color: ui.text }]}>{item.name}</Text>
              <Text style={[styles.cmd, { color: ui.textMuted }]} numberOfLines={1}>
                {item.command}
              </Text>
            </Pressable>
            <Pressable onPress={() => remove(item)} hitSlop={8}>
              <Text style={{ color: ui.danger }}>Delete</Text>
            </Pressable>
          </View>
        ))
      )}
      <Text style={[styles.section, { color: ui.textMuted }]}>New command</Text>
      <SheetInput
        value={name}
        onChangeText={setName}
        placeholder="Name"
        placeholderTextColor={ui.textMuted}
        style={[styles.input, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
      />
      <SheetInput
        value={command}
        onChangeText={setCommand}
        placeholder="git status"
        autoCapitalize="none"
        autoCorrect={false}
        placeholderTextColor={ui.textMuted}
        style={[styles.input, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
      />
      {error ? <Text style={{ color: ui.danger }}>{error}</Text> : null}
      <Pressable
        style={[styles.primary, { backgroundColor: ui.accent, opacity: busy ? 0.6 : 1 }]}
        onPress={() => void save()}
        disabled={busy}>
        <Text style={[styles.primaryLabel, { color: ui.accentText }]}>Save command</Text>
      </Pressable>
    </AppSheet>
  );
}

const styles = StyleSheet.create({
  hint: { fontSize: 13, lineHeight: 18 },
  input: { borderWidth: 1, borderRadius: 10, paddingHorizontal: 12, paddingVertical: 10, fontSize: 15 },
  row: {
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  rowMain: { flex: 1, gap: 2 },
  name: { fontSize: 16, fontWeight: "600" },
  cmd: { fontSize: 12, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
  section: { fontSize: 12, fontWeight: "700", letterSpacing: 0.6, textTransform: "uppercase", marginTop: 8 },
  primary: { borderRadius: 10, paddingVertical: 12, alignItems: "center" },
  primaryLabel: { fontWeight: "700" },
});
