import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {HostPathField, HostPathPicker, type PathListing} from './HostPathPicker';
import AddProjectDialog from './AddProjectDialog';
const {request, gql} = vi.hoisted(() => ({request: vi.fn(), gql: vi.fn()}));
vi.mock('../lib/api', () => ({request, gql}));
beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);
const listing = (path = '/fixture', entries: PathListing['entries'] = [{name: 'app', path: '/fixture/app', dir: true}]): PathListing => ({path, entries, parent: path === '/' ? null : '/', breadcrumbs: [{name: '/', path: '/'}, {name: 'fixture', path: '/fixture'}], next_offset: null, total: entries.length, home: '/fixture'});
const params = (call = request.mock.calls.at(-1)) => new URL('http://fixture' + call![0]).searchParams;
function deferred<T>() {let resolve!: (value: T) => void; const promise = new Promise<T>(done => {resolve = done;}); return {promise, resolve};}

it('browses real listing paths and chooses the opened folder without typing a path', async () => {
  request.mockImplementation(async url => params([url]).get('path') === '/fixture/app' ? listing('/fixture/app', []) : listing());
  const choose = vi.fn(), close = vi.fn();
  render(<HostPathPicker open onOpenChange={close} onChoose={choose}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Open folder app'}));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Choose folder'})).toBeEnabled());
  expect(params().get('path')).toBe('/fixture/app');
  fireEvent.click(screen.getByRole('button', {name: 'Choose folder'}));
  expect(choose).toHaveBeenCalledWith('/fixture/app'); expect(close).toHaveBeenCalledWith(false);
});

it('navigates parent and breadcrumb paths supplied by the host', async () => {
  request.mockImplementation(async url => listing(params([url]).get('path') || '/fixture'));
  render(<HostPathPicker open onOpenChange={vi.fn()} onChoose={vi.fn()}/>);
  await screen.findByRole('button', {name: 'Open folder app'});
  fireEvent.click(screen.getByRole('button', {name: 'Parent folder'}));
  await waitFor(() => expect(params().get('path')).toBe('/'));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Choose folder'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button', {name: 'Home folder'}));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(3));
  await waitFor(() => expect(screen.getByRole('button', {name: 'Choose folder'})).toBeEnabled());
  fireEvent.click(within(screen.getByRole('navigation', {name: 'Folder breadcrumbs'})).getByRole('button', {name: '/'}));
  await waitFor(() => expect(request).toHaveBeenCalledTimes(4));
});

it('filters folders, includes hidden entries on request and loads subsequent pages', async () => {
  request.mockImplementation(async url => ({...listing(), entries: params([url]).get('offset') === '200' ? [{name: 'next', path: '/fixture/next', dir: true}] : [{name: 'app', path: '/fixture/app', dir: true}], next_offset: params([url]).get('offset') === '200' ? null : 200}));
  render(<HostPathPicker open onOpenChange={vi.fn()} onChoose={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button', {name: 'Show more'}));
  await screen.findByRole('button', {name: 'Open folder next'});
  expect(screen.getByRole('button', {name: 'Open folder app'})).toBeVisible();
  expect(params().get('offset')).toBe('200');
  fireEvent.change(screen.getByLabelText('Filter folders'), {target: {value: 'ap'}});
  await waitFor(() => expect(params().get('query')).toBe('ap'));
  expect(params().get('offset')).toBe('0');
  fireEvent.click(screen.getByLabelText('Show hidden'));
  await waitFor(() => expect(params().get('show_hidden')).toBe('true'));
});

it('selects a file from a confined conversation checkout and never offers host home', async () => {
  request.mockResolvedValue({...listing('', [{name: 'main.ts', path: 'src/main.ts', dir: false}]), parent: null, breadcrumbs: [{name: 'project', path: ''}], home: null});
  const choose = vi.fn();
  render(<HostPathPicker kind="file" projectId="project" sessionId="conversation" worktreeId="tree" ownerId="owner" open onOpenChange={vi.fn()} onChoose={choose}/>);
  expect(screen.queryByRole('button', {name: 'Home folder'})).toBeNull();
  expect(screen.getByRole('button', {name: 'Choose file'})).toBeDisabled();
  fireEvent.click(await screen.findByRole('button', {name: 'Select file main.ts'}));
  expect(screen.getByRole('button', {name: 'Select file main.ts'})).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', {name: 'Choose file'}));
  expect(choose).toHaveBeenCalledWith('src/main.ts');
  expect(Object.fromEntries(params())).toMatchObject({project_id: 'project', session_id: 'conversation', worktree_id: 'tree', kind: 'file', path: ''});
});

it('shows a recoverable inline error and keeps selection disabled until retry succeeds', async () => {
  request.mockRejectedValueOnce(new Error('This folder is not accessible')).mockResolvedValue(listing());
  render(<HostPathPicker open onOpenChange={vi.fn()} onChoose={vi.fn()}/>);
  expect(await screen.findByRole('alert')).toHaveTextContent('not accessible');
  expect(screen.getByRole('button', {name: 'Choose folder'})).toBeDisabled();
  fireEvent.click(screen.getByRole('button', {name: 'Try again'}));
  await screen.findByRole('button', {name: 'Open folder app'});
  expect(screen.getByRole('button', {name: 'Choose folder'})).toBeEnabled();
});

it('discards listings from a previous owner and clears the selected file synchronously', async () => {
  const old = deferred<PathListing>(), next = deferred<PathListing>();
  request.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
  const props = {open: true, onOpenChange: vi.fn(), onChoose: vi.fn(), kind: 'file' as const, projectId: 'project'};
  const view = render(<HostPathPicker {...props} ownerId="first"/>);
  view.rerender(<HostPathPicker {...props} ownerId="second"/>);
  await act(async () => old.resolve(listing('/private-first', [{name: 'private.txt', path: 'private.txt', dir: false}])));
  expect(screen.queryByText('private.txt')).toBeNull();
  await act(async () => next.resolve(listing('', [])));
  expect(screen.getByRole('button', {name: 'Choose file'})).toBeDisabled();
});

it('clears and closes a pending picker when locked and fences its late response', async () => {
  const pending = deferred<PathListing>(); request.mockReturnValue(pending.promise);
  const close = vi.fn(); const props = {open: true, onOpenChange: close, onChoose: vi.fn()};
  const view = render(<HostPathPicker {...props}/>);
  act(() => window.dispatchEvent(new Event('termx-session-locked')));
  expect(close).toHaveBeenCalledWith(false);
  view.rerender(<HostPathPicker {...props} open={false}/>);
  await act(async () => pending.resolve(listing('/private-first')));
  expect(screen.queryByRole('dialog')).toBeNull();
});

it('offers browse first, keeps manual entry optional, and restores focus after Escape', async () => {
  request.mockResolvedValue(listing()); const user = userEvent.setup();
  render(<HostPathField value="" onChange={vi.fn()}/>);
  const browse = screen.getByRole('button', {name: 'Browse folders'});
  expect(screen.getByLabelText('Folder path')).not.toBeVisible();
  await user.click(browse); await screen.findByRole('button', {name: 'Open folder app'});
  await user.keyboard('{Escape}'); await waitFor(() => expect(browse).toHaveFocus());
  expect(screen.queryByRole('dialog')).toBeNull();
  await user.click(screen.getByText('Enter path')); expect(screen.getByLabelText('Folder path')).toBeVisible();
});

it('registers a folder selected from the picker', async () => {
  request.mockResolvedValue(listing()); const project = {id: 'p1', name: 'fixture', path: '/fixture'};
  gql.mockResolvedValue({register_project: project}); const registered = vi.fn();
  render(<AddProjectDialog open ownerId="owner" onOpenChange={vi.fn()} onRegistered={registered}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Browse folders'}));
  const picker = await screen.findByRole('dialog', {name: 'Choose folder'});
  await within(picker).findByRole('button', {name: 'Open folder app'});
  fireEvent.click(within(picker).getByRole('button', {name: 'Choose folder'}));
  fireEvent.click(screen.getByRole('button', {name: 'Add project'}));
  await waitFor(() => expect(registered).toHaveBeenCalledWith(project, expect.any(Function)));
  expect(gql.mock.calls[0][1]).toEqual({input: {path: '/fixture', name: ''}});
});
