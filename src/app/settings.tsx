import { useRouter } from "expo-router";
import { useState } from "react";
import {
  Alert,
  KeyboardAvoidingView,
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
import {
  themeWithDerivedTerminal,
  type AppTheme,
  type TerminalTheme,
  type ThemeKind,
  type ThemeUi,
} from "@/lib/themes";
import { getCurrentConnection } from "@/lib/types";

const HEX = /^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

const EDITABLE: { key: keyof ThemeUi; label: string }[] = [
  { key: "background", label: "Background" },
  { key: "surface", label: "Bars / panels" },
  { key: "surfaceAlt", label: "Keys / inputs" },
  { key: "surfaceActive", label: "Active surface" },
  { key: "border", label: "Borders" },
  { key: "text", label: "Text" },
  { key: "textMuted", label: "Muted text" },
  { key: "accent", label: "Accent" },
  { key: "accentText", label: "Text on accent" },
  { key: "danger", label: "Error / danger" },
];

type Draft = ThemeUi & {
  id?: string;
  kind: ThemeKind;
  name: string;
  terminal: TerminalTheme;
};

function draftFrom(theme: AppTheme, name = `${theme.name} custom`): Draft {
  return {
    id: undefined,
    kind: theme.kind,
    name,
    terminal: theme.terminal,
    ...theme.ui,
  };
}

export default function SettingsScreen() {
  const router = useRouter();
  const { theme, themeId, themes, setTheme, saveCustomTheme, deleteCustomTheme } = useAppTheme();
  const { ui } = theme;
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState("");
  const connected = getCurrentConnection() != null;

  const value = draft ?? draftFrom(theme);
  const builtIn = themes.filter((item) => !item.custom);
  const customThemes = themes.filter((item) => item.custom);

  const update = (key: keyof Draft, next: string) => {
    setDraft({ ...value, [key]: next });
    setError("");
  };

  const saveCustom = () => {
    if (!value.name.trim()) {
      setError("Give your theme a name.");
      return;
    }
    for (const slot of EDITABLE) {
      if (!HEX.test(value[slot.key])) {
        setError(`"${slot.label}" needs a hex color like #1e1e1e.`);
        return;
      }
    }
    const { name, kind, id, terminal, ...colors } = value;
    const next: AppTheme = themeWithDerivedTerminal({
      id: id ?? `custom-${Date.now().toString(36)}`,
      name: name.trim(),
      kind,
      custom: true,
      ui: colors,
      terminal,
    });
    saveCustomTheme(next);
    setDraft(null);
    setError("");
  };

  const confirmDelete = (item: AppTheme) => {
    const remove = () => deleteCustomTheme(item.id);
    if (Platform.OS === "web") {
      if (globalThis.confirm?.(`Delete "${item.name}"?`)) remove();
      return;
    }
    Alert.alert("Delete theme?", item.name, [
      { text: "Cancel", style: "cancel" },
      { text: "Delete", style: "destructive", onPress: remove },
    ]);
  };

  const renderTheme = (item: AppTheme) => {
    const selected = item.id === themeId;
    return (
      <View
        key={item.id}
        style={[
          styles.themeRow,
          { backgroundColor: ui.surface, borderColor: selected ? ui.accent : ui.border },
        ]}>
        <Pressable style={styles.themeMain} onPress={() => setTheme(item.id)}>
          <View style={[styles.swatch, { backgroundColor: item.ui.background, borderColor: item.ui.border }]}>
            <View style={[styles.swatchDot, { backgroundColor: item.ui.accent }]} />
          </View>
          <View style={styles.themeText}>
            <Text style={[styles.themeName, { color: ui.text }, selected && { fontWeight: "700" }]}>
              {item.name}
            </Text>
            <Text style={[styles.themeMeta, { color: ui.textMuted }]}>
              {item.kind === "dark" ? "Dark" : "Light"}
              {item.custom ? " · custom" : ""}
              {selected ? " · active" : ""}
            </Text>
          </View>
        </Pressable>
        {item.custom ? (
          <>
            <Pressable onPress={() => setDraft({ ...draftFrom(item, item.name), id: item.id })}>
              <Text style={[styles.rowAction, { color: ui.accent }]}>Edit</Text>
            </Pressable>
            <Pressable onPress={() => confirmDelete(item)}>
              <Text style={[styles.rowAction, { color: ui.danger }]}>Delete</Text>
            </Pressable>
          </>
        ) : null}
      </View>
    );
  };

  return (
    <View style={[styles.screen, { backgroundColor: ui.background }]}>
      <SafeAreaView style={styles.safe} edges={["top", "left", "right"]}>
        <View style={[styles.topBar, { borderBottomColor: ui.border }]}>
          <Pressable onPress={() => router.back()} hitSlop={12}>
            <Text style={[styles.back, { color: ui.accent }]}>‹ Back</Text>
          </Pressable>
          <Text style={[styles.title, { color: ui.text }]}>Settings</Text>
          <View style={styles.backSpacer} />
        </View>
        <KeyboardAvoidingView
          style={styles.fill}
          behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <ScrollView
            style={styles.fill}
            contentContainerStyle={styles.content}
            keyboardShouldPersistTaps="handled">
            {connected ? (
              <Pressable onPress={() => router.replace("/workspace")}>
                <Text style={[styles.footer, { color: ui.accent, marginTop: 0 }]}>
                  Connected — open workspace to edit host settings
                </Text>
              </Pressable>
            ) : (
              <Text style={[styles.footer, { color: ui.textMuted, marginTop: 0 }]}>
                Connect to a machine first
              </Text>
            )}
            <Text style={[styles.section, { color: ui.textMuted }]}>Appearance</Text>
            <View style={styles.list}>{builtIn.map(renderTheme)}</View>

            {customThemes.length ? (
              <>
                <Text style={[styles.section, { color: ui.textMuted }]}>Your themes</Text>
                <View style={styles.list}>{customThemes.map(renderTheme)}</View>
              </>
            ) : null}

            <Text style={[styles.section, { color: ui.textMuted }]}>Terminal defaults</Text>
            <Text style={[styles.footer, { color: ui.textMuted, marginTop: 0 }]}>
              Edit shell and working directory from the workspace Defaults sheet while connected.
            </Text>
            <Text style={[styles.section, { color: ui.textMuted }]}>Remote control</Text>
            <Text style={[styles.footer, { color: ui.textMuted, marginTop: 0 }]}>
              View-only by default. Accessibility and screen recording are granted on the host.
            </Text>
            <Text style={[styles.section, { color: ui.textMuted }]}>Tunnels</Text>
            <Text style={[styles.footer, { color: ui.textMuted, marginTop: 0 }]}>
              Start or stop tunnels from the workspace Tunnel sheet.
            </Text>
            <Text style={[styles.section, { color: ui.textMuted }]}>Security</Text>
            <Text style={[styles.footer, { color: ui.textMuted, marginTop: 0 }]}>
              Passcode and QR pairing. Themes stay on this device.
            </Text>

            <Text style={[styles.section, { color: ui.textMuted }]}>Create your own</Text>
            <View style={[styles.card, { backgroundColor: ui.surface, borderColor: ui.border }]}>
              <Text style={[styles.fieldLabel, { color: ui.text }]}>Name</Text>
              <TextInput
                value={value.name}
                onChangeText={(next) => update("name", next)}
                placeholder="My theme"
                placeholderTextColor={ui.textMuted}
                autoCapitalize="none"
                autoCorrect={false}
                style={[styles.input, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
              />
              {EDITABLE.map((slot) => (
                <View key={slot.key} style={styles.colorRow}>
                  <View
                    style={[
                      styles.colorPreview,
                      {
                        backgroundColor: HEX.test(value[slot.key]) ? value[slot.key] : "transparent",
                        borderColor: ui.border,
                      },
                    ]}
                  />
                  <Text style={[styles.fieldLabel, styles.colorLabel, { color: ui.text }]}>
                    {slot.label}
                  </Text>
                  <TextInput
                    value={value[slot.key]}
                    onChangeText={(next) => update(slot.key, next)}
                    placeholder="#000000"
                    placeholderTextColor={ui.textMuted}
                    autoCapitalize="none"
                    autoCorrect={false}
                    spellCheck={false}
                    style={[styles.input, styles.hexInput, { color: ui.text, backgroundColor: ui.surfaceAlt, borderColor: ui.border }]}
                  />
                </View>
              ))}

              <View style={[styles.preview, { backgroundColor: value.background, borderColor: value.border }]}>
                <Text style={[styles.previewTitle, { color: value.text }]}>Preview</Text>
                <Text style={{ color: value.textMuted }}>Muted text sample</Text>
                <View style={styles.previewRow}>
                  <View style={[styles.previewButton, { backgroundColor: value.accent }]}>
                    <Text style={{ color: value.accentText, fontWeight: "600" }}>Connect</Text>
                  </View>
                  <View style={[styles.previewButton, { backgroundColor: value.surfaceAlt }]}>
                    <Text style={{ color: value.text }}>Key</Text>
                  </View>
                  <Text style={{ color: value.danger }}>Error</Text>
                </View>
              </View>

              {error ? <Text style={{ color: ui.danger }}>{error}</Text> : null}

              <View style={styles.actions}>
                <Pressable
                  style={[styles.primaryButton, { backgroundColor: ui.accent }]}
                  onPress={saveCustom}>
                  <Text style={[styles.primaryLabel, { color: ui.accentText }]}>
                    {value.id ? "Save changes" : "Save custom theme"}
                  </Text>
                </Pressable>
                <Pressable
                  style={[styles.secondaryButton, { borderColor: ui.border }]}
                  onPress={() => {
                    setDraft(null);
                    setError("");
                  }}>
                  <Text style={{ color: ui.text }}>Use current theme</Text>
                </Pressable>
              </View>
            </View>

            <Text style={[styles.footer, { color: ui.textMuted }]}>
              Themes are saved on this device. Custom themes also tint the terminal.
            </Text>
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  safe: { flex: 1 },
  fill: { flex: 1 },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  back: { fontSize: 16, minWidth: 64 },
  backSpacer: { minWidth: 64 },
  title: { fontSize: 17, fontWeight: "700" },
  content: {
    padding: 16,
    gap: 10,
    width: "100%",
    maxWidth: 720,
    alignSelf: "center",
  },
  section: { fontSize: 12, fontWeight: "700", textTransform: "uppercase", letterSpacing: 1, marginTop: 8 },
  list: { gap: 8 },
  themeRow: {
    flexDirection: "row",
    alignItems: "center",
    borderWidth: 1,
    borderRadius: 10,
    paddingRight: 12,
    gap: 4,
  },
  themeMain: { flex: 1, flexDirection: "row", alignItems: "center", gap: 12, padding: 12 },
  swatch: {
    width: 36,
    height: 36,
    borderRadius: 8,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  swatchDot: { width: 12, height: 12, borderRadius: 6 },
  themeText: { flex: 1, gap: 2 },
  themeName: { fontSize: 15 },
  themeMeta: { fontSize: 12 },
  rowAction: { fontSize: 13, paddingHorizontal: 6, paddingVertical: 6 },
  card: { borderWidth: 1, borderRadius: 12, padding: 14, gap: 10 },
  fieldLabel: { fontSize: 14 },
  input: {
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: Platform.OS === "ios" ? 10 : 6,
    fontSize: 14,
    fontFamily: Platform.select({ ios: "Menlo", default: "monospace" }),
  },
  colorRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  colorPreview: { width: 22, height: 22, borderRadius: 6, borderWidth: 1 },
  colorLabel: { flex: 1 },
  hexInput: { width: 120, textAlign: "center" },
  preview: { borderWidth: 1, borderRadius: 10, padding: 12, gap: 8 },
  previewTitle: { fontSize: 16, fontWeight: "700" },
  previewRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  previewButton: { borderRadius: 6, paddingHorizontal: 12, paddingVertical: 7 },
  actions: { flexDirection: "row", gap: 10, flexWrap: "wrap" },
  primaryButton: { borderRadius: 8, paddingHorizontal: 16, paddingVertical: 12 },
  primaryLabel: { fontWeight: "700" },
  secondaryButton: { borderWidth: 1, borderRadius: 8, paddingHorizontal: 16, paddingVertical: 12 },
  footer: { fontSize: 12, marginTop: 8 },
});
