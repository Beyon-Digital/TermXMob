import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import AdvancedSql from './AdvancedSql';

const base = {open: true, onClose: vi.fn(), title: 'Orders', sql: 'SELECT 1', onSql: vi.fn(), parameterText: '', onParameterText: vi.fn(), locked: false,
  canRun: true, running: false, runLabel: 'Run', canCancel: false, onRun: vi.fn(), onCancel: vi.fn(), canSave: true, onSave: vi.fn()};
beforeEach(() => window.localStorage.clear());
afterEach(() => {cleanup(); vi.restoreAllMocks();});

it('shows the run shortcut, the saved queries of this source and a plain-language error', () => {
  const onOpenQuery = vi.fn();
  render(<AdvancedSql {...base} queries={[{id: 'q1', name: 'Top customers'}, {id: 'q2', name: 'Late orders'}]} activeQueryId="q2" onOpenQuery={onOpenQuery} error="no such table: ordres"/>);
  expect(screen.getByText(/\+Enter to run/)).toBeInTheDocument();
  expect(screen.getByRole('alert')).toHaveTextContent('There is no table "ordres" in this source.');
  fireEvent.click(screen.getByText('Saved queries (2)'));
  expect(screen.getByRole('button', {name: 'Late orders'})).toHaveAttribute('aria-current', 'true');
  fireEvent.click(screen.getByRole('button', {name: 'Top customers'}));
  expect(onOpenQuery).toHaveBeenCalledWith('q1');
});
it('moves with the arrow keys, remembers the position for this person and resets it', () => {
  const view = render(<AdvancedSql {...base} ownerId="alice"/>);
  const move = screen.getByRole('button', {name: 'Move SQL editor'});
  fireEvent.keyDown(move, {key: 'ArrowRight'});
  fireEvent.keyDown(move, {key: 'ArrowDown'});
  expect(JSON.parse(window.localStorage.getItem('termx.data.advanced-layout.alice')!)).toMatchObject({x: 16, y: 16});
  expect(screen.getByRole('dialog', {name: 'SQL editor'}).style.translate).toBe('16px 16px');
  view.unmount();
  render(<AdvancedSql {...base} ownerId="alice"/>);
  expect(screen.getByRole('dialog', {name: 'SQL editor'}).style.translate).toBe('16px 16px');
  cleanup();
  render(<AdvancedSql {...base} ownerId="bob"/>);
  expect(screen.getByRole('dialog', {name: 'SQL editor'}).style.translate).toBe('0px 0px');
  cleanup();
  render(<AdvancedSql {...base} ownerId="alice"/>);
  fireEvent.click(screen.getByRole('button', {name: 'Reset editor size and position'}));
  expect(window.localStorage.getItem('termx.data.advanced-layout.alice')).toBeNull();
});
it('opens at the default place when storage is unavailable', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {throw new Error('blocked');});
  render(<AdvancedSql {...base} ownerId="alice"/>);
  expect(screen.getByRole('dialog', {name: 'SQL editor'}).style.translate).toBe('0px 0px');
});
