import {act, cleanup, renderHook, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {useWindowTransfer} from './WindowTransfer';

const mocks = vi.hoisted(() => {
  const calls: string[] = [];
  const note = (name: string, value?: unknown) =>
    vi.fn(async (...args: unknown[]) => {
      calls.push(name);
      void args;
      return value;
    });
  return {
    calls,
    native: {value: false, detached: false},
    report: vi.fn(),
    request: vi.fn(),
    gql: vi.fn(),
    redockListener: undefined as undefined | ((event: Record<string, unknown>) => Promise<void>),
    nativeListener: undefined as undefined | ((event: Record<string, unknown>) => void),
    releaseBrowser: vi.fn(),
    releaseNative: vi.fn(),
    drafts: new Map<string, unknown>(),
    validate: vi.fn(),
    freezeBuffers: note('freezeBuffers'),
    freezeArtifacts: note('freezeArtifacts'),
    flushDraft: note('flushDraft'),
    dropBuffers: note('dropBuffers'),
    dropArtifacts: note('dropArtifacts'),
    persistArtifacts: note('persistArtifacts'),
    persistBuffers: note('persistBuffers'),
    unfreezeBuffers: vi.fn(() => void calls.push('unfreezeBuffers')),
    unfreezeArtifacts: vi.fn(() => void calls.push('unfreezeArtifacts')),
    requestBrowserRedock: vi.fn(async () => (calls.push('requestBrowserRedock'), 'browser-token')),
    commitBrowserRedock: note('commitBrowserRedock'),
    cancelBrowserRedock: vi.fn(() => void calls.push('cancelBrowserRedock')),
    requestRedock: vi.fn(async () => (calls.push('requestRedock'), 'native-token')),
    commitRedock: note('commitRedock'),
    cancelRedock: note('cancelRedock'),
    acceptRedock: note('acceptRedock'),
    rejectRedock: note('rejectRedock'),
    detachWorkspace: vi.fn(async () => {}),
    openBrowserWindow: vi.fn(async () => {}),
    snapshotWindow: vi.fn(),
    acceptTransferred: vi.fn(),
    receiveBuffers: note('receiveBuffers'),
    receiveArtifacts: note('receiveArtifacts'),
    persistDraft: note('persistDraft'),
    persistRecord: vi.fn(async () => {}),
    rememberTerminal: vi.fn(),
    recoverable: vi.fn(),
    bufferCount: vi.fn(),
    restoreArtifacts: vi.fn(),
  };
});

vi.mock('./AuthGate', () => ({
  useAuth: () => ({auth: {principal: {id: 'owner', display_name: 'Owner', scopes: []}, session_id: 's'}}),
}));
vi.mock('./errors', () => ({useReport: () => mocks.report}));
vi.mock('../lib/api', () => ({request: mocks.request, gql: mocks.gql}));
vi.mock('../lib/drafts', () => ({sessionDrafts: mocks.drafts}));
vi.mock('../lib/artifact-drafts', () => ({
  dropArtifactDrafts: mocks.dropArtifacts,
  freezeArtifactDrafts: mocks.freezeArtifacts,
  persistArtifactDrafts: mocks.persistArtifacts,
  receiveArtifactDrafts: mocks.receiveArtifacts,
  restoreArtifactDrafts: mocks.restoreArtifacts,
  snapshotArtifactDrafts: () => [{id: 'artifact-before'}],
  unfreezeArtifactDrafts: mocks.unfreezeArtifacts,
}));
vi.mock('../lib/browser-windows', () => ({
  cancelBrowserRedock: mocks.cancelBrowserRedock,
  commitBrowserRedock: mocks.commitBrowserRedock,
  openBrowserWindow: mocks.openBrowserWindow,
  requestBrowserRedock: mocks.requestBrowserRedock,
  listenBrowserRedock: (_owner: string, handler: (event: Record<string, unknown>) => Promise<void>) => {
    mocks.redockListener = handler;
    return mocks.releaseBrowser;
  },
}));
vi.mock('../lib/native', () => ({
  nativeWorkspace: () => mocks.native.value,
  isDetachedWindow: () => mocks.native.detached,
  acceptRedock: mocks.acceptRedock,
  rejectRedock: mocks.rejectRedock,
  cancelRedock: mocks.cancelRedock,
  commitRedock: mocks.commitRedock,
  requestRedock: mocks.requestRedock,
  detachWorkspace: mocks.detachWorkspace,
  listenNativeRedock: async (handler: (event: Record<string, unknown>) => void) => {
    mocks.nativeListener = handler;
    return mocks.releaseNative;
  },
}));
vi.mock('../lib/redock-state', () => ({
  acceptTransferredDraft: mocks.acceptTransferred,
  flushRedockDraft: mocks.flushDraft,
  snapshotWindow: mocks.snapshotWindow,
}));
vi.mock('../lib/window-journal', () => ({
  bufferCount: mocks.bufferCount,
  persistWindowDraft: mocks.persistDraft,
  persistWindowRecord: mocks.persistRecord,
  recoverableWindows: mocks.recoverable,
}));
vi.mock('../lib/window-state', () => ({
  dropWindowBuffers: mocks.dropBuffers,
  freezeWindowBuffers: mocks.freezeBuffers,
  persistBuffers: mocks.persistBuffers,
  receiveBuffers: mocks.receiveBuffers,
  rememberTerminal: mocks.rememberTerminal,
  snapshotBuffers: () => [],
  unfreezeWindowBuffers: mocks.unfreezeBuffers,
  validateWindowState: mocks.validate,
}));

type Props = {selected: string; surface?: 'chat' | 'browser' | 'workbench'};

function setup(props: Props = {selected: 'chat-1'}) {
  const onArrive = vi.fn(async () => {});
  const hook = renderHook(
    (current: Props) => useWindowTransfer({selected: current.selected, surface: current.surface ?? 'chat', onArrive}),
    {initialProps: props},
  );
  return {...hook, onArrive};
}

const state = (extra: Record<string, unknown> = {}) => ({
  version: 1,
  sessionId: 'chat-1',
  buffers: [{project_id: 'proj'}],
  draft: null,
  ...extra,
});

beforeEach(() => {
  mocks.calls.length = 0;
  mocks.native.value = false;
  mocks.native.detached = false;
  mocks.redockListener = undefined;
  mocks.nativeListener = undefined;
  mocks.drafts.clear();
  mocks.snapshotWindow.mockReturnValue(state());
  mocks.validate.mockImplementation((payload: unknown) => payload);
  mocks.request.mockImplementation(async (path: string) =>
    path === '/auth/me'
      ? {principal: {id: 'owner'}}
      : {id: 'chat-1', cwd: '/work', draft_text: '', draft_context: []},
  );
  mocks.gql.mockImplementation(async (query: string) =>
    query.includes('projects') ? {projects: [{id: 'proj'}]} : {sessions: [{id: 'term', cwd: '/work', exited: false}]},
  );
  mocks.recoverable.mockResolvedValue([]);
  mocks.bufferCount.mockResolvedValue(0);
  mocks.restoreArtifacts.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('window journal', () => {
  it('records the window in a browser and again when the surface or conversation changes', async () => {
    const {rerender} = setup({selected: 'chat-1', surface: 'chat'});
    await waitFor(() => expect(mocks.persistRecord).toHaveBeenCalledWith('owner', 'chat-1', 'chat'));
    rerender({selected: 'chat-1', surface: 'browser'});
    await waitFor(() => expect(mocks.persistRecord).toHaveBeenLastCalledWith('owner', 'chat-1', 'browser'));
    rerender({selected: '', surface: 'browser'});
    await waitFor(() => expect(mocks.persistRecord).toHaveBeenLastCalledWith('owner', null, 'browser'));
  });

  it('leaves the journal to the native shell', () => {
    mocks.native.value = true;
    setup();
    expect(mocks.persistRecord).not.toHaveBeenCalled();
  });
});

describe('detach', () => {
  it('opens the current surface in a browser window', () => {
    const {result} = setup({selected: 'chat-1', surface: 'browser'});
    result.current.detach();
    expect(mocks.openBrowserWindow).toHaveBeenCalledWith('owner', 'chat-1', 'browser');
    result.current.detachSurface('workbench');
    expect(mocks.openBrowserWindow).toHaveBeenLastCalledWith('owner', 'chat-1', 'workbench');
  });

  it('asks the native shell with the conversation or null, and reports a failure', async () => {
    mocks.native.value = true;
    mocks.detachWorkspace.mockRejectedValueOnce(new Error('No window'));
    const {result} = setup({selected: ''});
    result.current.detachSurface('terminal' as never);
    expect(mocks.detachWorkspace).toHaveBeenCalledWith(null, 'terminal');
    await waitFor(() => expect(mocks.report).toHaveBeenCalledWith(expect.objectContaining({message: 'No window'})));
    expect(mocks.openBrowserWindow).not.toHaveBeenCalled();
  });
});

describe('redock from a detached window', () => {
  it('freezes, requests, commits and keeps the window covered once committed', async () => {
    const {result} = setup();
    await act(async () => {
      await result.current.redock();
    });
    expect(mocks.calls).toEqual([
      'freezeBuffers',
      'freezeArtifacts',
      'flushDraft',
      'requestBrowserRedock',
      'dropBuffers',
      'commitBrowserRedock',
      'dropArtifacts',
    ]);
    expect(mocks.requestBrowserRedock).toHaveBeenCalledWith('owner', 'chat-1', 'chat', state());
    expect(mocks.commitBrowserRedock).toHaveBeenCalledWith('browser-token');
    expect(result.current.redocking).toBe(true);
    expect(mocks.report).not.toHaveBeenCalled();
  });

  it('uses the native handshake in the desktop app', async () => {
    mocks.native.value = true;
    const {result} = setup();
    await act(async () => {
      await result.current.redock();
    });
    expect(mocks.requestRedock).toHaveBeenCalledWith('chat-1', 'chat', state());
    expect(mocks.commitRedock).toHaveBeenCalledWith('native-token');
    expect(mocks.requestBrowserRedock).not.toHaveBeenCalled();
  });

  it('cancels, restores the frozen state and stays open when the state changes during the handoff', async () => {
    mocks.snapshotWindow.mockReturnValueOnce(state()).mockReturnValue(state({buffers: []}));
    const {result} = setup();
    await act(async () => {
      await result.current.redock();
    });
    expect(mocks.cancelBrowserRedock).toHaveBeenCalledWith('browser-token');
    expect(mocks.commitBrowserRedock).not.toHaveBeenCalled();
    expect(mocks.unfreezeBuffers).toHaveBeenCalled();
    expect(mocks.unfreezeArtifacts).toHaveBeenCalled();
    expect(mocks.persistArtifacts).toHaveBeenCalled();
    expect(mocks.persistBuffers).toHaveBeenCalled();
    expect(mocks.report).toHaveBeenCalledWith(
      expect.objectContaining({message: 'Workspace state changed during handoff. This window remains open.'}),
    );
    expect(result.current.redocking).toBe(false);
  });

  it('does nothing without a conversation', async () => {
    const {result} = setup({selected: ''});
    await act(async () => {
      await result.current.redock();
    });
    expect(mocks.freezeBuffers).not.toHaveBeenCalled();
    expect(result.current.redocking).toBe(false);
  });
});

describe('accepting a redocked window', () => {
  const event = (extra: Record<string, unknown> = {}) => ({
    id: 'handoff-1',
    ownerId: 'owner',
    sessionId: 'chat-1',
    panel: 'workbench',
    payload: state(),
    ...extra,
  });

  it('validates, stores the moved buffers and then shows the conversation (browser windows)', async () => {
    const {onArrive} = setup();
    await act(async () => {
      await mocks.redockListener!(event());
    });
    expect(mocks.receiveBuffers).toHaveBeenCalledWith('owner', state().buffers, 'handoff-1');
    expect(mocks.acceptTransferred).toHaveBeenCalledWith(state());
    expect(onArrive).toHaveBeenCalledWith({sessionId: 'chat-1', panel: 'workbench', hasBuffers: true});
    expect(mocks.calls.indexOf('receiveBuffers')).toBeLessThan(onArrive.mock.invocationCallOrder[0]);
  });

  it('refuses a handoff from another principal, another conversation or a changed draft', async () => {
    const {onArrive} = setup();
    await expect(mocks.redockListener!(event({ownerId: 'other'}))).rejects.toThrow('principal changed');
    await expect(mocks.redockListener!(event({sessionId: 'chat-2'}))).rejects.toThrow('conversation changed');
    mocks.request.mockImplementation(async (path: string) =>
      path === '/auth/me' ? {principal: {id: 'owner'}} : {id: 'chat-1', cwd: '/work', draft_text: 'newer'},
    );
    await expect(mocks.redockListener!(event({payload: state({draft: {text: 'older'}})}))).rejects.toThrow(
      'Conversation draft changed during handoff',
    );
    expect(onArrive).not.toHaveBeenCalled();
  });

  it('refuses a different unsent draft in this window', async () => {
    mocks.drafts.set('chat-1', {dirty: true, text: 'typed here'});
    const {onArrive} = setup();
    await expect(
      mocks.redockListener!(event({payload: state({draft: {text: 'typed there'}})})),
    ).rejects.toThrow('different unsent draft');
    expect(onArrive).not.toHaveBeenCalled();
  });

  it('refuses buffers of a project this account can no longer reach', async () => {
    mocks.gql.mockResolvedValue({projects: []});
    const {onArrive} = setup();
    await expect(mocks.redockListener!(event())).rejects.toThrow('no longer access');
    expect(onArrive).not.toHaveBeenCalled();
  });

  it('only takes over a transferred terminal that is still running in the same checkout', async () => {
    const {onArrive} = setup();
    const handoff = vi.fn();
    window.addEventListener('termx-terminal-handoff', handoff);
    await act(async () => {
      await mocks.redockListener!(event({payload: state({terminalId: 'term'})}));
    });
    window.removeEventListener('termx-terminal-handoff', handoff);
    expect(mocks.rememberTerminal).toHaveBeenCalledWith('chat-1', 'term');
    expect(handoff).toHaveBeenCalledTimes(1);
    expect(onArrive).toHaveBeenCalledTimes(1);
    mocks.gql.mockImplementation(async (query: string) =>
      query.includes('projects') ? {projects: [{id: 'proj'}]} : {sessions: []},
    );
    await expect(mocks.redockListener!(event({payload: state({terminalId: 'term'})}))).rejects.toThrow(
      'Transferred terminal is no longer available',
    );
  });

  it('accepts through the native shell and rejects with a report when it fails', async () => {
    mocks.native.value = true;
    const {onArrive} = setup();
    await waitFor(() => expect(mocks.nativeListener).toBeDefined());
    await act(async () => {
      mocks.nativeListener!(event());
    });
    await waitFor(() => expect(mocks.acceptRedock).toHaveBeenCalledWith('handoff-1'));
    expect(onArrive).toHaveBeenCalledTimes(1);

    onArrive.mockRejectedValueOnce(new Error('Could not show it'));
    await act(async () => {
      mocks.nativeListener!(event({id: 'handoff-2'}));
    });
    await waitFor(() => expect(mocks.rejectRedock).toHaveBeenCalledWith('handoff-2'));
    expect(mocks.report).toHaveBeenCalledWith(expect.objectContaining({message: 'Could not show it'}));
  });

  it('never listens inside a detached native window, and stops listening on unmount', async () => {
    mocks.native.value = true;
    mocks.native.detached = true;
    setup();
    expect(mocks.nativeListener).toBeUndefined();
    cleanup();
    mocks.native.value = false;
    mocks.native.detached = false;
    const {unmount} = setup();
    unmount();
    expect(mocks.releaseBrowser).toHaveBeenCalled();
  });

  it('uses the latest arrival handler without listening again', async () => {
    const first = vi.fn(async () => {});
    const second = vi.fn(async () => {});
    const {rerender} = renderHook(
      ({onArrive}: {onArrive: () => Promise<void>}) => useWindowTransfer({selected: 'chat-1', surface: 'chat', onArrive}),
      {initialProps: {onArrive: first}},
    );
    rerender({onArrive: second});
    await act(async () => {
      await mocks.redockListener!(event());
    });
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});

describe('recovery', () => {
  it('lists only windows that left something behind', async () => {
    mocks.recoverable.mockResolvedValue([
      {slot: 'a', sessionId: 'chat-1', panel: 'chat', drafts: 0},
      {slot: 'b', sessionId: 'chat-2', panel: 'workbench', drafts: 2},
      {slot: 'c', sessionId: null, panel: 'chat', drafts: 0},
    ]);
    mocks.bufferCount.mockImplementation(async (_owner: string, slot: string) => (slot === 'c' ? 3 : 0));
    const {result} = setup();
    await act(async () => {
      await result.current.recoverWindows();
    });
    expect(result.current.recoveryOpen).toBe(true);
    expect(result.current.recoveryRows.map(row => [row.slot, row.buffers, row.drafts, row.artifacts])).toEqual([
      ['b', 0, 2, 0],
      ['c', 3, 0, 0],
    ]);
  });

  it('reopens a recovered window and closes the dialog, but not a row without a conversation', async () => {
    const rows = [
      {slot: 'b', sessionId: 'chat-2', panel: 'workbench', drafts: 2},
      {slot: 'c', sessionId: null, panel: 'chat', drafts: 1},
    ];
    mocks.recoverable.mockResolvedValue(rows);
    const {result} = setup();
    await act(async () => {
      await result.current.recoverWindows();
    });
    await act(async () => {
      await result.current.reopenRecovered(result.current.recoveryRows[0]);
    });
    expect(mocks.openBrowserWindow).toHaveBeenCalledWith('owner', 'chat-2', 'workbench', result.current.recoveryRows[0]);
    expect(result.current.recoveryOpen).toBe(false);
    mocks.openBrowserWindow.mockClear();
    await act(async () => {
      await result.current.reopenRecovered(result.current.recoveryRows[1]);
    });
    expect(mocks.openBrowserWindow).not.toHaveBeenCalled();
  });

  it('reports a failure to read the journal', async () => {
    mocks.recoverable.mockRejectedValue(new Error('Journal unavailable'));
    const {result} = setup();
    await act(async () => {
      await result.current.recoverWindows();
    });
    expect(mocks.report).toHaveBeenCalledWith(expect.objectContaining({message: 'Journal unavailable'}));
    expect(result.current.recoveryOpen).toBe(false);
  });
});
