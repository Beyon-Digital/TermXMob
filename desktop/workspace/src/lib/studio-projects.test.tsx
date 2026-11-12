import {act, cleanup, renderHook, waitFor} from '@testing-library/react';
import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {useStudioProjects, type StudioProject} from './studio-projects';
const api = vi.hoisted(() => ({request: vi.fn()}));
vi.mock('./api', () => ({request: api.request, json: (method: string, data: unknown) => ({method, body: JSON.stringify(data)})}));
const project: StudioProject = {id: 'studio_one', name: 'Film', description: '', revision: 1, archived: false, created_at: 1, updated_at: 1};
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());
function deferred<T>() {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done;}); return {resolve, promise};}

it('creates a folder-free collection and sends the last known revision for edits and archiving', async () => {
  api.request.mockResolvedValueOnce({projects: []}).mockResolvedValueOnce(project).mockResolvedValueOnce({...project, name: 'Renamed', revision: 2}).mockResolvedValueOnce({...project, name: 'Renamed', revision: 3, archived: true});
  const {result} = renderHook(() => useStudioProjects({ownerId: 'alice'}));
  await waitFor(() => expect(result.current.loading).toBe(false));
  await act(async () => {await result.current.create({name: 'Film', description: ''});});
  expect(result.current.projects).toEqual([project]);
  await act(async () => {await result.current.update(result.current.projects[0], {name: 'Renamed', description: ''});});
  await act(async () => {await result.current.archive(result.current.projects[0]);});
  expect(api.request.mock.calls.slice(1).map(([path, init]) => [path, init.method, JSON.parse(init.body)])).toEqual([
    ['/api/media/studio/projects', 'POST', {name: 'Film', description: ''}],
    ['/api/media/studio/projects/studio_one', 'PUT', {name: 'Renamed', description: '', revision: 1}],
    ['/api/media/studio/projects/studio_one/archive', 'POST', {revision: 2}],
  ]);
  expect(result.current.projects[0].archived).toBe(true);
});

it('hides previous owner projects immediately and ignores their late list response', async () => {
  const old = deferred<{projects: StudioProject[]}>();
  api.request.mockReturnValueOnce(old.promise).mockResolvedValueOnce({projects: [{...project, id: 'studio_bob', name: 'Bob film'}]});
  const {result, rerender} = renderHook(({ownerId}) => useStudioProjects({ownerId}), {initialProps: {ownerId: 'alice'}});
  rerender({ownerId: 'bob'});
  expect(result.current.projects).toEqual([]);
  await waitFor(() => expect(result.current.projects[0]?.id).toBe('studio_bob'));
  await act(async () => {old.resolve({projects: [project]});});
  expect(result.current.projects.map(row => row.id)).toEqual(['studio_bob']);
});

it('aborts owner-changing mutations and rejects stale success instead of selecting another owner project', async () => {
  const pending = deferred<StudioProject>();
  api.request.mockResolvedValueOnce({projects: []}).mockReturnValueOnce(pending.promise).mockResolvedValueOnce({projects: []});
  const {result, rerender} = renderHook(({ownerId}) => useStudioProjects({ownerId}), {initialProps: {ownerId: 'alice'}});
  await waitFor(() => expect(result.current.loading).toBe(false));
  let outcome!: Promise<unknown>;
  act(() => {outcome = result.current.create({name: 'Film', description: ''}).catch(reason => reason);});
  const signal = api.request.mock.calls[1][1].signal;
  rerender({ownerId: 'bob'});
  expect(signal.aborted).toBe(true);
  await act(async () => {pending.resolve(project);});
  expect(await outcome).toMatchObject({name: 'AbortError'});
  expect(result.current.projects).toEqual([]);
  expect(result.current.busy).toBe(false);
});

it('keeps a failed edit visible until an explicit list refresh reconciles it', async () => {
  api.request.mockResolvedValueOnce({projects: [project]}).mockRejectedValueOnce(new Error('Resource changed; reload')).mockResolvedValueOnce({projects: [{...project, name: 'Other window', revision: 2}]});
  const {result} = renderHook(() => useStudioProjects({ownerId: 'alice'}));
  await waitFor(() => expect(result.current.projects).toEqual([project]));
  await act(async () => {await expect(result.current.update(project, {name: 'Mine', description: ''})).rejects.toThrow('reload');});
  expect(result.current.projects).toEqual([project]);
  act(() => result.current.reload());
  await waitFor(() => expect(result.current.projects[0].revision).toBe(2));
});


it('never lets an older list erase a just-created collection', async () => {
  const stale = deferred<{projects: StudioProject[]}>();
  api.request.mockReturnValueOnce(stale.promise).mockResolvedValueOnce(project).mockResolvedValueOnce({projects: [project]});
  const {result} = renderHook(() => useStudioProjects({ownerId: 'alice'}));
  await act(async () => {await result.current.create({name: 'Film', description: ''});});
  await act(async () => {stale.resolve({projects: []});});
  expect(result.current.projects).toEqual([project]);
  await waitFor(() => expect(api.request).toHaveBeenCalledTimes(3));
});

it('ignores a mutation receipt when Studio is hidden or locked and reloads on return', async () => {
  const pending = deferred<StudioProject>();
  api.request.mockResolvedValueOnce({projects: []}).mockReturnValueOnce(pending.promise).mockResolvedValueOnce({projects: [project]});
  const {result, rerender} = renderHook(({enabled}) => useStudioProjects({ownerId: 'alice', enabled}), {initialProps: {enabled: true}});
  await waitFor(() => expect(result.current.loading).toBe(false));
  let outcome!: Promise<unknown>;
  act(() => {outcome = result.current.create({name: 'Film', description: ''}).catch(reason => reason);});
  rerender({enabled: false});
  await act(async () => {pending.resolve(project);});
  expect(await outcome).toMatchObject({name: 'AbortError'});
  expect(result.current.projects).toEqual([]);
  rerender({enabled: true});
  await waitFor(() => expect(result.current.projects).toEqual([project]));
});
