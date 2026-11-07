import {useEffect, useRef, useState} from 'react';
import {taskIsActive} from './tool-activity';

export interface AnnouncedTask {sessionId: string; taskId: string; status: string; pendingApproval: boolean}
interface TaskLike {id: string; status: string; approvals?: {status: string}[]}

const needsApproval = (task: AnnouncedTask) => task.status === 'awaiting_approval' || task.pendingApproval;

/** What a screen-reader user should hear when a conversation's latest task moves from `previous` to `next`. */
export function announcementFor(previous: AnnouncedTask | null, next: AnnouncedTask | null): string {
  if (!previous || !next || previous.sessionId !== next.sessionId) return '';
  if (needsApproval(next)) return needsApproval(previous) && previous.taskId === next.taskId ? '' : 'Approval needed';
  if (previous.taskId !== next.taskId) return taskIsActive(next.status) ? 'Response started' : '';
  if (previous.status === next.status) return '';
  if (next.status === 'completed') return 'Response complete';
  if (next.status === 'failed') return 'Response failed';
  if (next.status === 'cancelled') return 'Response stopped';
  return '';
}

/**
 * The only live region in the conversation. The transcript itself is `aria-live="off"`.
 * The text changes whenever there is something to announce. A trailing space is added when the
 * message repeats, so an identical announcement for a later task is still new to assistive tech.
 * A loaded conversation without a task is tracked as an empty snapshot, so its first task is announced.
 */
export function StatusAnnouncer({sessionId, task}: {sessionId: string; task: TaskLike | undefined}) {
  const [message, setMessage] = useState('');
  const previous = useRef<AnnouncedTask | null>(null);
  const pendingApproval = !!task?.approvals?.some(approval => approval.status === 'pending');
  useEffect(() => {
    const next: AnnouncedTask = {sessionId, taskId: task?.id ?? '', status: task?.status ?? '', pendingApproval};
    const text = announcementFor(previous.current, next);
    previous.current = next;
    if (text) setMessage(current => current === text ? text + ' ' : text);
  }, [sessionId, task?.id, task?.status, pendingApproval]);
  return <div className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{message}</div>;
}
