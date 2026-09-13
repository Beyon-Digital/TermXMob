import { useRef, useState } from "react";
import { Image, Pressable, StyleSheet, Text, View } from "react-native";

import { DisplayDock } from "@/components/display-dock";
import { useAppTheme } from "@/hooks/use-app-theme";
import type { DisplayInfo } from "@/lib/types";

type Props = {
  frame: string | null;
  viewOnly: boolean;
  displays: DisplayInfo[];
  selectedDisplayId?: string;
  error?: string;
  canCreateVirtual: boolean;
  virtualReason?: string;
  fps?: number;
  onToggleControl: () => void;
  onPointer: (action: string, x: number, y: number) => void;
  onSelectDisplay: (id: string) => void;
  onCreateVirtual: () => void;
  onClipboardGet?: () => void;
  onClipboardSet?: () => void;
  onExit?: () => void;
};

function requestWebFullscreen() {
  if (typeof document === "undefined") return;
  const root = document.documentElement as { requestFullscreen?: () => Promise<void> };
  if (typeof root.requestFullscreen === "function") void root.requestFullscreen();
}

export function DesktopView({
  frame,
  viewOnly,
  displays,
  selectedDisplayId,
  error,
  canCreateVirtual,
  virtualReason,
  fps,
  onToggleControl,
  onPointer,
  onSelectDisplay,
  onCreateVirtual,
  onClipboardGet,
  onClipboardSet,
  onExit,
}: Props) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const box = useRef({ w: 1, h: 1 });
  const [trackpad, setTrackpad] = useState(false);

  const normalize = (evt: { nativeEvent: { locationX: number; locationY: number } }) => {
    const x = Math.max(0, Math.min(1, evt.nativeEvent.locationX / box.current.w));
    const y = Math.max(0, Math.min(1, evt.nativeEvent.locationY / box.current.h));
    return { x, y };
  };

  return (
    <View style={styles.fill}>
      <View
        style={[styles.canvas, { backgroundColor: "#000" }]}
        onLayout={(e) => {
          box.current = { w: e.nativeEvent.layout.width || 1, h: e.nativeEvent.layout.height || 1 };
        }}
        onStartShouldSetResponder={() => !viewOnly}
        onMoveShouldSetResponder={() => !viewOnly}
        onResponderGrant={(e) => {
          const p = normalize(e);
          onPointer(trackpad ? "click" : "down", p.x, p.y);
        }}
        onResponderMove={(e) => {
          const p = normalize(e);
          onPointer("move", p.x, p.y);
        }}
        onResponderRelease={(e) => {
          const p = normalize(e);
          onPointer(trackpad ? "click" : "up", p.x, p.y);
        }}>
        {frame ? (
          <Image source={{ uri: frame }} style={styles.frame} resizeMode="contain" />
        ) : (
          <View style={styles.empty}>
            <Text style={{ color: ui.textMuted }}>{error || "Waiting for display…"}</Text>
          </View>
        )}
        <View style={styles.overlay} pointerEvents="box-none">
          <Chip
            bg={viewOnly ? ui.surface : ui.accent}
            color={viewOnly ? ui.text : ui.accentText}
            label={viewOnly ? "View only" : "Enable control"}
            accessibilityLabel={viewOnly ? "View only" : "Enable control"}
            selected={!viewOnly}
            onPress={onToggleControl}
          />
          <Chip
            bg={trackpad ? ui.accent : ui.surface}
            color={trackpad ? ui.accentText : ui.text}
            label={trackpad ? "Trackpad" : "Direct touch"}
            accessibilityLabel={trackpad ? "Trackpad" : "Direct touch"}
            selected={trackpad}
            onPress={() => setTrackpad((v) => !v)}
          />
          <Chip
            bg={ui.surface}
            color={ui.text}
            label="Full screen"
            accessibilityLabel="Full screen"
            onPress={requestWebFullscreen}
          />
          <Chip
            bg={ui.surface}
            color={ui.text}
            label="Copy from Mac"
            accessibilityLabel="Copy from Mac"
            onPress={() => onClipboardGet?.()}
          />
          <Chip
            bg={ui.surface}
            color={ui.text}
            label="Paste to Mac"
            accessibilityLabel="Paste to Mac"
            onPress={() => onClipboardSet?.()}
          />
          <Chip
            bg={ui.surface}
            color={ui.text}
            label="Exit control"
            accessibilityLabel="Exit control"
            onPress={() => onExit?.()}
          />
          {typeof fps === "number" ? (
            <View
              style={[styles.chip, { backgroundColor: ui.surface }]}
              accessibilityLabel={`${fps} fps`}
              accessibilityLiveRegion="polite">
              <Text style={{ color: ui.textMuted, fontWeight: "700" }}>{fps} fps</Text>
            </View>
          ) : null}
        </View>
      </View>
      {error ? <Text style={[styles.error, { color: ui.danger }]}>{error}</Text> : null}
      <DisplayDock
        displays={displays}
        selectedId={selectedDisplayId}
        canCreateVirtual={canCreateVirtual}
        virtualReason={virtualReason}
        onSelect={onSelectDisplay}
        onCreateVirtual={onCreateVirtual}
      />
    </View>
  );
}

function Chip({
  bg,
  color,
  label,
  accessibilityLabel,
  selected,
  onPress,
}: {
  bg: string;
  color: string;
  label: string;
  accessibilityLabel: string;
  selected?: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel}
      accessibilityState={selected !== undefined ? { selected } : undefined}
      onPress={onPress}
      style={[styles.chip, { backgroundColor: bg }]}>
      <Text style={{ color, fontWeight: "700" }}>{label}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, minHeight: 0 },
  canvas: { flex: 1, minHeight: 0, overflow: "hidden" },
  frame: { width: "100%", height: "100%" },
  empty: { flex: 1, alignItems: "center", justifyContent: "center" },
  overlay: {
    position: "absolute",
    top: 10,
    right: 10,
    left: 10,
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "flex-end",
    gap: 8,
  },
  chip: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
    minHeight: 44,
    justifyContent: "center",
    alignItems: "center",
  },
  error: { paddingHorizontal: 12, paddingVertical: 6 },
});
