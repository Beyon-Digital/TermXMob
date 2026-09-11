import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { StyleSheet, Text, View } from "react-native";
import { WebView } from "react-native-webview";

import type { TerminalHandle, TerminalViewProps } from "@/components/terminal-view.types";
import { useAppTheme } from "@/hooks/use-app-theme";

function toB64(data: Uint8Array): string {
  let s = "";
  data.forEach((b) => {
    s += String.fromCharCode(b);
  });
  return btoa(s);
}

export const TerminalView = forwardRef<TerminalHandle, TerminalViewProps>(function TerminalView(
  { embedUrl, onInput, onResize },
  ref,
) {
  const { theme } = useAppTheme();
  const terminalTheme = theme.terminal;
  const webRef = useRef<WebView>(null);
  const readyRef = useRef(false);
  const themeRef = useRef(terminalTheme);
  const [loadError, setLoadError] = useState("");
  themeRef.current = terminalTheme;

  const inject = (msg: object) => {
    const payload = JSON.stringify(msg);
    webRef.current?.injectJavaScript(
      `window.dispatchEvent(new MessageEvent('message', { data: ${JSON.stringify(payload)} })); true;`,
    );
  };

  const sendTheme = () => inject({ type: "theme", theme: themeRef.current });

  useImperativeHandle(ref, () => ({
    write(data) {
      if (typeof data === "string") inject({ type: "write", data });
      else inject({ type: "bin", data: toB64(data) });
    },
    reset() {
      inject({ type: "reset" });
    },
    fit() {
      inject({ type: "fit" });
    },
  }));

  useEffect(() => {
    if (readyRef.current) sendTheme();
  }, [terminalTheme]);

  return (
    <View style={[styles.fill, { backgroundColor: terminalTheme.background }]}>
      <WebView
        ref={webRef}
        source={{ uri: embedUrl }}
        originWhitelist={["*"]}
        javaScriptEnabled
        automaticallyAdjustContentInsets={false}
        setSupportMultipleWindows={false}
        scrollEnabled={false}
        hideKeyboardAccessoryView
        keyboardDisplayRequiresUserAction={false}
        nestedScrollEnabled
        androidLayerType="hardware"
        onLoadStart={() => setLoadError("")}
        onLoadEnd={() => {
          readyRef.current = true;
          sendTheme();
          inject({ type: "fit" });
        }}
        onError={(event) => {
          setLoadError(event.nativeEvent.description || "Terminal failed to load");
        }}
        onHttpError={(event) => {
          if (event.nativeEvent.statusCode >= 400) {
            setLoadError(`Terminal HTTP ${event.nativeEvent.statusCode}`);
          }
        }}
        onMessage={(event) => {
          try {
            const msg = JSON.parse(event.nativeEvent.data) as {
              type?: string;
              data?: string;
              cols?: number;
              rows?: number;
            };
            if (msg.type === "input" && typeof msg.data === "string") onInput(msg.data);
            if ((msg.type === "resize" || msg.type === "ready") && msg.cols && msg.rows) {
              readyRef.current = true;
              onResize(msg.cols, msg.rows);
            }
          } catch {
            /* ignore */
          }
        }}
        containerStyle={styles.fill}
        style={[styles.fill, { backgroundColor: terminalTheme.background, opacity: 0.99 }]}
      />
      {loadError ? (
        <View style={styles.error} pointerEvents="none">
          <Text style={styles.errorText}>{loadError}</Text>
        </View>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  fill: { flex: 1, minHeight: 0 },
  error: {
    ...StyleSheet.absoluteFill,
    alignItems: "center",
    justifyContent: "center",
    padding: 16,
  },
  errorText: { color: "#ff8a8a", textAlign: "center" },
});
