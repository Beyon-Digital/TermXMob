import {useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction} from 'react';
import {leaveSettings} from '../features/settings-forms';
import type {AgentPreset} from '../features/PresetPicker';
import {gql, json, request, type Project, type Session} from '../lib/api';
import {isSettingsCategory} from '../lib/settings-categories';
import type {Layout} from '../lib/use-layout';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';
import {useReport} from './errors';
import {useWorkspaceData} from './WorkspaceDataProvider';

/**
 * Starting a conversation: the one-click new chat, and the "new chat with options" dialog with its project, agent
 * preset and project folder. Creating a conversation is a navigation, so the shell hands in the few things it owns
 * (the navigation intent counter, the page that may need leaving, layout and sidebar setters).
 */
type Options = {
  /** Bumped on every navigation; a creation that finds it changed meanwhile was overtaken and only reloads. */
  navigationIntent: MutableRefObject<number>;
  workspace: WorkspaceMode;
  manager: string;
  workspaceNow: MutableRefObject<WorkspaceMode>;
  managerNow: MutableRefObject<string>;
  setWorkspaceState: Dispatch<SetStateAction<WorkspaceMode>>;
  setProjectWorkbenchId: Dispatch<SetStateAction<string>>;
  setManager: (value: string) => void;
  chooseLayout: (layout: Layout) => void;
  setLeft: Dispatch<SetStateAction<boolean>>;
};

type CreateOptions = {projectId: string; presetId: string; folder: string};

export function useNewChat(options: Options) {
  const {
    navigationIntent,
    workspace,
    manager,
    workspaceNow,
    managerNow,
    setWorkspaceState,
    setProjectWorkbenchId,
    setManager,
    chooseLayout,
    setLeft,
  } = options;
  const {auth, fence} = useAuth();
  const report = useReport();
  const {
    projects,
    providers,
    projectId,
    setProjectId,
    selected,
    setSelected,
    selection,
    session,
    setSession,
    load,
  } = useWorkspaceData();
  const [newOpen, setNewOpen] = useState(false);
  const [folder, setFolder] = useState('');
  const [registering, setRegistering] = useState(false);
  const [newPreset, setNewPreset] = useState('');
  const [newPresets, setNewPresets] = useState<AgentPreset[]>([]);
  const [newPresetsLoading, setNewPresetsLoading] = useState(false);
  const newChatFlight = useRef(false);
  /** The conversation whose composer should take focus once it has rendered. */
  const newComposerFocus = useRef<{id: string; intent: number} | null>(null);

  // The agent presets that apply to the chosen project, while the dialog is open.
  useEffect(() => {
    if (!newOpen) return;
    let stopped = false;
    setNewPresets([]);
    setNewPresetsLoading(true);
    request<{presets: AgentPreset[]}>(
      '/api/workspace/presets' + (projectId ? '?project_id=' + encodeURIComponent(projectId) : ''),
    )
      .then(value => {
        if (!stopped) setNewPresets(value.presets || []);
      })
      .catch(report)
      .finally(() => {
        if (!stopped) setNewPresetsLoading(false);
      });
    return () => {
      stopped = true;
    };
  }, [newOpen, projectId, auth.principal.id]);

  function openNewChatWithOptions(targetProject = '') {
    const open = () => {
      navigationIntent.current++;
      setWorkspaceState('agent');
      setProjectId(targetProject);
      setFolder('');
      setNewPreset('');
      setNewOpen(true);
    };
    if (isSettingsCategory(manager)) leaveSettings(open);
    else open();
  }

  function openNewChat(targetProject = '') {
    if (newChatFlight.current) return;
    const open = () => {
      setWorkspaceState('agent');
      void createConversation({projectId: targetProject, presetId: '', folder: ''});
    };
    if (isSettingsCategory(manager)) leaveSettings(open);
    else open();
  }

  async function createConversation(requested?: CreateOptions) {
    if (newChatFlight.current) return;
    newChatFlight.current = true;
    const pending = fence();
    const intent = ++navigationIntent.current;
    setRegistering(true);
    const chosenOptions = requested || {projectId, presetId: newPreset, folder};
    try {
      let chosen = projects.find(project => project.id === chosenOptions.projectId);
      if (chosenOptions.folder.trim()) {
        const result = await gql<{register_project: Project}>(
          'mutation($input:ProjectInput!){register_project(input:$input){id name path}}',
          {input: {path: chosenOptions.folder, name: ''}},
        );
        chosen = result.register_project;
      }
      if (!pending.current() || intent !== navigationIntent.current) return;
      const preset = newPresets.find(row => row.id === chosenOptions.presetId);
      const chat = await request<Session>(
        '/api/workspace/sessions',
        json('POST', {
          project_id: chosen?.id,
          cwd: chosen?.path,
          engine: preset?.engine === 'inherit' ? 'internal' : preset?.engine || 'internal',
          provider_id: preset?.provider_id || providers.find(provider => provider.secret_configured)?.id,
          model: preset?.model || null,
          mode: 'agent',
          custom_agent_id: chosenOptions.presetId || null,
        }),
      );
      if (!pending.current()) return;
      if (intent !== navigationIntent.current) {
        void load();
        return;
      }
      setProjectWorkbenchId('');
      setSession(chat);
      setSelected(chat.id);
      setProjectId(chat.project_id || chosen?.id || '');
      setManager('');
      chooseLayout('chat');
      newComposerFocus.current = {id: chat.id, intent: navigationIntent.current};
      setNewOpen(false);
      setFolder('');
      setNewPreset('');
      if (innerWidth <= 850) setLeft(false);
      await load();
    } catch (reason) {
      if (pending.current()) report(reason);
    } finally {
      newChatFlight.current = false;
      setRegistering(false);
    }
  }

  // Focus the new conversation's composer once it is on screen, unless the person went elsewhere meanwhile.
  useEffect(() => {
    const pending = newComposerFocus.current;
    if (
      !pending ||
      selected !== pending.id ||
      session?.id !== pending.id ||
      workspace !== 'agent' ||
      newOpen
    ) {
      return;
    }
    const focus = () => {
      if (
        navigationIntent.current !== pending.intent ||
        selection.current !== pending.id ||
        managerNow.current ||
        workspaceNow.current !== 'agent'
      ) {
        newComposerFocus.current = null;
        return true;
      }
      const field = document.querySelector<HTMLTextAreaElement>('#agent-workspace textarea[name="message"]');
      if (!field || field.disabled) return false;
      field.focus();
      newComposerFocus.current = null;
      return true;
    };
    if (focus()) return;
    const target = document.getElementById('agent-workspace');
    if (!target) return;
    const observer = new MutationObserver(() => {
      if (focus()) observer.disconnect();
    });
    observer.observe(target, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['disabled'],
    });
    return () => observer.disconnect();
  }, [selected, session?.id, workspace, newOpen]);

  return {
    newOpen,
    setNewOpen,
    folder,
    setFolder,
    registering,
    newPreset,
    setNewPreset,
    newPresets,
    newPresetsLoading,
    newComposerFocus,
    openNewChat,
    openNewChatWithOptions,
    createConversation,
  };
}
