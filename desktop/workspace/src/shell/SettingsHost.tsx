import {lazy, Suspense, type Dispatch, type SetStateAction} from 'react';
import {Select} from '../components/ui/select';
import SettingsGeneral from '../features/SettingsGeneral';
import SettingsSurface from '../features/SettingsSurface';
import {leaveSettings} from '../features/settings-forms';
import {signOut, type Project} from '../lib/api';
import type {Bindings} from '../lib/keybindings';
import {scopeProjects} from '../lib/project-labels';
import {canonicalSettingsCategory} from '../lib/settings-categories';
import type {ThemePreference} from '../lib/theme';
import {useAuth} from './AuthGate';
import {useWorkspaceData} from './WorkspaceDataProvider';

const Characters = lazy(() => import('../features/Characters'));
const SafetyManager = lazy(() => import('../features/SafetyManager'));
const Runners = lazy(() => import('../features/Runners'));
const Managers = lazy(() => import('../features/Managers'));

/** Settings categories whose values can differ per project, so they show a scope picker. */
export const projectSettingsCategories = new Set([
  'agents',
  'extensions',
  'memory',
  'automations',
  'runners',
  'access',
]);

export type ManagerScope = 'global' | 'workspace';

type Props = {
  /** The open settings category, as routed (aliases allowed). */
  manager: string;
  onSection: (value: string) => void;
  onClose: () => void;
  scope: ManagerScope;
  onScope: Dispatch<SetStateAction<ManagerScope>>;
  settingsProjectId: string;
  onSettingsProject: Dispatch<SetStateAction<string>>;
  /** The project the conversation or workbench in the background belongs to. */
  project: Project | null;
  waiting: number;
  theme: ThemePreference;
  onTheme: (theme: ThemePreference) => void;
  bindings: Bindings;
  onBindings: (bindings: Bindings) => void;
  /** `page` shows Settings in the space it is given, as a tab, instead of as a modal. */
  presentation?: 'dialog' | 'page';
  /** Called with whether the open page has unsaved edits. */
  onDirtyChange?: (dirty: boolean) => void;
};

/** The Settings surface: category list, scope picker and the page for the chosen category. */
export default function SettingsHost(props: Props) {
  const {manager, scope, settingsProjectId, project} = props;
  const {auth} = useAuth();
  const {projects, sessions, selected, load} = useWorkspaceData();
  const category = canonicalSettingsCategory(manager);
  const scoped = projectSettingsCategories.has(category);
  const scopeProjectId = scope === 'global' ? null : settingsProjectId || project?.id || null;
  return (
    <SettingsSurface
      waiting={props.waiting}
      key={auth.principal.id + ':' + (auth.host_id || auth.session_id)}
      scopeKey={scoped ? scope + settingsProjectId : 'account'}
      context={
        scoped ? (
          <label className="settings-scope" data-settings-filter>
            Settings apply to
            <Select
              aria-label="Settings scope"
              value={scope === 'global' ? '' : settingsProjectId || project?.id || ''}
              onChange={event => {
                const value = event.target.value;
                leaveSettings(() => {
                  props.onSettingsProject(value);
                  props.onScope(value ? 'workspace' : 'global');
                });
              }}
            >
              <option value="">Your account and this host</option>
              {scopeProjects(projects, settingsProjectId || project?.id || '').map(row => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          </label>
        ) : (
          <p className="settings-active-context">{auth.principal.display_name} · This host</p>
        )
      }
      category={category}
      onSelect={props.onSection}
      onClose={props.onClose}
      presentation={props.presentation}
      onDirtyChange={props.onDirtyChange}
    >
      <Suspense fallback={<p>Opening settings…</p>}>
        {manager === 'characters' ? (
          <Characters key={auth.principal.id} sessions={sessions} ownerId={auth.principal.id} />
        ) : category === 'preferences' ? (
          <SettingsGeneral
            key={auth.principal.id + ':' + (auth.host_id || auth.session_id)}
            account={auth.principal.display_name}
            theme={props.theme}
            onTheme={props.onTheme}
            bindings={props.bindings}
            onBindings={props.onBindings}
            onSignOut={signOut}
          />
        ) : manager === 'safety' ? (
          <SafetyManager />
        ) : manager === 'runners' ? (
          <Runners ownerId={auth.principal.id} projectId={scopeProjectId} />
        ) : (
          <Managers
            embedded
            section={manager}
            projectId={scoped && scope !== 'global' ? settingsProjectId || project?.id || null : null}
            sessionId={
              scoped && scope !== 'global' && (!settingsProjectId || settingsProjectId === project?.id)
                ? selected || null
                : null
            }
            onSection={props.onSection}
            onCapabilitiesChanged={() => void load()}
          />
        )}
      </Suspense>
    </SettingsSurface>
  );
}
