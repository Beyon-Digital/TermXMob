import {renderHook} from '@testing-library/react';
import {afterEach, describe, expect, it, vi} from 'vitest';
import {SETTINGS_LEAVE} from '../features/settings-forms';
import {
  routeInitialLayout,
  routeLayout,
  routeManager,
  routeWorkbenchProject,
  useWorkspacePopstate,
  workspaceRoute,
} from './routes';

afterEach(() => {
  history.replaceState(null, '', '/');
  vi.restoreAllMocks();
});

describe('workspaceRoute', () => {
  it('reads a conversation from the path, then from the detached-window query', () => {
    expect(workspaceRoute('/session/abc%20def', '')).toEqual({selected: 'abc def', manager: '', project: ''});
    expect(workspaceRoute('/', '?session=s1&project=p1')).toEqual({selected: 's1', manager: '', project: 'p1'});
    expect(workspaceRoute('/session/path', '?session=query').selected).toBe('path');
  });

  it('reads manager pages from /manage/<category>', () => {
    expect(workspaceRoute('/manage/projects', '').manager).toBe('projects');
    expect(workspaceRoute('/manage/access%20keys', '').manager).toBe('access keys');
    expect(workspaceRoute('/elsewhere', '').manager).toBe('');
  });
});

describe('route helpers', () => {
  it('treats the legacy media and data pages as workspace modes', () => {
    expect(routeManager({manager: 'media'})).toBe('');
    expect(routeManager({manager: 'data'})).toBe('');
    expect(routeManager({manager: 'workflows'})).toBe('workflows');
  });

  it('opens a project as files only while no conversation is selected', () => {
    expect(routeWorkbenchProject({selected: '', project: 'p1'})).toBe('p1');
    expect(routeWorkbenchProject({selected: 's1', project: 'p1'})).toBe('');
  });

  it('accepts only the known panel arrangements from ?layout=', () => {
    expect(routeLayout('?layout=terminal')).toBe('terminal');
    expect(routeLayout('?layout=focus')).toBeNull();
    expect(routeLayout('?layout=nonsense')).toBeNull();
    expect(routeLayout('')).toBeNull();
  });

  it('lets a project route win over the layout parameter', () => {
    const none = {selected: '', manager: '', project: ''};
    expect(routeInitialLayout({...none, project: 'p1'}, '?layout=browser')).toBe('workbench');
    expect(routeInitialLayout({...none, project: 'p1', selected: 's1'}, '?layout=browser')).toBe('browser');
    expect(routeInitialLayout(none, '')).toBeNull();
  });
});

describe('useWorkspacePopstate', () => {
  const options = (onRoute = vi.fn()) => ({
    manager: '',
    selected: 's1',
    workspace: 'agent' as const,
    studioSection: 'create' as const,
    projectWorkbenchId: '',
    onRoute,
  });

  it('applies the route the browser went to', () => {
    history.replaceState(null, '', '/session/s2?studio=music');
    const onRoute = vi.fn();
    renderHook(() => useWorkspacePopstate(options(onRoute)));
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(onRoute).toHaveBeenCalledTimes(1);
    expect(onRoute).toHaveBeenCalledWith({
      route: {selected: 's2', manager: '', project: ''},
      mode: 'agent',
      studioSection: 'music',
    });
  });

  it('puts the URL back and waits when unsaved settings veto the navigation', () => {
    history.replaceState(null, '', '/session/s2');
    const onRoute = vi.fn();
    let proceed: (() => void) | undefined;
    const veto = (event: Event) => {
      event.preventDefault();
      proceed = (event as CustomEvent<{proceed: () => void}>).detail.proceed;
    };
    window.addEventListener(SETTINGS_LEAVE, veto);
    renderHook(() => useWorkspacePopstate(options(onRoute)));
    window.dispatchEvent(new PopStateEvent('popstate'));
    window.removeEventListener(SETTINGS_LEAVE, veto);
    expect(onRoute).not.toHaveBeenCalled();
    expect(location.pathname).toBe('/session/s1');
    proceed?.();
    expect(onRoute).toHaveBeenCalledTimes(1);
  });

  it('calls the latest handler without re-registering the listener', () => {
    const add = vi.spyOn(window, 'addEventListener');
    const first = vi.fn();
    const second = vi.fn();
    const {rerender} = renderHook(({handler}) => useWorkspacePopstate(options(handler)), {
      initialProps: {handler: first},
    });
    rerender({handler: second});
    window.dispatchEvent(new PopStateEvent('popstate'));
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
    expect(add.mock.calls.filter(([type]) => type === 'popstate')).toHaveLength(1);
  });
});
