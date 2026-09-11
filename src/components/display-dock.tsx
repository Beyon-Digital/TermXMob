import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useAppTheme } from "@/hooks/use-app-theme";
import type { DisplayInfo } from "@/lib/types";

type Props = {
  displays: DisplayInfo[];
  selectedId?: string;
  canCreateVirtual: boolean;
  virtualReason?: string;
  onSelect: (id: string) => void;
  onCreateVirtual: () => void;
};

export function DisplayDock({
  displays,
  selectedId,
  canCreateVirtual,
  virtualReason,
  onSelect,
  onCreateVirtual,
}: Props) {
  const insets = useSafeAreaInsets();
  const { theme } = useAppTheme();
  const { ui } = theme;
  return (
    <View
      style={[
        styles.wrap,
        { backgroundColor: ui.surface, borderTopColor: ui.border, paddingBottom: Math.max(insets.bottom, 8) },
      ]}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
        {displays.map((display) => {
          const selected = display.id === selectedId || display.selected;
          return (
            <Pressable
              key={display.id}
              onPress={() => onSelect(display.id)}
              accessibilityRole="button"
              accessibilityLabel={display.name}
              accessibilityState={{ selected }}
              style={[
                styles.tile,
                { borderColor: selected ? ui.accent : ui.border, backgroundColor: ui.surfaceAlt },
              ]}>
              <Text style={[styles.kind, { color: ui.textMuted }]}>{display.kind}</Text>
              <Text style={[styles.name, { color: selected ? ui.accent : ui.text }]} numberOfLines={1}>
                {display.name}
              </Text>
              {display.width && display.height ? (
                <Text style={[styles.size, { color: ui.textMuted }]}>
                  {display.width}×{display.height}
                </Text>
              ) : null}
            </Pressable>
          );
        })}
        <Pressable
          onPress={onCreateVirtual}
          disabled={!canCreateVirtual}
          accessibilityRole="button"
          accessibilityLabel="Create virtual display"
          accessibilityState={{ disabled: !canCreateVirtual }}
          style={[
            styles.tile,
            { borderColor: ui.border, backgroundColor: ui.surfaceAlt, opacity: canCreateVirtual ? 1 : 0.55 },
          ]}>
          <Text style={[styles.kind, { color: ui.textMuted }]}>virtual</Text>
          <Text style={[styles.name, { color: ui.text }]}>{canCreateVirtual ? "Create for this device" : "Unavailable"}</Text>
        </Pressable>
      </ScrollView>
      {!canCreateVirtual && virtualReason ? (
        <Text style={[styles.reason, { color: ui.textMuted }]}>{virtualReason}</Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { borderTopWidth: StyleSheet.hairlineWidth, paddingTop: 8, gap: 6 },
  row: { paddingHorizontal: 10, gap: 8 },
  tile: {
    width: 148,
    minHeight: 64,
    borderWidth: 1,
    borderRadius: 12,
    padding: 10,
    gap: 4,
  },
  kind: { fontSize: 10, fontWeight: "700", letterSpacing: 0.7, textTransform: "uppercase" },
  name: { fontSize: 13, fontWeight: "600" },
  size: { fontSize: 11, fontWeight: "600" },
  reason: { paddingHorizontal: 14, fontSize: 12, paddingBottom: 4 },
});
