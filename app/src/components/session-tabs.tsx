import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { useAppTheme } from "@/hooks/use-app-theme";
import type { SessionInfo } from "@/lib/types";

type Props = {
  sessions: SessionInfo[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onKill: () => void;
  onServers: () => void;
  onSettings: () => void;
};

export function SessionTabs({
  sessions,
  activeId,
  onSelect,
  onNew,
  onKill,
  onServers,
  onSettings,
}: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  return (
    <View style={[styles.bar, { backgroundColor: ui.surface, borderBottomColor: ui.border }]}>
      <Pressable
        onPress={onServers}
        style={[styles.icon, { backgroundColor: ui.surfaceAlt }]}
        accessibilityLabel="Saved servers">
        <Text style={[styles.iconLabel, { color: ui.text }]}>⇄</Text>
      </Pressable>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.tabs}>
        {sessions.map((session) => {
          const active = session.id === activeId;
          return (
            <Pressable
              key={session.id}
              onPress={() => onSelect(session.id)}
              style={[styles.tab, { backgroundColor: ui.surfaceAlt }, active && { backgroundColor: ui.surfaceActive }]}>
              <Text
                style={[styles.tabLabel, { color: ui.textMuted }, active && { color: ui.accent }]}
                numberOfLines={1}>
                {session.title}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
      <Pressable
        onPress={onNew}
        style={[styles.icon, { backgroundColor: ui.surfaceAlt }]}
        accessibilityLabel="New session">
        <Text style={[styles.iconLabel, { color: ui.text }]}>+</Text>
      </Pressable>
      <Pressable
        onPress={onKill}
        style={[styles.icon, { backgroundColor: ui.surfaceAlt }]}
        accessibilityLabel="Kill session">
        <Text style={[styles.iconLabel, { color: ui.text }]}>×</Text>
      </Pressable>
      <Pressable
        onPress={onSettings}
        style={[styles.icon, { backgroundColor: ui.surfaceAlt }]}
        accessibilityLabel="Settings">
        <Text style={[styles.iconLabel, { color: ui.text }]}>⚙</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexShrink: 0,
    flexDirection: "row",
    alignItems: "center",
    borderBottomWidth: StyleSheet.hairlineWidth,
    minHeight: 44,
    paddingHorizontal: 6,
    gap: 6,
  },
  tabs: { gap: 4, alignItems: "center", paddingVertical: 6 },
  tab: {
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    maxWidth: 160,
  },
  tabLabel: { fontSize: 13 },
  icon: {
    width: 36,
    height: 36,
    borderRadius: 6,
    alignItems: "center",
    justifyContent: "center",
  },
  iconLabel: { fontSize: 18, lineHeight: 20 },
});
