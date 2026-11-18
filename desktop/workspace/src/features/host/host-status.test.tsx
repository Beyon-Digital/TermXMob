import {act, cleanup, render, renderHook, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import HostStatusPopover from '../../components/shell/HostStatusPopover';
import StatusBar from '../../components/shell/StatusBar';
import {ApiError, gql} from '../../lib/api';
import {holderSummary, recoveryAttention, uptimeLabel, type HostStatus} from '../../lib/host-status';
import {expectNoAxeViolations} from '../../test-axe';
import {useHostStatus, type HostHealth} from './use-host-status';

vi.mock('../../lib/api', async () => {
  class Error403 extends Error {
    constructor(
      public status: number,
      message: string,
    ) {
      super(message);
    }
  }
  return {gql: vi.fn(), ApiError: Error403};
});
let emit: (value: HostStatus) => void = () => {};
vi.mock('../../lib/graphql-subscription', () => ({
  useGraphqlSubscription: (options: {onEvent: (value: HostStatus) => void}) => {
    emit = options.onEvent;
  },
}));

const api = vi.mocked(gql);

const status = (over: Partial<HostStatus> = {}): HostStatus => ({
  version: '0.9.0',
  pid: 42,
  started_at: 1,
  uptime_s: 3 * 3600 + 12 * 60,
  service: {backend: 'launchd', supported: true, installed: true, managed: true, state: 'running'},
  holder: {mode: 'held', requested: 'auto', reason: null, running: true, pid: 7, version: '1', protocol: 1, sessions: 2, exited_sessions: 0, draining: false, direct_terminals: 0},
  recovery: {recovering_tasks: 0, running_tasks: 1, lost_cli_sessions: 0, resumable_cli_sessions: 0, live_cli_sessions: 1, lost_engine_sessions: 0, resumable_engine_sessions: 0},
  keep_awake: {enabled: true, active: true, supported: true, backend: 'caffeinate', reasons: ['agent task running'], count: 1, error: null},
  heartbeat: {at: 100, age_s: 4.2, interval_s: 10, file: '/cfg/heartbeat.json', error: null},
  ...over,
});

const health = (over: Partial<HostHealth> = {}): HostHealth => ({status: status(), availability: 'ready', disconnected: false, silentFor: 0, ...over});

beforeEach(() => {
  api.mockReset();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('host status wording', () => {
  it('formats uptime', () => {
    expect(uptimeLabel(20)).toBe('Under a minute');
    expect(uptimeLabel(11520)).toBe('3 h 12 min');
    expect(uptimeLabel(90000)).toBe('1 d 1 h');
  });

  it('never hides a downgrade to direct mode: it says so and gives the reason', () => {
    const held = holderSummary(status().holder);
    expect(held).toEqual({text: 'Held by the PTY holder: 2 terminals survive a host restart.', direct: false, reason: ''});
    const direct = holderSummary(status({holder: {...status().holder, mode: 'direct', requested: 'auto', reason: 'holder socket path too long'}}).holder);
    expect(direct.direct).toBe(true);
    expect(direct.text).toContain('auto was requested');
    expect(direct.reason).toBe('holder socket path too long');
    expect(holderSummary({...status().holder, mode: 'direct', reason: null}).reason).toBe('No reason was reported.');
  });

  it('counts the recovery work', () => {
    expect(recoveryAttention(status())).toBe(0);
    expect(recoveryAttention(status({recovery: {...status().recovery, lost_cli_sessions: 1, resumable_cli_sessions: 1, recovering_tasks: 2}}))).toBe(4);
  });
});

describe('HostStatusPopover', () => {
  it('shows uptime, service, terminals, recovery, keep-awake reasons and the heartbeat age', () => {
    render(<HostStatusPopover health={health()} hostName="this Mac" onOpenAgents={vi.fn()} />);
    const group = screen.getByRole('group', {name: 'Host status'});
    expect(within(group).getByText('this Mac')).toBeInTheDocument();
    expect(within(group).getByText('TermX 0.9.0')).toBeInTheDocument();
    expect(within(group).getByText('3 h 12 min')).toBeInTheDocument();
    expect(within(group).getByText('launchd: running')).toBeInTheDocument();
    expect(within(group).getByText(/Held by the PTY holder: 2 terminals survive/)).toBeInTheDocument();
    expect(within(group).getByText('On')).toBeInTheDocument();
    expect(within(group).getByText('agent task running')).toBeInTheDocument();
    expect(within(group).getByText('4 s ago')).toBeInTheDocument();
    expect(within(group).queryByText(/Reason:/)).not.toBeInTheDocument();
  });

  it('shows the reason when the host runs terminals directly, with an icon and words, not only colour', () => {
    const direct = status({holder: {...status().holder, mode: 'direct', requested: 'auto', reason: 'the holder could not start: permission denied', direct_terminals: 1}});
    const {container} = render(<HostStatusPopover health={health({status: direct})} hostName="" onOpenAgents={vi.fn()} />);
    expect(screen.getByText(/Direct mode \(auto was requested\)/)).toBeInTheDocument();
    const reason = screen.getByText(/Reason: the holder could not start: permission denied/);
    expect(reason.querySelector('svg')).not.toBeNull();
    expect(screen.getByText('1 direct terminal')).toBeInTheDocument();
    expect(container.textContent).toContain('terminals end when the host stops');
  });

  it('links to the Agents view when CLI sessions are lost or can resume', async () => {
    const user = userEvent.setup();
    const onOpenAgents = vi.fn();
    const busy = status({recovery: {...status().recovery, lost_cli_sessions: 1, resumable_cli_sessions: 1}});
    render(<HostStatusPopover health={health({status: busy})} hostName="" onOpenAgents={onOpenAgents} />);
    expect(screen.getByText('1 CLI session lost, 1 resumable')).toBeInTheDocument();
    await user.click(screen.getByRole('button', {name: 'Open CLI sessions'}));
    expect(onOpenAgents).toHaveBeenCalled();
  });

  it('says when keep-awake is off, unsupported or failed, and when the heartbeat is missing', () => {
    const off = status({keep_awake: {enabled: false, active: false, supported: true, backend: null, reasons: [], count: 0, error: null}, heartbeat: {at: null, age_s: null, interval_s: 10, file: 'f', error: null}});
    const view = render(<HostStatusPopover health={health({status: off})} hostName="" onOpenAgents={vi.fn()} />);
    expect(screen.getByText('Off')).toBeInTheDocument();
    expect(screen.getByText('Not written yet')).toBeInTheDocument();
    view.unmount();
    render(<HostStatusPopover health={health({status: status({keep_awake: {enabled: true, active: false, supported: false, backend: null, reasons: [], count: 0, error: 'no inhibitor'}})})} hostName="" onOpenAgents={vi.fn()} />);
    expect(screen.getByText('Not supported on this system')).toBeInTheDocument();
    expect(screen.getByText('no inhibitor')).toBeInTheDocument();
  });

  it('says so when the host stops answering, and has no axe violations', async () => {
    const {container} = render(<HostStatusPopover health={health({disconnected: true, silentFor: 41})} hostName="this Mac" onOpenAgents={vi.fn()} />);
    expect(screen.getByRole('alert')).toHaveTextContent('No answer from the host for 41 seconds. Still retrying.');
    await expectNoAxeViolations(container);
  });
});

describe('StatusBar host health', () => {
  const bar = (extra: Partial<React.ComponentProps<typeof StatusBar>> = {}) =>
    render(
      <StatusBar
        online
        hostName="this Mac"
        problems={0}
        needYou={0}
        mainAgent=""
        cursor={null}
        onOpenSourceControl={vi.fn()}
        onOpenProblems={vi.fn()}
        onOpenAgents={vi.fn()}
        onOpenMainAgent={vi.fn()}
        onLock={vi.fn()}
        {...extra}
      />,
    );

  it('keeps plain text when this sign-in may not read host health (forbidden is hidden quietly)', () => {
    bar();
    expect(screen.getByRole('status')).toHaveTextContent('Connected · this Mac');
    expect(screen.queryByRole('button', {name: /Connected/})).not.toBeInTheDocument();
  });

  it('turns the health item into a popover trigger when details exist', async () => {
    const user = userEvent.setup();
    bar({hostDetails: <HostStatusPopover health={health()} hostName="this Mac" onOpenAgents={vi.fn()} />});
    await user.click(screen.getByRole('button', {name: /Connected · this Mac/}));
    expect(await screen.findByRole('group', {name: 'Host status'})).toBeVisible();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('group', {name: 'Host status'})).not.toBeInTheDocument());
  });

  it('shows a danger dot and the word Disconnected when the host has been silent', () => {
    const {container} = bar({disconnected: true});
    expect(screen.getByRole('status')).toHaveTextContent('Disconnected · this Mac');
    expect(container.querySelector('.status-health')).toHaveClass('down');
  });
});

describe('useHostStatus', () => {
  it('reads the host status, takes subscription events and reports forbidden without retrying', async () => {
    api.mockResolvedValue({host_status: status()});
    const {result} = renderHook(() => useHostStatus(true));
    await waitFor(() => expect(result.current.availability).toBe('ready'));
    expect(result.current.status?.version).toBe('0.9.0');
    act(() => emit(status({version: '0.9.1'})));
    expect(result.current.status?.version).toBe('0.9.1');
    expect(result.current.disconnected).toBe(false);
  });

  it('is forbidden, and stops asking, when the sign-in lacks machine-view', async () => {
    api.mockRejectedValue(new ApiError(403, 'missing machine-view scope'));
    const {result} = renderHook(() => useHostStatus(true));
    await waitFor(() => expect(result.current.availability).toBe('forbidden'));
    expect(result.current.status).toBeNull();
    expect(result.current.disconnected).toBe(false);
    const asked = api.mock.calls.length;
    await new Promise(resolve => setTimeout(resolve, 50));
    expect(api.mock.calls.length).toBe(asked);
  });

  it('counts a host that stopped answering as disconnected after 30 seconds, keeps retrying, and recovers', async () => {
    vi.useFakeTimers();
    api.mockResolvedValue({host_status: status()});
    const {result} = renderHook(() => useHostStatus(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10);
    });
    expect(result.current.availability).toBe('ready');
    api.mockRejectedValue(new Error('Failed to fetch'));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(25000);
    });
    expect(result.current.disconnected).toBe(false);
    const before = api.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(result.current.disconnected).toBe(true);
    expect(result.current.silentFor).toBeGreaterThan(30);
    expect(api.mock.calls.length).toBeGreaterThan(before);
    api.mockResolvedValue({host_status: status()});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11000);
    });
    expect(result.current.disconnected).toBe(false);
  });
});
