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
 * `extra` carries a spoken state from elsewhere in the conversation (live voice). It goes through this region because a
 * region that is mounted at the same moment as its first text is not announced, and the chat keeps only one.
 */
export function StatusAnnouncer({sessionId, task, extra = ''}: {sessionId: string; task: TaskLike | undefined; extra?: string}) {
  const [message, setMessage] = useState('');
  const previous = useRef<AnnouncedTask | null>(null);
  const pendingApproval = !!task?.approvals?.some(approval => approval.status === 'pending');
  useEffect(() => {
    const next: AnnouncedTask = {sessionId, taskId: task?.id ?? '', status: task?.status ?? '', pendingApproval};
    const text = announcementFor(previous.current, next);
    previous.current = next;
    if (text) setMessage(current => current === text ? text + ' ' : text);
  }, [sessionId, task?.id, task?.status, pendingApproval]);
  const spoken = useRef('');
  useEffect(() => {
    if (extra) {
      spoken.current = extra;
      setMessage(current => current === extra ? extra + ' ' : extra);
      return;
    }
    // The state that was announced is over (live voice closed or locked): drop it so no stale "Listening" is left behind.
    // A task announcement that arrived since is not ours to clear.
    const old = spoken.current;
    spoken.current = '';
    if (old) setMessage(current => current.trim() === old.trim() ? '' : current);
  }, [extra]);
  return <div className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">{message}</div>;
}
