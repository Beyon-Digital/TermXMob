import { useKeepAwake } from "expo-keep-awake";
import { useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import { KeyboardAvoidingView, Platform, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { CommandsSheet } from "@/components/commands-sheet";
import { DefaultsSheet } from "@/components/defaults-sheet";
import { DirectoriesSheet } from "@/components/directories-sheet";
import { DesktopView } from "@/components/desktop-view";
import { ExtraKeys } from "@/components/extra-keys";
import { SessionTabs } from "@/components/session-tabs";
import { TerminalPane, type TerminalPaneHandle } from "@/components/terminal-pane";
import { TunnelSheet } from "@/components/tunnel-sheet";
import { WorkspaceChrome } from "@/components/workspace-chrome";
import { useAppTheme } from "@/hooks/use-app-theme";
import { useDesktop } from "@/hooks/use-desktop";
import { useLayout } from "@/hooks/use-layout";
import { createSession, createVirtualDisplay, fetchHealth, killSession, listSessions } from "@/lib/api";
import { connectionToHostInput } from "@/lib/parse-url";
import { httpBase, getCurrentConnection, type Connection, type Health, type SessionInfo, type WorkspaceMode } from "@/lib/types";

function reconcilePanes(
  previous: string[],
  available: string[],
  preferId: string | null | undefined,
  maxPanes: number,
): string[] {
  const wanted = [preferId ?? previous[0] ?? available[0], ...previous.slice(1)];
  const out: string[] = [];
  const used = new Set<string>();
  const take = (id?: string | null) => {
    const pick = id && available.includes(id) && !used.has(id) ? id : available.find((item) => !used.has(item));
    if (pick && !used.has(pick)) {
      out.push(pick);
      used.add(pick);
    }
  };
  take(wanted[0]);
  for (let index = 1; index < Math.min(wanted.length, maxPanes); index += 1) {
    take(wanted[index]);
  }
  return out.length ? out : available.slice(0, 1);
}

function assignToPane(previous: string[], id: string, focus: number, splitEnabled: boolean): string[] {
  if (!previous.length) return [id];
  const next = previous.slice(0, splitEnabled ? 2 : 1);
  const index = Math.min(focus, next.length - 1);
  if (next.length > 1) {
    const other = index === 0 ? 1 : 0;
    if (next[other] === id) next[other] = next[index];
  }
  next[index] = id;
  return next;
}

export default function WorkspaceScreen() {
  useKeepAwake();
  const router = useRouter();
  const { theme } = useAppTheme();
  const layout = useLayout();
  const [connection, setConnection] = useState<Connection | null>(getCurrentConnection());
  const [health, setHealth] = useState<Health | null>(null);
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [panes, setPanes] = useState<string[]>([]);
  const [focused, setFocused] = useState(0);
  const [ctrlOn, setCtrlOn] = useState(false);
  const [altOn, setAltOn] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<WorkspaceMode>("terminal");
  const [sheet, setSheet] = useState<"commands" | "defaults" | "folders" | "tunnel" | null>(null);
  const paneHandles = useRef<Record<string, TerminalPaneHandle | null>>({});
  const sizeRef = useRef({ cols: 80, rows: 24 });
  const focusedRef = useRef(focused);
  const panesRef = useRef(panes);
  const layoutRef = useRef(layout);
  focusedRef.current = focused;
  panesRef.current = panes;
  layoutRef.current = layout;

  const activeId = panes[focused] ?? sessions[0]?.id ?? null;

  const refresh = useCallback(async (conn: Connection, preferId?: string | null) => {
    const existing = await listSessions(conn);
    let next = existing;
    if (!next.length) {
      next = [await createSession(conn, sizeRef.current.cols, sizeRef.current.rows)];
    }
    setSessions(next);
    const available = next.map((session) => session.id);
    const maxPanes = layoutRef.current.splitEnabled ? 2 : 1;
    const nextPanes = reconcilePanes(panesRef.current, available, preferId, maxPanes);
    panesRef.current = nextPanes;
    setPanes(nextPanes);
    if (preferId) {
      const index = nextPanes.indexOf(preferId);
      if (index >= 0) setFocused(index);
    }
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

  const desktop = useDesktop(connection, mode === "desktop", {
    webrtc: Boolean(health?.capabilities?.webrtc),
  });

  const openSession = useCallback((created: SessionInfo) => {
    setSessions((current) => [...current, created]);
    setPanes((previous) =>
      assignToPane(previous, created.id, focusedRef.current, layoutRef.current.splitEnabled),
    );
  }, []);

  const selectSession = useCallback((id: string) => {
    setPanes((previous) => {
      if (!previous.length) return [id];
      const next = [...previous];
      const index = Math.min(focusedRef.current, next.length - 1);
      if (next.length > 1) {
        const other = index === 0 ? 1 : 0;
        if (next[other] === id) next[other] = next[index];
      }
      next[index] = id;
      return next;
    });
  }, []);

  const newSession = useCallback(async () => {
    if (!connection) return;
    const created = await createSession(connection, sizeRef.current.cols, sizeRef.current.rows);
    openSession(created);
  }, [connection, openSession]);

  const killActive = useCallback(async () => {
    if (!connection || !activeId) return;
    await killSession(connection, activeId);
    await refresh(connection, null);
  }, [activeId, connection, refresh]);

  const toggleSplit = useCallback(() => {
    if (!connection) return;
    const current = panesRef.current;
    if (current.length > 1) {
      const keep = current[Math.min(focusedRef.current, current.length - 1)] ?? current[0];
      const next = [keep];
      panesRef.current = next;
      setPanes(next);
      setFocused(0);
      return;
    }
    const other = sessions.find((session) => !current.includes(session.id));
    if (other) {
      const next = [current[0], other.id];
      panesRef.current = next;
      setPanes(next);
      return;
    }
    void createSession(connection, sizeRef.current.cols, sizeRef.current.rows).then((created) => {
      setSessions((previous) => [...previous, created]);
      const next = [panesRef.current[0], created.id];
      panesRef.current = next;
      setPanes(next);
    });
  }, [connection, sessions]);

  useEffect(() => {
    if (layout.splitEnabled || panesRef.current.length <= 1) return;
    const keep = panesRef.current[Math.min(focusedRef.current, panesRef.current.length - 1)] ?? panesRef.current[0];
    const next = [keep];
    panesRef.current = next;
    setPanes(next);
    setFocused(0);
  }, [layout.splitEnabled]);

  if (!connection) return null;
  const embedUrl = `${httpBase(connection)}/_/embed.html`;
  const caps = health?.capabilities;
  const focusedIndex = Math.min(focused, Math.max(panes.length - 1, 0));
  const focusedSessionId = panes[focusedIndex] ?? activeId;
  const focusedHandle = focusedSessionId ? paneHandles.current[focusedSessionId] : null;

  return (
    <View style={[styles.screen, { backgroundColor: theme.ui.background }]}>
      <SafeAreaView style={styles.safe} edges={["top", "left", "right"]}>
        <KeyboardAvoidingView
          style={styles.flex}
          behavior={Platform.OS === "ios" ? "padding" : undefined}
          keyboardVerticalOffset={0}>
          <WorkspaceChrome
            title={health?.hostname || connectionToHostInput(connection)}
            mode={mode}
            tunnel={health?.tunnel}
            onMode={(next) => {
              if (mode === "desktop" && next !== "desktop") desktop.send({ type: "release_all" });
              setMode(next);
            }}
            onCommands={() => setSheet("commands")}
            onDefaults={() => setSheet("defaults")}
            onFolders={() => setSheet("folders")}
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
                splitEnabled={layout.splitEnabled}
                splitActive={panes.length > 1}
                onSelect={selectSession}
                onNew={() => void newSession()}
                onKill={() => void killActive()}
                onSplit={toggleSplit}
              />
              <View style={[styles.panes, panes.length > 1 && styles.panesRow]}>
                {panes.map((id, index) => {
                  const session = sessions.find((item) => item.id === id);
                  return (
                    <TerminalPane
                      key={id}
                      ref={(handle) => {
                        paneHandles.current[id] = handle;
                      }}
                      connection={connection}
                      sessionId={id}
                      title={session?.title ?? "Session"}
                      embedUrl={embedUrl}
                      focused={focusedIndex === index}
                      ctrlOn={ctrlOn}
                      altOn={altOn}
                      showHeader={panes.length > 1}
                      onModifiersUsed={() => {
                        setCtrlOn(false);
                        setAltOn(false);
                      }}
                      onFocus={() => setFocused(index)}
                      onSize={(cols, rows) => {
                        sizeRef.current = { cols, rows };
                      }}
                      onExit={() => {
                        if (connection) void refresh(connection);
                      }}
                      onClosePane={
                        panes.length > 1
                          ? () => {
                              const remaining = panesRef.current.filter((_, itemIndex) => itemIndex !== index);
                              const next = remaining.length ? remaining : [id];
                              panesRef.current = next;
                              setPanes(next);
                              setFocused(0);
                            }
                          : undefined
                      }
                    />
                  );
                })}
              </View>
              {layout.showExtraKeys ? (
                <ExtraKeys
                  ctrlOn={ctrlOn}
                  altOn={altOn}
                  onCtrl={() => setCtrlOn((value) => !value)}
                  onAlt={() => setAltOn((value) => !value)}
                  onInput={(data) => {
                    if (data === "\x03") focusedHandle?.sendSignal("int");
                    focusedHandle?.sendRaw(data);
                  }}
                  onSignal={(name) => focusedHandle?.sendSignal(name)}
                  onHideKeyboard={() => focusedHandle?.blur()}
                />
              ) : null}
            </>
          ) : (
            <DesktopView
              frame={desktop.frame}
              viewOnly={desktop.viewOnly}
              displays={desktop.displays}
              selectedDisplayId={desktop.selectedDisplayId}
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
              onSelectDisplay={(id) => desktop.selectDisplay(id)}
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
        onRun={(command) => focusedHandle?.sendRaw(command)}
      />
      <DefaultsSheet
        connection={connection}
        isPresented={sheet === "defaults"}
        onDismiss={() => setSheet(null)}
        onApplied={async () => {
          const created = await createSession(connection, sizeRef.current.cols, sizeRef.current.rows);
          openSession(created);
          setSheet(null);
        }}
      />
      <DirectoriesSheet
        connection={connection}
        isPresented={sheet === "folders"}
        onDismiss={() => setSheet(null)}
        onOpen={async (cwd) => {
          const created = await createSession(connection, sizeRef.current.cols, sizeRef.current.rows, { cwd });
          openSession(created);
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
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  safe: { flex: 1 },
  flex: { flex: 1, minHeight: 0 },
  panes: { flex: 1, minHeight: 0 },
  panesRow: { flexDirection: "row" },
  error: { paddingHorizontal: 12, paddingVertical: 6 },
});
