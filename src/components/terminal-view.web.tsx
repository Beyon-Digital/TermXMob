import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { StyleSheet, View } from "react-native";

import type { TerminalHandle, TerminalViewProps } from "@/components/terminal-view.types";
import { useAppTheme } from "@/hooks/use-app-theme";

import "@xterm/xterm/css/xterm.css";

export const TerminalView = forwardRef<TerminalHandle, TerminalViewProps>(function TerminalView(
  { onInput, onResize },
  ref,
) {
  const { theme } = useAppTheme();
  const terminalTheme = theme.terminal;
  const hostRef = useRef<HTMLDivElement | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const onInputRef = useRef(onInput);
  const onResizeRef = useRef(onResize);
  const themeRef = useRef(terminalTheme);
  onInputRef.current = onInput;
  onResizeRef.current = onResize;
  themeRef.current = terminalTheme;

  useImperativeHandle(ref, () => ({
    write(data) {
      termRef.current?.write(data as string | Uint8Array);
    },
    reset() {
      termRef.current?.reset();
    },
    fit() {
      try {
        fitRef.current?.fit();
      } catch {
        /* layout may not be ready */
      }
      const term = termRef.current;
      if (term) onResizeRef.current(term.cols, term.rows);
      term?.focus();
    },
  }));

  useEffect(() => {
    const el = hostRef.current;
    if (!el) return;
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
      theme: themeRef.current,
      scrollback: 4000,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(el);
    termRef.current = term;
    fitRef.current = fit;
    const dataDisp = term.onData((d) => onInputRef.current(d));
    const ro = new ResizeObserver(() => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
      onResizeRef.current(term.cols, term.rows);
    });
    ro.observe(el);
    requestAnimationFrame(() => {
      try {
        fit.fit();
      } catch {
        /* ignore */
      }
      onResizeRef.current(term.cols, term.rows);
      term.focus();
    });
    el.addEventListener("mousedown", () => term.focus());
    return () => {
      dataDisp.dispose();
      ro.disconnect();
      term.dispose();
      termRef.current = null;
    };
  }, []);

  useEffect(() => {
    const term = termRef.current;
    if (term) term.options.theme = terminalTheme;
  }, [terminalTheme]);

  return (
    <View style={[styles.fill, { backgroundColor: terminalTheme.background }]} pointerEvents="box-none">
      <div
        ref={hostRef}
        style={{
          height: "100%",
          width: "100%",
          background: terminalTheme.background,
          cursor: "text",
        }}
      />
    </View>
  );
});

const styles = StyleSheet.create({
  fill: { flex: 1, minHeight: 0, overflow: "hidden", zIndex: 0 },
});
