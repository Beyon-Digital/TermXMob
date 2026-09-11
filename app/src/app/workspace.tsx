import { useKeepAwake } from "expo-keep-awake";
import { useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { BottomSheetModalProvider } from "@expo/ui/community/bottom-sheet";
import { KeyboardAvoidingView, Platform, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { CommandsSheet } from "@/components/commands-sheet";
import { DefaultsSheet } from "@/components/defaults-sheet";
import { DesktopView } from "@/components/desktop-view";
import { ExtraKeys } from "@/components/extra-keys";
import { SessionTabs } from "@/components/session-tabs";
import { TerminalView, type TerminalHandle } from "@/components/terminal-view";
import { TunnelSheet } from "@/components/tunnel-sheet";
import { WorkspaceChrome } from "@/components/workspace-chrome";
import { useAppTheme } from "@/hooks/use-app-theme";
import { useDesktop } from "@/hooks/use-desktop";
import { usePty } from "@/hooks/use-pty";
import { createSession, createVirtualDisplay, fetchHealth, killSession, listSessions } from "@/lib/api";
import { ctrlChar } from "@/lib/keys";
import { connectionToHostInput } from "@/lib/parse-url";
import { httpBase, getCurrentConnection, type Connection, type Health, type SessionInfo, type WorkspaceMode } from "@/lib/types";

export default function WorkspaceScreen() {
  useKeepAwake();
  const router = useRouter();
  const { theme } = useAppTheme();
  const [connection, setConnection] = useState<Connection | null>(getCurrentConnection());
  const [health, setHealth] = useState<Health | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [ctrlOn, setCtrlOn] = useState(false);
  const [altOn, setAltOn] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<WorkspaceMode>("terminal");
  const [sheet, setSheet] = useState<"commands" | "defaults" | "tunnel" | null>(null);
  const termRef = useRef<TerminalHandle>(null);
  const sizeRef = useRef({ cols: 80, rows: 24 });

  const onOutput = useCallback((data: string | Uint8Array) => {
    termRef.current?.write(data);
  }, []);

  const refresh = useCallback(async (conn: Connection, preferId?: string | null) => {
    const existing = await listSessions(conn);
    let next = existing;
    if (!next.length) {
      next = [await createSession(conn, sizeRef.current.cols, sizeRef.current.rows)];
    }
    setSessions(next);
    const pick = next.find((s) => s.id === preferId) ?? next[0];
    setActiveId(pick.id);
  }, []);

  useEffect(() => {
    const conn = getCurrentConnection();
    if (!conn) {
      router.replace("/");
      return;
    }
    setConnection(conn);
    fetchHealth(conn)
      .then((next) => {
        setHealth(next);
        return refresh(conn);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "Lost server");
        router.replace("/");
      });
  }, [refresh, router]);

  const { sendRaw, sendResize, sendSignal } = usePty(connection, activeId, {
    onOutput,
    onReconnect: () => termRef.current?.fit(),
    onExit: () => {
      if (connection) void refresh(connection);
    },
  });

  const desktop = useDesktop(connection, mode === "desktop", {
    webrtc: Boolean(health?.capabilities?.webrtc),
  });

  useEffect(() => {
    termRef.current?.reset();
    termRef.current?.fit();
  }, [activeId]);

  const onKeyboardInput = useCallback(
    (data: string) => {
      let next = data;
      if (ctrlOn) {
        next = ctrlChar(next);
        setCtrlOn(false);
      }
      if (altOn) {
        next = `\x1b${next}`;
        setAltOn(false);
      }
      if (next === "\x03") sendSignal("int");
      sendRaw(next);
    },
    [altOn, ctrlOn, sendRaw, sendSignal],
  );

  const onResize = useCallback(
    (cols: number, rows: number) => {
      sizeRef.current = { cols, rows };
      sendResize(cols, rows);
    },
    [sendResize],
  );

  if (!connection) return null;
  const embedUrl = `${httpBase(connection)}/_/embed.html`;
  const caps = health?.capabilities;

  return (
    <BottomSheetModalProvider>
    <View style={[styles.screen, { backgroundColor: theme.ui.background }]}>
      <SafeAreaView style={styles.safe} edges={["top", "left", "right"]}>
        <KeyboardAvoidingView
          style={styles.flex}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          keyboardVerticalOffset={0}>
          <WorkspaceChrome
            title={health?.hostname || connectionToHostInput(connection)}
            subtitle={connectionToHostInput(connection)}
            mode={mode}
            tunnel={health?.tunnel}
            onMode={(next) => {
              if (mode === "desktop" && next !== "desktop") desktop.send({ type: "release_all" });
              setMode(next);
            }}
            onCommands={() => setSheet("commands")}
            onDefaults={() => setSheet("defaults")}
            onTunnel={() => setSheet("tunnel")}
            onSettings={() => router.push("/settings")}
            onMachines={() => router.replace("/")}
          />
          {error ? <Text style={[styles.error, { color: theme.ui.danger }]}>{error}</Text> : null}
          {mode === "terminal" ? (
            <>
              <SessionTabs
                sessions={sessions}
                activeId={activeId}
                onSelect={(id) => setActiveId(id)}
                onNew={async () => {
                  const created = await createSession(connection, sizeRef.current.cols, sizeRef.current.rows);
                  setSessions((s) => [...s, created]);
                  setActiveId(created.id);
                }}
                onKill={async () => {
                  if (!activeId) return;
                  await killSession(connection, activeId);
                  await refresh(connection, null);
                }}
                onServers={() => router.replace("/")}
                onSettings={() => router.push("/settings")}
              />
              <View style={styles.term}>
                <TerminalView ref={termRef} embedUrl={embedUrl} onInput={onKeyboardInput} onResize={onResize} />
              </View>
              <ExtraKeys
                ctrlOn={ctrlOn}
                altOn={altOn}
                onCtrl={() => setCtrlOn((v) => !v)}
                onAlt={() => setAltOn((v) => !v)}
                onInput={(data) => {
                  if (data === "\x03") sendSignal("int");
                  sendRaw(data);
                }}
                onSignal={sendSignal}
              />
            </>
          ) : (
            <DesktopView
              frame={desktop.frame}
              viewOnly={desktop.viewOnly}
              displays={desktop.displays}
              error={desktop.error}
              canCreateVirtual={Boolean(caps?.virtual_display)}
              virtualReason={
                caps?.virtual_display
                  ? undefined
                  : "This host cannot create a virtual display yet. Physical screens can still be mirrored when capture is available."
              }
              fps={undefined}
              onExit={() => router.replace("/")}
              onToggleControl={() => desktop.setControl(!desktop.viewOnly)}
              onPointer={desktop.pointer}
              onSelectDisplay={() => {}}
              onClipboardGet={() => desktop.send({ type: "clipboard", action: "get" })}
              onClipboardSet={() => {
                void (async () => {
                  let text = "";
                  try {
                    const clip = (globalThis as { navigator?: { clipboard?: { readText?: () => Promise<string> } } })
                      .navigator?.clipboard;
                    if (clip?.readText) text = await clip.readText();
                  } catch {
                    text = "";
                  }
                  desktop.send({ type: "clipboard", action: "set", text });
                })();
              }}
              onCreateVirtual={() => {
                void createVirtualDisplay(connection, { width: 1170, height: 2532 }).catch((err: unknown) => {
                  setError(err instanceof Error ? err.message : "Virtual display unavailable");
                });
              }}
            />
          )}
        </KeyboardAvoidingView>
      </SafeAreaView>
      <CommandsSheet
        connection={connection}
        isPresented={sheet === "commands"}
        onDismiss={() => setSheet(null)}
        onRun={(command) => sendRaw(command)}
      />
      <DefaultsSheet
        connection={connection}
        isPresented={sheet === "defaults"}
        onDismiss={() => setSheet(null)}
        onApplied={async () => {
          const created = await createSession(connection, sizeRef.current.cols, sizeRef.current.rows);
          setSessions((s) => [...s, created]);
          setActiveId(created.id);
          setSheet(null);
        }}
      />
      <TunnelSheet
        connection={connection}
        isPresented={sheet === "tunnel"}
        onDismiss={() => {
          setSheet(null);
          fetchHealth(connection).then(setHealth).catch(() => {});
        }}
      />
    </View>
    </BottomSheetModalProvider>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  safe: { flex: 1 },
  flex: { flex: 1, minHeight: 0 },
  term: { flex: 1, minHeight: 0 },
  error: { paddingHorizontal: 12, paddingVertical: 6 },
});
