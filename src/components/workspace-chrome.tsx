import { Pressable, StyleSheet, Text, View } from "react-native";

import { useAppTheme } from "@/hooks/use-app-theme";
import { statusColors } from "@/lib/themes";
import type { TunnelStatus, WorkspaceMode } from "@/lib/types";

type Props = {
  title: string;
  subtitle?: string;
  mode: WorkspaceMode;
  tunnel?: TunnelStatus | null;
  onMode: (mode: WorkspaceMode) => void;
  onCommands: () => void;
  onDefaults: () => void;
  onTunnel: () => void;
  onSettings: () => void;
  onMachines: () => void;
};

export function WorkspaceChrome({
  title,
  subtitle,
  mode,
  tunnel,
  onMode,
  onCommands,
  onDefaults,
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
      <View style={styles.top}>
        <Pressable
          onPress={onMachines}
          hitSlop={8}
          style={styles.identity}
          accessibilityRole="button"
          accessibilityLabel="Machines">
          <Text style={[styles.title, { color: ui.text }]} numberOfLines={1}>
            {title}
          </Text>
          <Text style={[styles.sub, { color: ui.textMuted }]} numberOfLines={1}>
            {subtitle || (tunnelOn ? tunnel?.url : "LAN")}
          </Text>
        </Pressable>
        <View style={[styles.switch, { backgroundColor: ui.surfaceAlt }]}>
          {(["terminal", "desktop"] as const).map((item) => {
            const on = mode === item;
            return (
              <Pressable
                key={item}
                onPress={() => onMode(item)}
                accessibilityRole="button"
                accessibilityLabel={item === "terminal" ? "Terminal" : "Desktop"}
                accessibilityState={{ selected: on }}
                style={[styles.switchItem, on && { backgroundColor: ui.surfaceActive }]}>
                <Text style={[styles.switchLabel, { color: on ? ui.accent : ui.textMuted }]}>
                  {item === "terminal" ? "Terminal" : "Desktop"}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </View>
      <View style={styles.actions}>
        <Action label="Commands" color={ui.text} bg={ui.surfaceAlt} onPress={onCommands} />
        <Action label="Defaults" color={ui.text} bg={ui.surfaceAlt} onPress={onDefaults} />
        <Action
          label={tunnelOn ? "Tunnel on" : "Tunnel"}
          accessibilityLabel="Tunnel"
          color={tunnelOn ? status.success : ui.text}
          bg={ui.surfaceAlt}
          onPress={onTunnel}
        />
        <Action label="Settings" color={ui.text} bg={ui.surfaceAlt} onPress={onSettings} />
      </View>
    </View>
  );
}

function Action({
  label,
  accessibilityLabel,
  color,
  bg,
  onPress,
}: {
  label: string;
  accessibilityLabel?: string;
  color: string;
  bg: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      style={[styles.action, { backgroundColor: bg }]}>
      <Text style={[styles.actionLabel, { color }]}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexShrink: 0,
    borderBottomWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingTop: 6,
    paddingBottom: 8,
    gap: 8,
  },
  top: { flexDirection: "row", alignItems: "center", gap: 8 },
  identity: { flex: 1, minWidth: 0 },
  title: { fontSize: 16, fontWeight: "700" },
  sub: { fontSize: 11 },
  switch: { flexDirection: "row", borderRadius: 10, padding: 3 },
  switchItem: { paddingHorizontal: 10, paddingVertical: 7, borderRadius: 8 },
  switchLabel: { fontSize: 12, fontWeight: "700" },
  actions: { flexDirection: "row", gap: 6 },
  action: { flex: 1, borderRadius: 8, paddingVertical: 8, alignItems: "center" },
  actionLabel: { fontSize: 11, fontWeight: "700" },
});
