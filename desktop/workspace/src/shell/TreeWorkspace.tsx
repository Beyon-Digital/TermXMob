import {Suspense} from 'react';
import TabGroups from '../components/shell/TabGroups';
import {KIND_LABEL} from '../components/shell/tab-meta';
import type {Tab} from '../lib/layout/model';
import type {WindowPanel} from '../lib/window-journal';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAgentPanels, type AgentPanelProps} from './agent-panels';
import type {TreeView} from './layout-controller';
import TabContent, {type TabHost} from './tab-content';
import {useWorkspaceData} from './WorkspaceDataProvider';
import {windowPanelForKind} from './window-panel';

type Props = {
  workspace: WorkspaceMode;
  tree: TreeView;
  panels: AgentPanelProps;
  host: Omit<TabHost, 'panel' | 'closeTab' | 'setDirty'>;
  /** Open a surface in its own window. */
  onDetach: (panel: WindowPanel) => void;
};

/** The Agent workspace in the tab-group layout: the layout tree, its tabs and the content of each. */
export default function TreeWorkspace({workspace, tree, panels, host, onDetach}: Props) {
  const panel = useAgentPanels(panels);
  const {session, selected} = useWorkspaceData();
  const tabHost: TabHost = {...host, panel, closeTab: tree.closeTab, setDirty: tree.setDirty};

  function titleOf(tab: Tab): string {
    switch (tab.kind) {
      case 'chat':
        return panels.primaryId && selected === panels.primaryId ? 'Main agent' : session?.title || 'Chat';
      case 'file':
        return tab.ref ? (tab.ref.split('/').filter(Boolean).pop() ?? tab.ref) : KIND_LABEL.file;
      default:
        return KIND_LABEL[tab.kind];
    }
  }

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
        dirty={tree.dirty}
        onClose={tree.closeTab}
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
    </div>
  );
}
