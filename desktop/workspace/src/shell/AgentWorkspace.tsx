import DockWorkspace from '../components/DockWorkspace';
import {panelSurface, panels as dockPanels} from '../lib/docking';
import type {WindowPanel} from '../lib/window-journal';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAgentPanels, type AgentPanelProps} from './agent-panels';
import type {LayoutController, LegacyController} from './layout-controller';
import type {TabHostInput} from './tab-content';
import TreeWorkspace from './TreeWorkspace';
import {useAuth} from './AuthGate';

export type {FileRequest} from './agent-panels';

type Props = {
  workspace: WorkspaceMode;
  manager: string;
  /** What the panels need from the shell, whichever layout shows them. `bottom` is filled in from the layout. */
  panels: Omit<AgentPanelProps, 'bottom'>;
  layout: LayoutController;
  onFullscreen: () => void;
  /** Open a surface in its own window. */
  onDetach: (panel: WindowPanel) => void;
  /** What the tab layout's Settings, Projects, Workflows, Data and Studio tabs need. Unused by the old dock. */
  tabHost: TabHostInput;
  /** Phone: the tab layout draws no tab strips. */
  hideStrip?: boolean;
  chosenTab?: string | null;
  onChosenTab?: (id: string) => void;
};

/**
 * The Agent workspace. A small switch: the old dock of seven panels, or the tab-group layout. Both show the same panel
 * components (`agent-panels.tsx`); they differ in where those sit.
 */
export default function AgentWorkspace(props: Props) {
  const {layout, workspace} = props;
  if (layout.engine === 'tree') {
    return (
      <TreeWorkspace
        workspace={workspace}
        tree={layout.tree}
        // The editor's own drawer (Terminal, Problems, Run & debug, Preview) stays closed: the layout has a real bottom panel.
        panels={{...props.panels, bottom: false}}
        host={props.tabHost}
        onDetach={props.onDetach}
        hideStrip={props.hideStrip}
        chosenTab={props.chosenTab}
        onChosenTab={props.onChosenTab}
      />
    );
  }
  return <DockedWorkspace {...props} layout={layout} />;
}

/** The old dock: Chat, Files, Changes, Terminal, Browser, Computer and Artifacts in regions. */
function DockedWorkspace(props: Props & {layout: LegacyController}) {
  const {workspace, manager, onFullscreen, onDetach} = props;
  const {dock, right, bottom, rightWidth, dockGeometry, panelActivation, visited, visiblePanels} = props.layout.legacy;
  const {locked} = useAuth();
  const panel = useAgentPanels({...props.panels, bottom});
  const children = Object.fromEntries(
    dockPanels.map(name => [
      name,
      // The conversation is always mounted; the other panels are mounted the first time they are shown.
      name === 'conversation' || visited.has(name) ? panel(name, visiblePanels.includes(name)) : null,
    ]),
  );
  return (
    <div
      id="agent-workspace"
      className="workspace-dock-host"
      role="region"
      aria-label="Agent workspace"
      hidden={workspace !== 'agent'}
      inert={!!manager || workspace !== 'agent'}
      aria-hidden={manager || workspace !== 'agent' ? true : undefined}
    >
      <DockWorkspace
        active={workspace === 'agent' && !manager && !locked}
        activation={panelActivation}
        onFullscreen={onFullscreen}
        onVisiblePanels={props.layout.legacy.setVisiblePanels}
        map={dock}
        onChange={props.layout.legacy.changeDock}
        rightVisible={right}
        bottomVisible={bottom}
        rightWidth={rightWidth}
        onRightWidth={props.layout.legacy.setRightWidth}
        geometry={dockGeometry}
        onGeometry={props.layout.legacy.setDockGeometry}
        onResizeStart={props.layout.rememberLayout}
        onDetach={name => onDetach(panelSurface(name))}
        children={children}
      />
    </div>
  );
}
