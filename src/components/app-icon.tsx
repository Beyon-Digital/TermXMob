import { SymbolView, type SymbolViewProps } from "expo-symbols";
import { Pressable, StyleSheet, type StyleProp, type ViewStyle } from "react-native";

export const glyphs = {
  close: { ios: "xmark", android: "close", web: "close" },
  back: { ios: "chevron.left", android: "arrow_back", web: "arrow_back" },
  terminal: { ios: "terminal", android: "terminal", web: "terminal" },
  desktop: { ios: "display", android: "computer", web: "computer" },
  commands: { ios: "list.bullet", android: "list", web: "list" },
  shell: { ios: "chevron.left.forwardslash.chevron.right", android: "code", web: "code" },
  folder: { ios: "folder", android: "folder", web: "folder" },
  tunnel: { ios: "cloud", android: "cloud", web: "cloud" },
  settings: { ios: "gearshape", android: "settings", web: "settings" },
  add: { ios: "plus", android: "add", web: "add" },
  kill: { ios: "xmark", android: "close", web: "close" },
  split: { ios: "rectangle.split.2x1", android: "splitscreen", web: "splitscreen" },
  unsplit: { ios: "rectangle", android: "crop_square", web: "crop_square" },
  search: { ios: "magnifyingglass", android: "search", web: "search" },
  chevron: { ios: "chevron.right", android: "chevron_right", web: "chevron_right" },
} as const;

type GlyphName = keyof typeof glyphs;

export function AppIcon({
  name,
  color,
  size = 18,
}: {
  name: GlyphName;
  color: string;
  size?: number;
}) {
  return (
    <SymbolView
      name={glyphs[name] as SymbolViewProps["name"]}
      size={size}
      tintColor={color}
      weight="medium"
    />
  );
}

export function IconButton({
  name,
  color,
  bg,
  onPress,
  accessibilityLabel,
  selected,
  size = 32,
}: {
  name: GlyphName;
  color: string;
  bg?: string;
  onPress: () => void;
  accessibilityLabel: string;
  selected?: boolean;
  size?: number;
}) {
  return (
    <Pressable
      onPress={onPress}
      hitSlop={6}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={selected ? { selected: true } : undefined}
      style={[
        styles.btn,
        { width: size, height: size, backgroundColor: bg ?? "transparent" },
        selected && styles.selected,
      ] as StyleProp<ViewStyle>}>
      <AppIcon name={name} color={color} size={Math.round(size * 0.56)} />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  btn: {
    borderRadius: 8,
    alignItems: "center",
    justifyContent: "center",
  },
  selected: { opacity: 1 },
});
