export type WorkspaceMode = 'agent' | 'studio' | 'data';
export type StudioSection = 'create' | 'library' | 'assets' | 'settings' | 'music';

export function readStudioSection(search = location.search): StudioSection {
  const section = new URLSearchParams(search).get('studio');
  return section === 'library' || section === 'assets' || section === 'settings' || section === 'music' ? section : 'create';
}

/** Native shells may read query selections without changing their trusted root URL. */
export function readWorkspaceMode(pathname = location.pathname, search = location.search): WorkspaceMode {
  if (pathname === '/manage/data' || new URLSearchParams(search).get('workspace') === 'data') return 'data';
  return pathname === '/manage/media' || new URLSearchParams(search).get('workspace') === 'studio' ? 'studio' : 'agent';
}

/** Keep the selected conversation and detached-window context across workspace history. */
export function workspaceHistoryTarget(manager: string, selected: string, workspace: WorkspaceMode = 'agent', search = '', section: StudioSection = 'create', project = '') {
  const path = manager ? '/manage/' + encodeURIComponent(manager) : workspace !== 'agent' ? '/' : selected ? '/session/' + encodeURIComponent(selected) : '/';
  const params = new URLSearchParams(search);
  params.delete('workspace');
  params.delete('session');
  params.delete('studio');
  params.delete('project');
  if (project && !selected) params.set('project', project);
  if (workspace !== 'data') {params.delete('data_workspace');params.delete('data_view');params.delete('data_report');params.delete('data_source');}
  if (workspace === 'data') {params.set('workspace', 'data');if (selected) params.set('session', selected);}
  if (workspace === 'studio') {
    params.set('workspace', 'studio');
    if (section !== 'create') params.set('studio', section);
    if (selected) params.set('session', selected);
  }
  const query = params.toString();
  return path + (query ? '?' + query : '');
}
