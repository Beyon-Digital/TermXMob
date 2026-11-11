import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {json, request} from './api';

/** Studio collections never register or delete an Agent workspace folder. */
export type StudioProject = {
  id: string; name: string; description: string; revision: number; archived: boolean;
  created_at: number; updated_at: number;
};
export type StudioProjectValues = {name: string; description: string};
export type StudioProjectsController = {
  projects: StudioProject[]; loading: boolean; error: string; busy: boolean;
  reload: () => void;
  create: (values: StudioProjectValues) => Promise<StudioProject>;
  update: (project: StudioProject, values: StudioProjectValues) => Promise<StudioProject>;
  archive: (project: StudioProject) => Promise<StudioProject>;
  restore: (project: StudioProject) => Promise<StudioProject>;
};
const base = '/api/media/studio/projects';
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);
const interrupted = () => new DOMException('Project request interrupted. Refresh projects before continuing.', 'AbortError');

export function useStudioProjects({ownerId, enabled = true}: {ownerId?: string; enabled?: boolean}): StudioProjectsController {
  const [snapshot, setSnapshot] = useState<{owner?: string; projects: StudioProject[]}>({projects: []});
  const [loading, setLoading] = useState(false), [error, setError] = useState(''), [busy, setBusy] = useState(false), [revision, setRevision] = useState(0);
  const epoch = useRef(0), owner = useRef(ownerId), active = useRef(enabled), mounted = useRef(false);
  const mutation = useRef<AbortController | null>(null), completedMutations = useRef(0);
  owner.current = ownerId; active.current = enabled;
  useLayoutEffect(() => {
    mounted.current = true; epoch.current++; mutation.current?.abort(); mutation.current = null;
    setSnapshot({owner: ownerId, projects: []}); setError(''); setBusy(false); setLoading(false);
    return () => {mounted.current = false; epoch.current++; mutation.current?.abort(); mutation.current = null;};
  }, [ownerId]);
  useEffect(() => {
    if (!ownerId || !enabled) {mutation.current?.abort(); mutation.current = null; setBusy(false); setLoading(false); return;}
    const token = epoch.current, atMutation = completedMutations.current, controller = new AbortController(); let live = true;
    setLoading(true); setError('');
    void request<{projects: StudioProject[]}>(base, {signal: controller.signal}).then(result => {
      if (!live || !mounted.current || token !== epoch.current || owner.current !== ownerId) return;
      // An earlier list must never erase a project just created or edited here.
      if (atMutation !== completedMutations.current) {setRevision(value => value + 1); return;}
      setSnapshot({owner: ownerId, projects: result.projects});
    }).catch(reason => {if (live && !controller.signal.aborted && token === epoch.current) setError(message(reason));})
      .finally(() => {if (live && token === epoch.current) setLoading(false);});
    return () => {live = false; controller.abort();};
  }, [ownerId, enabled, revision]);
  const reload = useCallback(() => setRevision(value => value + 1), []);
  const mutate = useCallback(async (path: string, method: string, values: object) => {
    const principal = owner.current, token = epoch.current;
    if (!principal || !active.current || !mounted.current) throw interrupted();
    if (mutation.current) throw new Error('Wait for the current project change to finish.');
    const controller = new AbortController(); mutation.current = controller; setBusy(true);
    try {
      const project = await request<StudioProject>(path, {...json(method, values), signal: controller.signal});
      if (controller.signal.aborted || !active.current || !mounted.current || token !== epoch.current || owner.current !== principal) throw interrupted();
      completedMutations.current++;
      setSnapshot(current => ({owner: principal, projects: [project, ...(current.owner === principal ? current.projects : []).filter(row => row.id !== project.id)]}));
      return project;
    } finally {
      if (mutation.current === controller) {mutation.current = null; if (mounted.current && token === epoch.current) setBusy(false);}
    }
  }, []);
  return {
    projects: snapshot.owner === ownerId ? snapshot.projects : [], loading, error, busy, reload,
    create: values => mutate(base, 'POST', values),
    update: (project, values) => mutate(base + '/' + encodeURIComponent(project.id), 'PUT', {...values, revision: project.revision}),
    archive: project => mutate(base + '/' + encodeURIComponent(project.id) + '/archive', 'POST', {revision: project.revision}),
    restore: project => mutate(base + '/' + encodeURIComponent(project.id) + '/restore', 'POST', {revision: project.revision}),
  };
}
