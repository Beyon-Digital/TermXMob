import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import {request} from '../lib/api';
import Managers from './Managers';
import WebSearchSettings, {type WebSearchStatus} from './WebSearchSettings';
import {SettingsFormsContext} from './settings-forms';
import SettingsActions from './SettingsActions';

vi.mock('../lib/api', () => ({request: vi.fn(), json: (method: string, value?: unknown) => ({method, body: JSON.stringify(value)})}));
const mocked = vi.mocked(request);
afterEach(cleanup);
beforeEach(() => {
  mocked.mockReset();
});

const KEY = 'BSA-synthetic-write-only';
const none: WebSearchStatus = {available: false, provider: 'brave', source: null, store_available: true, custom_endpoint: false, error: null};
const stored: WebSearchStatus = {...none, available: true, source: 'credential_store'};

function backend(initial: WebSearchStatus, options: {failSet?: string} = {}) {
  let status = initial;
  const calls: string[] = [];
  mocked.mockImplementation(async (_path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body || '{}')) as {query: string; variables?: {key?: string}};
    calls.push(body.query);
    if (body.query.includes('set_web_search_key')) {
      if (options.failSet) throw new Error(options.failSet);
      status = {...status, available: true, source: 'credential_store'};
      return {data: {set_web_search_key: status}} as never;
    }
    if (body.query.includes('clear_web_search_key')) {
      status = {...status, available: false, source: null};
      return {data: {clear_web_search_key: status}} as never;
    }
    return {data: {web_search_status: status}} as never;
  });
  return calls;
}

it('explains approvals per website and shows that search is not set up', async () => {
  backend(none);
  const {container} = render(<WebSearchSettings admin />);
  expect(await screen.findByText('Not available. Add a key to turn it on')).toBeInTheDocument();
  expect(screen.getByText(/ask before they search the web or open a website/)).toBeInTheDocument();
  expect(screen.getByText(/approve each website once/)).toBeInTheDocument();
  expect(screen.getByText('Brave Search', {selector: 'dd'})).toBeInTheDocument();
  expect(screen.getByLabelText('Brave Search API key')).toHaveAttribute('type', 'password');
  await expectNoAxeViolations(container);
});

it('saves the key, never shows it again and offers Replace and Remove', async () => {
  const calls = backend(none);
  const {container} = render(<WebSearchSettings admin />);
  const input = await screen.findByLabelText('Brave Search API key');
  fireEvent.click(screen.getByRole('button', {name: 'Show key'}));
  expect(input).toHaveAttribute('type', 'text');
  fireEvent.change(input, {target: {value: ` ${KEY} `}});
  fireEvent.submit(screen.getByRole('form', {name: 'Web search key'}));
  expect(await screen.findByText('Key saved')).toBeInTheDocument();
  expect(calls.filter(call => call.includes('set_web_search_key'))).toHaveLength(1);
  const sent = JSON.parse(String(mocked.mock.calls.find(([, init]) => String(init?.body).includes('set_web_search_key'))?.[1]?.body));
  expect(sent.variables).toEqual({key: KEY});
  expect(container.innerHTML).not.toContain(KEY);
  expect(screen.getByText('Available to agents')).toBeInTheDocument();
  expect(screen.getByText('Saved in this computer’s credential store')).toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Replace key'})).toBeInTheDocument();
  await expectNoAxeViolations(container);
});

it('names the problem beside the field for an empty key, a key with spaces and a host refusal', async () => {
  backend(none, {failSet: 'Secure credential storage is unavailable. Set TERMX_AI_WEB_SEARCH_API_KEY on the host instead.'});
  render(<WebSearchSettings admin />);
  const input = await screen.findByLabelText('Brave Search API key');
  const form = screen.getByRole('form', {name: 'Web search key'});
  fireEvent.submit(form);
  expect(input).toHaveAccessibleDescription(expect.stringContaining('Enter your Brave Search API key.'));
  fireEvent.change(input, {target: {value: 'two words'}});
  fireEvent.submit(form);
  expect(input).toHaveAccessibleDescription(expect.stringContaining('cannot contain spaces'));
  fireEvent.change(input, {target: {value: KEY}});
  fireEvent.submit(form);
  expect(await screen.findByText(/Secure credential storage is unavailable/)).toHaveAttribute('role', 'alert');
  expect(input).toHaveValue(KEY);
});

it('replaces a saved key and can keep it instead', async () => {
  const calls = backend(stored);
  render(<WebSearchSettings admin />);
  fireEvent.click(await screen.findByRole('button', {name: 'Replace key'}));
  expect(screen.getByLabelText('New Brave Search API key')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', {name: 'Keep saved key'}));
  expect(screen.getByText('Key saved')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Replace key'}));
  fireEvent.change(screen.getByLabelText('New Brave Search API key'), {target: {value: KEY}});
  fireEvent.submit(screen.getByRole('form', {name: 'Web search key'}));
  await waitFor(() => expect(calls.filter(call => call.includes('set_web_search_key'))).toHaveLength(1));
  await waitFor(() => expect(screen.getByText('Key saved')).toBeInTheDocument());
  expect(screen.queryByLabelText('New Brave Search API key')).not.toBeInTheDocument();
});

it('removes the key after the settings review dialog confirms it', async () => {
  const calls = backend(stored);
  render(<SettingsActions><WebSearchSettings admin /></SettingsActions>);
  fireEvent.click(await screen.findByRole('button', {name: 'Remove key'}));
  expect(calls.some(call => call.includes('clear_web_search_key'))).toBe(false);
  fireEvent.click(await screen.findByRole('button', {name: 'Confirm remove key'}));
  expect(await screen.findByText('Not available. Add a key to turn it on')).toBeInTheDocument();
  expect(calls.filter(call => call.includes('clear_web_search_key'))).toHaveLength(1);
  expect(screen.getByLabelText('Brave Search API key')).toHaveAttribute('type', 'password');
});

it('reports a key set on the host environment without offering a form for it', async () => {
  backend({...stored, source: 'environment'});
  render(<WebSearchSettings admin />);
  expect(await screen.findByText('Set on the host with TERMX_AI_WEB_SEARCH_API_KEY')).toBeInTheDocument();
  expect(screen.queryByRole('form', {name: 'Web search key'})).not.toBeInTheDocument();
  expect(screen.getByText(/Unset TERMX_AI_WEB_SEARCH_API_KEY on the host/)).toBeInTheDocument();
});

it('points to the host environment when secure credential storage is unavailable', async () => {
  backend({...none, store_available: false});
  render(<WebSearchSettings admin />);
  expect(await screen.findByText(/Secure credential storage is unavailable on this computer/)).toBeInTheDocument();
  expect(screen.queryByRole('form', {name: 'Web search key'})).not.toBeInTheDocument();
});

it('says why it is read-only and asks the host for nothing when the person is not an administrator', () => {
  backend(none);
  render(<WebSearchSettings admin={false} />);
  expect(screen.getByRole('status')).toHaveTextContent('Only a host administrator can change web search');
  expect(mocked).not.toHaveBeenCalled();
});

it('shows the host error with a retry when the status cannot be read', async () => {
  mocked.mockRejectedValueOnce(new Error('Host status is unavailable')).mockResolvedValue({data: {web_search_status: none}} as never);
  render(<WebSearchSettings admin />);
  expect(await screen.findByText(/Host status is unavailable/)).toHaveAttribute('role', 'alert');
  fireEvent.click(screen.getByRole('button', {name: 'Retry'}));
  expect(await screen.findByLabelText('Brave Search API key')).toBeInTheDocument();
});

it('reports unsaved key edits to the settings footer and clears them after saving', async () => {
  backend(none);
  const reports: Array<[string, unknown]> = [];
  const registry = {report: (key: string, form: unknown) => void reports.push([key, form])};
  render(<SettingsFormsContext.Provider value={registry}><WebSearchSettings admin /></SettingsFormsContext.Provider>);
  fireEvent.change(await screen.findByLabelText('Brave Search API key'), {target: {value: KEY}});
  await waitFor(() => expect(reports.some(([, form]) => form !== null)).toBe(true));
  fireEvent.submit(screen.getByRole('form', {name: 'Web search key'}));
  await screen.findByText('Key saved');
  expect(reports.at(-1)?.[1]).toBeNull();
});

it('appears as a Web search disclosure on Tools and connections and loads only when opened', async () => {
  const calls: string[] = [];
  mocked.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/auth/me') return {principal: {id: 'p', scopes: ['host-admin']}} as never;
    const query = String(JSON.parse(String(init?.body || '{}')).query || '');
    calls.push(query);
    return {data: query.includes('web_search_status') ? {web_search_status: none} : {mcp_connections: [], projects: [], desktop_control_status: null}} as never;
  });
  render(<Managers section="connections" projectId={null} sessionId={null} />);
  const summary = await screen.findByText('Web search', {selector: 'summary'});
  expect(calls.some(call => call.includes('web_search_status'))).toBe(false);
  fireEvent.click(summary);
  const body = (summary.closest('details') as HTMLElement);
  expect(await within(body).findByLabelText('Brave Search API key')).toBeInTheDocument();
});
