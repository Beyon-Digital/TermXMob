import {ChevronRight, CircleUser, GitBranch, PanelBottom, PanelLeft, Search} from 'lucide-react';
import {formatShortcut} from '../../lib/shortcut-label';
import {DropdownMenu, DropdownMenuContent, DropdownMenuTrigger} from '../ui/dropdown-menu';
import {Tooltip} from '../ui/tooltip';
import {AccountItems} from './ActivityBar';
import './chrome.css';

type Props = {
  /** The project this window is about, or `''` when there is none. */
  project: string;
  branch?: string;
  /** The stored binding of Quick open (for example `Mod+P`), shown in the command center. */
  quickOpenKeys: string;
  sideBarVisible: boolean;
  panelVisible: boolean;
  /** Approvals and bots waiting for the owner. */
  needYou: number;
  account: string;
  online: boolean;
  onToggleSideBar: () => void;
  onTogglePanel: () => void;
  onOpenCommandCenter: () => void;
  onOpenApprovals: () => void;
  onLock: () => void;
  onSignOut: () => void;
  onSettings: () => void;
};

/**
 * The title bar: side bar toggle and `project > branch` on the left, the command center in the middle, the layout toggles,
 * the "N need you" badge and the account menu on the right. Nothing here is a primary action.
 *
 * The window has no custom drag region: the native title bar is the one the old header used too, so there is nothing to
 * keep in step. A drag region would be added here and in `desktop/src-tauri` together.
 */
export default function TitleBar(props: Props) {
  const {needYou} = props;
  return (
    <header className="title-bar" aria-label="Window">
      <div className="title-start">
        <Tooltip label="Toggle side bar">
          <button
            type="button"
            className="chrome-button"
            aria-label="Toggle side bar"
            aria-pressed={props.sideBarVisible}
            onClick={props.onToggleSideBar}
          >
            <PanelLeft size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </Tooltip>
        <nav className="title-breadcrumb" aria-label="Project and branch">
          <span className="title-project">{props.project || 'TermX'}</span>
          {props.branch && (
            <>
              <ChevronRight size={12} strokeWidth={1.5} aria-hidden="true" />
              <span className="title-branch">
                <GitBranch size={12} strokeWidth={1.5} aria-hidden="true" />
                {props.branch}
              </span>
            </>
          )}
        </nav>
        {!props.online && (
          <span className="title-offline" role="status">
            Offline
          </span>
        )}
      </div>
      <button type="button" className="command-center" aria-label="Search files and commands" onClick={props.onOpenCommandCenter}>
        <Search size={16} strokeWidth={1.5} aria-hidden="true" />
        <span className="command-center-text">Search files and commands</span>
        <kbd>{formatShortcut(props.quickOpenKeys)}</kbd>
      </button>
      <div className="title-end">
        <Tooltip label="Toggle bottom panel">
          <button
            type="button"
            className="chrome-button"
            aria-label="Toggle bottom panel"
            aria-pressed={props.panelVisible}
            onClick={props.onTogglePanel}
          >
            <PanelBottom size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </Tooltip>
        {needYou > 0 && (
          <button type="button" className="need-you" onClick={props.onOpenApprovals}>
            {needYou === 1 ? '1 needs you' : needYou + ' need you'}
          </button>
        )}
        <DropdownMenu>
          <Tooltip label="Account">
            <DropdownMenuTrigger asChild>
              <button type="button" className="chrome-button" aria-label="Account menu">
                <CircleUser size={16} strokeWidth={1.5} aria-hidden="true" />
              </button>
            </DropdownMenuTrigger>
          </Tooltip>
          <DropdownMenuContent>
            <AccountItems account={props.account} onLock={props.onLock} onSignOut={props.onSignOut} onSettings={props.onSettings} />
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
