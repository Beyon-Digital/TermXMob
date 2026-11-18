import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import type {ProviderAccount, ProviderAccountInput} from '../lib/host-types';
import {ANTHROPIC_MODELS} from '../lib/provider-kinds';
import ProviderAccountForm from './ProviderAccountForm';
import {SettingsFormsContext} from './settings-forms';

afterEach(cleanup);

const KEY = 'sk-ant-api03-synthetic-write-only';

const saved: ProviderAccount = {
  id: 'claude', name: 'Claude', kind: 'anthropic', base_url: 'https://api.anthropic.com', model: ANTHROPIC_MODELS.join('\n'),
  capabilities: ['shell', 'functions'], wire: 'messages', secret_configured: true,
  model_catalog: {
    'claude-opus-5-5': {context_window: 1_000_000, max_output: 64000, capabilities: ['tools', 'vision', 'reasoning'], source: 'anthropic'},
    'claude-haiku-5-5': {context_window: 200000, max_output: 32000, capabilities: ['tools'], source: 'anthropic'},
  },
  model_overrides: {}, catalog_refreshed_at: null, catalog_error: null,
};

function open(props: Partial<React.ComponentProps<typeof ProviderAccountForm>> = {}) {
  const onSave = vi.fn(async (input: ProviderAccountInput) => {
    void input;
    return {};
  });
  const view = render(<ProviderAccountForm busy={false} onSave={onSave} {...props} />);
  return {...view, onSave};
}

function chooseAnthropic() {
  fireEvent.change(screen.getByRole('combobox', {name: 'Provider'}), {target: {value: 'anthropic'}});
}

it('offers Anthropic (Claude) when adding and swaps to a form with seeded models, a fixed request format and a hidden key', () => {
  const {container} = open();
  expect(within(screen.getByRole('combobox', {name: 'Provider'})).getByRole('option', {name: 'Anthropic (Claude)'})).toBeInTheDocument();
  chooseAnthropic();
  expect(screen.getByRole('combobox', {name: 'Provider'})).toHaveValue('anthropic');
  expect(screen.getByLabelText('API key')).toHaveAttribute('type', 'password');
  expect(screen.queryByRole('combobox', {name: 'Request format'})).not.toBeInTheDocument();
  const rows = screen.getAllByRole('checkbox', {name: /^claude-/});
  expect(rows.map(row => (row as HTMLInputElement).value)).toEqual(ANTHROPIC_MODELS);
  expect(rows.every(row => (row as HTMLInputElement).checked)).toBe(true);
  expect(screen.getByRole('combobox', {name: 'Default model'})).toHaveValue('claude-opus-5-5');
  expect(screen.getByText(/Default/, {selector: '.model-row-default'})).toBeInTheDocument();
  expect(container).not.toHaveTextContent('Chat Completions');
});

it('keeps the display name when the provider type changes and returns to the OpenAI form', () => {
  open();
  fireEvent.change(screen.getByLabelText('Display name'), {target: {value: 'Work account'}});
  chooseAnthropic();
  expect(screen.getByLabelText('Display name')).toHaveValue('Work account');
  fireEvent.change(screen.getByRole('combobox', {name: 'Provider'}), {target: {value: 'openrouter'}});
  expect(screen.getByLabelText('Display name')).toHaveValue('Work account');
  expect(screen.getByRole('combobox', {name: 'Provider'})).toHaveValue('openrouter');
  expect(screen.getByRole('combobox', {name: 'Request format'})).toBeInTheDocument();
});

it('saves an Anthropic account with the messages wire, the default address and the chosen models', async () => {
  const {onSave} = open();
  chooseAnthropic();
  fireEvent.change(screen.getByLabelText('Display name'), {target: {value: 'Claude'}});
  fireEvent.change(screen.getByLabelText('API key'), {target: {value: ` ${KEY} `}});
  fireEvent.click(screen.getByRole('checkbox', {name: /claude-fable-5-2/}));
  fireEvent.change(screen.getByRole('combobox', {name: 'Default model'}), {target: {value: 'claude-sonnet-5-5'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save account'}));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][0]).toMatchObject({
    kind: 'anthropic', wire: 'messages', base_url: 'https://api.anthropic.com', api_key: KEY, capabilities: ['shell', 'functions'],
    model: 'claude-sonnet-5-5\nclaude-opus-5-5\nclaude-haiku-5-5',
  });
  expect(onSave.mock.calls[0][0].id).toMatch(/^anthropic-/);
});

it('names each problem beside its field and recovers without losing what was typed', async () => {
  const {onSave} = open();
  chooseAnthropic();
  fireEvent.click(screen.getByRole('button', {name: 'Save account'}));
  const name = screen.getByLabelText('Display name'), key = screen.getByLabelText('API key');
  expect(name).toHaveAccessibleDescription(expect.stringContaining('Enter a display name.'));
  expect(key).toHaveAccessibleDescription(expect.stringContaining('Enter your Anthropic API key.'));
  fireEvent.change(name, {target: {value: 'Claude'}});
  fireEvent.change(key, {target: {value: 'two words'}});
  fireEvent.click(screen.getByText('Advanced account options'));
  fireEvent.change(screen.getByLabelText('API address (optional)'), {target: {value: 'gateway.example/v1'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save account'}));
  expect(key).toHaveAccessibleDescription(expect.stringContaining('cannot contain spaces'));
  expect(screen.getByLabelText('API address (optional)')).toHaveAccessibleDescription(expect.stringContaining('starts with https://'));
  fireEvent.change(key, {target: {value: KEY}});
  fireEvent.change(screen.getByLabelText('API address (optional)'), {target: {value: 'https://gateway.example/v1'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save account'}));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][0].base_url).toBe('https://gateway.example/v1');
});

it('keeps what was typed, including the key, when the host rejects the account', async () => {
  const {onSave} = open({onSave: vi.fn(async () => null)});
  chooseAnthropic();
  fireEvent.change(screen.getByLabelText('Display name'), {target: {value: 'Claude'}});
  fireEvent.change(screen.getByLabelText('API key'), {target: {value: KEY}});
  fireEvent.click(screen.getByRole('button', {name: 'Save account'}));
  expect(await screen.findByText(/nothing changed/)).toHaveAttribute('role', 'alert');
  expect(screen.getByLabelText('API key')).toHaveValue(KEY);
  void onSave;
});

it('shows a saved key as saved, never renders it, and keeps it unless it is replaced', async () => {
  const {container, onSave} = open({account: saved});
  expect(screen.getByText('Key saved')).toBeInTheDocument();
  expect(container.querySelector('input[name="api_key"]')).toBeNull();
  expect(container.innerHTML).not.toContain('sk-ant');
  fireEvent.click(screen.getByRole('button', {name: 'Save account changes'}));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][0]).toMatchObject({id: 'claude', kind: 'anthropic', wire: 'messages', api_key: null});
});

it('replaces a saved key through Replace key and can go back to the saved one', async () => {
  const {onSave} = open({account: saved});
  fireEvent.click(screen.getByRole('button', {name: 'Replace key'}));
  expect(screen.getByLabelText('New API key')).toHaveValue('');
  fireEvent.click(screen.getByRole('button', {name: 'Save account changes'}));
  expect(screen.getByLabelText('New API key')).toHaveAccessibleDescription(expect.stringContaining('Keep saved key'));
  fireEvent.click(screen.getByRole('button', {name: 'Keep saved key'}));
  expect(screen.getByText('Key saved')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', {name: 'Replace key'}));
  fireEvent.change(screen.getByLabelText('New API key'), {target: {value: KEY}});
  fireEvent.click(screen.getByRole('button', {name: 'Save account changes'}));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][0].api_key).toBe(KEY);
  await waitFor(() => expect(screen.getByText('Key saved')).toBeInTheDocument());
  expect(screen.queryByDisplayValue(KEY)).not.toBeInTheDocument();
});

it('shows discovery capability hints only when the payload has them, and says cost is unpriced', () => {
  open({account: saved});
  const opus = screen.getByRole('checkbox', {name: /claude-opus-5-5/}).closest('li') as HTMLElement;
  const haiku = screen.getByRole('checkbox', {name: /claude-haiku-5-5/}).closest('li') as HTMLElement;
  expect(opus).toHaveTextContent('Cost unpriced');
  expect(opus).toHaveTextContent('Image input, Thinking');
  expect(haiku).toHaveTextContent('Cost unpriced');
  expect(haiku).not.toHaveTextContent('Thinking');
  expect(opus).not.toHaveTextContent('Unknown');
  expect(screen.getByText(/no prices, so cost shows as unpriced/)).toBeInTheDocument();
});

it('lets you set your own price, which replaces the unpriced text, and saves it as an override', async () => {
  const {onSave} = open({account: saved});
  const row = screen.getByRole('checkbox', {name: /claude-opus-5-5/}).closest('li') as HTMLElement;
  fireEvent.click(within(row).getByText('Override values for claude-opus-5-5'));
  fireEvent.change(await within(row).findByLabelText('Input price (USD per 1M tokens)'), {target: {value: '5'}});
  fireEvent.change(within(row).getByLabelText('Output price (USD per 1M tokens)'), {target: {value: '25'}});
  expect(row).not.toHaveTextContent('Cost unpriced');
  expect(row).toHaveTextContent('Input $5 per 1M tokens (your value)');
  fireEvent.click(screen.getByRole('button', {name: 'Save account changes'}));
  await waitFor(() => expect(onSave).toHaveBeenCalled());
  expect(onSave.mock.calls[0][0].model_meta).toEqual({'claude-opus-5-5': {price_in: 5, price_out: 25}});
});

it('never lets the last model be unchecked', () => {
  open({account: {...saved, model: 'claude-opus-5-5', model_catalog: {}}});
  expect(screen.getByRole('checkbox', {name: /claude-opus-5-5/})).toBeDisabled();
});

it('does not offer media, voice or realtime capabilities for Anthropic', () => {
  open({account: saved});
  fireEvent.click(screen.getByText('Advanced account options'));
  expect(screen.getByRole('checkbox', {name: 'Shell tools'})).toBeChecked();
  expect(screen.queryByRole('checkbox', {name: /Image|Video|Audio|Live voice/})).not.toBeInTheDocument();
});

it('refreshes models for a saved key and offers nothing to refresh before the key is saved', () => {
  const refresh = vi.fn(async () => ({}));
  const view = open({account: saved, onRefresh: refresh});
  fireEvent.click(screen.getByRole('button', {name: 'Refresh models'}));
  expect(refresh).toHaveBeenCalled();
  view.unmount();
  open({account: {...saved, secret_configured: false}, onRefresh: refresh});
  expect(screen.queryByRole('button', {name: 'Refresh models'})).not.toBeInTheDocument();
});

it('reports unsaved edits to the settings footer and clears them after a save', async () => {
  const reports: Array<[string, unknown]> = [];
  const registry = {report: (key: string, form: unknown) => void reports.push([key, form])};
  const onSave = vi.fn(async () => ({}));
  render(<SettingsFormsContext.Provider value={registry}><ProviderAccountForm busy={false} account={saved} onSave={onSave} /></SettingsFormsContext.Provider>);
  expect(reports.filter(([, form]) => form !== null)).toEqual([]);
  fireEvent.change(screen.getByLabelText('Display name'), {target: {value: 'Claude work'}});
  await waitFor(() => expect(reports.some(([, form]) => form !== null)).toBe(true));
  fireEvent.click(screen.getByRole('button', {name: 'Save account changes'}));
  await waitFor(() => expect(onSave).toHaveBeenCalled());
  await waitFor(() => expect(reports.at(-1)?.[1]).toBeNull());
});

it('has no axe violations when adding, editing, replacing a key or showing errors', async () => {
  const add = open();
  chooseAnthropic();
  fireEvent.click(screen.getByRole('button', {name: 'Save account'}));
  await expectNoAxeViolations(add.container);
  add.unmount();
  const edit = open({account: saved, onRefresh: vi.fn(), onCancel: vi.fn()});
  await expectNoAxeViolations(edit.container);
  fireEvent.click(screen.getByRole('button', {name: 'Replace key'}));
  fireEvent.click(screen.getByText('Advanced account options'));
  await expectNoAxeViolations(edit.container);
});
