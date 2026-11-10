import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {gql} from '../lib/api';
import type {Board, BoardNode} from '../lib/orchestrator-board';
import {expectNoAxeViolations} from '../test-axe';
import TaskBoard from './TaskBoard';

vi.mock('../lib/api', () => ({gql: vi.fn()}));
const api = vi.mocked(gql);

const now = () => Date.now() / 1000;
const node = (over: Partial<BoardNode>): BoardNode => ({
  id: 'n', title: 'Task', role: 'worker', depends_on: [], blocked_by: [], task_id: null, status: 'pending', attempts: 0, max_attempts: 2,
  last_event_at: null, result_summary: '', error: '', provider_id: 'p1', model: 'model-a', tokens: 0, cost_usd: null, created_at: now() - 600, updated_at: now() - 60, ...over,
});
const rollup = (nodes: BoardNode[], over: Partial<Board['rollup']> = {}): Board['rollup'] => ({
  total: nodes.length, counts: nodes.reduce<Record<string, number>>((all, row) => ({...all, [row.status]: (all[row.status] || 0) + 1}), {}),
  percent_complete: Math.round(100 * nodes.filter(row => row.status === 'completed').length / (nodes.length || 1)), tokens: nodes.reduce((sum, row) => sum + row.tokens, 0), cost_usd: null, ...over,
});
const board = (nodes: BoardNode[], over: Partial<Board['rollup']> = {}): Board => ({nodes, rollup: rollup(nodes, over)});
const collect = node({id: 'collect', title: 'Collect sources', status: 'completed', tokens: 1200, created_at: now() - 900, updated_at: now() - 300});
const draft = node({id: 'draft', title: 'Draft summary', status: 'running', depends_on: ['collect'], tokens: 800, model: 'model-b', provider_id: 'p2'});
const review = node({id: 'review', title: 'Review summary', status: 'stalled', depends_on: ['draft'], attempts: 1});
const publish = node({id: 'publish', title: 'Publish report', status: 'pending', depends_on: ['review'], blocked_by: ['review']});
const providers = [
  {id: 'p1', name: 'Local Ollama', models: ['model-a', 'llama3:8b'], secret_configured: false, catalog_refreshed_at: 5},
  {id: 'p2', name: 'Hosted', models: ['model-b'], secret_configured: true, catalog_refreshed_at: null},
  {id: 'p3', name: 'Undiscovered', models: null, secret_configured: false, catalog_refreshed_at: null},
];
let current: Board;
const calls = (word: string) => api.mock.calls.filter(([query]) => query.includes(word));
const row = (title: string) => screen.getByRole('listitem', {name: title});
beforeEach(() => {
  current = board([collect, draft, review, publish]);
  api.mockReset();
  api.mockImplementation(async (query: string) => {
    if (query.includes('agent_providers')) return {agent_providers: providers};
    if (query.includes('retry_node')) return {retry_node: current};
    if (query.includes('cancel_node')) return {cancel_node: current};
    if (query.includes('reassign_node')) return {reassign_node: current};
    return {orchestrator_board: current};
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); });

it('shows a rollup with percent complete, counts by status and unknown cost', async () => {
  render(<TaskBoard enabled/>);
  const bar = await screen.findByRole('progressbar', {name: 'Tasks complete'});
  expect(bar).toHaveAttribute('aria-valuenow', '25');
  const summary = screen.getByRole('group', {name: 'Task board summary'});
  expect(within(summary).getByText(/25% complete/)).toBeInTheDocument();
  expect(within(summary).getByText('1 running')).toBeInTheDocument();
  expect(within(summary).getByText('1 stalled')).toBeInTheDocument();
  expect(within(summary).getByText('1 waiting')).toBeInTheDocument();
  expect(within(summary).getByText('2K tokens · Unknown')).toBeInTheDocument();
});

it('lists tasks indented under their dependencies with model chip, status, elapsed time and a Stalled badge', async () => {
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  expect(row('Collect sources').style.getPropertyValue('--depth')).toBe('0');
  expect(row('Draft summary').style.getPropertyValue('--depth')).toBe('1');
  expect(row('Publish report').style.getPropertyValue('--depth')).toBe('3');
  expect(within(row('Draft summary')).getByText('model-b')).toBeInTheDocument();
  expect(within(row('Draft summary')).getByText('Running')).toBeInTheDocument();
  expect(within(row('Draft summary')).getByText('10 min')).toBeInTheDocument();
  expect(within(row('Draft summary')).getByText('Depends on Collect sources')).toBeInTheDocument();
  expect(within(row('Review summary')).getByText('Stalled')).toBeInTheDocument();
  expect(within(row('Publish report')).getByText('Waiting for Review summary')).toBeInTheDocument();
  expect(within(row('Publish report')).queryByText(/min$/)).not.toBeInTheDocument();
});

it('pulses the status dot only while a task is running', async () => {
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  const dot = (title: string) => row(title).querySelector('.task-dot');
  expect(dot('Draft summary')).toHaveAttribute('data-live', 'true');
  for (const title of ['Collect sources', 'Review summary', 'Publish report']) expect(dot(title)).not.toHaveAttribute('data-live');
});

it('says what to do next when the board is empty', async () => {
  current = board([]);
  render(<TaskBoard enabled/>);
  expect(await screen.findByText(/No planned tasks yet/)).toHaveTextContent('Ask your main agent to split a goal into tasks');
});

it('explains a failed load and refreshes on request', async () => {
  api.mockRejectedValueOnce(new Error('Host unreachable'));
  render(<TaskBoard enabled/>);
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent('Could not load the task board. Host unreachable. Check the connection to this host, then refresh the board.');
  fireEvent.click(within(alert).getByRole('button', {name: 'Refresh board'}));
  await screen.findByText('Draft summary');
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('keeps the last board visible and says so when a later update fails', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  api.mockRejectedValue(new Error('Offline'));
  await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
  expect(await screen.findByRole('alert')).toHaveTextContent('Showing the last received state.');
  expect(screen.getByText('Draft summary')).toBeInTheDocument();
});

it('announces what polling finds: a task failing, stalling, waiting on you, and the last one finishing', async () => {
  vi.useFakeTimers({shouldAdvanceTime: true});
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  const updates = screen.getByRole('status', {name: 'Task board updates'});
  expect(updates).toHaveTextContent('');  // the first load is not news
  const poll = async () => { await act(async () => { await vi.advanceTimersByTimeAsync(4100); }); };

  current = board([collect, {...draft, status: 'failed', error: 'model exploded'}, review, publish]);
  await poll();
  expect(updates).toHaveTextContent('“Draft summary” failed.');

  current = board([collect, {...draft, status: 'failed', error: 'model exploded'}, {...review, status: 'running', waiting: 'approval'}, publish]);
  await poll();
  expect(updates).toHaveTextContent('“Review summary” is waiting for your approval.');
  expect(within(row('Review summary')).getByText(/Waiting for your approval\. Open the task/)).toBeInTheDocument();

  current = board([collect, {...draft, status: 'failed'}, {...review, status: 'stalled'}, publish]);
  await poll();
  expect(updates).toHaveTextContent('“Review summary” has stalled.');

  current = board([collect, {...draft, status: 'completed'}, {...review, status: 'completed'}, {...publish, status: 'completed', blocked_by: []}]);
  await poll();
  expect(updates).toHaveTextContent('All tasks are complete.');

  const quiet = updates.textContent;
  await poll();  // nothing changed: nothing is announced again
  expect(updates.textContent).toBe(quiet);
});

it('does not request anything while disabled', () => {
  render(<TaskBoard enabled={false}/>);
  expect(api).not.toHaveBeenCalled();
});

it('offers only the actions that fit each status', async () => {
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  expect(within(row('Collect sources')).queryByRole('button', {name: /Actions for/})).not.toBeInTheDocument();
  await user.click(within(row('Draft summary')).getByRole('button', {name: 'Actions for Draft summary'}));
  expect(screen.getAllByRole('menuitem').map(item => item.textContent)).toEqual(['Cancel task']);
  await user.keyboard('{Escape}');
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  expect(screen.getAllByRole('menuitem').map(item => item.textContent)).toEqual(['Retry task', 'Reassign model', 'Cancel task']);
});

it('opens the row menu with the keyboard, moves with arrow keys, closes with Escape and returns focus', async () => {
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  const trigger = within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'});
  expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
  trigger.focus();
  await user.keyboard('{Enter}');
  expect(trigger).toHaveAttribute('aria-expanded', 'true');
  const items = screen.getAllByRole('menuitem');
  expect(items[0]).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(items[1]).toHaveFocus();
  await user.keyboard('{End}');
  expect(items[2]).toHaveFocus();
  await user.keyboard('{ArrowDown}');
  expect(items[0]).toHaveFocus();
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
  expect(trigger).toHaveAttribute('aria-expanded', 'false');
});

it('retries a stalled task from the keyboard, shows pending on that row only, and announces the result', async () => {
  const user = userEvent.setup();
  let finish!: (value: unknown) => void;
  api.mockImplementation(async (query: string) => {
    if (query.includes('retry_node')) return new Promise(resolve => { finish = resolve; });
    if (query.includes('agent_providers')) return {agent_providers: providers};
    return {orchestrator_board: current};
  });
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}).focus();
  await user.keyboard('{Enter}{Enter}');
  expect(api).toHaveBeenCalledWith(expect.stringContaining('retry_node'), {id: 'review'});
  expect(within(row('Review summary')).getByText('Retrying…')).toBeInTheDocument();
  expect(row('Review summary')).toHaveAttribute('aria-busy', 'true');
  expect(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'})).toHaveAttribute('aria-disabled', 'true');
  expect(within(row('Draft summary')).getByRole('button', {name: 'Actions for Draft summary'})).toBeEnabled();
  current = board([collect, draft, {...review, status: 'running', attempts: 2}, publish]);
  await act(async () => finish({retry_node: current}));
  expect(within(row('Review summary')).queryByText('Retrying…')).not.toBeInTheDocument();
  expect(within(row('Review summary')).getByText('Running')).toBeInTheDocument();
  expect(screen.getByRole('status', {name: 'Task board updates'})).toHaveTextContent('Retried “Review summary”.');
});

it('does not offer Retry once the attempts are used and points to Reassign', async () => {
  const user = userEvent.setup();
  current = board([node({id: 'x', title: 'Flaky', status: 'failed', attempts: 2, error: 'Provider returned 500'})]);
  render(<TaskBoard enabled/>);
  await screen.findByText('Flaky');
  expect(within(row('Flaky')).getByText('Provider returned 500')).toBeInTheDocument();
  expect(within(row('Flaky')).getByText('Used all 2 attempts. Reassign it to another model or cancel it.')).toBeInTheDocument();
  await user.click(within(row('Flaky')).getByRole('button', {name: 'Actions for Flaky'}));
  expect(screen.getAllByRole('menuitem').map(item => item.textContent)).toEqual(['Reassign model', 'Cancel task']);
});

it('reassigns to a discovered model of a connected provider', async () => {
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  await user.click(screen.getByRole('menuitem', {name: 'Reassign model'}));
  const select = await screen.findByRole('combobox', {name: 'Model for Review summary'});
  await waitFor(() => expect(within(select).getAllByRole('option').length).toBeGreaterThan(1));
  const labels = within(select).getAllByRole('option').map(option => option.textContent);
  expect(labels).toEqual(['model-a', 'llama3:8b', 'model-b']);
  expect(within(select).getAllByRole('group').map(group => group.getAttribute('label'))).toEqual(['Local Ollama', 'Hosted']);
  const submit = screen.getByRole('button', {name: 'Reassign model'});
  expect(submit).toBeDisabled();
  await user.selectOptions(select, 'llama3:8b');
  expect(submit).toBeEnabled();
  await user.click(submit);
  expect(api).toHaveBeenCalledWith(expect.stringContaining('reassign_node'), {id: 'review', input: {provider_id: 'p1', model: 'llama3:8b'}});
  await waitFor(() => expect(screen.queryByRole('combobox', {name: 'Model for Review summary'})).not.toBeInTheDocument());
  expect(screen.getByRole('status', {name: 'Task board updates'})).toHaveTextContent('Reassigned “Review summary” to llama3:8b.');
});

it('lists the models of a keyless provider that was never refreshed', async () => {
  const user = userEvent.setup();
  api.mockImplementation(async (query: string) => query.includes('agent_providers')
    ? {agent_providers: [{id: 'local', name: 'LM Studio', models: ['qwen'], secret_configured: false, catalog_refreshed_at: null}]}
    : {orchestrator_board: current});
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  await user.click(screen.getByRole('menuitem', {name: 'Reassign model'}));
  const select = await screen.findByRole('combobox', {name: 'Model for Review summary'});
  await waitFor(() => expect(within(select).getByRole('option', {name: 'qwen'})).toBeInTheDocument());
});

it('keeps the current model selectable when it is not among the discovered models', async () => {
  const user = userEvent.setup();
  current = board([node({id: 'x', title: 'Odd one', status: 'failed', attempts: 1, provider_id: 'gone', model: 'retired-model'})]);
  render(<TaskBoard enabled/>);
  await screen.findByText('Odd one');
  await user.click(within(row('Odd one')).getByRole('button', {name: 'Actions for Odd one'}));
  await user.click(screen.getByRole('menuitem', {name: 'Reassign model'}));
  const select = await screen.findByRole('combobox', {name: 'Model for Odd one'});
  await waitFor(() => expect(within(select).getAllByRole('option').length).toBeGreaterThan(1));
  expect(select).toHaveValue(JSON.stringify(['gone', 'retired-model']));
  expect(within(select).getByRole('option', {name: 'retired-model (current)'})).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Reassign model'})).toBeDisabled();
});

it('closes the reassign form with Escape and returns focus to the row menu button', async () => {
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  const trigger = within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'});
  await user.click(trigger);
  await user.click(screen.getByRole('menuitem', {name: 'Reassign model'}));
  await screen.findByRole('combobox', {name: 'Model for Review summary'});
  await user.keyboard('{Escape}');
  expect(screen.queryByRole('combobox', {name: 'Model for Review summary'})).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('tells the user where to add a model when discovery found none', async () => {
  const user = userEvent.setup();
  api.mockImplementation(async (query: string) => query.includes('agent_providers') ? {agent_providers: [providers[2]]} : {orchestrator_board: current});
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  await user.click(screen.getByRole('menuitem', {name: 'Reassign model'}));
  expect(await screen.findByText(/No models available/)).toHaveTextContent('Connect a provider and refresh its models in Settings, then reassign');
  expect(screen.getByRole('button', {name: 'Reassign model'})).toBeDisabled();
});

it('cancels a running task and says which verb happened', async () => {
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  current = board([collect, {...draft, status: 'cancelled'}, review, publish]);
  await user.click(within(row('Draft summary')).getByRole('button', {name: 'Actions for Draft summary'}));
  await user.click(screen.getByRole('menuitem', {name: 'Cancel task'}));
  expect(api).toHaveBeenCalledWith(expect.stringContaining('cancel_node'), {id: 'draft'});
  await waitFor(() => expect(within(row('Draft summary')).getByText('Cancelled')).toBeInTheDocument());
  expect(screen.getByRole('status', {name: 'Task board updates'})).toHaveTextContent('Cancelled “Draft summary”.');
});

it('offers Start ready tasks only on a waiting task with nothing left to wait for', async () => {
  const ready = node({id: 'ready', title: 'Failing check', status: 'pending', depends_on: ['collect'], blocked_by: []});
  current = board([collect, ready, publish]);
  const started = board([collect, {...ready, status: 'running'}, publish]);
  api.mockImplementation(async (query: string) => {
    if (query.includes('start_ready_nodes')) { current = started; return {start_ready_nodes: started}; }
    return {orchestrator_board: current};
  });
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Failing check');
  await user.click(within(row('Publish report')).getByRole('button', {name: 'Actions for Publish report'}));
  expect(screen.queryByRole('menuitem', {name: 'Start ready tasks'})).not.toBeInTheDocument();
  await user.keyboard('{Escape}');
  await user.click(within(row('Failing check')).getByRole('button', {name: 'Actions for Failing check'}));
  await user.click(screen.getByRole('menuitem', {name: 'Start ready tasks'}));
  await waitFor(() => expect(calls('start_ready_nodes')).toHaveLength(1));
  await waitFor(() => expect(row('Failing check')).toHaveAttribute('data-status', 'running'));
});

it('keeps focus on the row menu button after retry and cancel', async () => {
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  const trigger = within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'});
  await user.click(trigger);
  await user.click(screen.getByRole('menuitem', {name: 'Retry task'}));
  await waitFor(() => expect(within(row('Review summary')).queryByText('Retrying…')).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
  await user.click(trigger);
  await user.click(screen.getByRole('menuitem', {name: 'Cancel task'}));
  await waitFor(() => expect(within(row('Review summary')).queryByText('Cancelling…')).not.toBeInTheDocument());
  expect(trigger).toHaveFocus();
});

it('explains why dependents were cancelled and counts them in the confirmation', async () => {
  const user = userEvent.setup();
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  current = board([collect, draft, {...review, status: 'cancelled', error: 'Cancelled'},
    {...publish, status: 'cancelled', error: 'Cancelled because "Review summary" was cancelled'}]);
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  await user.click(screen.getByRole('menuitem', {name: 'Cancel task'}));
  await waitFor(() => expect(screen.getByRole('status', {name: 'Task board updates'})).toHaveTextContent('Cancelled “Review summary” and 1 task that depends on it.'));
  expect(within(row('Publish report')).getByText('Cancelled because "Review summary" was cancelled')).toBeInTheDocument();
});

it('does not suggest cancelling a task that is already cancelled', async () => {
  current = board([node({id: 'x', title: 'Stopped', status: 'cancelled', attempts: 2, error: 'Cancelled'})]);
  render(<TaskBoard enabled/>);
  expect(await screen.findByText('Used all 2 attempts. Reassign it to another model to run it again.')).toBeInTheDocument();
});

it('keeps the board a retry returned when an older poll answers afterwards', async () => {
  const user = userEvent.setup();
  vi.useFakeTimers({shouldAdvanceTime: true});
  const retried = board([collect, draft, {...review, status: 'running', attempts: 2}, publish]);
  let answerPoll!: (value: unknown) => void;
  let polls = 0;
  api.mockImplementation(async (query: string) => {
    if (query.includes('retry_node')) return {retry_node: retried};
    if (query.includes('agent_providers')) return {agent_providers: providers};
    polls += 1;
    if (polls === 2) return new Promise(resolve => { answerPoll = resolve; });
    return {orchestrator_board: polls === 1 ? current : retried};
  });
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  await act(async () => { await vi.advanceTimersByTimeAsync(4100); });
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  await user.click(screen.getByRole('menuitem', {name: 'Retry task'}));
  await waitFor(() => expect(within(row('Review summary')).getByText('Running')).toBeInTheDocument());
  await act(async () => answerPoll({orchestrator_board: current}));
  expect(within(row('Review summary')).getByText('Running')).toBeInTheDocument();
});

it('shows an inline error on the failed row with the next step and leaves other rows usable', async () => {
  const user = userEvent.setup();
  api.mockImplementation(async (query: string) => {
    if (query.includes('retry_node')) throw new Error('Node is running; only failed nodes can be retried');
    return {orchestrator_board: current};
  });
  render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  await user.click(screen.getByRole('menuitem', {name: 'Retry task'}));
  const alert = await within(row('Review summary')).findByRole('alert');
  expect(alert).toHaveTextContent('Could not retry “Review summary”. Node is running; only failed nodes can be retried. Refresh the board to see its current state, then try again or reassign it to another model.');
  expect(within(row('Draft summary')).queryByRole('alert')).not.toBeInTheDocument();
  expect(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'})).toBeEnabled();
  await user.click(within(alert).getByRole('button', {name: 'Dismiss'}));
  expect(within(row('Review summary')).queryByRole('alert')).not.toBeInTheDocument();
});

it('has no accessibility violations with the board, menu and reassign form open', async () => {
  const user = userEvent.setup();
  const view = render(<TaskBoard enabled/>);
  await screen.findByText('Draft summary');
  await expectNoAxeViolations(view.container);
  await user.click(within(row('Review summary')).getByRole('button', {name: 'Actions for Review summary'}));
  await expectNoAxeViolations(view.container);
  await user.click(screen.getByRole('menuitem', {name: 'Reassign model'}));
  await screen.findByRole('combobox', {name: 'Model for Review summary'});
  await expectNoAxeViolations(view.container);
});

it('has no accessibility violations in the empty and error states', async () => {
  current = board([]);
  const empty = render(<TaskBoard enabled/>);
  await screen.findByText(/No planned tasks yet/);
  await expectNoAxeViolations(empty.container);
  empty.unmount();
  api.mockRejectedValue(new Error('Offline'));
  const failed = render(<TaskBoard enabled/>);
  await screen.findByRole('alert');
  await expectNoAxeViolations(failed.container);
});
