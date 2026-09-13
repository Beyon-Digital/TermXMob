import { StyleSheet, Text, View } from "react-native";

import { IconButton } from "@/components/app-icon";
import { useAppTheme } from "@/hooks/use-app-theme";
import { statusColors } from "@/lib/themes";
import type { TunnelStatus, WorkspaceMode } from "@/lib/types";

type Props = {
  title: string;
  mode: WorkspaceMode;
  tunnel?: TunnelStatus | null;
  onMode: (mode: WorkspaceMode) => void;
  onCommands: () => void;
  onDefaults: () => void;
  onFolders: () => void;
  onTunnel: () => void;
  onSettings: () => void;
  onMachines: () => void;
};

export function WorkspaceChrome({
  title,
  mode,
  tunnel,
  onMode,
  onCommands,
  onDefaults,
  onFolders,
  onTunnel,
  onSettings,
  onMachines,
}: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const status = statusColors(ui, theme.kind);
  const tunnelOn = tunnel?.state === "connected";

  return (
    <View style={[styles.wrap, { backgroundColor: ui.surface, borderBottomColor: ui.border }]}>
      <IconButton name="back" color={ui.text} onPress={onMachines} accessibilityLabel="Machines" />
      <Text style={[styles.title, { color: ui.text }]} numberOfLines={1}>
        {title}
      </Text>
      <IconButton
        name="terminal"
        color={mode === "terminal" ? ui.accent : ui.textMuted}
        bg={mode === "terminal" ? ui.surfaceActive : "transparent"}
        selected={mode === "terminal"}
        onPress={() => onMode("terminal")}
        accessibilityLabel="Terminal"
      />
      <IconButton
        name="desktop"
        color={mode === "desktop" ? ui.accent : ui.textMuted}
        bg={mode === "desktop" ? ui.surfaceActive : "transparent"}
        selected={mode === "desktop"}
        onPress={() => onMode("desktop")}
        accessibilityLabel="Desktop"
      />
      <IconButton name="commands" color={ui.text} onPress={onCommands} accessibilityLabel="Commands" />
      <IconButton name="shell" color={ui.text} onPress={onDefaults} accessibilityLabel="Shell" />
      <IconButton name="folder" color={ui.text} onPress={onFolders} accessibilityLabel="Folders" />
      <IconButton
        name="tunnel"
        color={tunnelOn ? status.success : ui.text}
        onPress={onTunnel}
        accessibilityLabel="Tunnel"
      />
      <IconButton name="settings" color={ui.text} onPress={onSettings} accessibilityLabel="Settings" />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexShrink: 0,
    flexDirection: "row",
    alignItems: "center",
    gap: 2,
    paddingHorizontal: 6,
    paddingVertical: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    minHeight: 40,
  },
  title: { flex: 1, minWidth: 0, fontSize: 15, fontWeight: "600" },
});
