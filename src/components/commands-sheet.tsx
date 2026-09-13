import { useCallback, useEffect, useState } from "react";
import { Alert, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/app-icon";
import {
  AppSheet,
  SheetButton,
  SheetEmpty,
  SheetError,
  SheetField,
  SheetSearchField,
  SheetSection,
} from "@/components/app-sheet";
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

  const q = query.trim().toLowerCase();
  const filtered = items.filter(
    (item) =>
      !q ||
      item.name.toLowerCase().includes(q) ||
      item.command.toLowerCase().includes(q),
  );

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
    <AppSheet
      title="Commands"
      subtitle={items.length ? `${items.length} saved` : "Save commands you run often"}
      isPresented={isPresented}
      onDismiss={onDismiss}>
      <SheetSearchField
        value={query}
        onChangeText={setQuery}
        placeholder="Search commands"
        inputMode="search"
        enterKeyHint="search"
        returnKeyType="search"
        autoCapitalize="none"
        autoCorrect={false}
      />
      {filtered.length === 0 ? (
        <SheetEmpty
          icon="commands"
          title={items.length === 0 ? "No saved commands yet" : "No matching commands"}
          copy={
            items.length === 0
              ? "Save the commands you run often and fire them in one tap."
              : "Try a different search."
          }
        />
      ) : (
        <SheetSection label={`Saved · ${filtered.length}`}>
          {filtered.map((item, index) => (
            <View
              key={item.id}
              style={[
                styles.row,
                index > 0 && { borderTopColor: ui.border, borderTopWidth: StyleSheet.hairlineWidth },
              ]}>
              <Pressable
                style={styles.rowMain}
                onPress={() => run(item)}
                accessibilityRole="button"
                accessibilityLabel={`Run ${item.name}`}>
                <View style={[styles.tile, { backgroundColor: ui.surfaceActive }]}>
                  <AppIcon name="commands" color={ui.text} size={16} />
                </View>
                <View style={styles.rowText}>
                  <Text style={[styles.name, { color: ui.text }]}>{item.name}</Text>
                  <Text style={[styles.cmd, { color: ui.textMuted }]} numberOfLines={1}>
                    {item.command}
                  </Text>
                </View>
                <AppIcon name="chevron" color={ui.textMuted} size={14} />
              </Pressable>
              <Pressable
                onPress={() => remove(item)}
                hitSlop={12}
                style={styles.deleteBtn}
                accessibilityRole="button"
                accessibilityLabel={`Delete ${item.name}`}>
                <AppIcon name="kill" color={ui.danger} size={14} />
              </Pressable>
            </View>
          ))}
        </SheetSection>
      )}
      <SheetSection label="New command">
        <View style={styles.form}>
          <SheetField
            value={name}
            onChangeText={setName}
            placeholder="Name"
            style={{ backgroundColor: ui.surface }}
          />
          <SheetField
            value={command}
            onChangeText={setCommand}
            placeholder="git status"
            autoCapitalize="none"
            autoCorrect={false}
            style={{ backgroundColor: ui.surface }}
          />
          <SheetError message={error} />
          <SheetButton
            label={busy ? "Saving…" : "Save command"}
            onPress={() => void save()}
            disabled={busy}
          />
        </View>
      </SheetSection>
    </AppSheet>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingVertical: 12,
  },
  rowMain: { flex: 1, flexDirection: "row", alignItems: "center", gap: 10, minWidth: 0 },
  tile: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  rowText: { flex: 1, gap: 2, minWidth: 0 },
  deleteBtn: { padding: 6 },
  name: { fontSize: 16, fontWeight: "600" },
  cmd: { fontSize: 12, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
  form: { gap: 10, paddingVertical: 10 },
});
