import { useEffect, useRef, useState } from "react";
import { Keyboard, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import { useAppTheme } from "@/hooks/use-app-theme";
import {
  EXTRA_KEY_ROWS,
  REPEAT_KEYS,
  ctrlChar,
  isGlyphKey,
  keyA11yLabel,
  keyGlyph,
  sequenceForKey,
} from "@/lib/keys";

type Props = {
  ctrlOn: boolean;
  altOn: boolean;
  onCtrl: () => void;
  onAlt: () => void;
  onInput: (data: string) => void;
  onSignal?: (name: string) => void;
  onHideKeyboard?: () => void;
};

export function ExtraKeys({ ctrlOn, altOn, onCtrl, onAlt, onInput, onSignal, onHideKeyboard }: Props) {
  const insets = useSafeAreaInsets();
  const { theme } = useAppTheme();
  const { ui } = theme;
  const [keyboardVisible, setKeyboardVisible] = useState(false);
  const hold = useRef<{ delay?: ReturnType<typeof setTimeout>; next?: ReturnType<typeof setTimeout> }>({});

  const stopHold = () => {
    if (hold.current.delay) clearTimeout(hold.current.delay);
    if (hold.current.next) clearTimeout(hold.current.next);
    hold.current = {};
  };

  useEffect(() => stopHold, []);

  useEffect(() => {
    const show = Keyboard.addListener(Platform.OS === "ios" ? "keyboardWillShow" : "keyboardDidShow", () => {
      setKeyboardVisible(true);
    });
    const hide = Keyboard.addListener(Platform.OS === "ios" ? "keyboardWillHide" : "keyboardDidHide", () => {
      setKeyboardVisible(false);
    });
    return () => {
      show.remove();
      hide.remove();
    };
  }, []);

  const fire = (name: string) => {
    if (name === "CTRL") {
      onCtrl();
      return;
    }
    if (name === "ALT") {
      onAlt();
      return;
    }
    if (name === "INT") {
      onSignal?.("int");
      onInput("\x03");
      return;
    }
    if (name === "KB") {
      Keyboard.dismiss();
      onHideKeyboard?.();
      return;
    }
    let seq = sequenceForKey(name);
    if (ctrlOn) {
      seq = ctrlChar(seq);
      onCtrl();
    }
    if (altOn) {
      seq = `\x1b${seq}`;
      onAlt();
    }
    onInput(seq);
  };

  const startHold = (name: string) => {
    stopHold();
    fire(name);
    if (!REPEAT_KEYS.has(name)) return;
    const seq = sequenceForKey(name);
    const tick = (ms: number) => {
      hold.current.next = setTimeout(() => {
        onInput(seq);
        tick(Math.max(32, ms * 0.85));
      }, ms);
    };
    hold.current.delay = setTimeout(() => tick(80), 400);
  };

  return (
    <View
      pointerEvents="auto"
      style={[
        styles.wrap,
        {
          backgroundColor: ui.surface,
          borderTopColor: ui.border,
          paddingBottom: keyboardVisible ? 4 : Math.max(insets.bottom, 8),
        },
      ]}>
      {EXTRA_KEY_ROWS.map((row, i) => (
        <View key={i} style={styles.row}>
          {row.map((name) => {
            const active = (name === "CTRL" && ctrlOn) || (name === "ALT" && altOn);
            const modifier = name === "CTRL" || name === "ALT";
            return (
              <Pressable
                key={name}
                accessibilityRole="button"
                accessibilityLabel={keyA11yLabel(name)}
                accessibilityState={modifier ? { selected: active } : undefined}
                onPressIn={() => startHold(name)}
                onPressOut={stopHold}
                style={[
                  styles.key,
                  { backgroundColor: active ? ui.accent : modifier ? ui.surfaceActive : ui.surfaceAlt },
                ]}>
                <Text
                  style={[
                    styles.label,
                    isGlyphKey(name) && styles.glyph,
                    { color: active ? ui.accentText : ui.text },
                    active && styles.labelOn,
                  ]}>
                  {keyGlyph(name)}
                </Text>
              </Pressable>
            );
          })}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    flexShrink: 0,
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 6,
    paddingTop: 6,
    gap: 4,
    zIndex: 50,
    elevation: 50,
  },
  row: { flexDirection: "row", gap: 4 },
  key: {
    flex: 1,
    minHeight: 36,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 6,
  },
  label: { fontSize: 11, fontVariant: ["tabular-nums"] },
  glyph: { fontSize: 15, lineHeight: 18 },
  labelOn: { fontWeight: "700" },
});
