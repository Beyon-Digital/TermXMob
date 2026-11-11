import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import {afterEach, expect, it, vi} from 'vitest';
import StudioProjectsDialog from './StudioProjectsDialog';
import type {StudioProject, StudioProjectsController} from '../lib/studio-projects';
const first: StudioProject = {id: 'studio_one', name: 'Launch campaign', description: 'Product film', revision: 2, archived: false, created_at: 1, updated_at: 2};
function controller(projects: StudioProject[] = [first]): StudioProjectsController {
  return {projects, loading: false, error: '', busy: false, reload: vi.fn(), create: vi.fn().mockResolvedValue(first), update: vi.fn().mockResolvedValue({...first, revision: 3}), archive: vi.fn().mockResolvedValue({...first, archived: true, revision: 3}), restore: vi.fn().mockResolvedValue({...first, archived: false, revision: 4})};
}
afterEach(cleanup);

it('creates and selects a named Studio project without requesting an Agent folder', async () => {
  const managed = controller([]), onProject = vi.fn(), onOpenChange = vi.fn();
  render(<StudioProjectsDialog open initialCreate controller={managed} projectId={null} onProject={onProject} onOpenChange={onOpenChange}/>);
  expect(screen.queryByLabelText(/folder/i)).not.toBeInTheDocument();
  expect(screen.getByRole('button', {name: 'Create project'})).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Project name/), {target: {value: '  Launch campaign  '}});
  fireEvent.change(screen.getByLabelText('Description'), {target: {value: 'Product film'}});
  fireEvent.click(screen.getByRole('button', {name: 'Create project'}));
  await waitFor(() => expect(managed.create).toHaveBeenCalledWith({name: 'Launch campaign', description: 'Product film'}));
  expect(onProject).toHaveBeenCalledWith('studio_one'); expect(onOpenChange).toHaveBeenCalledWith(false);
});

it('requires the exact visible target name before archiving and preserves unrelated selections', async () => {
  const managed = controller(), onProject = vi.fn();
  render(<StudioProjectsDialog open controller={managed} projectId="other" onProject={onProject} onOpenChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Edit Launch campaign'}));
  fireEvent.click(screen.getByRole('button', {name: 'Archive project'}));
  expect(screen.getByText(/Generations, uploaded references and saved assets stay/)).toBeInTheDocument();
  const confirm = screen.getByRole('button', {name: 'Archive project'});
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Project name/), {target: {value: 'Other campaign'}});
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Project name/), {target: {value: 'Launch campaign'}});
  fireEvent.click(confirm);
  await waitFor(() => expect(managed.archive).toHaveBeenCalledWith(first));
  expect(onProject).not.toHaveBeenCalled();
  expect(screen.getByRole('status')).toHaveTextContent('media and history are preserved');
});

it('lets users recover archived projects and blocks archive when a draft is still unsaved', async () => {
  const archived = {...first, archived: true}, managed = controller([archived]);
  const view = render(<StudioProjectsDialog open controller={managed} projectId={null} onProject={vi.fn()} onOpenChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Show archived'}));
  fireEvent.click(screen.getByRole('button', {name: 'Restore Launch campaign'}));
  await waitFor(() => expect(managed.restore).toHaveBeenCalledWith(archived));
  view.rerender(<StudioProjectsDialog open controller={controller()} projectChangeBlocked="Discard or finish your draft before archiving." projectId={first.id} onProject={vi.fn()} onOpenChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Edit Launch campaign'}));
  expect(screen.getByRole('button', {name: 'Archive project'})).toBeDisabled();
  expect(screen.getByText('Discard or finish your draft before archiving.')).toBeInTheDocument();
});

it('rejects a stale editor after another window changes the project without losing the typed name', () => {
  const managed = controller();
  const view = render(<StudioProjectsDialog open controller={managed} projectId={first.id} onProject={vi.fn()} onOpenChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Edit Launch campaign'}));
  fireEvent.change(screen.getByLabelText(/Project name/), {target: {value: 'My renamed campaign'}});
  view.rerender(<StudioProjectsDialog open controller={{...managed, projects: [{...first, revision: 3}]}} projectId={first.id} onProject={vi.fn()} onOpenChange={vi.fn()}/>);
  expect(screen.getByRole('button', {name: 'Save changes'})).toBeDisabled();
  expect(screen.getByLabelText(/Project name/)).toHaveValue('My renamed campaign');
  expect(screen.getByRole('status')).toHaveTextContent('changed');
});

it('retains failed edits and gives an explicit reload path', async () => {
  const managed = controller(); vi.mocked(managed.update).mockRejectedValue(new Error('Studio project changed in another window. Reload projects before saving'));
  render(<StudioProjectsDialog open controller={managed} projectId={first.id} onProject={vi.fn()} onOpenChange={vi.fn()}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Edit Launch campaign'}));
  fireEvent.change(screen.getByLabelText(/Project name/), {target: {value: 'Revised film'}});
  fireEvent.click(screen.getByRole('button', {name: 'Save changes'}));
  expect(await screen.findByText('Studio project changed in another window. Reload projects before saving')).toHaveAttribute('role', 'alert');
  expect(screen.getByLabelText(/Project name/)).toHaveValue('Revised film');
  fireEvent.click(screen.getByRole('button', {name: 'Refresh projects'}));
  expect(managed.reload).toHaveBeenCalledOnce();
});


it('waits for the central draft decision and cancels archive without a backend call when declined', async () => {
  const managed = controller(), onBeforeArchive = vi.fn().mockResolvedValue(false);
  render(<StudioProjectsDialog open controller={managed} projectId={first.id} onProject={vi.fn()} onOpenChange={vi.fn()} onBeforeArchive={onBeforeArchive}/>);
  fireEvent.click(screen.getByRole('button', {name: 'Edit Launch campaign'}));
  fireEvent.click(screen.getByRole('button', {name: 'Archive project'}));
  fireEvent.change(screen.getByLabelText(/Project name/), {target: {value: first.name}});
  fireEvent.click(screen.getByRole('button', {name: 'Archive project'}));
  await waitFor(() => expect(onBeforeArchive).toHaveBeenCalledWith(first));
  expect(managed.archive).not.toHaveBeenCalled();
  expect(screen.getByRole('heading', {name: 'Archive project'})).toBeInTheDocument();
});
