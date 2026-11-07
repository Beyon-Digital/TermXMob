import {act, cleanup, render, screen} from '@testing-library/react';
import {afterEach, expect, it} from 'vitest';
import {announcementFor, StatusAnnouncer, type AnnouncedTask} from './status-announcer';

afterEach(cleanup);
const snap = (status: string, extra: Partial<AnnouncedTask> = {}): AnnouncedTask => ({sessionId: 's1', taskId: 't1', status, pendingApproval: false, ...extra});

it('says nothing on first sight of a session, or when the session changes', () => {
  expect(announcementFor(null, snap('running'))).toBe('');
  expect(announcementFor(snap('running'), snap('completed', {sessionId: 's2'}))).toBe('');
  expect(announcementFor(snap('completed'), null)).toBe('');
});

it('announces when a new task starts, completes, needs approval, fails or is stopped', () => {
  expect(announcementFor(snap('completed'), snap('running', {taskId: 't2'}))).toBe('Response started');
  expect(announcementFor(snap('running'), snap('completed'))).toBe('Response complete');
  expect(announcementFor(snap('running'), snap('awaiting_approval', {pendingApproval: true}))).toBe('Approval needed');
  expect(announcementFor(snap('running'), snap('failed'))).toBe('Response failed');
  expect(announcementFor(snap('running'), snap('cancelled'))).toBe('Response stopped');
});

it('does not repeat itself while the status is unchanged or for a task that was already finished', () => {
  expect(announcementFor(snap('running'), snap('running'))).toBe('');
  expect(announcementFor(snap('awaiting_approval', {pendingApproval: true}), snap('awaiting_approval', {pendingApproval: true}))).toBe('');
  expect(announcementFor(snap('completed'), snap('completed', {taskId: 't2'}))).toBe('');
});

it('announces approval again when a second approval arrives on the same task', () => {
  expect(announcementFor(snap('running'), snap('running', {pendingApproval: true}))).toBe('Approval needed');
});

it('keeps one always-mounted polite live region and updates it from task changes', () => {
  const view = render(<Harness task={{id: 't1', status: 'completed'}}/>);
  const region = screen.getByRole('status');
  expect(region).toHaveAttribute('aria-live', 'polite');
  expect(region).toHaveClass('visually-hidden');
  expect(region).toBeEmptyDOMElement();
  view.rerender(<Harness task={{id: 't2', status: 'running'}}/>);
  expect(screen.getByRole('status')).toBe(region);
  expect(region).toHaveTextContent('Response started');
  view.rerender(<Harness task={{id: 't2', status: 'completed'}}/>);
  expect(region).toHaveTextContent('Response complete');
  expect(screen.getAllByRole('status')).toHaveLength(1);
});

it('re-announces the same message for a later task', () => {
  const view = render(<Harness task={{id: 't1', status: 'completed'}}/>);
  view.rerender(<Harness task={{id: 't2', status: 'running'}}/>);
  const region = screen.getByRole('status');
  const first = region.textContent ?? '';
  view.rerender(<Harness task={{id: 't3', status: 'running'}}/>);
  expect((region.textContent ?? '').trim()).toBe(first.trim());
  expect(region.textContent).not.toBe(first);
});

it('announces the first task of a conversation that had none', () => {
  const view = render(<StatusAnnouncer sessionId="s1" task={undefined}/>);
  expect(screen.getByRole('status')).toBeEmptyDOMElement();
  view.rerender(<StatusAnnouncer sessionId="s1" task={{id: 't1', status: 'running'}}/>);
  expect(screen.getByRole('status')).toHaveTextContent('Response started');
});

it('announces approval for the first task of a conversation that went straight to awaiting approval', () => {
  const view = render(<StatusAnnouncer sessionId="s1" task={undefined}/>);
  view.rerender(<StatusAnnouncer sessionId="s1" task={{id: 't1', status: 'awaiting_approval'}}/>);
  expect(screen.getByRole('status')).toHaveTextContent('Approval needed');
});

function Harness({task}: {task: {id: string; status: string}}) {
  return <StatusAnnouncer sessionId="s1" task={task}/>;
}

it('treats a pending approval on a running task as needing approval', () => {
  const view = render(<StatusAnnouncer sessionId="s1" task={{id: 't1', status: 'running'}}/>);
  act(() => view.rerender(<StatusAnnouncer sessionId="s1" task={{id: 't1', status: 'running', approvals: [{status: 'pending'}]}}/>));
  expect(screen.getByRole('status')).toHaveTextContent('Approval needed');
});
