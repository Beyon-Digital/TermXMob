import {cleanup, fireEvent, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {TooltipProvider} from '../ui/tooltip';
import {expectNoAxeViolations} from '../../test-axe';
import ActivityBar, {ACTIVITY_VIEWS, activitySelection} from './ActivityBar';
import {MobileNav, MobileTopBar} from './MobileChrome';
import SideBar from './SideBar';
import StatusBar from './StatusBar';
import TitleBar from './TitleBar';

afterEach(cleanup);

function activityBar(extra: Partial<React.ComponentProps<typeof ActivityBar>> = {}) {
  const handlers = {
    onSelectView: vi.fn(),
    onProjects: vi.fn(),
    onSettings: vi.fn(),
    onLock: vi.fn(),
    onSignOut: vi.fn(),
  };
  const view = render(
    <TooltipProvider>
      <ActivityBar view="agents" sideBarVisible attention={0} account="Owner" {...handlers} {...extra} />
    </TooltipProvider>,
  );
  return {...handlers, ...view};
}

describe('ActivityBar', () => {
  it('is one vertical toolbar with the seven views, then Projects, Settings and Account', () => {
    activityBar();
    const bar = screen.getByRole('toolbar', {name: 'Activity bar'});
    expect(bar).toHaveAttribute('aria-orientation', 'vertical');
    const names = within(bar)
      .getAllByRole('button')
      .map(button => button.getAttribute('aria-label'));
    expect(names).toEqual(['Agents', 'Explorer', 'Search', 'Source control', 'Sites', 'Data', 'Studio', 'Projects', 'Settings', 'Account']);
    expect(ACTIVITY_VIEWS).toHaveLength(7);
  });

  it('marks the selected view only while the side bar shows, with a roving tab stop', () => {
    const {rerender, onSelectView, onProjects, onSettings, onLock, onSignOut} = activityBar();
    expect(screen.getByRole('button', {name: 'Agents'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', {name: 'Explorer'})).toHaveAttribute('aria-pressed', 'false');
    const stops = screen.getAllByRole('button').filter(button => button.tabIndex === 0);
    expect(stops).toHaveLength(1);
    rerender(
      <TooltipProvider>
        <ActivityBar view="agents" sideBarVisible={false} attention={0} account="Owner" {...{onSelectView, onProjects, onSettings, onLock, onSignOut}} />
      </TooltipProvider>,
    );
    expect(screen.getByRole('button', {name: 'Agents'})).toHaveAttribute('aria-pressed', 'false');
  });

  it('moves between buttons with the arrow keys, Home and End', async () => {
    const user = userEvent.setup();
    activityBar();
    screen.getByRole('button', {name: 'Agents'}).focus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('button', {name: 'Explorer'})).toHaveFocus();
    expect(screen.getByRole('button', {name: 'Explorer'}).tabIndex).toBe(0);
    expect(screen.getByRole('button', {name: 'Agents'}).tabIndex).toBe(-1);
    await user.keyboard('{End}');
    expect(screen.getByRole('button', {name: 'Account'})).toHaveFocus();
    await user.keyboard('{ArrowDown}');
    expect(screen.getByRole('button', {name: 'Agents'})).toHaveFocus();
    await user.keyboard('{ArrowUp}');
    expect(screen.getByRole('button', {name: 'Account'})).toHaveFocus();
    await user.keyboard('{Home}');
    expect(screen.getByRole('button', {name: 'Agents'})).toHaveFocus();
  });

  it('reports a click and names the attention on Agents in its label', async () => {
    const user = userEvent.setup();
    const {onSelectView, onSettings} = activityBar({attention: 2});
    await user.click(screen.getByRole('button', {name: 'Agents, 2 need you'}));
    expect(onSelectView).toHaveBeenCalledWith('agents');
    await user.click(screen.getByRole('button', {name: 'Settings'}));
    expect(onSettings).toHaveBeenCalled();
  });

  it('toggles the side bar when the showing view is chosen again, and shows it for any other', () => {
    expect(activitySelection({view: 'agents', visible: true}, 'agents')).toEqual({view: 'agents', visible: false});
    expect(activitySelection({view: 'agents', visible: false}, 'agents')).toEqual({view: 'agents', visible: true});
    expect(activitySelection({view: 'agents', visible: true}, 'explorer')).toEqual({view: 'explorer', visible: true});
    expect(activitySelection({view: 'agents', visible: false}, 'scm')).toEqual({view: 'scm', visible: true});
  });

  it('opens the account menu with Lock, Settings and Sign out', async () => {
    const user = userEvent.setup();
    const {onLock, onSignOut} = activityBar();
    await user.click(screen.getByRole('button', {name: 'Account'}));
    await user.click(await screen.findByRole('menuitem', {name: 'Lock workspace'}));
    expect(onLock).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Account'}));
    await user.click(await screen.findByRole('menuitem', {name: 'Sign out'}));
    expect(onSignOut).toHaveBeenCalled();
  });

  it('has no axe violations', async () => {
    const {container} = activityBar({attention: 1});
    await expectNoAxeViolations(container);
  });
});

function titleBar(extra: Partial<React.ComponentProps<typeof TitleBar>> = {}) {
  const handlers = {
    onToggleSideBar: vi.fn(),
    onTogglePanel: vi.fn(),
    onOpenCommandCenter: vi.fn(),
    onOpenApprovals: vi.fn(),
    onLock: vi.fn(),
    onSignOut: vi.fn(),
    onSettings: vi.fn(),
  };
  const view = render(
    <TooltipProvider>
      <TitleBar project="termxmob" branch="main" quickOpenKeys="Mod+P" sideBarVisible panelVisible={false} needYou={0} account="Owner" online {...handlers} {...extra} />
    </TooltipProvider>,
  );
  return {...handlers, ...view};
}

describe('TitleBar', () => {
  it('shows the project and branch, the command center with its key, and the toggles', async () => {
    const user = userEvent.setup();
    const {onToggleSideBar, onTogglePanel, onOpenCommandCenter} = titleBar();
    const crumbs = screen.getByRole('navigation', {name: 'Project and branch'});
    expect(crumbs).toHaveTextContent('termxmob');
    expect(crumbs).toHaveTextContent('main');
    const center = screen.getByRole('button', {name: 'Search files and commands'});
    expect(center).toHaveTextContent('Search files and commands');
    expect(center.querySelector('kbd')?.textContent).toMatch(/P/);
    await user.click(center);
    expect(onOpenCommandCenter).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Toggle side bar'}));
    expect(onToggleSideBar).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Toggle bottom panel'}));
    expect(onTogglePanel).toHaveBeenCalled();
    expect(screen.getByRole('button', {name: 'Toggle side bar'})).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', {name: 'Toggle bottom panel'})).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows the approvals badge only when something needs the owner', async () => {
    const user = userEvent.setup();
    const none = titleBar();
    expect(screen.queryByText(/need/)).not.toBeInTheDocument();
    none.unmount();
    const {onOpenApprovals} = titleBar({needYou: 2});
    await user.click(screen.getByRole('button', {name: '2 need you'}));
    expect(onOpenApprovals).toHaveBeenCalled();
  });

  it('says Offline when the host is unreachable and omits the branch when there is none', () => {
    titleBar({online: false, branch: undefined});
    expect(screen.getByRole('status')).toHaveTextContent('Offline');
    expect(screen.getByRole('navigation', {name: 'Project and branch'})).not.toHaveTextContent('main');
  });

  it('has an account menu with Lock workspace, Settings and Sign out', async () => {
    const user = userEvent.setup();
    const {onLock, onSettings, onSignOut} = titleBar();
    await user.click(screen.getByRole('button', {name: 'Account menu'}));
    expect(await screen.findAllByRole('menuitem')).toHaveLength(3);
    await user.click(screen.getByRole('menuitem', {name: 'Settings'}));
    expect(onSettings).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Account menu'}));
    await user.click(await screen.findByRole('menuitem', {name: 'Lock workspace'}));
    expect(onLock).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Account menu'}));
    await user.click(await screen.findByRole('menuitem', {name: 'Sign out'}));
    expect(onSignOut).toHaveBeenCalled();
  });

  it('has no axe violations', async () => {
    const {container} = titleBar({needYou: 1});
    await expectNoAxeViolations(container);
  });
});

function statusBar(extra: Partial<React.ComponentProps<typeof StatusBar>> = {}) {
  const handlers = {
    onOpenSourceControl: vi.fn(),
    onOpenProblems: vi.fn(),
    onOpenAgents: vi.fn(),
    onOpenMainAgent: vi.fn(),
    onLock: vi.fn(),
  };
  const view = render(
    <StatusBar online hostName="this Mac" branch="main" dirty problems={0} needYou={0} mainAgent="working" cursor={null} {...handlers} {...extra} />,
  );
  return {...handlers, ...view};
}

describe('StatusBar', () => {
  it('shows health and host, the branch with a dirty marker, the Main agent and Lock', async () => {
    const user = userEvent.setup();
    const {onOpenSourceControl, onOpenMainAgent, onLock} = statusBar();
    expect(screen.getByRole('status')).toHaveTextContent('Connected · this Mac');
    const branch = screen.getByRole('button', {name: /main/});
    expect(branch).toHaveTextContent('main*');
    expect(branch).toHaveTextContent('(uncommitted changes)');
    await user.click(branch);
    expect(onOpenSourceControl).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Main agent working'}));
    expect(onOpenMainAgent).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Lock'}));
    expect(onLock).toHaveBeenCalled();
  });

  it('hides problems until there are some, then counts them', async () => {
    const user = userEvent.setup();
    const first = statusBar();
    expect(screen.queryByText(/problem/)).not.toBeInTheDocument();
    first.unmount();
    const {onOpenProblems} = statusBar({problems: 3});
    await user.click(screen.getByRole('button', {name: '3 problems'}));
    expect(onOpenProblems).toHaveBeenCalled();
  });

  it('shows the caret position and language as text, not a button', () => {
    statusBar({cursor: {line: 12, column: 4, language: 'Python'}});
    const position = screen.getByText(/Ln 12, Col 4/);
    expect(position).toHaveTextContent('Ln 12, Col 4 · Python');
    expect(position.closest('button')).toBeNull();
  });

  it('names who needs the owner and makes it actionable', async () => {
    const user = userEvent.setup();
    const {onOpenAgents} = statusBar({needYou: 2});
    await user.click(screen.getByRole('button', {name: '2 need you'}));
    expect(onOpenAgents).toHaveBeenCalled();
  });

  it('reads Offline when the host is unreachable', () => {
    statusBar({online: false});
    expect(screen.getByRole('status')).toHaveTextContent('Offline');
  });

  it('reduces to the health dot and the approvals badge on a phone', () => {
    statusBar({compact: true, needYou: 1, cursor: {line: 1, column: 1, language: 'Python'}});
    expect(screen.getByRole('status')).toHaveTextContent('Connected');
    expect(screen.getByRole('status')).not.toHaveTextContent('this Mac');
    expect(screen.getByRole('button', {name: '1 needs you'})).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Lock'})).not.toBeInTheDocument();
    expect(screen.queryByText(/Ln 1/)).not.toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    const {container} = statusBar({problems: 1, needYou: 1, cursor: {line: 1, column: 1, language: 'TypeScript'}});
    await expectNoAxeViolations(container);
  });
});

describe('SideBar', () => {
  it('has a header with the view title, a body and a resize handle within 200 and 420', () => {
    const resize = vi.fn();
    const {container} = render(
      <SideBar title="Explorer" width={260} onResizeTo={resize}>
        <p>Body</p>
      </SideBar>,
    );
    expect(screen.getByRole('complementary', {name: 'Explorer'})).toBeInTheDocument();
    expect(screen.getByRole('heading', {name: 'Explorer'})).toBeInTheDocument();
    const handle = screen.getByRole('separator', {name: 'Resize side bar'});
    expect(handle).toHaveAttribute('aria-valuemin', '200');
    expect(handle).toHaveAttribute('aria-valuemax', '420');
    expect(handle).toHaveAttribute('aria-valuenow', '260');
    fireEvent.keyDown(handle, {key: 'ArrowRight'});
    expect(resize).toHaveBeenLastCalledWith(276);
    fireEvent.keyDown(handle, {key: 'ArrowLeft', shiftKey: true});
    expect(resize).toHaveBeenLastCalledWith(200); // 260 - 64 is below the 200 minimum
    fireEvent.keyDown(handle, {key: 'End'});
    expect(resize).toHaveBeenLastCalledWith(420);
    fireEvent.keyDown(handle, {key: 'Home'});
    expect(resize).toHaveBeenLastCalledWith(200);
    fireEvent.keyDown(handle, {key: 'Enter'});
    expect(resize).toHaveBeenLastCalledWith(260);
    expect(container.querySelector('#ide-side-bar')).not.toBeNull();
  });

  it('has no resize handle as a drawer, and no axe violations', async () => {
    const {container} = render(
      <SideBar title="Agents" width={260} drawer>
        <p>Body</p>
      </SideBar>,
    );
    expect(screen.queryByRole('separator')).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });
});

describe('phone chrome', () => {
  it('has a hamburger, the active tab as the title and a switcher over the open tabs', async () => {
    const user = userEvent.setup();
    const onMenu = vi.fn();
    const onPickTab = vi.fn();
    const {container} = render(
      <MobileTopBar title="Main agent" tabs={[{id: 'a', title: 'Main agent'}, {id: 'b', title: 'main.py'}]} activeTab="a" attention={0} onMenu={onMenu} onPickTab={onPickTab} />,
    );
    expect(screen.getByRole('heading', {name: 'Main agent'})).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Open side bar'}));
    expect(onMenu).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Switch tab, 2 open'}));
    await user.click(await screen.findByRole('menuitem', {name: 'main.py'}));
    expect(onPickTab).toHaveBeenCalledWith('b');
    await expectNoAxeViolations(container);
  });

  it('has Agents, Files, Git, Term and More in a 56px navigation', async () => {
    const user = userEvent.setup();
    const handlers = {onAgents: vi.fn(), onFiles: vi.fn(), onGit: vi.fn(), onTerminal: vi.fn()};
    const {container} = render(<MobileNav view="agents" drawerOpen terminalOpen={false} more={<p>More items</p>} {...handlers} />);
    const nav = screen.getByRole('navigation', {name: 'Primary'});
    expect(within(nav).getAllByRole('button').map(button => button.textContent)).toEqual(['Agents', 'Files', 'Git', 'Term', 'More']);
    expect(screen.getByRole('button', {name: 'Agents'})).toHaveAttribute('aria-current', 'page');
    await user.click(screen.getByRole('button', {name: 'Files'}));
    expect(handlers.onFiles).toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Term'}));
    expect(handlers.onTerminal).toHaveBeenCalled();
    await expectNoAxeViolations(container);
  });
});
