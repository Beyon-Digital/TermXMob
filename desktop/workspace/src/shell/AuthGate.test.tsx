import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import AuthGate, {useAuth} from './AuthGate';
import {ErrorProvider} from './errors';

const mocks = vi.hoisted(() => ({
  request: vi.fn(),
  lockState: vi.fn(),
  lockWorkspace: vi.fn(),
  preserve: vi.fn(),
  compatibility: vi.fn(),
  restoreDrafts: vi.fn(),
}));

vi.mock('../lib/api', () => ({
  request: mocks.request,
  json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)}),
  login: vi.fn(),
  lockState: mocks.lockState,
  lockWorkspace: mocks.lockWorkspace,
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));
vi.mock('../lib/compatibility', () => ({checkCompatibility: mocks.compatibility}));
vi.mock('../lib/lock-preservation', () => ({preserveBeforeLock: mocks.preserve}));
vi.mock('../lib/window-journal', () => ({restoreWindowDrafts: mocks.restoreDrafts}));
vi.mock('../lib/native', () => ({
  nativeWorkspace: () => false,
  listenNativeLocks: async () => () => {},
}));
vi.mock('../components/PairSignIn', () => ({default: () => null}));
vi.mock('../components/HostStop', () => ({
  default: ({open}: {open: boolean}) => (open ? <div role="dialog">Stop this host</div> : null),
}));
vi.mock('../components/LockScreen', () => ({
  default: ({
    preparing,
    notice,
    onUnlocked,
  }: {
    preparing: boolean;
    notice: string;
    onUnlocked: (value: unknown) => Promise<void>;
  }) => (
    <section aria-label="Lock screen">
      {preparing ? 'Locking' : 'Locked'} {notice}
      <button onClick={() => void onUnlocked(auth).catch(() => {})}>Unlock</button>
    </section>
  ),
}));

const auth = {principal: {id: 'owner', display_name: 'Owner', scopes: ['host-admin']}, session_id: 'session-1'};

type Probe = ReturnType<typeof useAuth>;
let probe: Probe | undefined;

function Workspace() {
  probe = useAuth();
  return <p>Workspace of {probe.auth.principal.display_name}</p>;
}

function renderGate() {
  return render(
    <ErrorProvider>
      <AuthGate>
        <Workspace />
      </AuthGate>
    </ErrorProvider>,
  );
}

function signedIn() {
  mocks.request.mockImplementation(async (path: string) => {
    if (path === '/auth/methods') return {configured: true, methods: []};
    if (path === '/auth/me') return auth;
    throw new Error('unexpected ' + path);
  });
}

beforeEach(() => {
  probe = undefined;
  sessionStorage.clear();
  mocks.compatibility.mockResolvedValue(undefined);
  mocks.restoreDrafts.mockResolvedValue(undefined);
  mocks.preserve.mockResolvedValue([]);
  mocks.lockWorkspace.mockResolvedValue(undefined);
  mocks.lockState.mockResolvedValue({locked: true, principal: {id: 'owner'}, session_id: 'session-1'});
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  document.body.className = '';
});

describe('AuthGate', () => {
  it('shows the boot screen, then the workspace of the signed-in principal', async () => {
    signedIn();
    renderGate();
    expect(screen.getByText('Connecting to your workspace…')).toBeInTheDocument();
    expect(await screen.findByText('Workspace of Owner')).toBeInTheDocument();
    expect(mocks.compatibility).toHaveBeenCalledTimes(1);
    expect(mocks.restoreDrafts).toHaveBeenCalledWith('owner');
    expect(probe?.locked).toBe(false);
  });

  it('keeps the document in the Agent theme scope while nobody is signed in', async () => {
    mocks.request.mockImplementation(async (path: string) => {
      if (path === '/auth/methods') return {configured: true, methods: []};
      throw new Error('Session expired');
    });
    renderGate();
    expect(await screen.findByRole('heading', {name: 'Connect to your workspace'})).toBeInTheDocument();
    expect(document.documentElement.dataset.appWorkspace).toBe('agent');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.queryByText(/Workspace of/)).toBeNull();
  });

  it('reports an unexpected boot failure on the sign-in screen', async () => {
    mocks.compatibility.mockRejectedValue(new Error('This host is too old'));
    renderGate();
    expect(await screen.findByRole('heading', {name: 'Set up your workspace'})).toBeInTheDocument();
    expect(await screen.findByText('This host is too old')).toBeInTheDocument();
  });

  it('opens on the lock screen when the host answers 423', async () => {
    const {ApiError} = await import('../lib/api');
    mocks.request.mockImplementation(async (path: string) => {
      if (path === '/auth/methods') return {configured: true, methods: []};
      throw new ApiError(423, 'locked');
    });
    renderGate();
    expect(await screen.findByLabelText('Lock screen')).toBeInTheDocument();
    expect(screen.queryByText(/Workspace of/)).toBeNull();
    expect(screen.queryByRole('heading', {name: 'Connect to your workspace'})).toBeNull();
  });

  it('covers the mounted workspace when it locks, and tells subscribers when it unlocks', async () => {
    signedIn();
    renderGate();
    await screen.findByText('Workspace of Owner');
    const unlocked = vi.fn();
    act(() => {
      probe!.onUnlocked(unlocked);
    });
    await act(async () => {
      await probe!.beginLock();
    });
    expect(mocks.preserve).toHaveBeenCalledWith('owner');
    expect(mocks.lockWorkspace).toHaveBeenCalledTimes(1);
    expect(probe?.locked).toBe(true);
    expect(probe?.lockedNow.current).toBe(true);
    expect(screen.getByText('Workspace of Owner')).toBeInTheDocument();
    expect(screen.getByLabelText('Lock screen')).toBeInTheDocument();
    expect(sessionStorage.getItem('termx-browser-view-paused')).toBe('true');
    expect(document.body).toHaveClass('workspace-lock-active');

    fireEvent.click(screen.getByRole('button', {name: 'Unlock'}));
    await waitFor(() => expect(unlocked).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(probe?.locked).toBe(false));
    expect(screen.queryByLabelText('Lock screen')).toBeNull();
    expect(document.body).not.toHaveClass('workspace-lock-active');
  });

  it('starts only one lock at a time', async () => {
    signedIn();
    renderGate();
    await screen.findByText('Workspace of Owner');
    await act(async () => {
      await Promise.all([probe!.beginLock(), probe!.beginLock()]);
    });
    expect(mocks.lockWorkspace).toHaveBeenCalledTimes(1);
  });

  it('returns to sign-in and drops the workspace when the session ends', async () => {
    signedIn();
    renderGate();
    await screen.findByText('Workspace of Owner');
    const epoch = probe!.authEpoch.current;
    act(() => {
      window.dispatchEvent(new Event('termx-signed-out'));
    });
    expect(await screen.findByRole('heading', {name: 'Connect to your workspace'})).toBeInTheDocument();
    expect(screen.queryByText(/Workspace of/)).toBeNull();
    expect(probe!.authEpoch.current).toBeGreaterThan(epoch);
    expect(probe!.authNow.current).toBeNull();
  });

  it('opens the host stop dialog only while unlocked', async () => {
    signedIn();
    renderGate();
    await screen.findByText('Workspace of Owner');
    act(() => probe!.openHostStop());
    expect(screen.getByRole('dialog')).toHaveTextContent('Stop this host');
    await act(async () => {
      await probe!.beginLock();
    });
    expect(screen.queryByRole('dialog')).toBeNull();
  });
});
