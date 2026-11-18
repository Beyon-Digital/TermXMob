import {cleanup, render, screen, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, describe, expect, it, vi} from 'vitest';
import type {CliSession} from '../../lib/cli-sessions';
import {expectNoAxeViolations} from '../../test-axe';
import type {Coordination} from '../use-coordination';
import AgentsView from './AgentsView';

const sessions = vi.hoisted(() => ({value: [] as unknown[]}));
vi.mock('../../features/cli/CliSessionsProvider', () => ({
  useCliSessions: () => {
    const list = sessions.value as {status: string; attention: boolean}[];
    return {sessions: list, loaded: true, available: true, error: '', attention: list.filter(item => item.attention).length, resumable: 0, get: () => undefined, placeOf: () => ''};
  },
}));
vi.mock('../AgentSidebar', () => ({default: () => <div />}));
vi.mock('./MainAgentSprite', () => ({default: () => <span />}));

afterEach(cleanup);

const cli = (over: Partial<CliSession>): CliSession =>
  ({id: 'c', cli: 'claude', label: 'Claude Code', title: 'Claude Code · app', status: 'working', attention: false, worktree: null, cwd: '/w/app', ...over}) as CliSession;

const coordination = (summary: Record<string, number> = {}) => ({summary: {attention: 0, active: 0, done: 0, ...summary}, setTarget: vi.fn()}) as unknown as Coordination;

function agents(extra: Partial<React.ComponentProps<typeof AgentsView>> = {}) {
  const props = {
    sidebar: {} as never,
    primaryId: 'main',
    mainSelected: false,
    mainStatus: 'idle',
    creating: false,
    coordination: coordination(),
    needs: [],
    manager: '',
    onNewChat: vi.fn(),
    onOpenMainAgent: vi.fn(),
    onOpenConversation: vi.fn(),
    onFindConversation: vi.fn(),
    onReviewNeeds: vi.fn(),
    onNewCliSession: vi.fn(),
    onOpenCliSession: vi.fn(),
    ...extra,
  };
  return {props, ...render(<AgentsView {...props} />)};
}

describe('the needs-you inbox with CLI sessions', () => {
  it('lists a CLI session that needs approval or input, counts it, and opens its tab', async () => {
    sessions.value = [cli({id: 'a', status: 'needs_approval', attention: true}), cli({id: 'b', title: 'Codex · api', status: 'waiting_input', attention: true}), cli({id: 'c', status: 'working'})];
    const user = userEvent.setup();
    const {props} = agents();
    const needs = screen.getByRole('region', {name: /Needs you/});
    expect(within(needs).getByRole('heading')).toHaveTextContent('2 need you');
    const rows = within(needs).getAllByRole('button');
    expect(rows[0]).toHaveTextContent('Claude Code · app: needs approval');
    expect(rows[1]).toHaveTextContent('Codex · api: waiting for input');
    await user.click(rows[0]);
    expect(props.onOpenCliSession).toHaveBeenCalledWith('a');
  });

  it('adds CLI sessions to the bots waiting on the owner and keeps Review for the bots', () => {
    sessions.value = [cli({id: 'a', status: 'needs_approval', attention: true})];
    agents({needs: [{id: '1', kind: 'approval', bot_id: 'b', bot_name: 'Nightly bot', title: 'Approve the deploy', detail: '', scheduled: false}]});
    const needs = screen.getByRole('region', {name: /Needs you/});
    expect(within(needs).getByRole('heading')).toHaveTextContent('2 need you');
    expect(within(needs).getByRole('button', {name: 'Review'})).toBeInTheDocument();
  });

  it('shows no Needs you section when no CLI session or bot waits', () => {
    sessions.value = [cli({status: 'working'})];
    agents();
    expect(screen.queryByRole('region', {name: /Needs you/})).not.toBeInTheDocument();
  });

  it('counts blocked planned tasks with the sub-agents that need attention', () => {
    sessions.value = [];
    agents({coordination: coordination({attention: 1, blocked: 2})});
    expect(screen.getByRole('heading', {name: /Sub-agents/})).toHaveTextContent('3 need you');
  });

  it('keeps New chat as its only primary button and has no axe violations', async () => {
    sessions.value = [cli({id: 'a', status: 'needs_approval', attention: true}), cli({id: 'b', status: 'lost'})];
    const {container} = agents();
    expect(container.querySelectorAll('.button-primary')).toHaveLength(1);
    await expectNoAxeViolations(container);
  });
});
