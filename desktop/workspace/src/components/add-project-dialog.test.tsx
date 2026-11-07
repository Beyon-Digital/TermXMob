import {afterEach, beforeEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import AddProjectDialog, {isAbsoluteHostPath} from './AddProjectDialog';

const gql = vi.hoisted(() => vi.fn());
vi.mock('../lib/api', () => ({gql}));
afterEach(cleanup);
beforeEach(() => vi.clearAllMocks());

describe('add project dialog', () => {
  it('accepts only absolute host paths', () => {
    for (const ok of ['/Users/a/app', '~/code', 'C:\\code\\app', 'D:/code', '\\\\host\\share']) expect(isAbsoluteHostPath(ok)).toBe(true);
    for (const bad of ['app', './app', '../x', '', '  ']) expect(isAbsoluteHostPath(bad)).toBe(false);
  });

  it('registers the folder and reports the project', async () => {
    gql.mockResolvedValue({register_project: {id: 'p1', name: 'app', path: '/Users/a/app'}});
    const onRegistered = vi.fn(), onOpenChange = vi.fn();
    render(<AddProjectDialog open onOpenChange={onOpenChange} onRegistered={onRegistered}/>);
    expect(screen.getByRole('button', {name: 'Add project'})).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Folder path/), {target: {value: ' /Users/a/app '}});
    fireEvent.click(screen.getByRole('button', {name: 'Add project'}));
    await waitFor(() => expect(onRegistered).toHaveBeenCalledWith({id: 'p1', name: 'app', path: '/Users/a/app'}));
    expect(gql.mock.calls[0][1]).toEqual({input: {path: '/Users/a/app', name: ''}});
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('explains a relative path without calling the host', () => {
    render(<AddProjectDialog open onOpenChange={vi.fn()} onRegistered={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText(/Folder path/), {target: {value: 'app'}});
    fireEvent.click(screen.getByRole('button', {name: 'Add project'}));
    expect(screen.getByRole('alert')).toHaveTextContent('full folder path');
    expect(gql).not.toHaveBeenCalled();
  });

  it('keeps the dialog open and shows the host error when registration fails', async () => {
    gql.mockRejectedValue(new Error('Folder does not exist'));
    const onOpenChange = vi.fn();
    render(<AddProjectDialog open onOpenChange={onOpenChange} onRegistered={vi.fn()}/>);
    fireEvent.change(screen.getByLabelText(/Folder path/), {target: {value: '/missing'}});
    fireEvent.click(screen.getByRole('button', {name: 'Add project'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Folder does not exist');
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
    expect(screen.getByRole('button', {name: 'Add project'})).toBeEnabled();
  });
});
