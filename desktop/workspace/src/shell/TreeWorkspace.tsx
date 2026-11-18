import {Suspense, useEffect, useMemo, useState} from 'react';
import TabGroups from '../components/shell/TabGroups';
import {KIND_LABEL, OPENABLE_KINDS, PANEL_OPENABLE_KINDS} from '../components/shell/tab-meta';
import {useBuffers} from '../features/editor/buffer-store';
import {useCliSessions} from '../features/cli/CliSessionsProvider';
import {deletePty} from '../features/terminal/terminal-api';
import {useBrowserTabTitles} from '../lib/browser-tab-titles';
import {sessionName} from '../lib/cli-sessions';
import {allTabs, type Tab} from '../lib/layout/model';
import type {WindowPanel} from '../lib/window-journal';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAgentPanels, type AgentPanelProps} from './agent-panels';
import DiscardFilesDialog from './dialogs/DiscardFilesDialog';
import {useReport} from './errors';
import type {TreeView} from './layout-controller';
import TabContent, {type TabHost, type TabHostInput} from './tab-content';
import {tabTitle} from './tab-titles';
import {useTerminalTabs} from './use-terminal-tabs';
import {useWorkspaceData} from './WorkspaceDataProvider';
import {windowPanelForKind} from './window-panel';

type Props = {
  workspace: WorkspaceMode;
  tree: TreeView;
  panels: AgentPanelProps;
  host: TabHostInput;
  /** Open a surface in its own window. */
  onDetach: (panel: WindowPanel) => void;
  /** Phone: draw no tab strips; the shell's top bar names and switches the tab. */
  hideStrip?: boolean;
  /** Phone: the tab shown in the single stack, owned by the shell so its top bar can switch it. */
  chosenTab?: string | null;
  onChosenTab?: (id: string) => void;
};

/** The Agent workspace in the tab-group layout: the layout tree, its tabs and the content of each. */
export default function TreeWorkspace({workspace, tree, panels, host, onDetach, hideStrip, chosenTab, onChosenTab}: Props) {
  const panel = useAgentPanels(panels);
  const {session, selected} = useWorkspaceData();
  const store = useBuffers();
  const report = useReport();
  const [discard, setDiscard] = useState<{paths: string[]; run: () => void} | null>(null);
  const cliSessions = useCliSessions();
  const browserTitles = useBrowserTabTitles();
  const terminals = useTerminalTabs({dispatch: tree.dispatch, project: host.project, session, onError: report});
  const tabs = allTabs(tree.layout);
  const terminalRefs = tabs.flatMap(tab => (tab.kind === 'terminal' && tab.ref ? [tab.ref] : []));
  const tabHost: TabHost = {...host, panel, closeTab: tree.closeTab, setDirty: tree.setDirty, dispatch: tree.dispatch, terminalRefs};

  // Closing a file tab with unsaved edits asks first; closing a file tab drops its buffer, and closing a terminal tab ends its shell.
  useEffect(() => {
    tree.setCloseGuard((closing, proceed) => {
      const files = closing.filter(tab => tab.kind === 'file' && tab.ref);
      const shells = closing.flatMap(tab => (tab.kind === 'terminal' && tab.ref ? [tab.ref] : []));
      if (!files.length && !shells.length) return false;
      const run = () => {
        for (const tab of files) store.remove(tab.ref!);
        proceed();
        for (const id of shells) void deletePty(id).catch(report);
      };
      const dirty = files.filter(tab => store.get(tab.ref!)?.dirty);
      if (dirty.length) setDiscard({paths: dirty.map(tab => store.get(tab.ref!)!.path), run});
      else run();
      return true;
    });
    return () => tree.setCloseGuard(null);
  }, [tree.setCloseGuard, store.get, store.remove, report]);

  // A buffer with unsaved edits always has a tab: restored or handed-over edits show up as tabs, and a layout swap cannot hide them.
  const open = new Set(tabs.flatMap(tab => (tab.kind === 'file' && tab.ref ? [tab.ref] : [])));
  const orphans = store.ready ? store.buffers.filter(buffer => buffer.dirty && !open.has(buffer.key)).map(buffer => buffer.key) : [];
  useEffect(() => {
    for (const key of orphans) tree.dispatch({type: 'openTab', kind: 'file', ref: key});
  }, [orphans.join('\u0000')]);

  const dirtyTabs = useMemo(() => {
    const set = new Set(tree.dirty);
    for (const tab of tabs) if (tab.kind === 'file' && tab.ref && store.get(tab.ref)?.dirty) set.add(tab.id);
    return set;
  }, [tree.dirty, tree.layout, store.buffers]);

  const titleOf = (tab: Tab) =>
    tabTitle(tab, {
      tabs,
      primaryId: panels.primaryId,
      selected,
      sessionTitle: session?.title,
      file: key => {
        const buffer = store.get(key);
        return buffer && {path: buffer.path, recovered: buffer.key.includes(':handoff:')};
      },
      cli: id => {
        const row = cliSessions.get(id);
        return row ? sessionName(row) : undefined;
      },
      browser: id => browserTitles.get(id),
    });

  return (
    <div
      id="agent-workspace"
      className="workspace-dock-host"
      role="region"
      aria-label="Agent workspace"
      hidden={workspace !== 'agent'}
      inert={workspace !== 'agent'}
      aria-hidden={workspace !== 'agent' ? true : undefined}
    >
      <TabGroups
        label="Workspace tabs"
        layout={tree.layout}
        dispatch={tree.dispatch}
        titleOf={titleOf}
        dirty={dirtyTabs}
        hideStrip={hideStrip}
        chosenTab={chosenTab}
        onChosenTab={onChosenTab}
        onClose={tree.closeTab}
        openableFor={group => (group.id === tree.layout.panel.group.id ? PANEL_OPENABLE_KINDS : OPENABLE_KINDS)}
        onNewTab={(kind, group) => {
          if (kind === 'terminal') {
            void terminals.create(group);
            return true;
          }
          if (kind === 'file') {
            host.findFile();
            return true;
          }
          return false;
        }}
        onDetach={tab => {
          const target = windowPanelForKind(tab.kind);
          if (target) onDetach(target);
        }}
        canDetach={tab => windowPanelForKind(tab.kind) !== null}
        render={(tab, {visible}) => (
          <Suspense fallback={<p className="panel-loading">Opening {KIND_LABEL[tab.kind].toLowerCase()}…</p>}>
            <TabContent tab={tab} visible={visible} host={tabHost} />
          </Suspense>
        )}
      />
      <DiscardFilesDialog
        paths={discard?.paths ?? []}
        onCancel={() => setDiscard(null)}
        onDiscard={() => {
          discard?.run();
          setDiscard(null);
        }}
      />
    </div>
  );
}
