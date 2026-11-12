import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import SourceDialog from './SourceDialog';
import {request} from '../../lib/api';
import type {DataConnection, DataEngine} from '../../lib/data-studio';
vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
const sqlite: DataEngine = {id: 'sqlite', label: 'SQLite', status: 'qualified', ready: true, family: 'relational', fields: [{name: 'path', label: 'Database path on host', type: 'file', required: true}], capabilities: {read: true, schema: true, cancel: true, export_csv: true, qualified: true}};
const postgres: DataEngine = {id: 'postgresql', label: 'PostgreSQL', status: 'installed', ready: true, family: 'relational', reason: 'Driver installed; source verification required.', fields: [{name: 'host', label: 'Host', type: 'text', required: true}, {name: 'port', label: 'Port', type: 'number'}, {name: 'database', label: 'Database', type: 'text', required: true}, {name: 'username', label: 'Username', type: 'text', required: true}, {name: 'tls', label: 'Verify TLS', type: 'boolean', default: true}], capabilities: {read: true, schema: true, export_csv: true}};
const source: DataConnection = {id: 'source', revision: 1, name: 'Local database', engine: 'sqlite', created_by: 'owner', config: {path: '/host/business.sqlite'}};
const api = vi.mocked(request), saved = vi.fn(), close = vi.fn();
const dialog = (props: Partial<React.ComponentProps<typeof SourceDialog>> = {}) => <SourceDialog open engines={[sqlite, postgres]} workspaceId="workspace" ownerId="owner" onClose={close} onSaved={saved} {...props}/>;
beforeEach(() => {vi.clearAllMocks(); localStorage.clear(); api.mockImplementation(async path => {throw Error('Unexpected request: ' + path);});});
afterEach(cleanup);

it('selects an actual SQLite file from the host picker before saving a source', async () => {
  api.mockImplementation(async (path, init) => {
    if (path.startsWith('/api/workspace/paths?')) return {path: '/host', parent: null, home: '/host', breadcrumbs: [], entries: [{name: 'orders.sqlite', path: '/host/orders.sqlite', dir: false}], next_offset: null, total: 1};
    if (path === '/api/data/workspaces/workspace/connections' && init?.method === 'POST') return {...source, id: 'new', name: 'Orders'};
    throw Error(path);
  });
  render(dialog());
  const form = screen.getByRole('dialog', {name: 'Connect database'});
  fireEvent.change(within(form).getByLabelText('Connection name'), {target: {value: 'Orders'}});
  expect(within(form).getByRole('button', {name: 'Save connection'})).toBeDisabled();
  fireEvent.click(within(form).getByRole('button', {name: 'Browse files'}));
  const picker = await screen.findByRole('dialog', {name: 'Choose file'});
  fireEvent.click(await within(picker).findByRole('button', {name: 'Select file orders.sqlite'}));
  fireEvent.click(within(picker).getByRole('button', {name: 'Choose file'}));
  await waitFor(() => expect(within(form).getByRole('button', {name: 'Save connection'})).toBeEnabled());
  fireEvent.click(within(form).getByRole('button', {name: 'Save connection'}));
  await waitFor(() => expect(saved).toHaveBeenCalledWith(expect.objectContaining({id: 'new'})));
  expect(api).toHaveBeenCalledWith('/api/data/workspaces/workspace/connections', expect.objectContaining({method: 'POST', body: JSON.stringify({name: 'Orders', engine: 'sqlite', config: {path: '/host/orders.sqlite'}, password: '', shared_read: false})}), false);
});

it('edits a profile with an empty password so the server preserves its saved credential', async () => {
  const network: DataConnection = {...source, engine: 'postgresql', config: {host: 'db.example.test', port: 5432, database: 'orders', username: 'reader', tls: true}, password_configured: true};
  api.mockResolvedValue({...network, revision: 2});
  render(dialog({record: network}));
  const form = screen.getByRole('dialog', {name: 'Edit connection'});
  expect(within(form).getByLabelText('Password')).toHaveValue('');
  fireEvent.change(within(form).getByLabelText('Connection name'), {target: {value: 'Warehouse'}});
  fireEvent.click(within(form).getByRole('button', {name: 'Save connection'}));
  await waitFor(() => expect(saved).toHaveBeenCalled());
  const call = api.mock.calls.find(([, init]) => init?.method === 'PATCH')!;
  expect(JSON.parse(call[1]!.body as string)).toMatchObject({revision: 1, password: '', name: 'Warehouse', config: {host: 'db.example.test', tls: true}});
});

it('exposes unavailable engines truthfully and disables saving', () => {
  render(dialog({engines: [{...sqlite, ready: false, status: 'unsupported', reason: 'SQL parser dependency is not installed.', capabilities: {}}]}));
  expect(screen.getByText(/SQL parser dependency is not installed/)).toBeInTheDocument();
  expect(screen.getByRole('option', {name: /SQLite · unsupported/})).toBeDisabled();
  expect(within(screen.getByRole('dialog')).getByRole('button', {name: 'Save connection'})).toBeDisabled();
});

it('shows a save failure and keeps the dialog open', async () => {
  api.mockRejectedValue(new Error('Name already used'));
  render(dialog({engines: [postgres]}));
  const form = screen.getByRole('dialog');
  fireEvent.change(within(form).getByLabelText('Connection name'), {target: {value: 'Warehouse'}});
  fireEvent.change(within(form).getByLabelText('Host'), {target: {value: 'db'}});
  fireEvent.change(within(form).getByLabelText('Database'), {target: {value: 'orders'}});
  fireEvent.change(within(form).getByLabelText('Username'), {target: {value: 'reader'}});
  fireEvent.click(within(form).getByRole('button', {name: 'Save connection'}));
  expect(await within(form).findByRole('alert')).toHaveTextContent('Name already used');
  expect(saved).not.toHaveBeenCalled();
  expect(close).not.toHaveBeenCalled();
});
