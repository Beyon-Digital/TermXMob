import {afterEach, describe, expect, it} from 'vitest';
import {allTabs, editorGroups, validateLayout, type Group} from '../lib/layout/model';
import {
  SHELL_ENGINE_KEY,
  readShellEngine,
  resetShellEngineMemory,
  storedShellEngine,
  urlShellEngine,
  writeShellEngine,
} from '../lib/shell-engine';
import {managerReturnLabel, pageBehindSettings, statusContext, statusContextPath, isControlPage, runningBrowserTaskId} from './shell-context';
import {managerTab, oneTabLayout, resolveTabRef} from './use-tree-layout';

afterEach(() => {
  localStorage.clear();
  resetShellEngineMemory();
});

describe('the shell engine flag', () => {
  it('reads ?shell=ide as the tab layout and ?shell=classic or legacy as the classic dock', () => {
    expect(urlShellEngine('?shell=ide')).toBe('tree');
    expect(urlShellEngine('?shell=classic')).toBe('legacy');
    expect(urlShellEngine('?shell=legacy')).toBe('legacy');
    expect(urlShellEngine('?shell=other')).toBeNull();
    expect(urlShellEngine('')).toBeNull();
  });

  it('defaults to the IDE layout, follows the stored choice, and lets the address win', () => {
    expect(readShellEngine('', localStorage)).toBe('tree');
    localStorage.setItem(SHELL_ENGINE_KEY, 'off');
    expect(storedShellEngine(localStorage)).toBe('legacy');
    expect(readShellEngine('', localStorage)).toBe('legacy');
    expect(readShellEngine('?shell=ide', localStorage)).toBe('tree');
    localStorage.setItem(SHELL_ENGINE_KEY, 'maybe');
    expect(readShellEngine('', localStorage)).toBe('tree');
    // A value stored by the earlier opt-in preview still means the IDE layout.
    localStorage.setItem(SHELL_ENGINE_KEY, 'on');
    expect(readShellEngine('', localStorage)).toBe('tree');
    expect(readShellEngine('?shell=classic', localStorage)).toBe('legacy');
  });

  it('writes and clears the stored choice, and survives storage that throws', () => {
    writeShellEngine('legacy', localStorage);
    expect(localStorage.getItem(SHELL_ENGINE_KEY)).toBe('off');
    writeShellEngine('tree', localStorage);
    expect(localStorage.getItem(SHELL_ENGINE_KEY)).toBeNull();
    const broken = {
      getItem: () => {
        throw new Error('blocked');
      },
      setItem: () => {
        throw new Error('blocked');
      },
      removeItem: () => {
        throw new Error('blocked');
      },
    };
    expect(storedShellEngine(broken)).toBe('tree');
    expect(() => writeShellEngine('legacy', broken)).not.toThrow();
  });

  it('is named so a person can find and clear it', () => {
    expect(SHELL_ENGINE_KEY).toBe('termx-new-workspace-layout');
  });
});

describe('tab refs and manager pages', () => {
  it('keeps tabs without a ref and checks the ones that have one', () => {
    expect(resolveTabRef({id: 'a', kind: 'file'})).toBe(true);
    expect(resolveTabRef({id: 'a', kind: 'settings', ref: 'models'})).toBe(true);
    expect(resolveTabRef({id: 'a', kind: 'settings', ref: 'general'})).toBe(true);
    expect(resolveTabRef({id: 'a', kind: 'settings', ref: 'nonsense'})).toBe(false);
    expect(resolveTabRef({id: 'a', kind: 'projects', ref: 'tasks'})).toBe(true);
    expect(resolveTabRef({id: 'a', kind: 'projects', ref: 'models'})).toBe(false);
    expect(resolveTabRef({id: 'a', kind: 'workflows', ref: 'a'.repeat(32)})).toBe(true);
    expect(resolveTabRef({id: 'a', kind: 'workflows', ref: 'not a run'})).toBe(false);
  });

  it('maps a manager page to a tab kind and ref', () => {
    expect(managerTab('')).toBeNull();
    expect(managerTab('workflows')).toEqual({kind: 'workflows'});
    expect(managerTab('projects')).toEqual({kind: 'projects', ref: 'projects'});
    expect(managerTab('overview')).toEqual({kind: 'projects', ref: 'overview'});
    expect(managerTab('tasks')).toEqual({kind: 'projects', ref: 'tasks'});
    expect(managerTab('models')).toEqual({kind: 'settings', ref: 'models'});
    // Aliases resolve to their category.
    expect(managerTab('appearance')).toEqual({kind: 'settings', ref: 'preferences'});
    expect(managerTab('mcp')).toEqual({kind: 'settings', ref: 'connections'});
  });

  it('builds the one-tab layout a detached window opens with', () => {
    const layout = oneTabLayout('file');
    expect(editorGroups(layout.root)).toHaveLength(1);
    expect(allTabs(layout).filter(tab => tab.kind === 'file')).toHaveLength(1);
    expect((layout.root as Group).tabs.map(tab => tab.kind)).toEqual(['file']);
    expect(layout.sideBar.visible).toBe(false);
    expect(layout.panel.visible).toBe(false);
    expect(validateLayout(layout)).toEqual(layout);
  });
});

describe('shell context', () => {
  const base = {
    workspace: 'agent' as const,
    manager: '',
    previousManager: '',
    session: null,
    selected: '',
    project: null,
    projectWorkbenchId: '',
    sessionsState: 'ready',
    studioProject: null,
    studioProjectId: '',
  };

  it('names the way back from a manager page', () => {
    expect(managerReturnLabel(base)).toBe('Back to chat');
    expect(managerReturnLabel({...base, projectWorkbenchId: 'p'})).toBe('Back to files');
    expect(managerReturnLabel({...base, projectWorkbenchId: 'p', selected: 's'})).toBe('Back to chat');
    expect(managerReturnLabel({...base, workspace: 'data'})).toBe('Back to Data');
    expect(managerReturnLabel({...base, workspace: 'studio'})).toBe('Back to Studio');
    expect(managerReturnLabel({...base, manager: 'models', previousManager: 'projects'})).toMatch(/^Back to /);
  });

  it('shows the project, or why there is none', () => {
    expect(statusContext(base)).toBe('No project open');
    expect(statusContext({...base, sessionsState: 'loading'})).toBe('Loading project context…');
    expect(statusContext({...base, projectWorkbenchId: 'p'})).toBe('Project unavailable');
    expect(statusContext({...base, workspace: 'data'})).toBe('Data workspace');
    expect(statusContext({...base, workspace: 'studio'})).toBe('No project');
    expect(statusContext({...base, workspace: 'studio', studioProjectId: 'x'})).toBe('Project unavailable');
    expect(statusContext({...base, workspace: 'studio', studioProject: {name: 'Films'}})).toBe('Films');
    expect(statusContextPath({...base, workspace: 'data'})).toBeUndefined();
    expect(statusContextPath({...base, workspace: 'studio', studioProject: {name: 'Films', path: '/films'}})).toBe('/films');
  });

  it('tells control pages from other pages and finds the page behind settings', () => {
    expect(isControlPage('overview')).toBe(true);
    expect(isControlPage('tasks')).toBe(true);
    expect(isControlPage('models')).toBe(false);
    expect(pageBehindSettings('models', 'projects')).toBe('projects');
    expect(pageBehindSettings('projects', 'tasks')).toBe('projects');
    expect(runningBrowserTaskId(null)).toBeNull();
  });
});
