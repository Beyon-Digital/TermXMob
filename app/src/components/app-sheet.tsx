import {
  BottomSheetModal,
  BottomSheetScrollView,
  BottomSheetTextInput,
  type BottomSheetModal as BottomSheetModalRef,
} from "@expo/ui/community/bottom-sheet";
import { useEffect, useRef, type ReactNode } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useAppTheme } from "@/hooks/use-app-theme";

type Props = {
  title: string;
  isPresented: boolean;
  onDismiss: () => void;
  children: ReactNode;
};

export { BottomSheetTextInput as SheetInput };

export function AppSheet({ title, isPresented, onDismiss, children }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const ref = useRef<BottomSheetModalRef>(null);

  useEffect(() => {
    if (isPresented) ref.current?.present();
    else ref.current?.dismiss();
  }, [isPresented]);

  return (
    <BottomSheetModal
      ref={ref}
      snapPoints={["58%", "92%"]}
      enablePanDownToClose
      onDismiss={onDismiss}
      backgroundStyle={{ backgroundColor: ui.surface }}
      handleIndicatorStyle={{ backgroundColor: ui.textMuted }}>
      <View style={[styles.header, { borderBottomColor: ui.border }]}>
        <Text style={[styles.title, { color: ui.text }]}>{title}</Text>
        <Pressable onPress={onDismiss} hitSlop={12} accessibilityRole="button" accessibilityLabel="Close">
          <Text style={[styles.close, { color: ui.accent }]}>Done</Text>
        </Pressable>
      </View>
      <BottomSheetScrollView
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={styles.body}
        style={{ backgroundColor: ui.surface }}>
        {children}
      </BottomSheetScrollView>
    </BottomSheetModal>
  );
}

const styles = StyleSheet.create({
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  title: { fontSize: 17, fontWeight: "700" },
  close: { fontSize: 16, fontWeight: "600" },
  body: { padding: 16, gap: 12, paddingBottom: 40 },
});
