import {Bot, Clapperboard, CircleUser, Database, Files, Folder, GitBranch, Globe, Lock, LogOut, Search, Settings, type LucideIcon} from 'lucide-react';
import {useRef, useState, type KeyboardEvent} from 'react';
import type {SideBarView} from '../../lib/layout/model';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger} from '../ui/dropdown-menu';
import {Tooltip} from '../ui/tooltip';
import './chrome.css';

export const VIEW_META: Record<SideBarView, {label: string; icon: LucideIcon}> = {
  agents: {label: 'Agents', icon: Bot},
  explorer: {label: 'Explorer', icon: Files},
  search: {label: 'Search', icon: Search},
  scm: {label: 'Source control', icon: GitBranch},
  sites: {label: 'Sites', icon: Globe},
  data: {label: 'Data', icon: Database},
  studio: {label: 'Studio', icon: Clapperboard},
};

export const ACTIVITY_VIEWS: readonly SideBarView[] = ['agents', 'explorer', 'search', 'scm', 'sites', 'data', 'studio'];

/** What choosing an activity bar view does: the view that is already showing hides the side bar (VS Code); any other shows it. */
export function activitySelection(current: {view: SideBarView; visible: boolean}, chosen: SideBarView): {view: SideBarView; visible: boolean} {
  if (current.view === chosen && current.visible) return {view: chosen, visible: false};
  return {view: chosen, visible: true};
}

type Props = {
  view: SideBarView;
  /** The side bar is showing. Choosing the selected view again toggles it, as in VS Code. */
  sideBarVisible: boolean;
  /** How many things need the owner (approvals, bots, sub-agents): shown as a dot on Agents, and spoken. */
  attention: number;
  projectsOpen?: boolean;
  settingsOpen?: boolean;
  account: string;
  onSelectView: (view: SideBarView) => void;
  onProjects: () => void;
  onSettings: () => void;
  onLock: () => void;
  onSignOut: () => void;
};

/**
 * The activity bar: one vertical toolbar. Arrow keys move between its buttons (a roving tab stop, so the whole bar is one Tab
 * stop), Home and End jump to the ends. Views at the top select what the side bar shows; Projects, Settings and the account
 * menu sit at the bottom.
 */
export default function ActivityBar(props: Props) {
  const {view, sideBarVisible, attention} = props;
  const root = useRef<HTMLDivElement>(null);
  const [stop, setStop] = useState(0);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (!['ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    const items = Array.from(root.current?.querySelectorAll<HTMLElement>('[data-roving]') ?? []);
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (at < 0) return;
    // Captured, so the arrow keys move along the bar even on the Account button, whose menu would otherwise open on ArrowDown.
    event.preventDefault();
    event.stopPropagation();
    const next =
      event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (at + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    setStop(next);
    items[next]?.focus();
  }

  let index = 0;
  const rove = () => {
    const mine = index++;
    return {'data-roving': '', tabIndex: mine === stop ? 0 : -1, onFocus: () => setStop(mine)};
  };

  return (
    <div ref={root} className="activity-bar" role="toolbar" aria-label="Activity bar" aria-orientation="vertical" onKeyDownCapture={onKeyDown}>
      <div className="activity-group">
        {ACTIVITY_VIEWS.map(id => {
          const {label, icon: Icon} = VIEW_META[id];
          const selected = view === id && sideBarVisible;
          const needs = id === 'agents' && attention > 0;
          const name = needs ? label + ', ' + (attention === 1 ? '1 needs you' : attention + ' need you') : label;
          return (
            <Tooltip key={id} label={label} side="right">
              <button
                type="button"
                className="activity-item"
                aria-label={name}
                aria-pressed={selected}
                data-view={id}
                data-selected={selected || undefined}
                onClick={() => props.onSelectView(id)}
                {...rove()}
              >
                <Icon size={20} strokeWidth={1.5} aria-hidden="true" />
                {needs && <span className="activity-badge" aria-hidden="true" />}
              </button>
            </Tooltip>
          );
        })}
      </div>
      <div className="activity-group">
        <Tooltip label="Projects" side="right">
          <button
            type="button"
            className="activity-item"
            aria-label="Projects"
            aria-pressed={!!props.projectsOpen}
            data-selected={props.projectsOpen || undefined}
            onClick={props.onProjects}
            {...rove()}
          >
            <Folder size={20} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </Tooltip>
        <Tooltip label="Settings" side="right">
          <button
            type="button"
            className="activity-item"
            aria-label="Settings"
            aria-pressed={!!props.settingsOpen}
            data-selected={props.settingsOpen || undefined}
            onClick={props.onSettings}
            {...rove()}
          >
            <Settings size={20} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </Tooltip>
        <DropdownMenu>
          <Tooltip label="Account" side="right">
            <DropdownMenuTrigger asChild>
              <button type="button" className="activity-item" aria-label="Account" {...rove()}>
                <CircleUser size={20} strokeWidth={1.5} aria-hidden="true" />
              </button>
            </DropdownMenuTrigger>
          </Tooltip>
          <DropdownMenuContent side="right" align="end">
            <AccountItems {...props} />
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}

/** What the account menu offers, shared by the activity bar and the title bar. */
export function AccountItems({account, onLock, onSignOut, onSettings}: Pick<Props, 'account' | 'onLock' | 'onSignOut' | 'onSettings'>) {
  return (
    <>
      <div className="account-name" role="presentation">
        {account}
      </div>
      <DropdownMenuSeparator />
      <DropdownMenuItem onSelect={onLock}>
        <Lock size={16} strokeWidth={1.5} aria-hidden="true" />
        Lock workspace
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={onSettings}>
        <Settings size={16} strokeWidth={1.5} aria-hidden="true" />
        Settings
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={onSignOut}>
        <LogOut size={16} strokeWidth={1.5} aria-hidden="true" />
        Sign out
      </DropdownMenuItem>
    </>
  );
}
