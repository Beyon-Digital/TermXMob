import {cleanup, renderHook} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {
  bindingWarnings,
  defaults,
  labels,
  validBinding,
  validateBindings,
  type Bindings,
} from '../lib/keybindings';
import {settingsCategories} from '../lib/settings-categories';
import {
  commandAvailable,
  commands,
  findCommand,
  isCommandId,
  keyedSpecs,
  paletteEntries,
  sharesContext,
  type CommandActions,
} from './commands';
import {useShortcuts} from './use-shortcuts';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

const keyed = commands.filter(command => command.defaultKeys);

describe('command registry', () => {
  it('has unique ids and a title and category for every command', () => {
    const ids = commands.map(command => command.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const command of commands) {
      expect(command.title.trim(), command.id).not.toBe('');
      expect(command.category, command.id).toBeTruthy();
    }
  });

  it('keeps the keybinding settings and the registry in step', () => {
    expect(Object.keys(defaults)).toEqual(keyed.map(command => command.id));
    for (const command of keyed) {
      expect(defaults[command.id as keyof Bindings]).toBe(command.defaultKeys);
      expect(labels[command.id as keyof Bindings]).toBe(command.title);
      expect(isCommandId(command.id)).toBe(true);
    }
    expect(isCommandId('lock')).toBe(false);
  });

  it('gives every command a default key the key grammar accepts', () => {
    for (const command of keyed) expect(validBinding(command.defaultKeys!), command.id).toBe(true);
  });

  it('never gives two commands in the same context the same default key', () => {
    const clashes: string[] = [];
    keyed.forEach((a, index) => {
      for (const b of keyed.slice(index + 1)) {
        if (a.defaultKeys!.toLowerCase() === b.defaultKeys!.toLowerCase() && sharesContext(a, b)) {
          clashes.push(a.id + ' and ' + b.id + ' share ' + a.defaultKeys);
        }
      }
    });
    expect(clashes).toEqual([]);
  });

  it('does not use the first step of a chord as a command of its own', () => {
    const prefixes = keyed
      .map(command => command.defaultKeys!.trim().split(/\s+/))
      .filter(steps => steps.length > 1)
      .map(steps => steps[0].toLowerCase());
    for (const command of keyed) {
      const steps = command.defaultKeys!.trim().split(/\s+/);
      if (steps.length === 1) expect(prefixes, command.id).not.toContain(steps[0].toLowerCase());
    }
  });

  it('carries the VS Code style keys from the plan, each mapped to existing behaviour', () => {
    const expected: Record<string, string> = {
      left: 'Mod+B',
      bottom: 'Mod+J',
      focusTerminal: 'Ctrl+`',
      explorer: 'Mod+Shift+E',
      search: 'Mod+Shift+F',
      sourceControl: 'Ctrl+Shift+G',
      settings: 'Mod+,',
      palette: 'Mod+Shift+P',
      quickFile: 'Mod+P',
      newConversation: 'Mod+Shift+N',
      voiceTalk: 'Mod+Shift+M',
      nextTab: 'Ctrl+Tab',
      previousTab: 'Ctrl+Shift+Tab',
    };
    for (const [id, keys] of Object.entries(expected)) expect(findCommand(id)?.defaultKeys, id).toBe(keys);
  });

  it('leaves the preset shortcuts and the tab-closing key as they were', () => {
    expect(defaults.chat).toBe('Mod+1');
    expect(defaults.terminal).toBe('Mod+7');
    expect(defaults.closeTab).toBe('Mod+W');
    expect(defaults.reopenTab).toBe('Mod+Shift+T');
    for (const planned of ['split', 'focusGroup1', 'focusGroup2', 'focusGroup3'])
      expect(findCommand(planned)).toBeUndefined();
  });

  it('runs every command through the shell actions and nothing else', () => {
    const calls: string[] = [];
    const actions = new Proxy({} as CommandActions, {
      get: (_target, name) => () => calls.push(String(name)),
    });
    for (const command of commands) {
      calls.length = 0;
      command.run(actions, {source: 'palette', terminalFocused: false});
      if (command.id === 'voiceTalk') expect(calls).toEqual([]);
      else expect(calls.length, command.id).toBeGreaterThan(0);
    }
  });

  it('toggles focus: into the terminal, and back to the conversation from inside it', () => {
    const actions = {openSurface: vi.fn(), focusPanel: vi.fn()} as unknown as CommandActions;
    keyedSpecs.focusTerminal.run(actions, {source: 'shortcut', terminalFocused: false});
    expect(actions.openSurface).toHaveBeenCalledWith('terminal');
    keyedSpecs.focusTerminal.run(actions, {source: 'shortcut', terminalFocused: true});
    expect(actions.focusPanel).toHaveBeenCalledWith('conversation');
  });
});

describe('command palette', () => {
  const agent = {workspace: 'agent' as const, hostAdmin: true};

  it('lists exactly the registry for the context, in registry order, with the stored shortcuts', () => {
    const entries = paletteEntries(agent, {...defaults, palette: 'Mod+Alt+P'}, () => {});
    expect(entries.map(entry => entry.id)).toEqual(commands.map(command => command.id));
    expect(entries.find(entry => entry.id === 'palette')?.shortcut).toBe('Mod+Alt+P');
    expect(entries.find(entry => entry.id === 'lock')?.shortcut).toBeUndefined();
    for (const category of settingsCategories)
      expect(entries.map(entry => entry.id)).toContain('manager-' + category.id);
  });

  it('hides workspace-panel commands outside the Agent workspace and host commands for non-admins', () => {
    const studio = paletteEntries({workspace: 'studio', hostAdmin: false}, defaults, () => {}).map(
      entry => entry.id,
    );
    expect(studio).toEqual(
      commands
        .filter(command => commandAvailable(command, {workspace: 'studio', hostAdmin: false}))
        .map(command => command.id),
    );
    for (const id of [
      'closeTab',
      'right',
      'bottom',
      'focus',
      'browserNew',
      'voiceTalk',
      'nextTab',
      'stop-host',
    ]) {
      expect(studio, id).not.toContain(id);
    }
    for (const id of ['chat', 'left', 'palette', 'lock', 'settings', 'workspace-data'])
      expect(studio, id).toContain(id);
  });

  it('runs the command it lists', () => {
    const run = vi.fn();
    const entries = paletteEntries(agent, defaults, run);
    entries.find(entry => entry.id === 'refresh')?.run();
    expect(run).toHaveBeenCalledWith(findCommand('refresh'));
  });
});

describe('keybinding grammar and warnings', () => {
  it('accepts the backtick, comma, backslash and Tab keys', () => {
    for (const binding of ['Ctrl+`', 'Mod+,', 'Mod+\\', 'Ctrl+Tab', 'Ctrl+Shift+Tab', 'Mod+K Mod+\\']) {
      expect(validBinding(binding), binding).toBe(true);
    }
    expect(validateBindings({chat: 'Ctrl+`', workbench: 'Mod+,'})).toMatchObject({
      chat: 'Ctrl+`',
      workbench: 'Mod+,',
    });
  });

  it('still rejects what it never accepted', () => {
    for (const binding of ['', 'Mod+', 'Mod+[', 'Mod+Enter', 'Mod+Shift', 'x'.repeat(81)]) {
      expect(validBinding(binding), binding).toBe(false);
    }
  });

  it('keeps stored bindings as they are and fills the rest from the defaults', () => {
    const stored = validateBindings({chat: 'Mod+9', bogus: 'Mod+Z'});
    expect(stored.chat).toBe('Mod+9');
    expect(stored.settings).toBe(defaults.settings);
    expect('bogus' in stored).toBe(false);
  });

  it('warns about browser-reserved combinations and suggests an Alt fallback', () => {
    const warnings = bindingWarnings({...defaults, chat: 'Mod+W', workbench: 'Ctrl+Tab', browser: 'Mod+9'});
    expect(
      warnings.some(warning => /^Chat: browsers may reserve Mod\+W\. Try Mod\+Alt\+W/.test(warning)),
    ).toBe(false);
    expect(warnings).toContain(
      'Workbench: browsers may reserve Ctrl+Tab. Try Ctrl+Alt+Tab instead, or use the Commands menu when the shortcut is unavailable.',
    );
    expect(
      warnings.some(warning => /^Browser: browsers may reserve Mod\+9\. Try Mod\+Alt\+9/.test(warning)),
    ).toBe(true);
  });

  it('does not suggest a fallback that another command already has', () => {
    const warnings = bindingWarnings({...defaults, chat: 'Mod+W'});
    const chat = warnings.find(warning => warning.startsWith('Chat: browsers'));
    expect(chat).toBe(
      'Chat: browsers may reserve Mod+W. Use the Commands menu when the shortcut is unavailable.',
    );
  });

  it('warns for the number and Tab combinations the plan names', () => {
    for (const binding of ['Mod+1', 'Mod+9', 'Ctrl+Tab', 'Ctrl+Shift+Tab', 'Mod+T', 'Mod+N']) {
      const warnings = bindingWarnings({...defaults, settings: binding});
      expect(
        warnings.some(warning => warning.startsWith('Open Settings: browsers may reserve ' + binding)),
        binding,
      ).toBe(true);
    }
    expect(
      bindingWarnings({...defaults, settings: 'Mod+,'}).some(warning => warning.startsWith('Open Settings')),
    ).toBe(false);
  });
});

describe('shortcut dispatch and terminal focus', () => {
  const press = (target: Element | Window, init: KeyboardEventInit) => {
    const event = new KeyboardEvent('keydown', {bubbles: true, cancelable: true, ...init});
    target.dispatchEvent(event);
    return event;
  };

  function mount(options: {suspended?: boolean; escape?: () => boolean} = {}) {
    const run = vi.fn();
    renderHook(() =>
      useShortcuts({
        bindings: {...defaults},
        suspended: () => !!options.suspended,
        run,
        escape: options.escape,
      }),
    );
    const page = document.body.appendChild(document.createElement('div'));
    const terminal = document.body.appendChild(document.createElement('div'));
    terminal.className = 'xterm';
    const terminalInput = terminal.appendChild(document.createElement('textarea'));
    const remote = document.body.appendChild(document.createElement('div'));
    remote.className = 'browser-remote-input';
    const remoteInput = remote.appendChild(document.createElement('textarea'));
    return {run, page, terminalInput, remoteInput};
  }

  it('runs bindings anywhere on the page, including the new keys', () => {
    const {run, page} = mount();
    press(page, {key: 'p', metaKey: true, shiftKey: true});
    press(page, {key: ',', metaKey: true});
    press(page, {key: '`', ctrlKey: true});
    press(page, {key: 'Tab', ctrlKey: true});
    press(page, {key: 'Tab', ctrlKey: true, shiftKey: true});
    expect(run.mock.calls).toEqual([
      ['palette', false],
      ['settings', false],
      ['focusTerminal', false],
      ['nextTab', false],
      ['previousTab', false],
    ]);
  });

  it('keeps every key but the allowlist for a focused terminal', () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel');
    const {run, terminalInput} = mount();
    const handled = [
      press(terminalInput, {key: 'p', metaKey: true, shiftKey: true}),
      press(terminalInput, {key: 'b', metaKey: true}),
      press(terminalInput, {key: 'j', metaKey: true}),
      press(terminalInput, {key: 'b', metaKey: true, altKey: true}),
      press(terminalInput, {key: 'Tab', ctrlKey: true}),
      press(terminalInput, {key: 'Tab', ctrlKey: true, shiftKey: true}),
      press(terminalInput, {key: '`', ctrlKey: true}),
    ];
    expect(run.mock.calls.map(([id]) => id)).toEqual([
      'palette',
      'left',
      'bottom',
      'right',
      'nextTab',
      'previousTab',
      'focusTerminal',
    ]);
    expect(run.mock.calls.every(([, terminalFocused]) => terminalFocused === true)).toBe(true);
    expect(handled.every(event => event.defaultPrevented)).toBe(true);

    run.mockClear();
    const passed = [
      press(terminalInput, {key: '1', metaKey: true}),
      press(terminalInput, {key: 'p', metaKey: true}),
      press(terminalInput, {key: 'w', metaKey: true}),
      press(terminalInput, {key: 'k', metaKey: true}),
      press(terminalInput, {key: ',', metaKey: true}),
      press(terminalInput, {key: 'n', metaKey: true, shiftKey: true}),
    ];
    expect(run).not.toHaveBeenCalled();
    expect(passed.some(event => event.defaultPrevented)).toBe(false);
  });

  it('lets Control combinations the shell uses reach the PTY on a Mac', () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('MacIntel');
    const {run, terminalInput, page} = mount();
    const event = press(terminalInput, {key: 'b', ctrlKey: true});
    press(terminalInput, {key: 'j', ctrlKey: true});
    press(terminalInput, {key: 'p', ctrlKey: true, shiftKey: true});
    expect(run).not.toHaveBeenCalled();
    expect(event.defaultPrevented).toBe(false);
    press(page, {key: 'b', ctrlKey: true});
    expect(run).toHaveBeenCalledWith('left', false);
  });

  it('uses Control as the command key in a terminal on other platforms', () => {
    vi.spyOn(navigator, 'platform', 'get').mockReturnValue('Linux x86_64');
    const {run, terminalInput} = mount();
    press(terminalInput, {key: 'b', ctrlKey: true});
    press(terminalInput, {key: 'b', metaKey: true});
    expect(run.mock.calls).toEqual([['left', true]]);
  });

  it('keeps Browser and Computer remote input fully protected', () => {
    const {run, remoteInput} = mount();
    press(remoteInput, {key: 'p', metaKey: true, shiftKey: true});
    press(remoteInput, {key: 'b', metaKey: true});
    press(remoteInput, {key: '`', ctrlKey: true});
    press(remoteInput, {key: 'Tab', ctrlKey: true});
    expect(run).not.toHaveBeenCalled();
  });

  it('does nothing while locked, composing, repeating or already handled', () => {
    const locked = mount({suspended: true});
    press(locked.page, {key: 'p', metaKey: true, shiftKey: true});
    expect(locked.run).not.toHaveBeenCalled();
    document.body.innerHTML = '';
    const {run, page} = mount();
    press(page, {key: 'p', metaKey: true, shiftKey: true, repeat: true});
    press(page, {key: 'p', metaKey: true, shiftKey: true, isComposing: true});
    const claimed = new KeyboardEvent('keydown', {
      key: 'p',
      metaKey: true,
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    });
    claimed.preventDefault();
    page.dispatchEvent(claimed);
    expect(run).not.toHaveBeenCalled();
  });

  it('runs a two-step chord and its single-key meaning only when the chord is not finished', () => {
    vi.useFakeTimers();
    try {
      const {run, page} = mount();
      press(page, {key: 'k', metaKey: true});
      press(page, {key: 'z'});
      expect(run.mock.calls).toEqual([['focus', false]]);
      run.mockClear();
      press(page, {key: 'k', metaKey: true});
      vi.advanceTimersByTime(901);
      expect(run).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('hands Escape to Focus mode on the page but never takes it from a terminal', () => {
    const escape = vi.fn(() => true);
    const {run, page, terminalInput} = mount({escape});
    const onPage = press(page, {key: 'Escape'});
    expect(escape).toHaveBeenCalledTimes(1);
    expect(onPage.defaultPrevented).toBe(true);
    const inTerminal = press(terminalInput, {key: 'Escape'});
    expect(escape).toHaveBeenCalledTimes(1);
    expect(inTerminal.defaultPrevented).toBe(false);
    expect(run).not.toHaveBeenCalled();
  });

  it('exposes the terminal allowlist as the commands that opt in', () => {
    expect(
      keyed
        .filter(command => command.terminal)
        .map(command => command.id)
        .sort(),
    ).toEqual(['bottom', 'focusTerminal', 'left', 'nextTab', 'palette', 'previousTab', 'right']);
  });
});
