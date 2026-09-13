import { useEffect, useRef, type ReactNode } from "react";
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput as RNTextInput,
  type TextInputProps,
  View,
} from "react-native";

import { AppIcon } from "@/components/app-icon";
import { useAppTheme } from "@/hooks/use-app-theme";
import { useLayout } from "@/hooks/use-layout";

type Props = {
  title: string;
  subtitle?: string;
  isPresented: boolean;
  onDismiss: () => void;
  children: ReactNode;
};

type FieldProps = Omit<TextInputProps, "value" | "onChangeText"> & {
  value: string;
  onChangeText: (text: string) => void;
};

export function SheetField({ value, onChangeText, style, ...props }: FieldProps) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  return (
    <RNTextInput
      value={value}
      onChangeText={onChangeText}
      placeholderTextColor={ui.textMuted}
      selectionColor={ui.accent}
      style={[
        styles.field,
        { backgroundColor: ui.surfaceAlt, borderColor: ui.border, color: ui.text },
        style,
      ]}
      {...props}
    />
  );
}

export function SheetSearchField({
  value,
  onChangeText,
  placeholder = "Search",
  style,
  ...props
}: FieldProps) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  return (
    <View accessibilityRole="search">
      <RNTextInput
        value={value}
        onChangeText={onChangeText}
        placeholder={placeholder}
        placeholderTextColor={ui.textMuted}
        selectionColor={ui.accent}
        style={[
          styles.search,
          { backgroundColor: ui.surfaceAlt, borderColor: ui.border, color: ui.text },
          style,
        ]}
        {...props}
      />
      <View style={styles.searchIcon} pointerEvents="none">
        <AppIcon name="search" color={ui.textMuted} size={16} />
      </View>
    </View>
  );
}

export function SheetSection({
  label,
  children,
}: {
  label?: string;
  children: ReactNode;
}) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  return (
    <View style={styles.section}>
      {label ? (
        <Text style={[styles.sectionLabel, { color: ui.textMuted }]}>{label}</Text>
      ) : null}
      <View
        style={[
          styles.card,
          { backgroundColor: ui.surfaceAlt, borderColor: ui.border },
        ]}>
        {children}
      </View>
    </View>
  );
}

type ButtonVariant = "primary" | "secondary" | "danger";

export function SheetButton({
  label,
  onPress,
  variant = "primary",
  disabled,
  accessibilityLabel,
}: {
  label: string;
  onPress: () => void;
  variant?: ButtonVariant;
  disabled?: boolean;
  accessibilityLabel?: string;
}) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const bg =
    variant === "primary" ? ui.accent : variant === "secondary" ? ui.surfaceActive : "transparent";
  const color = variant === "danger" ? ui.danger : variant === "primary" ? ui.accentText : ui.text;
  const borderColor = variant === "danger" ? ui.danger : variant === "secondary" ? ui.border : "transparent";
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? label}
      accessibilityState={disabled ? { disabled: true } : undefined}
      hitSlop={4}
      style={({ pressed }) => [
        styles.cta,
        { backgroundColor: bg, borderColor, opacity: disabled ? 0.5 : pressed ? 0.75 : 1 },
      ]}>
      <Text style={[styles.ctaLabel, { color }]}>{label}</Text>
    </Pressable>
  );
}

export function SheetEmpty({
  icon,
  title,
  copy,
}: {
  icon: "commands" | "folder" | "tunnel" | "shell";
  title: string;
  copy?: string;
}) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  return (
    <View style={styles.empty}>
      <View style={[styles.emptyTile, { backgroundColor: ui.surfaceActive }]}>
        <AppIcon name={icon} color={ui.textMuted} size={20} />
      </View>
      <Text style={[styles.emptyTitle, { color: ui.text }]}>{title}</Text>
      {copy ? <Text style={[styles.emptyCopy, { color: ui.textMuted }]}>{copy}</Text> : null}
    </View>
  );
}

export function SheetError({ message }: { message?: string }) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  if (!message) return null;
  return (
    <Text
      accessibilityLiveRegion="polite"
      style={[styles.error, { color: ui.danger }]}>
      {message}
    </Text>
  );
}

export function SheetStatusPill({
  tone = "muted",
  label,
}: {
  tone?: "success" | "muted";
  label: string;
}) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const isSuccess = tone === "success";
  return (
    <View
      style={[
        styles.pill,
        {
          backgroundColor: isSuccess ? `${ui.accent}26` : ui.surfaceActive,
          borderColor: isSuccess ? ui.accent : ui.border,
        },
      ]}>
      <View
        style={[
          styles.pillDot,
          { backgroundColor: isSuccess ? ui.accent : ui.textMuted },
        ]}
      />
      <Text
        style={[
          styles.pillLabel,
          { color: isSuccess ? ui.text : ui.textMuted },
        ]}>
        {label}
      </Text>
    </View>
  );
}

export function AppSheet({ title, subtitle, isPresented, onDismiss, children }: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const layout = useLayout();
  const dialog = layout.isWide;
  const wasPresented = useRef(isPresented);
  const dismissedByAction = useRef(false);

  useEffect(() => {
    if (wasPresented.current && !isPresented && !dismissedByAction.current) onDismiss();
    if (!isPresented) dismissedByAction.current = false;
    wasPresented.current = isPresented;
  }, [isPresented, onDismiss]);

  const dismiss = () => {
    dismissedByAction.current = true;
    onDismiss();
  };

  return (
    <Modal
      visible={isPresented}
      transparent
      animationType={dialog ? "fade" : "slide"}
      onRequestClose={dismiss}>
      <View style={[styles.overlay, dialog && styles.overlayDialog]}>
        <Pressable
          style={StyleSheet.absoluteFill}
          onPress={dismiss}
          accessibilityRole="button"
          accessibilityLabel={`Dismiss ${title}`}
        />
        {dialog ? null : <View style={styles.topSpace} />}
        <View
          style={[
            styles.root,
            dialog ? styles.rootDialog : styles.rootSheet,
            { backgroundColor: ui.surface, borderColor: ui.border },
          ]}
          accessibilityViewIsModal>
          <View style={styles.header}>
            <View style={styles.headerText}>
              <Text accessibilityRole="header" style={[styles.title, { color: ui.text }]}>
                {title}
              </Text>
              {subtitle ? (
                <Text style={[styles.subtitle, { color: ui.textMuted }]} numberOfLines={2}>
                  {subtitle}
                </Text>
              ) : null}
            </View>
            <Pressable
              onPress={dismiss}
              hitSlop={12}
              accessibilityRole="button"
              accessibilityLabel={`Close ${title}`}
              style={({ pressed }) => [
                styles.close,
                {
                  backgroundColor: ui.surfaceAlt,
                  borderColor: ui.border,
                  opacity: pressed ? 0.6 : 1,
                },
              ]}>
              <AppIcon name="close" color={ui.text} size={14} />
            </Pressable>
          </View>
          <ScrollView
            style={[styles.scroll, dialog && styles.scrollDialog]}
            contentContainerStyle={styles.content}
            horizontal={false}
            alwaysBounceHorizontal={false}
            showsHorizontalScrollIndicator={false}
            keyboardShouldPersistTaps="handled">
            {children}
          </ScrollView>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  overlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.45)" },
  overlayDialog: { alignItems: "center", justifyContent: "center", padding: 32 },
  topSpace: { height: "15%" },
  root: {
    minHeight: 0,
    overflow: "hidden",
  },
  rootSheet: {
    flex: 1,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
  },
  rootDialog: {
    width: "100%",
    maxWidth: 620,
    maxHeight: "85%",
    flexGrow: 0,
    flexShrink: 1,
    flexBasis: "auto",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: 20,
  },
  scroll: { flex: 1 },
  scrollDialog: { flexGrow: 0, flexShrink: 1, flexBasis: "auto" },
  content: {
    padding: 20,
    paddingBottom: 32,
    gap: 16,
  },
  header: { flexDirection: "row", flexShrink: 0, padding: 20, gap: 12 },
  headerText: { flex: 1, minWidth: 0, gap: 2 },
  title: { fontSize: 20, fontWeight: "700", letterSpacing: -0.3 },
  subtitle: { fontSize: 13, lineHeight: 18 },
  close: {
    width: 32,
    height: 32,
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: "center",
    justifyContent: "center",
    marginTop: 2,
  },
  searchIcon: {
    position: "absolute",
    left: 14,
    top: 0,
    bottom: 0,
    justifyContent: "center",
  },
  field: {
    borderRadius: 14,
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 12,
    minHeight: 48,
    fontSize: 15,
  },
  search: {
    borderRadius: 999,
    borderWidth: 1,
    paddingHorizontal: 16,
    paddingLeft: 40,
    paddingVertical: 12,
    minHeight: 48,
    fontSize: 15,
  },
  section: { gap: 8 },
  sectionLabel: { fontSize: 12, fontWeight: "700", letterSpacing: 0.8, textTransform: "uppercase" },
  card: {
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: "hidden",
    paddingHorizontal: 14,
    paddingVertical: 4,
  },
  cta: {
    minHeight: 50,
    borderRadius: 999,
    borderWidth: 1,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 16,
  },
  ctaLabel: { fontWeight: "700", fontSize: 16 },
  empty: { alignItems: "center", gap: 6, paddingVertical: 20, paddingHorizontal: 12 },
  emptyTile: { width: 48, height: 48, borderRadius: 24, alignItems: "center", justifyContent: "center" },
  emptyTitle: { fontSize: 15, fontWeight: "700" },
  emptyCopy: { fontSize: 13, lineHeight: 18, textAlign: "center" },
  error: { fontSize: 13, lineHeight: 18 },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  pillDot: { width: 8, height: 8, borderRadius: 4 },
  pillLabel: { fontSize: 12, fontWeight: "700" },
});
