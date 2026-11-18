import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../../test-axe';
import type {NeedsYouItem} from '../../lib/bot-schedules';
import {writeSidebarSprite} from '../../lib/sidebar-sprite';
import type {Coordination} from '../use-coordination';
import AgentsView from './AgentsView';
import DataView from './DataView';
import SitesView from './SitesView';

vi.mock('../AgentSidebar', () => ({default: ({listOnly}: {listOnly?: boolean}) => <div data-testid="chat-list" data-list-only={String(!!listOnly)} />}));
vi.mock('./MainAgentSprite', () => ({default: () => <span data-testid="sprite" />}));

beforeEach(() => localStorage.clear());
afterEach(cleanup);

const coordination = (attention = 0) => ({summary: {attention, active: 0, done: 0}, setTarget: vi.fn()}) as unknown as Coordination;
const item = (id: string, title: string): NeedsYouItem => ({id, kind: 'approval', bot_id: 'b', bot_name: 'Nightly bot', title, detail: '', scheduled: false});

function agents(extra: Partial<React.ComponentProps<typeof AgentsView>> = {}) {
  const props = {
    sidebar: {} as never,
    primaryId: 'main',
    mainSelected: false,
    mainStatus: 'idle',
    creating: false,
    coordination: coordination(),
    needs: [] as NeedsYouItem[],
    manager: '',
    onNewChat: vi.fn(),
    onOpenMainAgent: vi.fn(),
    onOpenConversation: vi.fn(),
    onFindConversation: vi.fn(),
    onReviewNeeds: vi.fn(),
    ...extra,
  };
  return {props, ...render(<AgentsView {...props} />)};
}

describe('AgentsView', () => {
  it('has New chat as its only primary button, then the sections in order', async () => {
    const user = userEvent.setup();
    const {props, container} = agents({needs: [item('1', 'Approve the deploy')]});
    const primary = container.querySelectorAll('.button-primary');
    expect(primary).toHaveLength(1);
    expect(primary[0]).toHaveTextContent('New chat');
    await user.click(screen.getByRole('button', {name: 'New chat'}));
    expect(props.onNewChat).toHaveBeenCalled();
    const headings = screen.getAllByRole('heading').map(heading => heading.textContent);
    expect(headings).toEqual(['Needs you1 needs you', 'Main agent', 'Sub-agents', 'CLI sessions']);
    expect(screen.getByTestId('chat-list')).toHaveAttribute('data-list-only', 'true');
  });

  it('lists what needs the owner as plain text and sends them to Settings to answer', async () => {
    const user = userEvent.setup();
    const {props} = agents({needs: [item('1', 'Approve the deploy'), item('2', 'Review <b>run</b>')]});
    const section = screen.getByRole('region', {name: /Needs you/});
    expect(within(section).getByText('Approve the deploy')).toBeInTheDocument();
    expect(within(section).getByText('Review <b>run</b>')).toBeInTheDocument();
    await user.click(within(section).getByRole('button', {name: 'Review'}));
    expect(props.onReviewNeeds).toHaveBeenCalled();
  });

  it('hides Needs you when nothing needs the owner', () => {
    agents();
    expect(screen.queryByText(/Needs you/)).not.toBeInTheDocument();
  });

  it('shows the Main agent with its state and opens it', async () => {
    const user = userEvent.setup();
    const {props} = agents({mainStatus: 'working', mainSelected: true});
    const row = screen.getByRole('button', {name: /Main agent/});
    expect(row).toHaveTextContent('working');
    expect(row).toHaveAttribute('aria-current', 'page');
    await user.click(row);
    expect(props.onOpenMainAgent).toHaveBeenCalled();
  });

  it('gives sub-agents a place for the orchestrator to draw into', () => {
    const value = coordination(2);
    agents({coordination: value});
    expect(value.setTarget).toHaveBeenCalledWith(expect.any(HTMLDivElement));
    expect(screen.getByRole('heading', {name: /Sub-agents/})).toHaveTextContent('2 need you');
  });

  it('shows CLI sessions as one sentence with no action', () => {
    agents();
    const section = screen.getByRole('region', {name: 'CLI sessions'});
    expect(within(section).queryByRole('button')).not.toBeInTheDocument();
    expect(within(section).getByText(/CLI sessions on this host will appear here/)).toBeInTheDocument();
  });

  it('shows a still character beside Main agent only when the person turned it on', () => {
    const off = agents();
    expect(screen.queryByTestId('sprite')).not.toBeInTheDocument();
    off.unmount();
    writeSidebarSprite(true);
    agents();
    expect(screen.getByTestId('sprite')).toBeInTheDocument();
  });

  it('has no axe violations', async () => {
    const {container} = agents({needs: [item('1', 'Approve the deploy')]});
    await expectNoAxeViolations(container);
  });
});

describe('SitesView and DataView', () => {
  it('shows Sites as one sentence with no action', async () => {
    const {container} = render(<SitesView />);
    expect(screen.getByText('No saved sites yet.')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it('gives the Data workspace an element for its navigation', () => {
    const host = vi.fn();
    render(<DataView host={host} />);
    expect(host).toHaveBeenCalledWith(expect.any(HTMLDivElement));
  });
});
