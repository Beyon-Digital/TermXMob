import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import DataStudio, {advancedKey} from './DataStudio';
import {request} from '../../lib/api';
import {expectNoAxeViolations} from '../../test-axe';
import {sqlValue, typeSql} from '../../test-utils/codemirror';
import {sizeGridScrollers} from '../../test-utils/virtual';
import type {DataBootstrap, DataRole, DataSnapshot} from '../../lib/data-studio';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
vi.mock('../../lib/transfers', () => ({transfer: vi.fn()}));
vi.mock('./BIStudio', () => ({default: () => <section aria-label="BI reports"/>}));
vi.mock('./DataCopilot', () => ({default: ({onClose}: {onClose: () => void}) => <section aria-label="Copilot conversation"><button onClick={onClose}>Close Copilot</button></section>}));

const engine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, fields: [], capabilities: {read: true, schema: true, cancel: true, export_csv: true}};
const api = vi.mocked(request);
let role: DataRole, runs: unknown[];
const snapshot = (): DataSnapshot => ({
  workspace: {id: 'w', revision: 1, name: 'Analytics', role}, members: [],
  connections: [{id: 'source', revision: 1, name: 'Orders source', engine: 'sqlite', config: {path: '/host/orders.sqlite'}, created_by: 'owner'}],
  queries: [{id: 'saved', revision: 2, name: 'Open orders', connection_id: 'source', sql: 'SELECT id, total FROM orders', parameters: {}}], reports: [], dashboards: [], activity: [],
});
beforeEach(() => {
  vi.clearAllMocks(); sizeGridScrollers(); localStorage.clear(); runs = []; role = 'owner';
  window.history.replaceState({}, '', '/?workspace=data');
  api.mockImplementation(async (path, init) => {
    if (path === '/api/data/bootstrap') return {workspaces: [{id: 'w', revision: 1, name: 'Analytics', role}], engines: [engine], capabilities: {}} satisfies DataBootstrap;
    if (path === '/api/data/workspaces/w') return snapshot();
    if (path.endsWith('/query-runs')) {
      runs.push(JSON.parse(String(init?.body)));
      return {id: 'job', revision: 1, status: 'completed', connection_id: 'source', result: {columns: [{name: 'id', type: 'integer'}, {name: 'total', type: 'real'}], rows: [[1, 12.5], [2, null]], row_count: 2, truncated: false}};
    }
    throw new Error('Unexpected request: ' + path);
  });
});
afterEach(() => {cleanup(); vi.restoreAllMocks();});

const ready = () => screen.findByRole('heading', {name: /source$/});
async function openAdvanced(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', {name: 'Advanced'}));
  await user.click(await screen.findByRole('menuitemcheckbox', {name: 'SQL editor'}));
}

it('keeps the SQL editor off until it is chosen from the Advanced menu', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  expect(screen.queryByLabelText('SQL query')).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog', {name: 'SQL editor'})).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Advanced'}));
  expect(await screen.findByRole('menuitemcheckbox', {name: 'SQL editor'})).toHaveAttribute('aria-checked', 'false');
  await user.keyboard('{Escape}');
  expect(screen.getByRole('button', {name: 'Advanced'})).toHaveFocus();
});

it('opens a floating, non-modal panel that closes with Escape or its button and is remembered', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await openAdvanced(user);
  const panel = await screen.findByRole('dialog', {name: 'SQL editor'});
  expect(panel).not.toHaveAttribute('aria-modal', 'true');
  expect(await screen.findByLabelText('SQL query')).toHaveAttribute('id', 'data-sql');
  expect(localStorage.getItem(advancedKey('owner'))).toBe('true');
  // The page behind stays usable: the Copilot toggle works while the panel is open.
  await user.click(screen.getByRole('button', {name: 'Copilot'}));
  expect(screen.getByRole('complementary', {name: 'Data Copilot'})).toBeInTheDocument();
  await user.click(within(panel).getByRole('button', {name: 'Close SQL editor'}));
  await waitFor(() => expect(screen.queryByRole('dialog', {name: 'SQL editor'})).not.toBeInTheDocument());
  expect(localStorage.getItem(advancedKey('owner'))).toBe('false');
  await openAdvanced(user);
  await screen.findByRole('dialog', {name: 'SQL editor'});
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog', {name: 'SQL editor'})).not.toBeInTheDocument());
});

it('runs SQL and shows the rows in the same grid as the table view', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await openAdvanced(user);
  await typeSql('SQL query', 'SELECT id, total FROM orders');
  await waitFor(() => expect(screen.getByRole('button', {name: 'Run'})).toBeEnabled());
  await user.click(screen.getByRole('button', {name: 'Run'}));
  expect(await screen.findByRole('cell', {name: '12.5'})).toBeInTheDocument();
  expect(screen.getByRole('table', {name: 'Result table'})).toHaveAttribute('aria-rowcount', '3');
  expect(screen.getByText('empty')).toBeInTheDocument();
  expect(runs[0]).toMatchObject({connection_id: 'source', sql: 'SELECT id, total FROM orders'});
  expect(screen.getByRole('columnheader', {name: 'total'})).toBeInTheDocument();
});

it('opens a saved query from the sidebar into the panel and keeps its text', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await user.click(screen.getByRole('button', {name: 'Open orders'}));
  expect(await screen.findByRole('heading', {level: 1, name: 'Open orders'})).toBeInTheDocument();
  await waitFor(async () => expect(await sqlValue('SQL query')).toBe('SELECT id, total FROM orders'));
  expect(screen.getByRole('button', {name: 'Save query'})).toBeInTheDocument();
});

it('lets the person return to the editor from an empty query tab', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await openAdvanced(user);
  await screen.findByRole('dialog', {name: 'SQL editor'});
  await user.click(screen.getByRole('button', {name: 'Close SQL editor'}));
  await waitFor(() => expect(screen.queryByRole('dialog', {name: 'SQL editor'})).not.toBeInTheDocument());
  expect(screen.getByText('Run a read-only query to see results.')).toBeInTheDocument();
  await user.click(screen.getByRole('button', {name: 'Open SQL editor'}));
  expect(await screen.findByRole('dialog', {name: 'SQL editor'})).toBeInTheDocument();
});

it('does not offer the SQL editor to viewers', async () => {
  role = 'viewer';
  render(<DataStudio ownerId="owner" visible/>);
  await screen.findByRole('tab', {name: 'Overview'});
  expect(screen.queryByRole('button', {name: 'Advanced'})).not.toBeInTheDocument();
  expect(screen.queryByLabelText('SQL query')).not.toBeInTheDocument();
});

it('has no accessibility violations with the panel open', async () => {
  const user = userEvent.setup();
  render(<DataStudio ownerId="owner" visible/>);
  await ready();
  await openAdvanced(user);
  const panel = await screen.findByRole('dialog', {name: 'SQL editor'});
  await screen.findByLabelText('SQL query');
  await expectNoAxeViolations(panel);
  fireEvent.keyDown(panel, {key: 'Escape'});
});
