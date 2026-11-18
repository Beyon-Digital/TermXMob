import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor, within} from '@testing-library/react';
import OrchestratorStatus, {type OrchestratorChild, type OrchestratorSnapshot} from './OrchestratorStatus';
import {gql, request, type Session} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';

vi.mock('../lib/api', () => ({gql: vi.fn(), request: vi.fn(), json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
const api = vi.mocked(request);
const board = vi.mocked(gql);
const now = 1800000000;
const board3 = {
  nodes: [],
  rollup: {total: 3, counts: {blocked: 2, running: 1}, percent_complete: 0, tokens: 0, cost_usd: null},
};
const child = (id: string, status = 'completed'): OrchestratorChild => ({
  session_id: id,
  task_id: 'task-' + id,
  title: 'Agent ' + id,
  status,
  engine: 'codex',
  origin: 'delegated',
  capability_limits: {},
  control_owner: 'orchestrator',
  revision: 3,
  updated_at: now - 3600,
  parent_session_id: 'main',
});
const snapshot = (children: OrchestratorChild[]): OrchestratorSnapshot => ({session: {id: 'main', title: 'Main agent'} as Session, children, activity: [], cursor: 1});
let aside: HTMLElement;
let target: HTMLDivElement;

beforeEach(() => {
  api.mockReset();
  board.mockReset().mockResolvedValue({orchestrator_board: board3});
  vi.spyOn(Date, 'now').mockReturnValue(now * 1000);
  aside = document.createElement('aside');
  aside.setAttribute('aria-label', 'Subagents');
  target = document.createElement('div');
  aside.append(target);
  document.body.append(aside);
  api.mockImplementation(async path => (path === '/api/workspace/orchestrator' ? snapshot([child('run', 'running'), child('done'), child('failed', 'failed')]) : {}));
});
afterEach(() => {
  cleanup();
  aside.remove();
  vi.restoreAllMocks();
});

const props = {ownerId: 'owner', selected: 'main', enabled: true, onOpen: vi.fn(), presentation: 'sidebar' as const};

it('draws one list with no Active or Done headings and counts when it is compact', async () => {
  const {container} = render(<OrchestratorStatus {...props} compact sidebarTarget={target} />);
  await screen.findByRole('button', {name: 'Open Agent run'});
  const group = screen.getByRole('region', {name: 'All subagents'});
  expect(within(group).getAllByRole('button', {name: /^Open Agent/})).toHaveLength(3);
  expect(screen.queryByRole('region', {name: 'Active subagents'})).not.toBeInTheDocument();
  expect(screen.queryByRole('region', {name: 'Done subagents'})).not.toBeInTheDocument();
  expect(within(group).getByRole('heading', {name: 'Subagents', hidden: true})).toHaveClass('visually-hidden');
  // The Plans heading keeps no count of its own, and no extra summary line sits under it.
  const plans = screen.getByRole('button', {name: /Plans/});
  expect(plans).not.toHaveTextContent('3');
  expect(screen.queryByText(/planned tasks? need/)).not.toBeInTheDocument();
  await expectNoAxeViolations(container);
});

it('keeps the grouped Active and Done lists when it is not compact', async () => {
  render(<OrchestratorStatus {...props} sidebarTarget={target} />);
  expect(await screen.findByRole('region', {name: 'Active subagents'})).toBeInTheDocument();
  expect(screen.getByRole('region', {name: 'Done subagents'})).toBeInTheDocument();
  expect(screen.queryByRole('region', {name: 'All subagents'})).not.toBeInTheDocument();
});

it('reports blocked planned tasks in its summary, apart from sub-agents needing attention', async () => {
  const summary = vi.fn();
  render(<OrchestratorStatus {...props} compact sidebarTarget={target} onSummary={summary} />);
  await waitFor(() => expect(summary).toHaveBeenLastCalledWith(expect.objectContaining({blocked: 2, plans: 3})));
});
