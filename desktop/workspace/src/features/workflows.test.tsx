import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import Workflows, {type WorkflowsProps} from './Workflows';
import {ApiError, request} from '../lib/api';
import type {WorkflowCapabilities, WorkflowDefinition, WorkflowRun} from '../lib/workflows';

vi.mock('../lib/api', async original => ({...await original<typeof import('../lib/api')>(), request: vi.fn()}));
const mocked = vi.mocked(request);
const path = '/api/workspace/workflows';
const limits = {max_agents: 16, concurrency: 4, timeout_s: 900};
const definition: WorkflowDefinition = {id: 'review', revision: 3, name: 'Review workflow', description: 'Inspect the selected request.', project_id: 'p1', language: 'javascript', source: 'return args;', input_schema: {type: 'object', properties: {}, additionalProperties: false}, limits, archived: false, created_at: 100, updated_at: 200};
const typed: WorkflowDefinition = {...definition, input_schema: {type: 'object', properties: {request: {type: 'string', minLength: 1}, count: {type: 'integer', minimum: 1, maximum: 8}, choice: {type: 'string', enum: ['quick', 'careful']}, approved: {type: 'boolean'}, config: {type: 'object', properties: {enabled: {type: 'boolean'}}, required: ['enabled'], additionalProperties: false}}, required: ['request', 'count', 'choice', 'approved', 'config'], additionalProperties: false}};
const makeRun = (changes: Partial<WorkflowRun> = {}): WorkflowRun => ({id: 'run-first', workflow_id: definition.id, workflow_revision: 3, name: 'Review workflow receipt', status: 'completed', args: {}, project_id: 'p1', parent_session_id: 'parent-chat', snapshot: {name: definition.name, description: definition.description, language: 'javascript', source: definition.source, input_schema: definition.input_schema, limits}, limits, calls: [], result: {summary: 'The saved result'}, error: null, allowed_actions: ['retry'], created_at: 250, updated_at: 260, finished_at: 260, ...changes});
const props = (changes: Partial<WorkflowsProps> = {}): WorkflowsProps => ({ownerId: 'owner', projectId: 'p1', projects: [{id: 'p1', name: 'App', path: '/projects/app'}, {id: 'p2', name: 'Docs', path: '/projects/docs'}], sessionId: 'parent-chat', visible: true, onNavigate: vi.fn(), onOpenSession: vi.fn(), ...changes});
let rows: WorkflowDefinition[];
let history: WorkflowRun[];
let capabilities: WorkflowCapabilities;
let detail: WorkflowRun;
function base(address: string, init?: RequestInit): unknown {
  const url = new URL(address, 'https://termx.test');
  if (init?.method && init.method !== 'GET') throw new Error('Unexpected mutation: ' + init.method + ' ' + address);
  if (url.pathname === path + '/capabilities') return capabilities;
  if (url.pathname === path) return {workflows: rows.filter(row => row.archived === (url.searchParams.get('archived') === 'true'))};
  if (url.pathname === path + '/runs') return {runs: history};
  if (url.pathname === path + '/runs/' + detail.id) return detail;
  if (url.pathname === path + '/' + definition.id) return rows.find(row => row.id === definition.id) || definition;
  throw new Error('Unexpected read: ' + address);
}
const writes = (address = path + '/runs', method = 'POST') => mocked.mock.calls.filter(([target, init]) => target === address && init?.method === method);
async function inputs(name = definition.name) {
  await screen.findByRole('button', {name: name + ' ' + definition.description});
  fireEvent.click(screen.getByRole('button', {name: 'Run'}));
  return screen.findByRole('dialog', {name});
}
function fillInputs(dialog: HTMLElement) {
  const controls = within(dialog);
  fireEvent.change(controls.getByLabelText(/^request/), {target: {value: 'Review these changes'}});
  fireEvent.change(controls.getByLabelText(/^count/), {target: {value: '3'}});
  fireEvent.change(controls.getByLabelText(/^choice/), {target: {value: '"careful"'}});
  fireEvent.change(controls.getByLabelText(/^approved/), {target: {value: 'false'}});
  fireEvent.change(controls.getByLabelText(/^config/), {target: {value: '{"enabled":true}'}});
}
beforeEach(() => {
  mocked.mockReset(); rows = [definition]; history = []; detail = makeRun();
  capabilities = {runtime: {available: true, reason: ''}, limits, helpers: ['agent', 'parallel', 'pipeline', 'phase', 'log'], agent_only: true, automatic_replay: false};
  mocked.mockImplementation(async (address, init) => base(address, init) as never);
});
afterEach(cleanup);

describe('workflow library and inputs', () => {
  it('routes through the shared workspace navigation and filters real definitions without writes', async () => {
    const callbacks = props(); render(<Workflows {...callbacks}/>);
    await screen.findByRole('button', {name: definition.name + ' ' + definition.description});
    const views = screen.getByRole('navigation', {name: 'Workspace views'});
    expect(within(views).getByRole('button', {name: 'Workflows'})).toHaveAttribute('aria-current', 'page');
    fireEvent.click(within(views).getByRole('button', {name: 'Activity'}));
    expect(callbacks.onNavigate).toHaveBeenCalledWith('tasks');
    fireEvent.change(screen.getByRole('searchbox', {name: 'Search workflows'}), {target: {value: 'missing'}});
    expect(screen.getByText('No workflows match your search.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Clear search'}));
    expect(screen.getByRole('button', {name: definition.name + ' ' + definition.description})).toBeInTheDocument();
    expect(mocked.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
  });

  it('keeps Start disabled while runtime is unavailable and retries the actual capability read', async () => {
    capabilities = {...capabilities, runtime: {available: false, reason: 'QuickJS runtime is not installed.'}};
    render(<Workflows {...props()}/>);
    const dialog = await inputs();
    expect(within(dialog).getByRole('alert')).toHaveTextContent('QuickJS runtime is not installed.');
    expect(within(dialog).getByRole('button', {name: 'Start'})).toBeDisabled();
    fireEvent.submit(within(dialog).getByRole('button', {name: 'Start'}).closest('form')!);
    expect(writes()).toHaveLength(0);
    capabilities = {...capabilities, runtime: {available: true, reason: ''}};
    fireEvent.click(within(dialog).getByRole('button', {name: 'Retry'}));
    await waitFor(() => expect(within(dialog).getByRole('button', {name: 'Start'})).toBeEnabled());
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument();
    expect(mocked.mock.calls.filter(([address]) => address === path + '/capabilities').length).toBeGreaterThan(1);
  });

  it('starts with typed enum, boolean, numeric and object inputs and exact revision/session context', async () => {
    rows = [typed];
    mocked.mockImplementation(async (address, init) => init?.method === 'POST' && address === path + '/runs' ? detail as never : base(address, init) as never);
    render(<Workflows {...props()}/>);
    const dialog = await inputs(); fillInputs(dialog);
    fireEvent.click(within(dialog).getByRole('button', {name: 'Start'}));
    await screen.findByRole('region', {name: 'Workflow run'});
    expect(writes()).toHaveLength(1);
    const payload = JSON.parse(String(writes()[0][1]?.body));
    expect(payload).toEqual({request_id: expect.any(String), workflow_id: 'review', revision: 3, project_id: 'p1', parent_session_id: 'parent-chat', limits:{max_agents:null,concurrency:null,timeout_s:null}, args: {request: 'Review these changes', count: 3, choice: 'careful', approved: false, config: {enabled: true}}});
    expect(payload.request_id.length).toBeGreaterThanOrEqual(8);
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs/run-first')).toBe(true);
  });

  it('shows input validation without submitting a workflow and preserves the entered values', async () => {
    rows = [typed]; render(<Workflows {...props()}/>);
    const dialog = await inputs(); fillInputs(dialog);
    fireEvent.change(within(dialog).getByLabelText(/^config/), {target: {value: '{"enabled":"yes"}'}});
    fireEvent.submit(within(dialog).getByRole('button', {name: 'Start'}).closest('form')!);
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('boolean');
    expect(writes()).toHaveLength(0);
    expect(within(dialog).getByLabelText(/^request/)).toHaveValue('Review these changes');
    expect(within(dialog).getByRole('button', {name: 'Start'})).toBeEnabled();
  });

  it('deduplicates synchronous Start submissions and reuses the request ID after a lost response', async () => {
    rows = [typed]; let reject!: (error: Error) => void;
    mocked.mockImplementation(async (address, init) => address === path + '/runs' && init?.method === 'POST' ? await new Promise((_, fail) => {reject = fail;}) as never : base(address, init) as never);
    render(<Workflows {...props()}/>);
    const dialog = await inputs(); fillInputs(dialog);
    const form = within(dialog).getByRole('button', {name: 'Start'}).closest('form')!;
    act(() => {fireEvent.submit(form); fireEvent.submit(form);});
    expect(writes()).toHaveLength(1);
    expect(within(dialog).getByRole('button', {name: 'Starting…'})).toBeDisabled();
    const first = JSON.parse(String(writes()[0][1]?.body));
    await act(async () => reject(new Error('Response lost; inspect the run before retrying.')));
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Response lost');
    expect(within(dialog).getByLabelText(/^count/)).toHaveValue(3);
    mocked.mockImplementation(async (address, init) => address === path + '/runs' && init?.method === 'POST' ? detail as never : base(address, init) as never);
    fireEvent.submit(form);
    await screen.findByRole('region', {name: 'Workflow run'});
    expect(writes()).toHaveLength(2);
    expect(JSON.parse(String(writes()[1][1]?.body))).toEqual(first);
  });

  it('requests active and archived definitions separately and merges both into the library', async () => {
    const archived = {...definition, id: 'archived', name: 'Earlier workflow', archived: true, revision: 6}; rows = [definition, archived];
    render(<Workflows {...props()}/>);
    await screen.findByRole('button', {name: definition.name + ' ' + definition.description});
    expect(screen.queryByRole('button', {name: 'Earlier workflow Archived'})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Workflow list options'}));
    fireEvent.click(within(screen.getByRole('dialog', {name: 'Workflow list options'})).getByRole('button', {name: 'Show archived'}));
    await screen.findByRole('button', {name: 'Earlier workflow Archived'});
    expect(screen.getByRole('button', {name: definition.name + ' ' + definition.description})).toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '?archived=false&project_id=p1')).toBe(true);
    expect(mocked.mock.calls.some(([address]) => address === path + '?archived=true&project_id=p1')).toBe(true);
    expect(within(screen.getByRole('button', {name: 'Earlier workflow Archived'}).closest('li')!).getByRole('button', {name: 'Run'})).toBeDisabled();
  });

  it('opens the exact durable run from history and routes an approval to its actual agent session', async () => {
    detail = makeRun({id: 'run-history', workflow_revision: 1, name: 'Saved review receipt', status: 'awaiting_approval', finished_at: null, allowed_actions: ['cancel'], calls: [{id: 'call-real', sequence: 1, operation: 'agent', name: 'Reviewer', status: 'awaiting_approval', session_id: 'reviewer-chat', task_id: 'reviewer-task', output: null, error: null, started_at: 252, finished_at: null}]});
    history = [detail]; const callbacks = props(); render(<Workflows {...callbacks}/>);
    fireEvent.click(await screen.findByRole('button', {name: definition.name + ' ' + definition.description}));
    const historyPanel = await screen.findByRole('region', {name: 'Run history'});
    fireEvent.click(await within(historyPanel).findByRole('button', {name: /Needs approval Version 1/}));
    const runPanel = await screen.findByRole('region', {name: 'Workflow run'});
    expect(await within(runPanel).findByRole('heading', {name: 'Saved review receipt'})).toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs?workflow_id=review')).toBe(true);
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs/run-history')).toBe(true);
    fireEvent.click(within(runPanel).getByRole('button', {name: 'Review'}));
    expect(callbacks.onOpenSession).toHaveBeenCalledWith('reviewer-chat');
    fireEvent.click(within(runPanel).getByRole('button', {name: 'History'}));
    expect(await screen.findByRole('region', {name: 'Run history'})).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });
});

describe('workflow owner and project lifecycle', () => {
  it.each(['owner-success', 'owner-failure', 'project-success', 'project-failure'])('fences late Start completion after %s scope changes', async variant => {
    let complete!: (value: WorkflowRun) => void, fail!: (error: Error) => void;
    mocked.mockImplementation(async (address, init) => address === path + '/runs' && init?.method === 'POST' ? await new Promise<WorkflowRun>((resolve, reject) => {complete = resolve; fail = reject;}) as never : base(address, init) as never);
    const initial = props(); const view = render(<Workflows {...initial}/>);
    const oldDialog = await inputs(); fireEvent.click(within(oldDialog).getByRole('button', {name: 'Start'}));
    await waitFor(() => expect(writes()).toHaveLength(1));
    const next = {...definition, id: 'next', name: 'Current scope workflow', project_id: variant.startsWith('project') ? 'p2' : 'p1'}; rows = [next];
    view.rerender(<Workflows {...initial} {...(variant.startsWith('owner') ? {ownerId: 'other-owner'} : {projectId: 'p2'})}/>);
    const currentDialog = await inputs(next.name);
    await act(async () => {if (variant.endsWith('success')) complete(makeRun({id: 'stale-run'})); else fail(new Error('Old scope failure'));});
    expect(screen.getByRole('dialog', {name: next.name})).toBe(currentDialog);
    expect(within(currentDialog).getByRole('button', {name: 'Start'})).toBeEnabled();
    expect(screen.queryByText('Old scope failure')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', {name: 'Workflow run'})).not.toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs/stale-run')).toBe(false);
  });

  it('clears sensitive workflow rows and dialogs on lock without accepting a late run response', async () => {
    let complete!: (value: WorkflowRun) => void;
    mocked.mockImplementation(async (address, init) => address === path + '/runs' && init?.method === 'POST' ? await new Promise<WorkflowRun>(resolve => {complete = resolve;}) as never : base(address, init) as never);
    render(<Workflows {...props()}/>);
    const dialog = await inputs(); fireEvent.click(within(dialog).getByRole('button', {name: 'Start'}));
    await waitFor(() => expect(writes()).toHaveLength(1));
    act(() => window.dispatchEvent(new Event('termx-locked')));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: definition.name + ' ' + definition.description})).not.toBeInTheDocument();
    await act(async () => complete(makeRun({id: 'late-locked-run'})));
    expect(screen.queryByRole('region', {name: 'Workflow run'})).not.toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs/late-locked-run')).toBe(false);
  });

  it('removes stale workflow data after an authorization failure and preserves the actual host error', async () => {
    render(<Workflows {...props()}/>);
    await screen.findByRole('button', {name: definition.name + ' ' + definition.description});
    mocked.mockImplementation(async (address, init) => address.startsWith(path + '?') ? Promise.reject(new ApiError(403, 'Workflow access was revoked.')) : base(address, init) as never);
    fireEvent.click(screen.getByRole('button', {name: 'Refresh workflows'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Workflow access was revoked.');
    expect(screen.getByRole('button', {name: 'New workflow', hidden: true})).toBeDisabled();
    expect(screen.queryByRole('button', {name: definition.name + ' ' + definition.description})).not.toBeInTheDocument();
    mocked.mockImplementation(async (address, init) => base(address, init) as never);
    fireEvent.click(screen.getByRole('button', {name: 'Retry'}));
    await screen.findByRole('button', {name: definition.name + ' ' + definition.description});
    expect(screen.getByRole('button', {name: 'New workflow'})).toBeEnabled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('does not let an old archive completion unlock another scope’s in-flight mutation', async () => {
    let oldDone!: (value: WorkflowDefinition) => void, currentDone!: (value: WorkflowDefinition) => void;
    mocked.mockImplementation(async (address, init) => {
      if (init?.method === 'PATCH') return await new Promise<WorkflowDefinition>(resolve => {if (address.endsWith('/review')) oldDone = resolve; else currentDone = resolve;}) as never;
      return base(address, init) as never;
    });
    const initial = props(); const view = render(<Workflows {...initial}/>);
    await screen.findByRole('button', {name: definition.name + ' ' + definition.description});
    fireEvent.click(screen.getByRole('button', {name: 'Review workflow options'}));
    fireEvent.click(within(screen.getByRole('dialog', {name: 'Review workflow options'})).getByRole('button', {name: 'Archive'}));
    await waitFor(() => expect(writes(path + '/review', 'PATCH')).toHaveLength(1));
    const current = {...definition, id: 'current', name: 'Current workflow'}; rows = [current];
    view.rerender(<Workflows {...initial} ownerId="next-owner"/>);
    await screen.findByRole('button', {name: 'Current workflow ' + definition.description});
    fireEvent.click(screen.getByRole('button', {name: 'Current workflow options'}));
    fireEvent.click(within(screen.getByRole('dialog', {name: 'Current workflow options'})).getByRole('button', {name: 'Archive'}));
    await waitFor(() => expect(writes(path + '/current', 'PATCH')).toHaveLength(1));
    await act(async () => oldDone({...definition, archived: true, revision: 4}));
    fireEvent.click(screen.getByRole('button', {name: 'Current workflow options'}));
    expect(within(screen.getByRole('dialog', {name: 'Current workflow options'})).getByRole('button', {name: 'Archive'})).toBeDisabled();
    await act(async () => currentDone({...current, archived: true, revision: 4}));
  });
});

describe('durable workflow run entry and retry', () => {
  it('opens an incoming run ID through its authorized detail read without requiring a library definition', async () => {
    rows = []; detail = makeRun({id: 'deep-linked-run', name: 'Durable external workflow receipt'});
    render(<Workflows {...props({runId: detail.id})}/>);
    const panel = await screen.findByRole('region', {name: 'Workflow run'});
    expect(await within(panel).findByRole('heading', {name: detail.name})).toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs/deep-linked-run')).toBe(true);
    expect(within(panel).getByText('The saved result')).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
    fireEvent.click(within(panel).getByRole('button', {name: 'Runs'}));
    expect(await screen.findByRole('region', {name: 'Recent workflow runs'})).toBeInTheDocument();
  });

  it('does not turn an unauthorized incoming run into a visible receipt or a runnable workflow', async () => {
    mocked.mockImplementation(async (address, init) => address === path + '/runs/private-run' ? Promise.reject(new ApiError(403, 'This workflow run is unavailable.')) : base(address, init) as never);
    render(<Workflows {...props({runId: 'private-run'})}/>);
    expect(await screen.findByRole('alert')).toHaveTextContent('This workflow run is unavailable.');
    expect(screen.queryByRole('button', {name: 'Run again'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Stop'})).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'New workflow'})).toBeDisabled();
    expect(writes()).toHaveLength(0);
  });

  it('lists saved and inline runs from the scoped Runs endpoint and discards the previous project list', async () => {
    const saved = makeRun({id: 'saved-recent', name: 'Saved definition receipt'});
    const inline = makeRun({id: 'inline-recent', workflow_id: null, workflow_revision: null, name: 'Inline data receipt'});
    history = [saved, inline]; render(<Workflows {...props()}/>);
    await screen.findByRole('button', {name: definition.name + ' ' + definition.description});
    fireEvent.click(within(screen.getByRole('group', {name: 'Workflow records'})).getByRole('button', {name: 'Runs'}));
    const recent = await screen.findByRole('region', {name: 'Recent workflow runs'});
    expect(await within(recent).findByRole('button', {name: /Saved definition receipt Completed/})).toBeInTheDocument();
    expect(within(recent).getByRole('button', {name: /Inline data receipt Completed · Inline script/})).toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs?project_id=p1')).toBe(true);
    history = [makeRun({id: 'docs-run', name: 'Docs project receipt', project_id: 'p2'})];
    fireEvent.change(screen.getByRole('combobox', {name: 'Filter workflows by project'}), {target: {value: 'p2'}});
    expect(await within(recent).findByRole('button', {name: /Docs project receipt Completed/})).toBeInTheDocument();
    expect(within(recent).queryByRole('button', {name: /Saved definition receipt/})).not.toBeInTheDocument();
    expect(within(recent).queryByRole('button', {name: /Inline data receipt/})).not.toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs?project_id=p2')).toBe(true);
    expect(writes()).toHaveLength(0);
  });

  it('retries an inline run only after explicit Start using its immutable source, schema, name and arguments', async () => {
    const input_schema = {type: 'object', properties: {count: {type: 'integer', minimum: 1}, choice: {type: 'string', enum: ['quick', 'careful']}, config: {type: 'object', properties: {enabled: {type: 'boolean'}}, additionalProperties: false}}, required: ['count', 'choice', 'config'], additionalProperties: false};
    const original = makeRun({id: 'inline-original', workflow_id: null, workflow_revision: null, name: 'Inline snapshot receipt', args: {count: 2, choice: 'careful', config: {enabled: true}}, snapshot: {name: 'Snapshot transform', description: 'Saved inline instructions', language: 'javascript', source: 'return {count: args.count, choice: args.choice};', input_schema, limits: {max_agents: 2, concurrency: 1, timeout_s: 30}}});
    const next = {...original, id: 'inline-new-run'};
    mocked.mockImplementation(async (address, init) => address === path + '/runs' && init?.method === 'POST' ? next as never : address === path + '/runs/' + original.id ? original as never : address === path + '/runs/' + next.id ? next as never : base(address, init) as never);
    render(<Workflows {...props({runId: original.id})}/>);
    const panel = await screen.findByRole('region', {name: 'Workflow run'});
    fireEvent.click(await within(panel).findByRole('button', {name: 'Run again'}));
    const dialog = await screen.findByRole('dialog', {name: 'Run workflow again'});
    expect(within(dialog).getByLabelText(/^count/)).toHaveValue(2);
    expect(within(dialog).getByLabelText(/^choice/)).toHaveValue('"careful"');
    expect(JSON.parse(String((within(dialog).getByLabelText(/^config/) as HTMLTextAreaElement).value))).toEqual({enabled: true});
    expect(writes()).toHaveLength(0);
    expect(mocked.mock.calls.some(([address]) => address === path + '/' + original.id || address === path + '/inline:' + original.id)).toBe(false);
    fireEvent.change(within(dialog).getByLabelText(/^count/), {target: {value: '4'}});
    fireEvent.click(within(dialog).getByRole('button', {name: 'Start'}));
    await waitFor(() => expect(writes()).toHaveLength(1));
    const payload = JSON.parse(String(writes()[0][1]?.body));
    expect(payload).toEqual({request_id: expect.stringMatching(/^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/i), source: original.snapshot.source, name: original.snapshot.name, limits:{max_agents:null,concurrency:null,timeout_s:null}, args: {count: 4, choice: 'careful', config: {enabled: true}}, project_id: 'p1', parent_session_id: 'parent-chat'});
    expect(payload).not.toHaveProperty('workflow_id');
    expect(payload).not.toHaveProperty('revision');
    expect(payload.request_id).not.toBe(original.id);
    expect(await screen.findByRole('heading', {name: next.name})).toBeInTheDocument();
    expect(mocked.mock.calls.some(([address]) => address === path + '/runs/' + next.id)).toBe(true);
  });

  it('refreshes the saved definition before retry and preserves typed prior inputs under its latest schema and revision', async () => {
    const prior = makeRun({id: 'saved-original', args: {request: 'Previous request', count: 5, choice: 'quick', approved: false, config: {enabled: true}}, snapshot: {...typed, source: 'return "old source";'}});
    const latest: WorkflowDefinition = {...typed, revision: 9, source: 'return "latest source";', input_schema: {type: 'object', properties: {...typed.input_schema.properties as Record<string, unknown>, priority: {type: 'string', enum: ['normal', 'high']}}, required: [...typed.input_schema.required as string[], 'priority'], additionalProperties: false}};
    const next = makeRun({id: 'saved-retry', workflow_revision: 9});
    rows = [definition];
    mocked.mockImplementation(async (address, init) => address === path + '/runs' && init?.method === 'POST' ? next as never : address === path + '/runs/' + prior.id ? prior as never : address === path + '/runs/' + next.id ? next as never : address === path + '/' + definition.id ? latest as never : base(address, init) as never);
    render(<Workflows {...props({runId: prior.id})}/>);
    const panel = await screen.findByRole('region', {name: 'Workflow run'});
    fireEvent.click(await within(panel).findByRole('button', {name: 'Run again'}));
    const dialog = await screen.findByRole('dialog', {name: 'Run workflow again'});
    expect(mocked.mock.calls.some(([address]) => address === path + '/review')).toBe(true);
    expect(within(dialog).getByLabelText(/^request/)).toHaveValue('Previous request');
    expect(within(dialog).getByLabelText(/^count/)).toHaveValue(5);
    expect(within(dialog).getByLabelText(/^choice/)).toHaveValue('"quick"');
    expect(within(dialog).getByLabelText(/^approved/)).toHaveValue('false');
    expect(JSON.parse((within(dialog).getByLabelText(/^config/) as HTMLTextAreaElement).value)).toEqual({enabled: true});
    expect(within(dialog).getByLabelText(/^priority/)).toHaveValue('');
    expect(writes()).toHaveLength(0);
    fireEvent.change(within(dialog).getByLabelText(/^priority/), {target: {value: '"high"'}});
    fireEvent.click(within(dialog).getByRole('button', {name: 'Start'}));
    await waitFor(() => expect(writes()).toHaveLength(1));
    const payload = JSON.parse(String(writes()[0][1]?.body));
    expect(payload).toMatchObject({workflow_id: definition.id, revision: 9, args: {...prior.args, priority: 'high'}});
    expect(payload).not.toHaveProperty('source');
    expect(payload.request_id).not.toBe(prior.id);
  });

  it('clears a directly opened run when its owner changes and ignores the old owner’s late receipt', async () => {
    let finish!: (run: WorkflowRun) => void;
    mocked.mockImplementation(async (address, init) => address === path + '/runs/direct-old-owner' ? await new Promise<WorkflowRun>(resolve => {finish = resolve;}) as never : base(address, init) as never);
    const initial = props({runId: 'direct-old-owner'}); const view = render(<Workflows {...initial}/>);
    await waitFor(() => expect(mocked.mock.calls.some(([address]) => address === path + '/runs/direct-old-owner')).toBe(true));
    expect(screen.getByRole('region', {name: 'Workflow run'})).toBeInTheDocument();
    view.rerender(<Workflows {...initial} ownerId="new-owner"/>);
    expect(screen.queryByRole('region', {name: 'Workflow run'})).not.toBeInTheDocument();
    await act(async () => finish(makeRun({id: 'direct-old-owner', name: 'Previous owner private receipt'})));
    expect(screen.queryByRole('heading', {name: 'Previous owner private receipt'})).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Run again'})).not.toBeInTheDocument();
    expect(await screen.findByRole('region', {name: 'Recent workflow runs'})).toBeInTheDocument();
    expect(writes()).toHaveLength(0);
  });
});


describe('workflow child-chat return context',()=>{
 it('keeps the exact selected run when reviewing a child establishes the already-selected project',async()=>{
  detail=makeRun({id:'owned-project-run',name:'Exact owned review',status:'awaiting_approval',finished_at:null,allowed_actions:['cancel'],calls:[{id:'review-call',sequence:1,operation:'agent',name:'Reviewer',status:'awaiting_approval',session_id:'owned-child',task_id:'owned-child-task',output:null,error:null,started_at:252,finished_at:null}]});history=[detail];
  const callbacks=props({projectId:null,sessionId:null}),view=render(<Workflows {...callbacks}/>);
  await screen.findByRole('button',{name:definition.name+' '+definition.description});
  fireEvent.change(screen.getByRole('combobox',{name:'Filter workflows by project'}),{target:{value:'p1'}});
  fireEvent.click(await screen.findByRole('button',{name:definition.name+' '+definition.description}));
  fireEvent.click(await within(screen.getByRole('region',{name:'Run history'})).findByRole('button',{name:/Needs approval Version 3/}));
  const panel=await screen.findByRole('region',{name:'Workflow run'});
  fireEvent.click(await within(panel).findByRole('button',{name:'Review'}));
  expect(callbacks.onOpenSession).toHaveBeenCalledWith('owned-child');
  view.rerender(<Workflows {...callbacks} projectId="p1" sessionId="owned-child" visible={false}/>);
  detail={...detail,status:'completed',finished_at:300,allowed_actions:['retry'],calls:[{...detail.calls[0],status:'completed',finished_at:300,output:'Approved result'}]};
  view.rerender(<Workflows {...callbacks} projectId="p1" sessionId="owned-child"/>);
  expect(await screen.findByRole('heading',{name:'Exact owned review'})).toBeInTheDocument();
  await waitFor(()=>expect(within(screen.getByRole('region',{name:'Workflow run'})).getAllByText('Completed').length).toBeGreaterThan(0));
  expect(screen.queryByRole('searchbox',{name:'Search workflows'})).not.toBeInTheDocument();
  expect(writes()).toHaveLength(0);
 });

 it('keeps an unsaved editor when the incoming child project matches the effective library filter',async()=>{
  const callbacks=props({projectId:null,sessionId:null}),view=render(<Workflows {...callbacks}/>);
  await screen.findByRole('button',{name:definition.name+' '+definition.description});
  fireEvent.change(screen.getByRole('combobox',{name:'Filter workflows by project'}),{target:{value:'p1'}});
  fireEvent.click(screen.getByRole('button',{name:'New workflow'}));
  fireEvent.change(within(await screen.findByRole('dialog',{name:'New workflow'})).getByLabelText('Name'),{target:{value:'Keep this draft'}});
  view.rerender(<Workflows {...callbacks} projectId="p1" sessionId="owned-child" visible={false}/>);
  view.rerender(<Workflows {...callbacks} projectId="p1" sessionId="owned-child"/>);
  expect(within(await screen.findByRole('dialog',{name:'New workflow'})).getByLabelText('Name')).toHaveValue('Keep this draft');
  expect(writes()).toHaveLength(0);
 });
});


describe('optional manual workflow limits',()=>{
 it('ignores legacy definition quotas and starts with every manual bound off',async()=>{
  mocked.mockImplementation(async(address,init)=>address===path+'/runs'&&init?.method==='POST'?detail as never:base(address,init) as never);
  render(<Workflows {...props()}/>);const dialog=await inputs();
  fireEvent.click(within(dialog).getByText('Limits'));
  const toggles=within(dialog).getAllByRole('checkbox');expect(toggles).toHaveLength(3);for(const toggle of toggles)expect(toggle).not.toBeChecked();
  expect(within(dialog).queryByRole('spinbutton')).not.toBeInTheDocument();
  fireEvent.click(within(dialog).getByRole('button',{name:'Start'}));await waitFor(()=>expect(writes()).toHaveLength(1));
  expect(JSON.parse(String(writes()[0][1]?.body)).limits).toEqual({max_agents:null,concurrency:null,timeout_s:null});
 });
 it('applies only explicitly enabled manual limits without the old numeric ceilings',async()=>{
  mocked.mockImplementation(async(address,init)=>address===path+'/runs'&&init?.method==='POST'?detail as never:base(address,init) as never);
  render(<Workflows {...props()}/>);const dialog=await inputs();fireEvent.click(within(dialog).getByText('Limits'));
  for(const [toggle,label,value] of [['Limit agent calls','Agent calls limit','1000'],['Limit parallel agents','Parallel agents limit','24'],['Limit seconds','Seconds limit','7200']]){
   fireEvent.click(within(dialog).getByRole('checkbox',{name:toggle}));const input=within(dialog).getByRole('spinbutton',{name:label});expect(input).not.toHaveAttribute('max');expect(input).toHaveValue(null);fireEvent.change(input,{target:{value}});
  }
  fireEvent.click(within(dialog).getByRole('button',{name:'Start'}));await waitFor(()=>expect(writes()).toHaveLength(1));
  expect(JSON.parse(String(writes()[0][1]?.body)).limits).toEqual({max_agents:1000,concurrency:24,timeout_s:7200});
 });
 it('requires a value for an enabled bound and clears the bound when switched off',async()=>{
  mocked.mockImplementation(async(address,init)=>address===path+'/runs'&&init?.method==='POST'?detail as never:base(address,init) as never);
  render(<Workflows {...props()}/>);const dialog=await inputs();fireEvent.click(within(dialog).getByText('Limits'));
  const toggle=within(dialog).getByRole('checkbox',{name:'Limit seconds'});fireEvent.click(toggle);
  fireEvent.submit(within(dialog).getByRole('button',{name:'Start'}).closest('form')!);expect(await within(dialog).findByRole('alert')).toHaveTextContent('positive whole number');expect(writes()).toHaveLength(0);
  fireEvent.change(within(dialog).getByRole('spinbutton',{name:'Seconds limit'}),{target:{value:'7200'}});fireEvent.click(toggle);
  fireEvent.click(within(dialog).getByRole('button',{name:'Start'}));await waitFor(()=>expect(writes()).toHaveLength(1));
  expect(JSON.parse(String(writes()[0][1]?.body)).limits).toEqual({max_agents:null,concurrency:null,timeout_s:null});
 });
});
