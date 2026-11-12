import {useEffect, useMemo, useRef, useState} from 'react';
import {createPortal} from 'react-dom';
import {CircleAlert, RefreshCw} from 'lucide-react';
import {Button} from '../components/ui/button';
import {request, type Task} from '../lib/api';
import {delegatedActivities, taskIsActive} from '../lib/tool-activity';
import {usePolling} from '../lib/use-polling';
import CoordinationSidebar, {subagentFinished, subagentNeedsAttention, type CoordinationSidebarItem} from './CoordinationSidebar';
import type {CoordinationSidebarSummary} from './OrchestratorStatus';

type TreeTask = Omit<Task, 'children'> & {agent_label?: string; delegated_task?: string; updated_at?: number; created_at?: number; parent_id?: string; truncated?: boolean; children?: TreeTask[]};
type Receipt = {key: string; trees: Record<string, TreeTask>; error: string; retry: string};
type Props = {ownerId: string; sessionId: string; tasks: Task[]; visible: boolean; enabled: boolean; resolving?: boolean; target: HTMLElement|null; onSummary?: (value: CoordinationSidebarSummary) => void; onInspectTask?: (id: string) => void};
const emptyChildren: [] = [];
const title = (task: TreeTask) => [task.agent_label, task.delegated_task, task.prompt].find(value => typeof value === 'string' && value.trim())?.replace(/\s+/g, ' ').trim().slice(0, 160) || 'Untitled subagent';
function hasChildren(task: Task) {return !!task.children?.length || delegatedActivities(task.events || [], [], task.status).length > 0;}
function treeIsLive(task: TreeTask): boolean {return !subagentFinished(task) || !!task.children?.some(treeIsLive);}
function flatten(trees: Record<string, TreeTask>, roots: Task[]) {
  const seen = new Set(roots.map(task => task.id)), rows: CoordinationSidebarItem[] = [];
  const pending = roots.flatMap(root => (trees[root.id]?.children || []).map(task => ({task, parent: root.id})));
  let truncated = Object.values(trees).some(task => task.truncated);
  while (pending.length && rows.length < 100) {
    const {task, parent} = pending.shift()!;
    if (!task.id || seen.has(task.id) || task.parent_id && task.parent_id !== parent) continue;
    seen.add(task.id); truncated ||= !!task.truncated;
    rows.push({id: task.id, task_id: task.id, title: title(task), status: task.status, updated_at: task.updated_at || task.created_at || 0,
      pending_approvals: task.approvals?.filter(approval => approval.status === 'pending').length || 0});
    pending.push(...(task.children || []).map(child => ({task: child, parent: task.id})));
  }
  return {rows, truncated: truncated || pending.length > 0};
}

/** One bounded, authorized tree reader for this conversation, never global chat history. */
export default function SessionTaskSubagents({ownerId, sessionId, tasks, visible, enabled, resolving = false, target, onSummary, onInspectTask}: Props) {
  const roots = useMemo(() => [...new Map(tasks.filter(task => task.engine !== 'coordination' && !task.coordination_report).slice(-10).map(task => [task.id, task])).values()].reverse(), [tasks]);
  const key = ownerId + ':' + sessionId + ':' + roots.map(task => task.id).join(',');
  const [receipt, setReceipt] = useState<Receipt|null>(null), [retry, setRetry] = useState(0);
  const opening = useRef({visible, epoch: 0});
  if (visible && !opening.current.visible) opening.current.epoch++;
  opening.current.visible = visible;
  const refreshToken = retry + ':' + opening.current.epoch;
  const current = receipt?.key === key ? receipt : null;
  const candidates = roots.filter(root => visible || hasChildren(root) || !!current?.trees[root.id]?.children?.length);
  const toRead = candidates.filter(root => !current?.trees[root.id] || current.retry !== refreshToken || taskIsActive(root.status) || treeIsLive(current.trees[root.id]));
  const queryKey = key + ':' + candidates.map(task => task.id).join(',') + ':' + refreshToken;
  usePolling(async signal => {
    const values: Record<string, TreeTask> = {};
    for (const root of toRead) {
      if (signal.aborted) throw new DOMException('Aborted', 'AbortError');
      const task = await request<TreeTask>('/api/workspace/tasks/' + encodeURIComponent(root.id) + '?activity=false', {signal});
      if (task.id !== root.id) throw Error('The host returned a different task tree.');
      values[root.id] = task;
    }
    return values;
  }, {interval: 4000, maxInterval: 15000, enabled: enabled && toRead.length > 0, resetKey: queryKey,
    onResult: values => setReceipt(previous => ({key, trees: {...(previous?.key === key ? previous.trees : {}), ...values}, error: '', retry: refreshToken})),
    // A failed authority read cannot keep publishing its old source rows.
    onError: reason => setReceipt({key, trees: {}, error: reason instanceof Error ? reason.message : 'Subagent tasks are unavailable', retry: refreshToken})});
  const {rows, truncated} = useMemo(() => flatten(current?.trees || {}, roots), [current?.trees, roots]);
  const attention = rows.filter(subagentNeedsAttention), done = rows.filter(subagentFinished).length;
  const loading = resolving || enabled && candidates.length > 0 && !current;
  const summary = useMemo<CoordinationSidebarSummary>(() => ({hasContent: rows.length > 0, loading, active: rows.length - done, attention: attention.length, done, plans: 0, stale: !!current?.error}), [key, rows.length, loading, done, attention.length, current?.error]);
  const callback = useRef(onSummary); callback.current = onSummary;
  useEffect(() => {callback.current?.(summary);}, [summary]);
  const inspect = (id: string) => onInspectTask?.(id);
  return <>
    {current?.error && <div className="orchestrator-error" role="alert"><span>Subagent tasks are unavailable. {current.error}</span><Button variant="ghost" size="sm" disabled={!enabled} onClick={() => setRetry(value => value + 1)}><RefreshCw size={14}/>Refresh subagents</Button></div>}
    {attention.length > 0 && <div className="orchestrator-sidebar-attention" role="status"><CircleAlert size={16} aria-hidden="true"/><span>{attention.length} {attention.length === 1 ? 'subagent needs' : 'subagents need'} attention</span><Button variant="ghost" size="sm" onClick={() => inspect(attention[0].id)}>Inspect task</Button></div>}
    {target && createPortal(<CoordinationSidebar ownerId={key} children={emptyChildren} items={rows} showPlans={false} selected="" loading={loading} stale={!!current?.error} error={current?.error || ''} enabled={enabled} truncated={truncated} truncatedNote="Some task children were omitted. Open the source run to inspect its current tree." note="Subagents from up to 10 recent runs in this conversation." board={null} boardError="" planOpen={false} onPlanOpenChange={() => {}} planContent={null} activity={[]} onOpen={inspect} onRefresh={() => setRetry(value => value + 1)}/>, target)}
  </>;
}
