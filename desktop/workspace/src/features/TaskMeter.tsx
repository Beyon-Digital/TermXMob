import {useState} from 'react';
import {autoLabel,meterContextLabel,loadTaskMeter,usageLabel,type TaskMeter} from '../lib/agentic';
import type {Task} from '../lib/api';
import {usePolling} from '../lib/use-polling';

/**
 * Context, usage and Auto status for the newest task. Polls while the task is live and reads once
 * for a finished one. Native engines do not report these, so they are skipped.
 */
export function useTaskMeter(task: Task | undefined, live: boolean, visible = true) {
  const key = task ? task.id + ':' + task.status : '';
  const [state, setState] = useState<{taskId: string; key: string; meter: TaskMeter} | null>(null);
  // A finished task is read once; if that read fails, give up instead of retrying forever with no meter to show.
  const [failedKey, setFailedKey] = useState('');
  const internal = !!task && (task.engine ?? 'internal') === 'internal';
  usePolling(signal => loadTaskMeter(task!.id, signal), {
    interval: 4000, maxInterval: 30000, resetKey: key, enabled: internal && visible && (live || (state?.key !== key && failedKey !== key)),
    onResult: meter => setState({taskId: task!.id, key, meter}),
    onError: () => { if (!live) setFailedKey(key); },
  });
  return internal && state?.taskId === task!.id ? state.meter : null;
}

export default function TaskMeterLine({meter}: {meter: TaskMeter}) {
  return <p className="task-meter">
    <span>{meterContextLabel(meter.context)}</span>
    <span>{usageLabel(meter.usage)}</span>
    {meter.auto?.enabled && <span>{autoLabel(meter.auto)}</span>}
  </p>;
}
