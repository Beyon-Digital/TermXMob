import {lazy, Suspense, type Dispatch, type SetStateAction} from 'react';
import {Button} from '../components/ui/button';
import type {Bindings} from '../lib/keybindings';
import {isSettingsCategory} from '../lib/settings-categories';
import type {ThemePreference} from '../lib/theme';
import {useAuth} from './AuthGate';
import {InspectedTaskSupervisor} from './CoordinationSidebar';
import AddProjectHost from './dialogs/AddProjectHost';
import CommandPaletteDialog from './dialogs/CommandPaletteDialog';
import LayoutsDialog from './dialogs/LayoutsDialog';
import NewChatDialog from './dialogs/NewChatDialog';
import RecoverWindowsDialog from './dialogs/RecoverWindowsDialog';
import type {CommandContext} from './commands';
import type {LayoutController} from './layout-controller';
import type {ShellNavigation} from './navigation-state';
import SettingsHost from './SettingsHost';
import type {Coordination} from './use-coordination';
import type {HostsState} from './use-hosts-state';
import type {useNewChat} from './use-new-chat';
import type {useWindowTransfer} from './WindowTransfer';
import type {WorkspaceNavigation as Navigation} from './use-workspace-navigation';
import type {Command} from './commands';
import {useWorkspaceData} from './WorkspaceDataProvider';

const CompanionHabitat = lazy(() => import('../features/CompanionHabitat'));

type Props = {
  navigation: ShellNavigation;
  nav: Navigation;
  hosts: HostsState;
  layout: LayoutController;
  coordination: Coordination;
  newChat: ReturnType<typeof useNewChat>;
  transfer: ReturnType<typeof useWindowTransfer>;
  error: string | null;
  clearError: () => void;
  report: (reason: unknown) => void;
  botsWaiting: number;
  theme: ThemePreference;
  setTheme: (theme: ThemePreference) => void;
  bindings: Bindings;
  setBindings: Dispatch<SetStateAction<Bindings>>;
  commandContext: CommandContext;
  onRunCommand: (command: Command) => void;
  palette: boolean;
  setPalette: Dispatch<SetStateAction<boolean>>;
  addProjectOpen: boolean;
  setAddProjectOpen: Dispatch<SetStateAction<boolean>>;
  layoutsOpen: boolean;
  setLayoutsOpen: Dispatch<SetStateAction<boolean>>;
};

/** Everything that floats over the workspace: the companion, the error toast, Settings (old layout), and the dialogs. */
export default function ShellOverlays(props: Props) {
  const {navigation, nav, hosts, layout, coordination, newChat, transfer, report} = props;
  const {workspace, manager} = navigation;
  const {auth, locked} = useAuth();
  const {sessions, projects, projectId, setProjectId} = useWorkspaceData();
  return (
    <>
      {!locked && (
        <Suspense fallback={null}>
          <div hidden={workspace !== 'agent'} inert={workspace !== 'agent'}>
            <CompanionHabitat
              key={auth.principal.id}
              onOpen={nav.openConversation}
              onSettings={() => nav.setManager('characters')}
            />
          </div>
        </Suspense>
      )}
      <InspectedTaskSupervisor
        coordination={coordination}
        active={workspace === 'agent' && !manager && !locked}
        onError={report}
      />
      {props.error && (
        <div className="toast error-toast" role="alert">
          <span>{props.error}</span>
          <Button variant="ghost" size="sm" onClick={props.clearError}>
            Dismiss
          </Button>
        </div>
      )}
      {isSettingsCategory(manager) && !locked && (
        <SettingsHost
          manager={manager}
          onSection={nav.setManager}
          onClose={() => nav.setManager(nav.previousManager.current)}
          scope={hosts.managerScope}
          onScope={hosts.setManagerScope}
          settingsProjectId={hosts.settingsProjectId}
          onSettingsProject={hosts.setSettingsProjectId}
          project={nav.project}
          waiting={props.botsWaiting}
          theme={props.theme}
          onTheme={props.setTheme}
          bindings={props.bindings}
          onBindings={props.setBindings}
        />
      )}
      <RecoverWindowsDialog
        open={transfer.recoveryOpen}
        onOpenChange={transfer.setRecoveryOpen}
        rows={transfer.recoveryRows}
        sessions={sessions}
        onReopen={row => void transfer.reopenRecovered(row)}
      />
      <AddProjectHost
        open={props.addProjectOpen && workspace === 'agent'}
        onOpenChange={props.setAddProjectOpen}
        navigationIntent={() => nav.navigationIntent.current}
        onProjectAdded={nav.openProject}
      />
      <NewChatDialog
        open={newChat.newOpen && workspace === 'agent'}
        onOpenChange={newChat.setNewOpen}
        ownerId={auth.principal.id}
        keepFocusOnClose={() => !!newChat.newComposerFocus.current}
        projects={projects}
        projectId={projectId}
        onProject={setProjectId}
        presets={newChat.newPresets}
        presetsLoading={newChat.newPresetsLoading}
        preset={newChat.newPreset}
        onPreset={newChat.setNewPreset}
        folder={newChat.folder}
        onFolder={newChat.setFolder}
        creating={newChat.registering}
        locked={locked}
        onCreate={() => void newChat.createConversation()}
      />
      <LayoutsDialog
        open={props.layoutsOpen && workspace === 'agent'}
        onOpenChange={props.setLayoutsOpen}
        presets={layout.saved}
        onSave={layout.saveLayout}
        onRestore={id => {
          if (layout.restoreSaved(id)) {
            nav.setManager('');
            props.setLayoutsOpen(false);
          }
        }}
        onRemove={layout.removeSaved}
        onReset={layout.resetLayout}
      />
      <CommandPaletteDialog
        open={props.palette}
        onOpenChange={props.setPalette}
        context={props.commandContext}
        bindings={props.bindings}
        onRun={props.onRunCommand}
      />
    </>
  );
}
