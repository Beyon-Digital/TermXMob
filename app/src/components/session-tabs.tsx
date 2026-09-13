import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";

import { IconButton } from "@/components/app-icon";
import { useAppTheme } from "@/hooks/use-app-theme";
import type { SessionInfo } from "@/lib/types";

type Props = {
  sessions: SessionInfo[];
  activeId: string | null;
  splitEnabled?: boolean;
  splitActive?: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onKill: () => void;
  onSplit?: () => void;
};

export function SessionTabs({
  sessions,
  activeId,
  splitEnabled = false,
  splitActive = false,
  onSelect,
  onNew,
  onKill,
  onSplit,
}: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  return (
    <View style={[styles.bar, { backgroundColor: ui.surface, borderBottomColor: ui.border }]}>
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
      {splitEnabled ? (
        <IconButton
          name={splitActive ? "unsplit" : "split"}
          color={splitActive ? ui.accent : ui.text}
          bg={splitActive ? ui.surfaceActive : ui.surfaceAlt}
          onPress={onSplit ?? (() => {})}
          accessibilityLabel={splitActive ? "Close split view" : "Split terminal"}
        />
      ) : null}
      <IconButton name="add" color={ui.text} bg={ui.surfaceAlt} onPress={onNew} accessibilityLabel="New session" />
      <IconButton name="kill" color={ui.text} bg={ui.surfaceAlt} onPress={onKill} accessibilityLabel="Kill session" />
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    flexShrink: 0,
    flexDirection: "row",
    alignItems: "center",
    borderBottomWidth: StyleSheet.hairlineWidth,
    minHeight: 40,
    paddingHorizontal: 6,
    gap: 6,
  },
  tabs: { gap: 4, alignItems: "center", paddingVertical: 5 },
  tab: {
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    maxWidth: 140,
  },
  tabLabel: { fontSize: 13 },
});
