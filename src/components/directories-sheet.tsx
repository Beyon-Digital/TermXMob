import { useCallback, useEffect, useState } from "react";
import { Alert, Platform, Pressable, StyleSheet, Text, View } from "react-native";

import { AppIcon } from "@/components/app-icon";
import {
  AppSheet,
  SheetButton,
  SheetEmpty,
  SheetError,
  SheetField,
  SheetSection,
} from "@/components/app-sheet";
import { useAppTheme } from "@/hooks/use-app-theme";
import {
  createDirectory,
  deleteDirectory,
  listDirectories,
  listFs,
  savePreferences,
  useDirectory,
} from "@/lib/api";
import type { Connection, FsListing, SavedDirectory } from "@/lib/types";

type Props = {
  connection: Connection;
  isPresented: boolean;
  onDismiss: () => void;
  onOpen: (cwd: string) => void | Promise<void>;
};

export function DirectoriesSheet({ connection, isPresented, onDismiss, onOpen }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const [items, setItems] = useState<SavedDirectory[]>([]);
  const [cwd, setCwd] = useState("");
  const [listing, setListing] = useState<FsListing | null>(null);
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const next = await listDirectories(connection);
      setItems(next.directories);
      setCwd(next.cwd);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load folders");
    }
  }, [connection]);

  const browse = useCallback(
    async (path?: string) => {
      try {
        setListing(await listFs(connection, path));
        setError("");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not list folder");
      }
    },
    [connection],
  );

  useEffect(() => {
    if (!isPresented) return;
    void reload();
    void browse();
  }, [browse, isPresented, reload]);

  const saveCurrent = async () => {
    if (!listing) return;
    setBusy(true);
    setError("");
    try {
      await createDirectory(connection, { name, path: listing.path });
      setName("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setBusy(false);
    }
  };

  const openPath = async (path: string) => {
    setBusy(true);
    setError("");
    try {
      await savePreferences(connection, { cwd: path });
      setCwd(path);
      await onOpen(path);
      onDismiss();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not use folder");
    } finally {
      setBusy(false);
    }
  };

  const openSaved = async (item: SavedDirectory) => {
    setBusy(true);
    setError("");
    try {
      const used = await useDirectory(connection, item.id);
      setCwd(used.cwd);
      await onOpen(used.cwd);
      onDismiss();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not use folder");
    } finally {
      setBusy(false);
    }
  };

  const remove = (item: SavedDirectory) => {
    const go = async () => {
      setBusy(true);
      setError("");
      try {
        await deleteDirectory(connection, item.id);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not delete folder");
      } finally {
        setBusy(false);
      }
    };
    if (Platform.OS === "web") {
      if (globalThis.confirm?.(`Delete ${item.name}?`)) void go();
      return;
    }
    Alert.alert("Delete folder?", item.path, [
      { text: "Cancel", style: "cancel" },
      { text: "Delete", style: "destructive", onPress: () => void go() },
    ]);
  };

  return (
    <AppSheet
      title="Folders"
      subtitle={`Current ${cwd || "—"}`}
      isPresented={isPresented}
      onDismiss={onDismiss}>
      {items.length === 0 ? (
        <SheetEmpty
          icon="folder"
          title="No saved folders yet"
          copy="Browse the host below and save the folders you jump between."
        />
      ) : (
        <SheetSection label={`Saved · ${items.length}`}>
          {items.map((item, index) => {
            const active = item.path === cwd;
            return (
              <View
                key={item.id}
                style={[
                  styles.row,
                  index > 0 && { borderTopColor: ui.border, borderTopWidth: StyleSheet.hairlineWidth },
                ]}>
                <Pressable
                  style={styles.rowMain}
                  onPress={() => void openSaved(item)}
                  disabled={busy}
                  accessibilityRole="button"
                  accessibilityLabel={`Use folder ${item.name}`}
                  accessibilityState={busy ? { disabled: true } : undefined}>
                  <View
                    style={[
                      styles.tile,
                      { backgroundColor: active ? ui.accent : ui.surfaceActive },
                    ]}>
                    <AppIcon name="folder" color={active ? ui.accentText : ui.text} size={16} />
                  </View>
                  <View style={styles.rowText}>
                    <Text style={[styles.name, { color: active ? ui.accent : ui.text }]}>
                      {item.name}
                    </Text>
                    <Text style={[styles.path, { color: ui.textMuted }]} numberOfLines={1}>
                      {item.path}
                    </Text>
                  </View>
                  <AppIcon name="chevron" color={ui.textMuted} size={14} />
                </Pressable>
                <Pressable
                  onPress={() => remove(item)}
                  disabled={busy}
                  hitSlop={12}
                  style={styles.deleteBtn}
                  accessibilityRole="button"
                  accessibilityLabel={`Delete ${item.name}`}
                  accessibilityState={busy ? { disabled: true } : undefined}>
                  <AppIcon name="kill" color={ui.danger} size={14} />
                </Pressable>
              </View>
            );
          })}
        </SheetSection>
      )}
      <SheetSection label="Browse host">
        <View style={styles.browseBody}>
          <Text style={[styles.browsePath, { color: ui.text }]} numberOfLines={1}>
            {listing?.path || "—"}
          </Text>
          <View style={styles.actions}>
            <View style={styles.actionFlex}>
              <SheetButton
                variant="secondary"
                label="Up"
                onPress={() => listing?.parent && void browse(listing.parent)}
                disabled={!listing?.parent || busy}
              />
            </View>
            <View style={styles.actionFlex}>
              <SheetButton
                variant="secondary"
                label="Home"
                onPress={() => listing && void browse(listing.home)}
                disabled={!listing || busy}
              />
            </View>
            <View style={styles.actionWide}>
              <SheetButton
                label={busy ? "Opening…" : "Use this folder"}
                onPress={() => listing && void openPath(listing.path)}
                disabled={busy || !listing}
              />
            </View>
          </View>
          {(listing?.entries || []).map((entry, entryIndex) => (
            <View
              key={entry.path}
              style={[
                styles.entryRow,
                entryIndex === 0 && styles.entryFirst,
                { borderTopColor: ui.border },
              ]}>
              <Pressable
                style={styles.rowMain}
                onPress={() => void browse(entry.path)}
                disabled={busy}
                accessibilityRole="button"
                accessibilityLabel={`Browse ${entry.name}`}
                accessibilityState={busy ? { disabled: true } : undefined}>
                <View style={[styles.tile, { backgroundColor: ui.surfaceActive }]}>
                  <AppIcon name="folder" color={ui.textMuted} size={16} />
                </View>
                <Text style={[styles.entryName, { color: ui.text }]} numberOfLines={1}>
                  {entry.name}
                </Text>
                <AppIcon name="chevron" color={ui.textMuted} size={14} />
              </Pressable>
            </View>
          ))}
        </View>
      </SheetSection>
      <SheetSection label="Save this folder">
        <View style={styles.form}>
          <SheetField
            value={name}
            onChangeText={setName}
            placeholder="Name (optional)"
            style={{ backgroundColor: ui.surface }}
          />
          <SheetError message={error} />
          <SheetButton
            label={busy ? "Saving…" : "Save folder"}
            onPress={() => void saveCurrent()}
            disabled={busy || !listing}
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
  path: { fontSize: 12, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
  browseBody: { gap: 10, paddingVertical: 10 },
  browsePath: { fontSize: 12, fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }) },
  actions: { flexDirection: "row", gap: 8 },
  actionFlex: { flex: 1 },
  actionWide: { flex: 2 },
  entryRow: { borderTopWidth: StyleSheet.hairlineWidth, paddingVertical: 10 },
  entryFirst: { marginTop: 2 },
  entryName: { flex: 1, fontSize: 15, minWidth: 0 },
  form: { gap: 10, paddingVertical: 10 },
});
