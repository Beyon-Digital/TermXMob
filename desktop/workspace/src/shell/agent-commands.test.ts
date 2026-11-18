import {afterEach, describe, expect, it, vi} from 'vitest';
import {onAgentSurfaceRequest, requestAgentSurface} from '../lib/agent-surface-requests';
import {tabTitle} from './tab-titles';
import {commandAvailable, findCommand, paletteEntries, type CommandActions} from './commands';

afterEach(() => vi.restoreAllMocks());

const tree = {workspace: 'agent' as const, hostAdmin: false, engine: 'tree' as const};
const legacy = {workspace: 'agent' as const, hostAdmin: false, engine: 'legacy' as const};

describe('agent surface commands', () => {
  it('registers the CLI, Sites and host commands for the new layout only, with no keys', () => {
    for (const id of ['cli-new', 'cli-focus', 'sites-add-current', 'host-status', 'view-sites']) {
      const command = findCommand(id);
      expect(command, id).toBeDefined();
      expect(command!.engine, id).toBe('tree');
      expect(command!.defaultKeys, id).toBeUndefined();
    }
    expect(findCommand('cli-new')!.title).toBe('New CLI session');
    expect(findCommand('cli-focus')!.title).toBe('Focus CLI session');
    expect(findCommand('sites-add-current')!.title).toBe('Add current page to Sites');
    expect(findCommand('host-status')!.title).toBe('Show host status');
  });

  it('lists them in the palette of the new layout and not in the old one', () => {
    const labels = (context: typeof tree | typeof legacy) => paletteEntries(context, {} as never, () => {}).map(entry => entry.label);
    for (const label of ['New CLI session', 'Focus CLI session', 'Add current page to Sites', 'Show host status', 'Show Sites']) {
      expect(labels(tree), label).toContain(label);
      expect(labels(legacy), label).not.toContain(label);
    }
  });

  it('is available while the Agent workspace shows, and host status is available in any workspace', () => {
    expect(commandAvailable(findCommand('cli-new')!, tree)).toBe(true);
    expect(commandAvailable(findCommand('cli-new')!, {...tree, workspace: 'studio'})).toBe(false);
    expect(commandAvailable(findCommand('host-status')!, {...tree, workspace: 'studio'})).toBe(true);
  });

  it('runs each command through the shell action it names', () => {
    const actions = {newCliSession: vi.fn(), focusCliSession: vi.fn(), addCurrentSite: vi.fn(), showHostStatus: vi.fn(), showView: vi.fn()} as unknown as CommandActions;
    const run = (id: string) => findCommand(id)!.run(actions, {source: 'palette', terminalFocused: false});
    run('cli-new');
    run('cli-focus');
    run('sites-add-current');
    run('host-status');
    run('view-sites');
    expect(actions.newCliSession).toHaveBeenCalledTimes(1);
    expect(actions.focusCliSession).toHaveBeenCalledTimes(1);
    expect(actions.addCurrentSite).toHaveBeenCalledTimes(1);
    expect(actions.showHostStatus).toHaveBeenCalledTimes(1);
    expect(actions.showView).toHaveBeenCalledWith('sites');
  });
});

describe('agent surface requests', () => {
  it('reaches the frame that listens, and stops after it stops listening', () => {
    const heard = vi.fn();
    const stop = onAgentSurfaceRequest(heard);
    requestAgentSurface('cli.new');
    requestAgentSurface('host.status');
    stop();
    requestAgentSurface('cli.focus');
    expect(heard.mock.calls.map(call => call[0])).toEqual(['cli.new', 'host.status']);
  });
});

describe('tab titles for CLI and browser tabs', () => {
  const facts = {
    tabs: [],
    primaryId: '',
    selected: '',
    sessionTitle: undefined,
    file: () => undefined,
    cli: (id: string) => (id === 'c1' ? 'Claude Code · repo' : undefined),
    browser: (id: string) => (id === 't1' ? 'Docs' : undefined),
  };
  it('names a CLI tab after its session and a browser tab after its page, with the kind as fallback', () => {
    expect(tabTitle({id: 'a', kind: 'cli', ref: 'c1'}, facts)).toBe('Claude Code · repo');
    expect(tabTitle({id: 'a', kind: 'cli', ref: 'gone'}, facts)).toBe('CLI session');
    expect(tabTitle({id: 'b', kind: 'browser', ref: 't1'}, facts)).toBe('Docs');
    expect(tabTitle({id: 'b', kind: 'browser'}, facts)).toBe('Browser');
  });
});
