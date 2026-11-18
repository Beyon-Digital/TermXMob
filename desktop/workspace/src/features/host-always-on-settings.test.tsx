import {cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import {request} from '../lib/api';
import Managers from './Managers';
import HostAlwaysOnSettings, {HOST_ADMIN_REASON, type HostStatus} from './HostAlwaysOnSettings';
import {SettingsFormsContext} from './settings-forms';

vi.mock('../lib/api', () => ({request: vi.fn(), json: (method: string, value?: unknown) => ({method, body: JSON.stringify(value)})}));
const mocked = vi.mocked(request);
afterEach(cleanup);
beforeEach(() => {
  mocked.mockReset();
});

const base: HostStatus = {
  version: '1.0.0', pid: 4242, uptime_s: 120,
  service: {backend: 'launchd', supported: true, installed: true, managed: true, state: 'running_managed'},
  holder: {mode: 'held', requested: 'auto', reason: null, running: true, pid: 4300, sessions: 2, draining: false, direct_terminals: 0},
  keep_awake: {enabled: false, active: false, supported: true, backend: 'caffeinate', reasons: [], error: null},
  heartbeat: {at: 1, age_s: 4, interval_s: 10, file: 'heartbeat.json', error: null},
};
const direct: HostStatus = {
  ...base,
  service: {backend: 'launchd', supported: true, installed: false, managed: false, state: 'not_installed'},
  holder: {mode: 'direct', requested: 'auto', reason: 'The terminal helper could not start: cannot start. Terminals will not survive a restart.', running: false, pid: null, sessions: 0, draining: false, direct_terminals: 3},
};

function backend(initial: HostStatus, options: {fail?: string} = {}) {
  let status = initial;
  const updates: unknown[] = [];
  mocked.mockImplementation(async (_path: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body || '{}')) as {query: string; variables?: {input: {keep_awake?: boolean; pty_holder?: string}}};
    if (body.query.includes('update_host_settings')) {
      if (options.fail) throw new Error(options.fail);
      const input = body.variables!.input;
      updates.push(input);
      status = {
        ...status,
        keep_awake: {...status.keep_awake, enabled: input.keep_awake ?? status.keep_awake.enabled, active: input.keep_awake ?? status.keep_awake.active},
        holder: {...status.holder, requested: input.pty_holder ?? status.holder.requested},
      };
      return {data: {update_host_settings: status}} as never;
    }
    return {data: {host_status: status}} as never;
  });
  return updates;
}

it('shows what the host reports: service, terminals, helper, keep awake and the health file', async () => {
  backend(base);
  const {container} = render(<HostAlwaysOnSettings admin />);
  const status = await screen.findByLabelText('Host status');
  expect(status).toHaveTextContent('Installed, and this host is running as the service');
  expect(status).toHaveTextContent('Terminals keep running after the app or service stops');
  expect(status).toHaveTextContent('Process 4300 holding 2 terminals');
  expect(status).toHaveTextContent('Off. The computer can sleep when it is idle');
  expect(status).toHaveTextContent('heartbeat.json');
  expect(status).toHaveTextContent('Updated 4 seconds ago, every 10 seconds');
  await expectNoAxeViolations(container);
});

it('explains a downgrade to direct mode with the host reason and the terminals that would end', async () => {
  backend(direct);
  render(<HostAlwaysOnSettings admin />);
  const status = await screen.findByLabelText('Host status');
  expect(status).toHaveTextContent('Terminals end with this host, 3 terminals running now');
  expect(within(status).getByText('Why')).toBeInTheDocument();
  expect(status).toHaveTextContent('Terminals will not survive a restart.');
  expect(status).toHaveTextContent('Not installed');
});

it('starts from what the host asked for and describes each terminal choice in plain words', async () => {
  backend({...base, holder: {...base.holder, requested: 'held'}});
  render(<HostAlwaysOnSettings admin />);
  const group = await screen.findByRole('group', {name: 'Terminals when the app or service stops'});
  expect(within(group).getByRole('radio', {name: /Keep running/})).toBeChecked();
  expect(group).toHaveTextContent('Terminals keep running after the app or service stops.');
  expect(group).toHaveTextContent('Terminals end when the app or service stops.');
  expect(group).toHaveTextContent('applies to terminals you open next');
});

it('sends only the fields that changed and shows the new state afterwards', async () => {
  const updates = backend(base);
  render(<HostAlwaysOnSettings admin />);
  fireEvent.click(await screen.findByRole('checkbox', {name: /Keep this computer awake/}));
  fireEvent.submit(screen.getByRole('form', {name: 'Always-on host settings'}));
  await waitFor(() => expect(updates).toEqual([{keep_awake: true}]));
  expect(await screen.findByText(/Awake now/)).toBeInTheDocument();
  expect(screen.getByRole('checkbox', {name: /Keep this computer awake/})).toBeChecked();
  fireEvent.click(screen.getByRole('radio', {name: /End with the host/}));
  fireEvent.submit(screen.getByRole('form', {name: 'Always-on host settings'}));
  await waitFor(() => expect(updates).toEqual([{keep_awake: true}, {pty_holder: 'direct'}]));
  expect(await screen.findByRole('radio', {name: /End with the host/})).toBeChecked();
});

it('keeps the edit and shows the host refusal beside the form', async () => {
  backend(base, {fail: 'provider wire must be valid'});
  render(<HostAlwaysOnSettings admin />);
  fireEvent.click(await screen.findByRole('radio', {name: /Keep running/}));
  fireEvent.submit(screen.getByRole('form', {name: 'Always-on host settings'}));
  expect(await screen.findByText('provider wire must be valid')).toHaveAttribute('role', 'alert');
  expect(screen.getByRole('radio', {name: /Keep running/})).toBeChecked();
});

it('disables keep awake with the reason when the computer offers no mechanism', async () => {
  backend({...base, keep_awake: {...base.keep_awake, supported: false, backend: null}});
  render(<HostAlwaysOnSettings admin />);
  expect(await screen.findByRole('checkbox', {name: /Keep this computer awake/})).toBeDisabled();
  expect(screen.getAllByText(/offers no way to stay awake/).length).toBeGreaterThan(0);
});

it('asks nothing of the host and says why when the person is not an administrator', () => {
  backend(base);
  render(<HostAlwaysOnSettings admin={false} />);
  expect(screen.getByRole('status')).toHaveTextContent(HOST_ADMIN_REASON);
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
  expect(mocked).not.toHaveBeenCalled();
});

it('shows a forbidden answer from the host as an error instead of controls', async () => {
  mocked.mockRejectedValue(new Error('Missing required scope: host-admin'));
  render(<HostAlwaysOnSettings admin />);
  expect(await screen.findByText('Missing required scope: host-admin')).toHaveAttribute('role', 'alert');
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
});

it('reports unsaved changes to the settings footer and clears them after saving', async () => {
  backend(base);
  const reports: Array<[string, unknown]> = [];
  const registry = {report: (key: string, form: unknown) => void reports.push([key, form])};
  render(<SettingsFormsContext.Provider value={registry}><HostAlwaysOnSettings admin /></SettingsFormsContext.Provider>);
  fireEvent.click(await screen.findByRole('checkbox', {name: /Keep this computer awake/}));
  await waitFor(() => expect(reports.some(([, form]) => form !== null)).toBe(true));
  fireEvent.submit(screen.getByRole('form', {name: 'Always-on host settings'}));
  await screen.findByText(/Awake now/);
  expect(reports.at(-1)?.[1]).toBeNull();
});

function accessHost(scopes: string[]) {
  mocked.mockImplementation(async (path: string, init?: RequestInit) => {
    if (path === '/auth/me') return {principal: {id: 'p', display_name: 'P', scopes}} as never;
    if (path === '/auth/access') return {role: 'Admin', runtime_boundary: 'Host policy'} as never;
    if (path === '/auth/sessions') return [] as never;
    if (path.startsWith('/auth/')) return (path.endsWith('configuration') ? {} : []) as never;
    const query = String(JSON.parse(String(init?.body || '{}')).query || '');
    if (query.includes('host_status')) return {data: {host_status: base}} as never;
    return {data: {projects: []}} as never;
  });
}

it('adds Always-on host to Access & sessions and reads the host only when it is opened', async () => {
  accessHost(['host-admin']);
  render(<Managers section="access" projectId={null} sessionId={null} />);
  const summary = await screen.findByText('Always-on host', {selector: 'summary'});
  expect(mocked.mock.calls.some(([, init]) => String(init?.body).includes('host_status'))).toBe(false);
  fireEvent.click(summary);
  expect(await screen.findByRole('checkbox', {name: /Keep this computer awake/})).toBeEnabled();
});

it('shows the same disclosure to a member with the reason and no controls', async () => {
  accessHost([]);
  render(<Managers section="access" projectId={null} sessionId={null} />);
  fireEvent.click(await screen.findByText('Always-on host', {selector: 'summary'}));
  expect(await screen.findByText(HOST_ADMIN_REASON)).toBeInTheDocument();
  expect(screen.queryByRole('checkbox', {name: /Keep this computer awake/})).not.toBeInTheDocument();
  expect(mocked.mock.calls.some(([, init]) => String(init?.body).includes('host_status'))).toBe(false);
});
