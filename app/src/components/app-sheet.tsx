import { type ReactNode } from "react";
import {
  KeyboardAvoidingView,
  Modal,
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

type Props = {
  title: string;
  isPresented: boolean;
  onDismiss: () => void;
  children: ReactNode;
};

export { TextInput as SheetInput };

export function AppSheet({ title, isPresented, onDismiss, children }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;

  return (
    <Modal
      visible={isPresented}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={onDismiss}>
      <SafeAreaView style={[styles.safe, { backgroundColor: ui.surface }]} edges={["top", "bottom"]}>
        <KeyboardAvoidingView
          style={styles.flex}
          behavior={Platform.OS === "ios" ? "padding" : undefined}>
          <View style={[styles.header, { borderBottomColor: ui.border, backgroundColor: ui.surface }]}>
            <Text style={[styles.title, { color: ui.text }]}>{title}</Text>
            <Pressable onPress={onDismiss} hitSlop={12} accessibilityRole="button" accessibilityLabel="Close">
              <Text style={[styles.close, { color: ui.accent }]}>Done</Text>
            </Pressable>
          </View>
          <ScrollView
            keyboardShouldPersistTaps="handled"
            contentContainerStyle={styles.body}
            style={[styles.flex, { backgroundColor: ui.surface }]}>
            {children}
          </ScrollView>
        </KeyboardAvoidingView>
      </SafeAreaView>
    </Modal>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1 },
  flex: { flex: 1 },
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
