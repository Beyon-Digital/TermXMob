import {Bot, ChevronDown, Ellipsis, Files, GitBranch, Menu, SquareTerminal, type LucideIcon} from 'lucide-react';
import type {ReactNode} from 'react';
import {DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger} from '../ui/dropdown-menu';
import './chrome.css';

export type MobileTab = {id: string; title: string};

type TopProps = {
  /** The active tab's title. */
  title: string;
  tabs: MobileTab[];
  activeTab: string | null;
  attention: number;
  onMenu: () => void;
  onPickTab: (id: string) => void;
  /** Close the tab on screen. A phone has no tab strip, so the switcher offers it (only with more than one tab open). */
  onCloseTab?: () => void;
};

/**
 * The 48px top bar of a phone: a hamburger that opens the side bar drawer, the active tab's title and a switcher listing
 * the open tabs. The tab stack is a projection of the stored layout: choosing a tab here never changes the layout.
 */
export function MobileTopBar({title, tabs, activeTab, attention, onMenu, onPickTab, onCloseTab}: TopProps) {
  return (
    <header className="mobile-top" aria-label="Window">
      <button type="button" className="mobile-button" aria-label="Open side bar" onClick={onMenu}>
        <Menu size={20} strokeWidth={1.5} aria-hidden="true" />
        {attention > 0 && <span className="activity-badge" aria-hidden="true" />}
      </button>
      <h1 className="mobile-title">{title}</h1>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" className="mobile-button" aria-label={'Switch tab, ' + tabs.length + ' open'}>
            <ChevronDown size={20} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          {tabs.map(tab => (
            <DropdownMenuItem key={tab.id} data-current={tab.id === activeTab || undefined} onSelect={() => onPickTab(tab.id)}>
              {tab.title}
            </DropdownMenuItem>
          ))}
          {onCloseTab && tabs.length > 1 && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={onCloseTab}>Close tab</DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}

type NavProps = {
  /** The side bar view the drawer would show, and whether the drawer is open. */
  view: string;
  drawerOpen: boolean;
  terminalOpen: boolean;
  onAgents: () => void;
  onFiles: () => void;
  onGit: () => void;
  onTerminal: () => void;
  more: ReactNode;
};

function NavButton({icon: Icon, label, selected, onClick}: {icon: LucideIcon; label: string; selected: boolean; onClick: () => void}) {
  return (
    <button type="button" className="mobile-nav-item" aria-current={selected ? 'page' : undefined} onClick={onClick}>
      <Icon size={20} strokeWidth={1.5} aria-hidden="true" />
      <span>{label}</span>
    </button>
  );
}

/** The 56px bottom navigation: Agents, Files, Git, Term and More. Every target is at least 44px. */
export function MobileNav({view, drawerOpen, terminalOpen, onAgents, onFiles, onGit, onTerminal, more}: NavProps) {
  return (
    <nav className="mobile-nav" aria-label="Primary">
      <NavButton icon={Bot} label="Agents" selected={drawerOpen && view === 'agents'} onClick={onAgents} />
      <NavButton icon={Files} label="Files" selected={drawerOpen && view === 'explorer'} onClick={onFiles} />
      <NavButton icon={GitBranch} label="Git" selected={drawerOpen && view === 'scm'} onClick={onGit} />
      <NavButton icon={SquareTerminal} label="Term" selected={terminalOpen} onClick={onTerminal} />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" className="mobile-nav-item" aria-label="More">
            <Ellipsis size={20} strokeWidth={1.5} aria-hidden="true" />
            <span>More</span>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="top" align="end">
          {more}
        </DropdownMenuContent>
      </DropdownMenu>
    </nav>
  );
}
