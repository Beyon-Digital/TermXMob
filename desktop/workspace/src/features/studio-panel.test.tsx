import {act, cleanup, fireEvent, render, screen} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach, expect, it, vi} from 'vitest';
import StudioPanel from './StudioPanel';
afterEach(() => {cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); document.documentElement.style.removeProperty('--motion-exit');});

it('wraps both Tab directions when a no-account panel has only its Close button', async () => {
  const user = userEvent.setup(), onClose = vi.fn();
  render(<StudioPanel open title="Generation settings" onClose={onClose}><select aria-label="Provider account" disabled><option>No account</option></select><div hidden><button>Hidden choice</button></div><div inert><button>Inert choice</button></div></StudioPanel>);
  const close = screen.getByRole('button', {name: 'Close Generation settings'});
  close.focus();
  await user.keyboard('{Shift>}{Tab}{/Shift}'); expect(close).toHaveFocus();
  await user.tab(); expect(close).toHaveFocus();
  expect(screen.getByRole('dialog', {name: 'Generation settings'})).toBeVisible();
  expect(onClose).not.toHaveBeenCalled();
});

it('wraps modal endpoints while preserving normal focus order between editable controls', async () => {
  const user = userEvent.setup();
  render(<StudioPanel open title="Generation settings" onClose={vi.fn()}><select aria-label="Provider account"><option>Connected</option></select><input aria-label="Negative prompt"/><button disabled>Unavailable</button><div style={{display: 'none'}}><button>CSS hidden</button></div></StudioPanel>);
  const close = screen.getByRole('button', {name: 'Close Generation settings'}), provider = screen.getByLabelText('Provider account'), last = screen.getByLabelText('Negative prompt');
  close.focus(); await user.tab(); expect(provider).toHaveFocus();
  await user.tab(); expect(last).toHaveFocus();
  await user.tab(); expect(close).toHaveFocus();
  await user.tab({shift: true}); expect(last).toHaveFocus();
});

it('keeps inline saved-assets sections outside the modal focus loop', () => {
  render(<StudioPanel open inline title="Saved assets" onClose={vi.fn()}><input aria-label="Name"/><button>Save</button></StudioPanel>);
  const input = screen.getByLabelText('Name'); input.focus();
  const event = new KeyboardEvent('keydown', {key: 'Tab', shiftKey: true, bubbles: true, cancelable: true});
  fireEvent(input, event); expect(event.defaultPrevented).toBe(false);
});


it('keeps a closing native panel mounted but inert until its exit ends and returns to its actual opener', () => {
  vi.useFakeTimers(); document.documentElement.style.setProperty('--motion-exit', '120ms');
  const charge = vi.fn();
  const view = render(<><button>Open panel</button><StudioPanel open={false} title="Review generation" onClose={vi.fn()}><input aria-label="Draft" defaultValue="Retained draft"/><button onClick={charge}>Confirm charge</button></StudioPanel></>);
  const opener = screen.getByRole('button', {name: 'Open panel'}); opener.focus();
  view.rerender(<><button>Open panel</button><StudioPanel open title="Review generation" onClose={vi.fn()}><input aria-label="Draft" defaultValue="Retained draft"/><button onClick={charge}>Confirm charge</button></StudioPanel></>);
  const panel = screen.getByRole('dialog', {name: 'Review generation'});
  const confirm = screen.getByRole('button', {name: 'Confirm charge'}); confirm.focus();
  view.rerender(<><button>Open panel</button><StudioPanel open={false} title="Review generation" onClose={vi.fn()}><input aria-label="Draft" defaultValue="Retained draft"/><button onClick={charge}>Confirm charge</button></StudioPanel></>);
  expect(panel).toHaveAttribute('data-state', 'closed'); expect(panel).toHaveAttribute('inert'); expect(panel).not.toHaveAttribute('hidden');
  expect(screen.queryByRole('dialog', {name: 'Review generation'})).not.toBeInTheDocument();
  fireEvent.click(confirm); expect(charge).not.toHaveBeenCalled();
  act(() => {vi.advanceTimersByTime(119);}); expect(panel).toHaveAttribute('open');
  act(() => {vi.advanceTimersByTime(1);}); expect(panel).not.toHaveAttribute('open'); expect(panel).toHaveAttribute('hidden'); expect(opener).toHaveFocus();
});

it('cancels a pending exit on rapid reopen without resetting children', () => {
  vi.useFakeTimers(); document.documentElement.style.setProperty('--motion-exit', '120ms');
  const view = render(<StudioPanel open title="Reference media" onClose={vi.fn()}><input aria-label="Draft" defaultValue="Keep this reference"/></StudioPanel>);
  const panel = screen.getByRole('dialog', {name: 'Reference media'}), draft = screen.getByLabelText('Draft');
  fireEvent.change(draft, {target: {value: 'Edited reference'}});
  view.rerender(<StudioPanel open={false} title="Reference media" onClose={vi.fn()}><input aria-label="Draft" defaultValue="Keep this reference"/></StudioPanel>);
  act(() => {vi.advanceTimersByTime(60);});
  view.rerender(<StudioPanel open title="Reference media" onClose={vi.fn()}><input aria-label="Draft" defaultValue="Keep this reference"/></StudioPanel>);
  act(() => {vi.advanceTimersByTime(180);});
  expect(panel).toHaveAttribute('open'); expect(panel).toHaveAttribute('data-state', 'open'); expect(panel).not.toHaveAttribute('inert');
  expect(screen.getByLabelText('Draft')).toBe(draft); expect(draft).toHaveValue('Edited reference');
});

it('uses current cleared children during exit and closes immediately with reduced motion', () => {
  vi.useFakeTimers(); document.documentElement.style.setProperty('--motion-exit', '120ms');
  const view = render(<StudioPanel open title="Reference media" onClose={vi.fn()}><p>Private image metadata</p></StudioPanel>);
  const panel = screen.getByRole('dialog', {name: 'Reference media'});
  view.rerender(<StudioPanel open={false} title="Reference media" onClose={vi.fn()}>{null}</StudioPanel>);
  expect(screen.queryByText('Private image metadata')).not.toBeInTheDocument(); expect(panel).toHaveAttribute('inert');
  vi.stubGlobal('matchMedia', vi.fn(() => ({matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn()})));
  view.rerender(<StudioPanel open title="Reference media" onClose={vi.fn()}><p>New workspace</p></StudioPanel>);
  view.rerender(<StudioPanel open={false} title="Reference media" onClose={vi.fn()}>{null}</StudioPanel>);
  expect(panel).toHaveAttribute('hidden'); expect(panel).not.toHaveAttribute('open');
});

it('keeps native summaries reachable in both directions of the modal Tab loop', async () => {
  const user = userEvent.setup();
  render(<StudioPanel open title="Camera" onClose={vi.fn()}><select aria-label="Focal length"><option>50 mm</option></select><details><summary>View composed prompt</summary><p>Current camera direction</p></details></StudioPanel>);
  const close = screen.getByRole('button', {name: 'Close Camera'}), focal = screen.getByLabelText('Focal length'), summary = screen.getByText('View composed prompt');
  close.focus(); await user.tab(); expect(focal).toHaveFocus();
  await user.tab(); expect(summary).toHaveFocus();
  await user.tab(); expect(close).toHaveFocus();
  await user.tab({shift: true}); expect(summary).toHaveFocus();
  await user.tab({shift: true}); expect(focal).toHaveFocus();
});
