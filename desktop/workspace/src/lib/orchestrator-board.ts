import {gql} from './api';
import {formatCost, formatTokens} from './agentic';

/** One task-graph node as GraphQL `orchestrator_board` reports it. Missing cost or time is null, never zero. */
export interface BoardNode {
  id: string; title: string; role: string; depends_on: string[]; blocked_by: string[];
  task_id: string | null; status: string; attempts: number; max_attempts: number;
  /** Set while a running node's task waits on a person: a tool approval, a pause, or recovery after a restart. */
  waiting?: 'approval' | 'paused' | 'recovery' | null;
  last_event_at: number | null; result_summary: string; error: string;
  provider_id: string | null; model: string | null; tokens: number; cost_usd: number | null;
  created_at: number | null; updated_at: number | null;
}
export interface BoardRollup {total: number; counts: Record<string, number>; percent_complete: number; tokens: number; cost_usd: number | null}
export interface Board {nodes: BoardNode[]; rollup: BoardRollup}
export interface ModelChoice {provider_id: string; provider: string; models: string[]}

const BOARD = 'nodes{id title role depends_on blocked_by task_id status attempts max_attempts waiting last_event_at result_summary error provider_id model tokens cost_usd created_at updated_at} rollup{total counts percent_complete tokens cost_usd}';
const NODE_ACTION = (name: string, extra = '', input = '') => `mutation($id:String!${extra}){${name}(node_id:$id${input}){${BOARD}}}`;

export async function loadBoard(): Promise<Board> {
  return (await gql<{orchestrator_board: Board}>(`{orchestrator_board{${BOARD}}}`)).orchestrator_board;
}
export async function retryNode(id: string): Promise<Board> {
  return (await gql<{retry_node: Board}>(NODE_ACTION('retry_node'), {id})).retry_node;
}
export async function cancelNode(id: string): Promise<Board> {
  return (await gql<{cancel_node: Board}>(NODE_ACTION('cancel_node'), {id})).cancel_node;
}
export async function reassignNode(id: string, providerId: string, model: string): Promise<Board> {
  const query = NODE_ACTION('reassign_node', ',$input:ReassignNodeInput!', ',input:$input');
  return (await gql<{reassign_node: Board}>(query, {id, input: {provider_id: providerId, model}})).reassign_node;
}

/** Models the user can pick for a worker: every provider that lists models. The host rejects a route it cannot use, and the row shows that error. */
export async function loadModelChoices(): Promise<ModelChoice[]> {
  const data = await gql<{agent_providers: {id: string; name: string; models: string[] | null}[]}>('{agent_providers{id name models}}');
  return data.agent_providers
    .filter(row => row.models?.length)
    .map(row => ({provider_id: row.id, provider: row.name || row.id, models: row.models as string[]}));
}

export const STATUS_LABEL: Record<string, string> = {pending: 'Waiting', running: 'Running', stalled: 'Stalled', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled'};
const SUMMARY_ORDER = ['running', 'stalled', 'pending', 'failed', 'completed', 'cancelled'];

/** Counts in a fixed order, skipping statuses with none. */
export function rollupCounts(rollup: BoardRollup) {
  return SUMMARY_ORDER.filter(status => rollup.counts[status] > 0).map(status => ({status, label: STATUS_LABEL[status], count: rollup.counts[status]}));
}
/** The host sends a null cost when any node ran on a model with no known price. */
export function rollupUsage(rollup: BoardRollup) {
  return `${formatTokens(rollup.tokens)} tokens · ${formatCost(rollup.cost_usd)}`;
}

const RETRYABLE = ['failed', 'stalled', 'cancelled'];
/** Running or stalled: the node has a task that may still produce output. */
export const isActive = (node: BoardNode) => node.status === 'running' || node.status === 'stalled';
export const canRetry = (node: BoardNode) => RETRYABLE.includes(node.status) && node.attempts < node.max_attempts;
export const canReassign = (node: BoardNode) => ['pending', ...RETRYABLE].includes(node.status);
export const canCancel = (node: BoardNode) => ['pending', 'running', 'stalled', 'failed'].includes(node.status);
export const outOfAttempts = (node: BoardNode) => RETRYABLE.includes(node.status) && node.attempts >= node.max_attempts;

/** Indent level = longest chain of dependencies above the node. Unknown ids and cycles stop the walk. */
export function depths(nodes: BoardNode[]) {
  const byId = new Map(nodes.map(node => [node.id, node]));
  const memo = new Map<string, number>();
  const walk = (node: BoardNode, seen: Set<string>): number => {
    const known = memo.get(node.id);
    if (known !== undefined) return known;
    if (seen.has(node.id)) return 0;
    seen.add(node.id);
    const value = node.depends_on.reduce((deepest, id) => byId.has(id) ? Math.max(deepest, 1 + walk(byId.get(id)!, seen)) : deepest, 0);
    seen.delete(node.id);
    memo.set(node.id, value);
    return value;
  };
  return new Map(nodes.map(node => [node.id, walk(node, new Set())]));
}

/** "Under a minute", "3 min", "1 h 5 min". Timestamps are epoch seconds. */
export function elapsedLabel(seconds: number) {
  const total = Math.max(0, Math.floor(seconds)), minutes = Math.floor(total / 60);
  if (minutes < 1) return 'Under a minute';
  if (minutes < 60) return `${minutes} min`;
  const rest = minutes % 60;
  return rest ? `${Math.floor(minutes / 60)} h ${rest} min` : `${Math.floor(minutes / 60)} h`;
}
/** Time since the node was planned: the host does not report a separate start time. */
export function nodeElapsed(node: BoardNode, now: number) {
  if (node.created_at == null || node.status === 'pending') return null;
  const end = isActive(node) ? now : node.updated_at;
  return end == null ? null : elapsedLabel(end - node.created_at);
}

const WAITING_TEXT: Record<string, string> = {approval: 'is waiting for your approval', paused: 'is paused', recovery: 'is recovering and needs your confirmation'};
const MAX_LISTED = 3;

/** What changed between two polls that someone using a screen reader should hear: failures, stalls, waits on the user, and the last task finishing. */
export function boardChanges(previous: Board | null, next: Board): string[] {
  if (!previous) return [];
  const before = new Map(previous.nodes.map(node => [node.id, node]));
  const notes: string[] = [];
  for (const node of next.nodes) {
    const old = before.get(node.id);
    if (!old) continue;
    if (node.status === 'failed' && old.status !== 'failed') notes.push(`“${node.title}” failed.`);
    else if (node.status === 'stalled' && old.status !== 'stalled') notes.push(`“${node.title}” has stalled.`);
    else if (node.waiting && node.waiting !== old.waiting && WAITING_TEXT[node.waiting]) notes.push(`“${node.title}” ${WAITING_TEXT[node.waiting]}.`);
  }
  if (next.rollup.total > 0 && next.rollup.percent_complete === 100 && previous.rollup.percent_complete < 100) notes.push('All tasks are complete.');
  return notes.length > MAX_LISTED ? [...notes.slice(0, MAX_LISTED), `${notes.length - MAX_LISTED} more updates.`] : notes;
}
