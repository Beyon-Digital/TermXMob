import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import {request} from '../lib/api';
import Managers from './Managers';

vi.mock('../lib/api', () => ({request: vi.fn(), json: (method: string, value?: unknown) => ({method, body: JSON.stringify(value)})}));
const mocked = vi.mocked(request);
afterEach(cleanup);

const claude = {
  id: 'claude', kind: 'anthropic', name: 'Claude', base_url: 'https://api.anthropic.com', model: 'claude-opus-5-5\nclaude-haiku-5-5',
  models: ['claude-opus-5-5', 'claude-haiku-5-5'], capabilities: ['shell', 'functions'], secret_configured: true, wire: 'messages',
  model_catalog: {'claude-opus-5-5': {context_window: 1_000_000, max_output: 64000, capabilities: ['tools', 'reasoning'], source: 'anthropic'}},
  model_overrides: {}, catalog_refreshed_at: null, catalog_error: null, limits: {},
};
const gptAccount = {...claude, id: 'oa', kind: 'openai', name: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt', models: ['gpt'], wire: 'responses', model_catalog: {}};

function host(options: {providers?: unknown[]; scopes?: string[]; testReply?: unknown} = {}) {
  const {providers = [claude], scopes = ['host-admin'], testReply = {status: 'connected', message: 'Connected. 4 models available.'}} = options;
  const calls: {query: string; variables: Record<string, unknown>}[] = [];
  mocked.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/auth/me') return {principal: {id: 'p', scopes}} as never;
    const body = JSON.parse(String(init?.body || '{}'));
    calls.push({query: body.query, variables: body.variables});
    if (body.query?.includes('test_agent_provider')) return {data: {test_agent_provider: testReply}} as never;
    if (body.query?.includes('save_agent_provider')) return {data: {save_agent_provider: {id: 'claude'}}} as never;
    return {data: {engines: [], agent_providers: providers, acp_registry: [], model_routing: {}}} as never;
  });
  return calls;
}

beforeEach(() => {
  mocked.mockReset();
});

it('lists an Anthropic account by name, offers Edit account and shows Key saved without the key', async () => {
  host();
  const {container} = render(<Managers section="models" projectId={null} sessionId={null} />);
  expect(await screen.findByText(/Anthropic \(Claude\) · Credential configured/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Edit account'}));
  const form = await screen.findByRole('form', {name: 'Edit provider account'});
  expect(within(form).getByText('Key saved')).toBeInTheDocument();
  expect(within(form).getByRole('button', {name: 'Replace key'})).toBeInTheDocument();
  expect(container.innerHTML).not.toMatch(/sk-ant/);
  expect(within(form).queryByRole('combobox', {name: 'Request format'})).not.toBeInTheDocument();
  await expectNoAxeViolations(container);
});

it('keeps Edit account for OpenAI accounts and hides it for a kind the form cannot edit', async () => {
  host({providers: [gptAccount, {...claude, id: 'chat', kind: 'chatgpt', name: 'ChatGPT plan'}]});
  render(<Managers section="models" projectId={null} sessionId={null} />);
  await screen.findAllByText(/ChatGPT plan/);
  expect(screen.getAllByRole('button', {name: 'Edit account'})).toHaveLength(1);
});

it('confirms the connection test in a dialog and says it only lists models', async () => {
  const calls = host();
  render(<Managers section="models" projectId={null} sessionId={null} />);
  fireEvent.click(await screen.findByRole('button', {name: 'Test connection'}));
  const dialog = await screen.findByRole('dialog', {name: 'Test Claude?'});
  expect(dialog).toHaveTextContent('list the models your key can use');
  expect(dialog).toHaveTextContent('nothing is billed');
  expect(dialog).toHaveTextContent('https://api.anthropic.com');
  expect(dialog).toHaveTextContent('claude-opus-5-5');
  expect(calls.some(call => call.query?.includes('test_agent_provider'))).toBe(false);
  fireEvent.click(within(dialog).getByRole('button', {name: 'Send test request'}));
  await screen.findByText('Provider connection tested');
  const sent = calls.find(call => call.query?.includes('test_agent_provider'));
  expect(sent?.variables).toEqual({id: 'claude'});
  expect(sent?.query).not.toContain('billed');
});

it('shows the failure inline when the test is rejected', async () => {
  host({testReply: {status: 'failed', message: 'The provider rejected the API key'}});
  render(<Managers section="models" projectId={null} sessionId={null} />);
  fireEvent.click(await screen.findByRole('button', {name: 'Test connection'}));
  fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button', {name: 'Send test request'}));
  expect(await screen.findByText('The provider rejected the API key')).toBeInTheDocument();
});

it('adds an Anthropic account from the add form and saves the kind, wire and models', async () => {
  const calls = host({providers: []});
  render(<Managers section="models" projectId={null} sessionId={null} />);
  const form = await screen.findByRole('form', {name: 'Add provider account'});
  fireEvent.change(within(form).getByRole('combobox', {name: 'Provider'}), {target: {value: 'anthropic'}});
  const next = screen.getByRole('form', {name: 'Add provider account'});
  fireEvent.change(within(next).getByLabelText('Display name'), {target: {value: 'Claude'}});
  fireEvent.change(within(next).getByLabelText('API key'), {target: {value: 'sk-ant-synthetic'}});
  fireEvent.click(within(next).getByRole('button', {name: 'Save account'}));
  await waitFor(() => expect(calls.some(call => call.query?.includes('save_agent_provider'))).toBe(true));
  const input = calls.find(call => call.query?.includes('save_agent_provider'))?.variables.input;
  expect(input).toMatchObject({kind: 'anthropic', wire: 'messages', api_key: 'sk-ant-synthetic', base_url: 'https://api.anthropic.com'});
  expect(String((input as {model: string}).model).split('\n')).toEqual(['claude-opus-5-5', 'claude-fable-5-2', 'claude-sonnet-5-5', 'claude-haiku-5-5']);
});

it('offers Anthropic models in the model routing picker with their cost shown as unpriced', async () => {
  host({providers: [claude, gptAccount]});
  render(<Managers section="models" projectId={null} sessionId={null} />);
  await screen.findByText(/Anthropic \(Claude\) · Credential configured/);
  const provider = (await screen.findAllByRole('combobox', {name: /\S+ provider$/i}))[0];
  expect(within(provider).getByRole('option', {name: 'Claude'})).toBeInTheDocument();
  fireEvent.change(provider, {target: {value: 'claude'}});
  const model = screen.getAllByRole('combobox', {name: /\S+ model$/i})[0];
  expect(within(model).getByRole('option', {name: /claude-opus-5-5/})).toBeInTheDocument();
  fireEvent.change(model, {target: {value: 'claude-opus-5-5'}});
  expect(await screen.findByText(/Cost unpriced/)).toBeInTheDocument();
});

it('does not offer Remove account for a ChatGPT account, which the host cannot remove here', async () => {
  host({providers: [gptAccount, {...claude, id: 'chat', kind: 'chatgpt', name: 'ChatGPT plan'}]});
  render(<Managers section="models" projectId={null} sessionId={null} />);
  await screen.findAllByText(/ChatGPT plan/);
  expect(screen.getAllByRole('button', {name: 'Remove account'})).toHaveLength(1);
});

it('shows why a provider or engine is unavailable instead of only a status word', async () => {
  mocked.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/auth/me') return {principal: {id: 'p', scopes: ['host-admin']}} as never;
    JSON.parse(String(init?.body || '{}'));
    return {data: {
      engines: [{id: 'codex', label: 'Codex', installed: true, auth_state: 'unauthenticated', auth_detail: 'Run codex login on this host', error: null}, {id: 'zed', label: 'Zed', installed: false, error: 'zed executable not found'}],
      agent_providers: [{...gptAccount, catalog_error: 'The provider rejected the API key'}], acp_registry: [], model_routing: {},
    }} as never;
  });
  render(<Managers section="models" projectId={null} sessionId={null} />);
  expect(await screen.findByText('Run codex login on this host')).toBeInTheDocument();
  expect(screen.getByText('zed executable not found')).toBeInTheDocument();
  expect(screen.getByText(/Model list failed: The provider rejected the API key/)).toBeInTheDocument();
});

it('refreshing an engine re-probes it before reading its models', async () => {
  const calls = host();
  mocked.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/auth/me') return {principal: {id: 'p', scopes: ['host-admin']}} as never;
    const body = JSON.parse(String(init?.body || '{}'));
    calls.push({query: body.query, variables: body.variables});
    if (body.query?.includes('engine_models')) return {data: {engine_models: ['m1']}} as never;
    return {data: {engines: [{id: 'codex', label: 'Codex', installed: true, auth_state: 'authenticated'}], agent_providers: [], acp_registry: [], model_routing: {}}} as never;
  });
  render(<Managers section="models" projectId={null} sessionId={null} />);
  fireEvent.click(await screen.findByRole('button', {name: 'Refresh models'}));
  await waitFor(() => expect(calls.some(call => call.query?.includes('engine_models'))).toBe(true));
  const queries = calls.map(call => call.query).filter(query => /refresh_engine_catalogue|engine_models/.test(query || ''));
  expect(queries[0]).toContain('refresh_engine_catalogue');
  expect(queries[1]).toContain('engine_models');
});

it('reports a failed model refresh instead of saying it was refreshed', async () => {
  const calls = host({providers: [gptAccount]});
  const base = mocked.getMockImplementation()!;
  mocked.mockImplementation(async (path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body || '{}'));
    if (body.query?.includes('refresh_provider_models')) {
      calls.push({query: body.query, variables: body.variables});
      return {data: {refresh_provider_models: {id: 'oa', model: 'gpt', catalog_error: 'The provider rejected the API key'}}} as never;
    }
    return base(path, init);
  });
  render(<Managers section="models" projectId={null} sessionId={null} />);
  fireEvent.click(await screen.findByRole('button', {name: 'Edit account'}));
  const form = await screen.findByRole('form', {name: 'Edit provider account'});
  fireEvent.click(within(form).getByRole('button', {name: 'Refresh models'}));
  expect(await screen.findByText(/Could not refresh the model list: The provider rejected the API key/)).toBeInTheDocument();
  expect(screen.queryByText('Model catalog refreshed')).not.toBeInTheDocument();
});
