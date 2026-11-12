import {afterEach, expect, it, vi} from 'vitest';
import {cleanup, render, screen, waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {useState} from 'react';
import SaveViewDialog from './SaveViewDialog';
import ConflictBanner from './ConflictBanner';
import {ApiError} from '../../lib/api';
import conflict from '../../test-utils/ui-fixtures/view-conflict.json';
import {viewError} from '../../lib/data-views';
import {expectNoAxeViolations} from '../../test-axe';

vi.mock('../../lib/api', () => ({request: vi.fn(), ApiError: class ApiError extends Error {constructor(public status: number, message: string) {super(message);}}}));
afterEach(cleanup);

function Host({onSubmit, onFail}: {onSubmit: (name: string) => Promise<unknown>; onFail?: React.ComponentProps<typeof SaveViewDialog>['onFail']}) {
  const [open, setOpen] = useState(false);
  return <><button onClick={() => setOpen(true)}>Open dialog</button>
    <SaveViewDialog open={open} title="Save pivot" submitLabel="Save view" helper="Everyone with access can open it." onSubmit={onSubmit} onFail={onFail} onClose={() => setOpen(false)}/></>;
}
const open = async (user: ReturnType<typeof userEvent.setup>) => {await user.click(screen.getByRole('button', {name: 'Open dialog'})); return screen.findByRole('dialog', {name: 'Save pivot'});};

it('labels the name field, focuses it and keeps Save off until there is a name', async () => {
  const user = userEvent.setup();
  render(<Host onSubmit={vi.fn()}/>);
  await open(user);
  expect(screen.getByLabelText('Name')).toHaveFocus();
  expect(screen.getByRole('button', {name: 'Save view'})).toBeDisabled();
  await user.type(screen.getByLabelText('Name'), '  ');
  expect(screen.getByRole('button', {name: 'Save view'})).toBeDisabled();
});
it('saves a trimmed name, closes and returns focus to the button that opened it', async () => {
  const user = userEvent.setup(), submit = vi.fn().mockResolvedValue(undefined);
  render(<Host onSubmit={submit}/>);
  await open(user);
  await user.type(screen.getByLabelText('Name'), '  Sales by region ');
  await user.keyboard('{Enter}');
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(submit).toHaveBeenCalledWith('Sales by region');
  await waitFor(() => expect(screen.getByRole('button', {name: 'Open dialog'})).toHaveFocus());
});
it('sends one request when the button is pressed twice', async () => {
  const user = userEvent.setup();
  let finish!: () => void;
  const submit = vi.fn(() => new Promise<void>(resolve => {finish = resolve;}));
  render(<Host onSubmit={submit}/>);
  await open(user);
  await user.type(screen.getByLabelText('Name'), 'Sales');
  const save = screen.getByRole('button', {name: 'Save view'});
  await user.click(save); await user.click(save);
  expect(submit).toHaveBeenCalledTimes(1);
  expect(screen.getByRole('button', {name: 'Saving…'})).toHaveAttribute('aria-disabled', 'true');
  finish();
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});
it('shows a taken name under the field and keeps the dialog open', async () => {
  const user = userEvent.setup(), submit = vi.fn().mockRejectedValue(new ApiError(409, JSON.stringify({code: 'name_taken', message: 'A pivot with this name already exists.'})));
  render(<Host onSubmit={submit}/>);
  await open(user);
  await user.type(screen.getByLabelText('Name'), 'Sales');
  await user.click(screen.getByRole('button', {name: 'Save view'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('A pivot with this name already exists.');
  expect(screen.getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true');
  await user.type(screen.getByLabelText('Name'), '2');
  expect(screen.queryByRole('alert')).toBeNull();
});
it('shows any other failure in the dialog, or hands a conflict to the caller', async () => {
  const user = userEvent.setup(), onFail = vi.fn(() => false), submit = vi.fn().mockRejectedValue(new ApiError(403, 'You cannot save views on this source.'));
  const {unmount} = render(<Host onSubmit={submit} onFail={onFail}/>);
  await open(user);
  await user.type(screen.getByLabelText('Name'), 'Sales');
  await user.click(screen.getByRole('button', {name: 'Save view'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('You cannot save views on this source.');
  unmount();
  const handled = vi.fn(() => true), clash = vi.fn().mockRejectedValue(new ApiError(409, JSON.stringify(conflict.response.detail)));
  render(<Host onSubmit={clash} onFail={handled}/>);
  await open(user);
  await user.type(screen.getByLabelText('Name'), 'Sales');
  await user.click(screen.getByRole('button', {name: 'Save view'}));
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  expect(handled).toHaveBeenCalledWith(expect.objectContaining({code: 'revision_conflict'}));
});
it('closes with Escape and has no axe violations', async () => {
  const user = userEvent.setup();
  render(<Host onSubmit={vi.fn()}/>);
  const dialog = await open(user);
  await expectNoAxeViolations(dialog);
  await user.keyboard('{Escape}');
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
});

it('names who changed the view and offers reload, save as new and dismiss', async () => {
  const user = userEvent.setup(), reload = vi.fn(), copy = vi.fn(), dismiss = vi.fn();
  const {container} = render(<ConflictBanner error={viewError(new ApiError(409, JSON.stringify(conflict.response.detail)))} onReload={reload} onSaveCopy={copy} onDismiss={dismiss}/>);
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent('Bob changed this view at');
  expect(alert).toHaveTextContent('Your edits have not been saved.');
  expect(alert).toHaveFocus();
  expect(screen.queryByRole('heading')).toBeNull();
  expect(container.querySelector('.button-primary')).toBeNull();
  await expectNoAxeViolations(container);
  await user.click(screen.getByRole('button', {name: 'Reload latest'}));
  await user.click(screen.getByRole('button', {name: 'Save as a new view'}));
  await user.click(screen.getByRole('button', {name: 'Dismiss'}));
  expect([reload, copy, dismiss].map(fn => fn.mock.calls.length)).toEqual([1, 1, 1]);
});
it('disables the actions while reloading and copes with an unknown author', () => {
  render(<ConflictBanner error={{status: 409, code: 'revision_conflict', message: 'x'}} onReload={vi.fn()} onDismiss={vi.fn()} reloading/>);
  expect(screen.getByRole('alert')).toHaveTextContent('Someone else changed this view.');
  expect(screen.getByRole('button', {name: 'Reloading…'})).toBeDisabled();
  expect(screen.queryByRole('button', {name: 'Save as a new view'})).toBeNull();
});
