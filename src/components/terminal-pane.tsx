import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef } from "react";
import { StyleSheet, Text, View } from "react-native";

import { IconButton } from "@/components/app-icon";
import { TerminalView, type TerminalHandle } from "@/components/terminal-view";
import { useAppTheme } from "@/hooks/use-app-theme";
import { usePty } from "@/hooks/use-pty";
import { ctrlChar } from "@/lib/keys";
import type { Connection } from "@/lib/types";

export type TerminalPaneHandle = {
  sendRaw: (data: string) => void;
  sendSignal: (name: string) => void;
  blur: () => void;
};

type Props = {
  connection: Connection;
  sessionId: string;
  title: string;
  embedUrl: string;
  focused: boolean;
  ctrlOn: boolean;
  altOn: boolean;
  showHeader: boolean;
  onModifiersUsed: () => void;
  onFocus: () => void;
  onSize: (cols: number, rows: number) => void;
  onExit: () => void;
  onClosePane?: () => void;
};

export const TerminalPane = forwardRef<TerminalPaneHandle, Props>(function TerminalPane(
  {
    connection,
    sessionId,
    title,
    embedUrl,
    focused,
    ctrlOn,
    altOn,
    showHeader,
    onModifiersUsed,
    onFocus,
    onSize,
    onExit,
    onClosePane,
  },
  ref,
) {
  const { theme } = useAppTheme();
  const { ui } = theme;
  const termRef = useRef<TerminalHandle>(null);
  const modifiersRef = useRef({ ctrlOn, altOn });
  const onExitRef = useRef(onExit);
  modifiersRef.current = { ctrlOn, altOn };
  onExitRef.current = onExit;

  const { sendRaw, sendResize, sendSignal } = usePty(connection, sessionId, {
    onOutput: (data) => termRef.current?.write(data),
    onReconnect: () => termRef.current?.fit(),
    onExit: () => onExitRef.current(),
  });

  useImperativeHandle(ref, () => ({ sendRaw, sendSignal, blur: () => termRef.current?.blur() }), [
    sendRaw,
    sendSignal,
  ]);

  const onInput = useCallback(
    (data: string) => {
      let next = data;
      const modifiers = modifiersRef.current;
      if (modifiers.ctrlOn) {
        next = ctrlChar(next);
        onModifiersUsed();
      }
      if (modifiers.altOn) {
        next = `\x1b${next}`;
        onModifiersUsed();
      }
      if (next === "\x03") sendSignal("int");
      sendRaw(next);
    },
    [onModifiersUsed, sendRaw, sendSignal],
  );

  const onResize = useCallback(
    (cols: number, rows: number) => {
      sendResize(cols, rows);
      onSize(cols, rows);
    },
    [onSize, sendResize],
  );

  useEffect(() => {
    termRef.current?.reset();
    termRef.current?.fit();
  }, [sessionId]);

  return (
    <View
      style={[styles.pane, focused ? { borderColor: ui.accent } : { borderColor: "transparent" }]}
      onTouchStart={onFocus}>
      {showHeader ? (
        <View style={[styles.header, { backgroundColor: ui.surface, borderBottomColor: ui.border }]}>
          <Text
            style={[styles.title, { color: focused ? ui.accent : ui.textMuted }]}
            numberOfLines={1}>
            {title}
          </Text>
          {onClosePane ? (
            <IconButton
              name="close"
              color={ui.textMuted}
              onPress={onClosePane}
              accessibilityLabel={`Close ${title} pane`}
              size={24}
            />
          ) : null}
        </View>
      ) : null}
      <View style={styles.body}>
        <TerminalView
          ref={termRef}
          embedUrl={embedUrl}
          onInput={onInput}
          onResize={onResize}
          onFocus={onFocus}
        />
      </View>
    </View>
  );
});

const styles = StyleSheet.create({
  pane: { flex: 1, minWidth: 0, minHeight: 0, borderWidth: 1, overflow: "hidden" },
  header: {
    flexShrink: 0,
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 30,
    paddingLeft: 10,
    paddingRight: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
  },
  title: { flex: 1, minWidth: 0, fontSize: 12, fontWeight: "600" },
  body: { flex: 1, minHeight: 0 },
});
