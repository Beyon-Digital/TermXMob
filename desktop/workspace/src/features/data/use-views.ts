import {useCallback, useEffect, useMemo, useRef, useState} from 'react';
import {dataError} from '../../lib/data-studio';
import {listViews, type ViewSummary} from '../../lib/data-views';

export type ViewsStatus = 'idle' | 'loading' | 'ready' | 'error';
export type ViewsController = {
  views: ViewSummary[]; status: ViewsStatus; error: string; refresh: () => void;
  /** Add or replace one view after a save, without waiting for a reload. */ upsert: (view: ViewSummary) => void; remove: (id: string) => void;
  byId: (id: string) => ViewSummary | undefined; nameOf: (id: string) => string | undefined;
};
/** The saved views of one workspace. A scope change aborts the request in flight and drops the old list. */
export function useViews(workspaceId: string, enabled: boolean): ViewsController {
  const [state, setState] = useState<{workspaceId: string; views: ViewSummary[]; status: ViewsStatus; error: string}>({workspaceId, views: [], status: 'idle', error: ''}), [tick, setTick] = useState(0), ticket = useRef(0);
  useEffect(() => {
    if (!workspaceId || !enabled) {setState({workspaceId, views: [], status: 'idle', error: ''}); return;}
    const abort = new AbortController(), mine = ++ticket.current;
    setState(previous => ({workspaceId, views: previous.workspaceId === workspaceId ? previous.views : [], status: 'loading', error: ''}));
    listViews(workspaceId, {}, abort.signal).then(result => {
      if (!abort.signal.aborted && mine === ticket.current) setState({workspaceId, views: result.views, status: 'ready', error: ''});
    }).catch(reason => {
      if (!abort.signal.aborted && mine === ticket.current) setState(previous => ({workspaceId, views: previous.views, status: 'error', error: dataError(reason)}));
    });
    return () => abort.abort();
  }, [workspaceId, enabled, tick]);
  const refresh = useCallback(() => setTick(value => value + 1), []);
  const upsert = useCallback((view: ViewSummary) => setState(previous => ({...previous, views: previous.views.some(row => row.id === view.id) ? previous.views.map(row => row.id === view.id ? view : row) : [...previous.views, view]})), []);
  const remove = useCallback((id: string) => setState(previous => ({...previous, views: previous.views.filter(row => row.id !== id)})), []);
  const views = state.workspaceId === workspaceId ? state.views : [];
  const byId = useCallback((id: string) => views.find(row => row.id === id), [views]);
  const nameOf = useCallback((id: string) => views.find(row => row.id === id)?.name, [views]);
  return useMemo(() => ({views, status: state.workspaceId === workspaceId ? state.status : 'idle', error: state.error, refresh, upsert, remove, byId, nameOf}), [views, state, workspaceId, refresh, upsert, remove, byId, nameOf]);
}
