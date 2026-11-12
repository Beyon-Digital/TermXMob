import {useState} from 'react';
import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AddProjectDialog, {isAbsoluteHostPath, sameHostPath} from './AddProjectDialog';

const gql = vi.hoisted(() => vi.fn());
vi.mock('../lib/api', () => ({gql}));
afterEach(cleanup);
beforeEach(() => vi.resetAllMocks());

const project = {id: 'p1', name: 'app', path: '/Users/a/app'};
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function fillFolder(path = project.path) {
  fireEvent.change(screen.getByLabelText('Folder path'), {target: {value: path}});
}
function submit() {
  fireEvent.submit(screen.getByLabelText('Folder path').closest('form')!);
}

describe('add project dialog', () => {
  it('accepts only absolute host paths', () => {
    for (const ok of ['/Users/a/app', '~/code', 'C:\\code\\app', 'D:/code', '\\\\host\\share']) expect(isAbsoluteHostPath(ok)).toBe(true);
    for (const bad of ['app', './app', '../x', '', '  ']) expect(isAbsoluteHostPath(bad)).toBe(false);
  });

  it('compares host path spelling without changing POSIX case or resolving host aliases', () => {
    expect(sameHostPath('/Users/a/app/', '/Users/a/app')).toBe(true);
    expect(sameHostPath('C:\\Code\\app\\', 'c:/code/app')).toBe(true);
    expect(sameHostPath('\\\\Host\\Share\\App', '\\\\host\\share\\app\\')).toBe(true);
    expect(sameHostPath('/Code/app', '/code/app')).toBe(false);
    expect(sameHostPath('/code/a\\b', '/code/a/b')).toBe(false);
    expect(sameHostPath('/code/link/../app', '/code/app')).toBe(false);
    expect(sameHostPath('~/app', '/Users/a/app')).toBe(false);
    expect(sameHostPath('', '/')).toBe(false);
  });

  it('registers the folder and reports the project with a current navigation guard', async () => {
    gql.mockResolvedValue({register_project: project});
    const onRegistered = vi.fn(), onOpenChange = vi.fn();
    render(<AddProjectDialog open ownerId="owner" onOpenChange={onOpenChange} onRegistered={onRegistered}/>);
    expect(screen.getByRole('button', {name: 'Add project'})).toBeDisabled();
    fillFolder(' /Users/a/app ');
    fireEvent.change(screen.getByLabelText(/Name/), {target: {value: ' My app '}});
    fireEvent.click(screen.getByRole('button', {name: 'Add project'}));
    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith(project, expect.any(Function)));
    expect(gql.mock.calls[0][1]).toEqual({input: {path: '/Users/a/app', name: 'My app'}});
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(onRegistered.mock.calls[0][1]()).toBe(false);
  });

  it('explains a relative path without calling the host', () => {
    render(<AddProjectDialog open onOpenChange={vi.fn()} onRegistered={vi.fn()}/>);
    fillFolder('app');
    fireEvent.click(screen.getByRole('button', {name: 'Add project'}));
    expect(screen.getByRole('alert')).toHaveTextContent('full folder path');
    expect(gql).not.toHaveBeenCalled();
  });

  it('retains the draft and allows retry after a host registration error', async () => {
    gql.mockRejectedValueOnce(new Error('Folder does not exist')).mockResolvedValueOnce({register_project: project});
    const onOpenChange = vi.fn();
    render(<AddProjectDialog open onOpenChange={onOpenChange} onRegistered={vi.fn()}/>);
    fillFolder('/missing');
    fireEvent.change(screen.getByLabelText(/Name/), {target: {value: 'My app'}});
    submit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Folder does not exist');
    expect(screen.getByLabelText('Folder path')).toHaveValue('/missing');
    expect(screen.getByLabelText(/Name/)).toHaveValue('My app');
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByRole('button', {name: 'Add project'})).toBeEnabled();
    submit();
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
    expect(gql).toHaveBeenCalledTimes(2);
  });

  it('blocks duplicate submits and dismissal while keeping pending focus in the dialog', async () => {
    const request = deferred<{register_project: typeof project}>();
    gql.mockReturnValue(request.promise);
    const onOpenChange = vi.fn(), user = userEvent.setup();
    render(<AddProjectDialog open onOpenChange={onOpenChange} onRegistered={vi.fn()}/>);
    fillFolder();
    act(() => { submit(); submit(); });
    expect(gql).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('Folder path')).toBeDisabled();
    expect(screen.getByLabelText(/Name/)).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Adding…'})).toBeDisabled();
    expect(screen.getByRole('button', {name: 'Close'})).toBeDisabled();
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(dialog).toHaveFocus();
    await user.tab();
    expect(dialog).toHaveFocus();
    await user.tab({shift: true});
    expect(dialog).toHaveFocus();
    await user.pointer({keys: '[MouseLeft]', target: document.querySelector('.dialog-overlay')!});
    expect(dialog).toHaveFocus();
    const outside = document.createElement('button');
    document.body.append(outside);
    act(() => outside.focus());
    expect(dialog).toHaveFocus();
    outside.remove();
    fireEvent.click(screen.getByRole('button', {name: 'Close'}));
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog')).toBeVisible();
    await act(async () => request.resolve({register_project: project}));
  });

  it('keeps a hidden pending request fenced on reopen and offers an explicit open after it completes', async () => {
    const request = deferred<{register_project: typeof project}>();
    gql.mockReturnValue(request.promise);
    const onRegistered = vi.fn(), onOpenChange = vi.fn();
    const props = {ownerId: 'owner', onRegistered, onOpenChange};
    const view = render(<AddProjectDialog {...props} open/>);
    fillFolder(); submit();
    view.rerender(<AddProjectDialog {...props} open={false}/>);
    view.rerender(<AddProjectDialog {...props} open/>);
    expect(screen.getByLabelText('Folder path')).toHaveValue(project.path);
    expect(screen.getByRole('button', {name: 'Adding…'})).toBeDisabled();
    submit();
    expect(gql).toHaveBeenCalledTimes(1);
    await act(async () => request.resolve({register_project: project}));
    expect(onRegistered).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', {name: 'Open project'}));
    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith(project, expect.any(Function)));
    expect(gql).toHaveBeenCalledTimes(1);
  });

  it('invalidates a pending registration on owner change without leaking the old draft or error', async () => {
    const request = deferred<{register_project: typeof project}>();
    gql.mockReturnValue(request.promise);
    const props = {onRegistered: vi.fn(), onOpenChange: vi.fn()};
    const view = render(<AddProjectDialog {...props} open ownerId="first"/>);
    fillFolder();
    fireEvent.change(screen.getByLabelText(/Name/), {target: {value: 'Private draft'}});
    submit();
    view.rerender(<AddProjectDialog {...props} open ownerId="second"/>);
    expect(screen.getByLabelText('Folder path')).toHaveValue('');
    expect(screen.getByLabelText(/Name/)).toHaveValue('');
    expect(screen.getByLabelText('Folder path')).toBeDisabled();
    await act(async () => request.reject(new Error('Old owner error')));
    expect(props.onRegistered).not.toHaveBeenCalled();
    expect(props.onOpenChange).not.toHaveBeenCalled();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Folder path')).toBeEnabled();
  });

  it('invalidates a pending registration when the workspace locks, including after unlock', async () => {
    const request = deferred<{register_project: typeof project}>();
    gql.mockReturnValue(request.promise);
    const props = {onRegistered: vi.fn(), onOpenChange: vi.fn(), ownerId: 'owner'};
    const view = render(<AddProjectDialog {...props} open/>);
    fillFolder(); submit();
    view.rerender(<AddProjectDialog {...props} open enabled={false}/>);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    view.rerender(<AddProjectDialog {...props} open/>);
    await act(async () => request.resolve({register_project: project}));
    expect(props.onRegistered).not.toHaveBeenCalled();
    expect(props.onOpenChange).not.toHaveBeenCalled();
    expect(screen.getByRole('button', {name: 'Open project'})).toBeEnabled();
  });

  it.each(['hidden', 'locked', 'owner changed', 'unmounted'])('invalidates a callback awaiting refresh when %s', async change => {
    gql.mockResolvedValue({register_project: project});
    const refresh = deferred<void>(), navigate = vi.fn(), onOpenChange = vi.fn();
    const onRegistered = vi.fn(async (_project, isCurrent: () => boolean) => { await refresh.promise; if (isCurrent()) navigate(); });
    const props = {onRegistered, onOpenChange, ownerId: 'owner'};
    const view = render(<AddProjectDialog {...props} open/>);
    fillFolder(); submit();
    await waitFor(() => expect(onRegistered).toHaveBeenCalledTimes(1));
    expect(onRegistered.mock.calls[0][1]()).toBe(true);
    if (change === 'hidden') view.rerender(<AddProjectDialog {...props} open={false}/>);
    else if (change === 'locked') view.rerender(<AddProjectDialog {...props} open enabled={false}/>);
    else if (change === 'owner changed') view.rerender(<AddProjectDialog {...props} open ownerId="second"/>);
    else view.unmount();
    await act(async () => refresh.resolve());
    expect(navigate).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it('opens an already registered path with its existing name without another mutation', async () => {
    const onRegistered = vi.fn();
    render(<AddProjectDialog open projects={[project]} onOpenChange={vi.fn()} onRegistered={onRegistered}/>);
    fireEvent.change(screen.getByLabelText(/Name/), {target: {value: 'Not a rename'}});
    fillFolder(project.path + '/');
    expect(screen.getByText('Already added as app.')).toBeVisible();
    expect(screen.queryByLabelText(/Name/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', {name: 'Open project'}));
    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith(project, expect.any(Function)));
    expect(gql).not.toHaveBeenCalled();
  });

  it('retries opening a registered project after refresh failure without repeating its mutation', async () => {
    gql.mockResolvedValue({register_project: project});
    const onRegistered = vi.fn().mockRejectedValueOnce(new Error('Could not refresh projects')).mockResolvedValueOnce(undefined);
    render(<AddProjectDialog open onOpenChange={vi.fn()} onRegistered={onRegistered}/>);
    fillFolder(); submit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh projects');
    expect(screen.getByLabelText('Folder path')).toHaveValue(project.path);
    fireEvent.click(screen.getByRole('button', {name: 'Open project'}));
    await waitFor(() => expect(onRegistered).toHaveBeenCalledTimes(2));
    expect(gql).toHaveBeenCalledTimes(1);
  });

  it('restores the opening control after an idle Escape close', async () => {
    const user = userEvent.setup();
    function Harness() {
      const [open, setOpen] = useState(false);
      return <><button onClick={() => setOpen(true)}>Add a project folder</button><AddProjectDialog open={open} onOpenChange={setOpen} onRegistered={vi.fn()}/></>;
    }
    render(<Harness/>);
    const opener = screen.getByRole('button', {name: 'Add a project folder'});
    await user.click(opener);
    expect(screen.getByRole('button', {name: 'Browse folders'})).toHaveFocus();
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    await waitFor(() => expect(opener).toHaveFocus());
    await new Promise(resolve => setTimeout(resolve, 200));
    expect(opener).toHaveFocus();
  });
});
