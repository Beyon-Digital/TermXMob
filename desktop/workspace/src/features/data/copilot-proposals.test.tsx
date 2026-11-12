import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {dataRequest} from '../../lib/data-studio';
import {asSegmentSpec, isSpecProposal, proposalChips, removeChip, type SpecProposal} from '../../lib/copilot-proposals';
import type {QuerySpec} from '../../lib/query-spec';
import {expectNoAxeViolations} from '../../test-axe';
import DataCopilot from './DataCopilot';
import CopilotPanel from './CopilotPanel';

vi.mock('../../lib/data-studio', async original => ({...await original<typeof import('../../lib/data-studio')>(), dataRequest: vi.fn()}));
const request = vi.mocked(dataRequest);

const col = (column: string) => ({table_alias: 's', column});
const base: QuerySpec = {version: 1, source: 'c', from: {table: 'sales', alias: 's'}, joins: [], select: [col('region'), col('amount')],
  filters: {op: 'and', items: [{field: col('region'), op: 'eq', value: 'East'}, {field: col('amount'), op: 'gt', value: 5}]},
  group_by: [], aggregates: [], sort: [], limit: 50, offset: 0};
const pivotSpec: QuerySpec = {...base, select: [], pivot: {rows: [col('region')], columns: col('channel'), value: {fn: 'sum', table_alias: 's', column: 'amount'}, totals: true, sort: {by: 'rows', direction: 'asc'}}};
const proposal = (type: SpecProposal['type'], spec: QuerySpec, title = 'Suggestion'): SpecProposal => ({type, title, definition: {kind: type, name: title, description: '', connection_id: 'c', spec}});
const copilot = {settings: {revision: 1, configured: true, provider_id: 'p', model: 'm', share_with_team: false, can_configure: true}, providers: [{id: 'p', name: 'Account', model: 'm', models: ['m']}], runs: [] as unknown[]};
const run = (proposals: unknown[], extra = {}) => ({id: 'r', status: 'completed', message: 'Show East sales', answer: 'Here is a suggestion.', events: [], proposals, ...extra});

beforeEach(() => request.mockReset());
afterEach(() => {cleanup(); window.localStorage.clear();});

describe('proposal chips', () => {
  it('describes a table in plain words and removes one chip at a time', () => {
    const chips = proposalChips('table', base);
    expect(chips.map(chip => chip.text)).toEqual(['Sales', 'Region', 'Amount', 'Region is East', 'Amount is greater than 5', 'First 50']);
    expect(removeChip(base, 'col:0').select).toEqual([col('amount')]);
    expect(removeChip(base, 'filter:1').filters.items).toHaveLength(1);
    expect(proposalChips('table', removeChip(removeChip(base, 'col:0'), 'col:0')).filter(chip => chip.group === 'Columns' && chip.removable)).toEqual([]);
  });
  it('keeps the last filter of a segment and the totals a pivot needs', () => {
    const one = removeChip(base, 'filter:0');
    expect(proposalChips('segment', one).find(chip => chip.id === 'filter:0')?.removable).toBe(false);
    expect(proposalChips('pivot', pivotSpec).find(chip => chip.id === 'pivot-columns')?.removable).toBe(true);
    expect(removeChip(pivotSpec, 'pivot-columns').pivot?.columns).toBeNull();
    expect(proposalChips('pivot', {...pivotSpec, pivot: {...pivotSpec.pivot!, totals: false}}).find(chip => chip.id === 'pivot-columns')?.removable).toBe(false);
  });
  it('turns a proposal into a filter-only segment and rejects malformed shapes', () => {
    const segment = asSegmentSpec(pivotSpec);
    expect(segment).toMatchObject({select: [], sort: [], aggregates: [], limit: 200});
    expect('pivot' in segment).toBe(false);
    expect(isSpecProposal(proposal('table', base))).toBe(true);
    expect(isSpecProposal({type: 'table', title: 'x', definition: {connection_id: 'c', spec: {version: 2}}})).toBe(false);
    expect(isSpecProposal({type: 'pivot', title: 'x', definition: {connection_id: 'c', spec: base}})).toBe(false);
    expect(isSpecProposal({type: 'query', title: 'x', definition: {}})).toBe(false);
  });
});

describe('Copilot proposal cards', () => {
  function open(proposals: unknown[], props = {}) {
    request.mockResolvedValue({...copilot, runs: [run(proposals)]});
    const handlers = {onApplyTable: vi.fn(), onOpenPivot: vi.fn(), onSaveSegment: vi.fn().mockResolvedValue({id: 'v'})};
    const view = render(<DataCopilot workspaceId="w" visible role="editor" connectionId="c" onClose={() => {}} {...handlers} {...props}/>);
    return {...handlers, ...view};
  }
  it('applies an edited table to a new table tab, once the person chooses it', async () => {
    const user = userEvent.setup(), {onApplyTable} = open([proposal('table', base, 'East sales')]);
    await screen.findByRole('heading', {name: 'East sales'});
    expect(onApplyTable).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', {name: 'Remove Amount is greater than 5'}));
    await user.clear(screen.getByLabelText('Suggestion name'));
    await user.type(screen.getByLabelText('Suggestion name'), 'My East sales');
    await user.click(screen.getByRole('button', {name: 'Apply to table'}));
    expect(onApplyTable).toHaveBeenCalledOnce();
    const [spec, title] = onApplyTable.mock.calls[0];
    expect(title).toBe('My East sales');
    expect(spec.filters.items).toHaveLength(1);
    expect(spec.select).toEqual(base.select);
    expect(request).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('button', {name: 'Undo changes'}));
    expect(screen.getByText('Amount is greater than 5')).toBeInTheDocument();
  });
  it('opens a pivot proposal as a pivot', async () => {
    const user = userEvent.setup(), {onOpenPivot} = open([proposal('pivot', pivotSpec, 'Sales by region')]);
    await user.click(await screen.findByRole('button', {name: 'Open as pivot'}));
    expect(onOpenPivot).toHaveBeenCalledWith(pivotSpec, 'Sales by region');
    expect(screen.queryByRole('button', {name: 'Apply to table'})).not.toBeInTheDocument();
  });
  it('saves a segment only through the naming dialog and the person\'s own click', async () => {
    const user = userEvent.setup(), {onSaveSegment} = open([proposal('segment', {...base, select: []}, 'East only')]);
    await user.click(await screen.findByRole('button', {name: 'Save as segment'}));
    expect(onSaveSegment).not.toHaveBeenCalled();
    await user.click(await screen.findByRole('button', {name: 'Save segment'}));
    await waitFor(() => expect(onSaveSegment).toHaveBeenCalledOnce());
    expect(onSaveSegment.mock.calls[0][1]).toBe('East only');
    expect(onSaveSegment.mock.calls[0][0]).toMatchObject({select: [], limit: 200});
    expect(await screen.findByText('Segment saved.')).toBeInTheDocument();
  });
  it('shows a failed segment save inside the dialog and hides saving from viewers', async () => {
    const user = userEvent.setup(), {onSaveSegment, unmount} = open([proposal('segment', {...base, select: []})]);
    onSaveSegment.mockRejectedValue(new Error('Could not save'));
    await user.click(await screen.findByRole('button', {name: 'Save as segment'}));
    await user.click(screen.getByRole('button', {name: 'Save segment'}));
    expect(await screen.findByRole('alert')).toHaveTextContent(/save/i);
    unmount();
    open([proposal('segment', {...base, select: []})], {role: 'viewer'});
    await screen.findByRole('heading', {name: 'Suggestion'});
    expect(screen.queryByRole('button', {name: 'Save as segment'})).not.toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Apply to table'})).toBeInTheDocument();
  });
  it('keeps written queries under Advanced and ignores malformed proposals', async () => {
    const user = userEvent.setup(), onOpenQuery = vi.fn();
    open([{type: 'table', title: 'Broken', definition: {spec: {}}}, {type: 'query', title: 'By region', definition: {connection_id: 'c', sql: 'select 1'}}], {onOpenQuery});
    await screen.findByText('Here is a suggestion.');
    expect(screen.queryByText('Broken')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Open query'})).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: /Advanced/}));
    await user.click(screen.getByRole('button', {name: 'Open query'}));
    expect(onOpenQuery).toHaveBeenCalledWith({connection_id: 'c', sql: 'select 1', name: 'By region'});
  });
  it('has no accessibility violations with proposals, consent switches and the activity list', async () => {
    const view = open([proposal('table', base, 'East sales'), proposal('pivot', pivotSpec, 'Pivot'), proposal('segment', {...base, select: []}, 'Segment')]);
    await screen.findByRole('heading', {name: 'East sales'});
    await expectNoAxeViolations(view.container);
  });
});

describe('Copilot consent, stop and states', () => {
  it('keeps both consent switches off and disabled without a source, and sends only what is on', async () => {
    const user = userEvent.setup();
    request.mockResolvedValue(copilot);
    const {unmount} = render(<DataCopilot workspaceId="w" visible role="editor" onClose={() => {}}/>);
    await screen.findByText('What would you like to explore?');
    expect(screen.getByRole('switch', {name: 'Table and column names'})).toBeDisabled();
    unmount();
    render(<DataCopilot workspaceId="w" visible role="editor" connectionId="c" queryId="q" onClose={() => {}}/>);
    await screen.findByText('What would you like to explore?');
    await user.click(screen.getByRole('switch', {name: 'Rows from the open query'}));
    expect(screen.getByRole('switch', {name: 'Rows from the open query'})).toHaveAttribute('aria-checked', 'true');
    await user.type(screen.getByLabelText('Message Copilot'), 'Hi');
    request.mockResolvedValue(run([]));
    await user.click(screen.getByRole('button', {name: 'Send message'}));
    await screen.findByText('Here is a suggestion.');
    const body = JSON.parse(String(request.mock.calls.find(([path]) => path.endsWith('/messages'))?.[1]?.body));
    expect(body).toMatchObject({include_schema: false, include_results: true});
  });
  it('shows a stop button while a run is active, then the stopped state', async () => {
    const user = userEvent.setup();
    request.mockResolvedValue({...copilot, runs: [run([], {status: 'running', answer: undefined})]});
    render(<DataCopilot workspaceId="w" visible onClose={() => {}}/>);
    expect(await screen.findByRole('button', {name: 'Stop Copilot'})).toBeEnabled();
    expect(screen.getByLabelText('Message Copilot')).toBeDisabled();
    request.mockResolvedValue(run([], {status: 'cancelled', answer: undefined}));
    await user.click(screen.getByRole('button', {name: 'Stop Copilot'}));
    expect(await screen.findByText('Stopped.')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Send message'})).toBeInTheDocument();
  });
  it('shows a loading state, then an error with a way to try again', async () => {
    request.mockResolvedValue(undefined);
    request.mockImplementationOnce(() => Promise.reject(new Error('Copilot is unavailable')));
    render(<DataCopilot workspaceId="w" visible onClose={() => {}}/>);
    expect(screen.getByRole('status')).toHaveTextContent('Opening Copilot');
    expect(await screen.findByText('Copilot is unavailable')).toBeInTheDocument();
    expect(screen.getByRole('button', {name: 'Try again'})).toBeInTheDocument();
  });
  it('offers setup to an owner and explains it to everyone else', async () => {
    request.mockResolvedValue({...copilot, settings: {...copilot.settings, configured: false}});
    const {unmount} = render(<DataCopilot workspaceId="w" visible role="owner" onClose={() => {}}/>);
    expect(await screen.findByRole('button', {name: 'Set up Copilot'})).toBeInTheDocument();
    unmount();
    request.mockResolvedValue({...copilot, settings: {...copilot.settings, configured: false, can_configure: false}});
    render(<DataCopilot workspaceId="w" visible role="editor" onClose={() => {}}/>);
    expect(await screen.findByText(/Ask a workspace owner/)).toBeInTheDocument();
    expect(screen.queryByRole('button', {name: 'Set up Copilot'})).not.toBeInTheDocument();
  });
});

describe('Copilot panel resizing', () => {
  const panel = () => screen.getByRole('separator', {name: 'Resize Copilot'});
  it('resizes with the arrow keys, Home, End and Enter, and remembers the width', () => {
    render(<CopilotPanel><p>Body</p></CopilotPanel>);
    expect(screen.getByRole('complementary', {name: 'Data Copilot'})).toBeInTheDocument();
    expect(panel()).toHaveAttribute('aria-valuenow', '400');
    fireEvent.keyDown(panel(), {key: 'ArrowLeft'});
    expect(panel()).toHaveAttribute('aria-valuenow', '416');
    fireEvent.keyDown(panel(), {key: 'ArrowRight', shiftKey: true});
    expect(panel()).toHaveAttribute('aria-valuenow', '352');
    fireEvent.keyDown(panel(), {key: 'End'});
    expect(panel()).toHaveAttribute('aria-valuenow', '720');
    fireEvent.keyDown(panel(), {key: 'ArrowLeft'});
    expect(panel()).toHaveAttribute('aria-valuenow', '720');
    fireEvent.keyDown(panel(), {key: 'Home'});
    expect(panel()).toHaveAttribute('aria-valuenow', '320');
    expect(window.localStorage.getItem('termx.data.copilot.width')).toBe('320');
    fireEvent.keyDown(panel(), {key: 'Enter'});
    expect(panel()).toHaveAttribute('aria-valuenow', '400');
    expect(screen.getByRole('complementary', {name: 'Data Copilot'}).getAttribute('style')).toContain('--copilot-width: 400px');
  });
  it('restores a saved width and drags with the pointer', () => {
    window.localStorage.setItem('termx.data.copilot.width', '500');
    render(<CopilotPanel><p>Body</p></CopilotPanel>);
    expect(panel()).toHaveAttribute('aria-valuenow', '500');
    // jsdom has no PointerEvent, so mouse events carry the coordinates under the pointer event names.
    const pointer = (type: string, clientX: number) => fireEvent(panel(), new MouseEvent(type, {bubbles: true, clientX}));
    pointer('pointerdown', 800);
    pointer('pointermove', 700);
    expect(panel()).toHaveAttribute('aria-valuenow', '600');
    pointer('pointerup', 100);
    expect(panel()).toHaveAttribute('aria-valuenow', '720');
  });
  it('has no accessibility violations', async () => {
    const {container} = render(<CopilotPanel><p>Body</p></CopilotPanel>);
    await expectNoAxeViolations(container);
  });
});
