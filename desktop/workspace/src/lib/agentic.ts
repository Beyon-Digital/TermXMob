import {gql, type Task, type Session} from './api';
import {eventObject} from './tool-activity';

/** Legacy Auto quota keys remain in the wire shape; app task quotas are unset. */
export type AutoCeilings = {tool_calls:number|null;steps:number|null;seconds:number|null;tokens:number|null;cost_usd:number|null};
export type AutoCeilingKey = keyof AutoCeilings;
export const AUTO_CEILING_DEFAULTS:AutoCeilings = {tool_calls:null,steps:null,seconds:null,tokens:null,cost_usd:null};

export interface AutoSetting {enabled: boolean; ceilings: AutoCeilings}
export const AUTO_OFF: AutoSetting = {enabled: false, ceilings: AUTO_CEILING_DEFAULTS};

/** What the host reports for one task through GraphQL `agent_task`. Missing numbers are null, never zero. */
export interface TaskMeter {
  auto: {enabled: boolean; ceilings: Partial<AutoCeilings> | null; used: Partial<AutoCeilings>} | null;
  usage: {tokens: number; cost_usd: number | null};
  context: {used: number | null; window: number | null; limit: number; window_known: boolean};
}

const METER_QUERY = 'query($id:String!){agent_task(task_id:$id){id auto usage{tokens cost_usd} context{used window limit window_known}}}';
export async function loadTaskMeter(taskId: string, signal?: AbortSignal): Promise<TaskMeter> {
  const data = await gql<{agent_task: TaskMeter}>(METER_QUERY, {id: taskId});
  if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
  return data.agent_task;
}

const AUTO_MUTATION = 'mutation($id:String!,$input:AutoModeInput!){set_task_auto(task_id:$id,input:$input){id}}';
export async function setTaskAuto(taskId: string, auto: AutoSetting) {
  await gql(AUTO_MUTATION, {id: taskId, input: {enabled: auto.enabled, ceilings: {...AUTO_CEILING_DEFAULTS}}});
}

const RESOLVE_MUTATION = 'mutation($task:String!,$approval:String!,$input:AgentApprovalInput!){resolve_agent_approval(task_id:$task,approval_id:$approval,input:$input)}';
export async function resolvePlan(taskId: string, approvalId: string, decision: 'approved' | 'revise', feedback?: string) {
  await gql(RESOLVE_MUTATION, {task: taskId, approval: approvalId, input: {decision, ...(feedback ? {feedback} : {})}});
}

export const UNKNOWN = 'Unknown';
const compact = new Intl.NumberFormat('en', {notation: 'compact', maximumFractionDigits: 1});
export const formatTokens = (value: number | null | undefined) => typeof value === 'number' ? compact.format(value) : UNKNOWN;
export const formatCost = (value: number | null | undefined) => typeof value === 'number' ? '$' + (value >= 1 ? value.toFixed(2) : String(Number(value.toFixed(4)))) : UNKNOWN;

/** "Context 12k of 128k" or, when discovery had no window, "Context 12k of an unknown window (120k assumed)". */
export function meterContextLabel(context: TaskMeter['context']) {
  const window = context.window_known && context.window ? formatTokens(context.window) : `an unknown window (${formatTokens(context.limit)} assumed)`;
  return `Context ${formatTokens(context.used)} of ${window}`;
}

export function usageLabel(usage: TaskMeter['usage']) {
  return `${formatTokens(usage.tokens)} tokens · ${formatCost(usage.cost_usd)}`;
}

export function autoLabel(auto: NonNullable<TaskMeter['auto']>) {
  return auto.enabled ? 'Auto' : 'Auto off';
}

/** Auto is enabled only after the host reports a ready local Git worktree. */
export function autoBlockedReason(session: Partial<Pick<Session,'engine'|'worktree_id'|'runner_id'|'mode'|'checkout_state'|'checkout_policy'|'project_id'>> | null) {
  if (!session) return 'Open a conversation to use Auto.';
  if (session.runner_id || session.checkout_state === 'remote') return 'Auto runs only on this host. Choose the local host in Session options.';
  if (session.engine && session.engine !== 'internal') return 'Auto runs only with the built-in agent. Switch the engine in Session options.';
  if (session.mode && session.mode !== 'agent' || session.checkout_state === 'read-only') return 'Choose Agent coding mode to use Auto with an isolated Git checkout.';
  if (session.checkout_state === 'unavailable') return 'Auto is unavailable because the isolated checkout could not be verified. Review the workspace checkout details.';
  if (session.checkout_state === 'pending' && session.checkout_policy === 'auto') return 'An isolated Git worktree is created automatically on your first planning or coding turn. Auto becomes available when the checkout is ready.';
  if (session.checkout_state === 'not-git') return 'Auto requires an isolated Git checkout. This project is not a Git repository.';
  if (session.checkout_state === 'scratch') return 'Auto requires a Git project with an isolated checkout. This chat uses a scratch folder.';
  if (session.checkout_state === 'browser') return 'Auto requires a local Agent coding checkout. This conversation uses browser tools.';
  if (!session.worktree_id || session.checkout_state === 'pending') return 'Auto requires a verified isolated Git checkout. No ready checkout has been reported.';
  return '';
}

export const planBlockedReason = (session: {engine?: string} | null) =>
  session?.engine && session.engine !== 'internal' ? 'Plan is available with the built-in agent. This engine plans on its own.' : '';

export type AgentNote = {key: string; text: string};
type TaskEvent = NonNullable<Task['events']>[number];
const ceilingNames: Record<string, string> = {tool_calls: 'tool-call', steps: 'step', seconds: 'time', tokens: 'token', cost_usd: 'cost'};

/**
 * Quiet transcript lines for plan and Auto events. Consecutive `auto.decision` events collapse into one
 * line so a long unattended run stays readable.
 */
export function agentNotes(events: TaskEvent[] = []): AgentNote[] {
  const notes: AgentNote[] = [];
  let run: TaskEvent[] = [];
  const flush = () => {
    if (!run.length) return;
    const tools = [...new Set(run.map(event => String(eventObject(event.payload).tool || 'tool')))];
    const shown = tools.slice(0, 3).join(', ') + (tools.length > 3 ? ` and ${tools.length - 3} more` : '');
    notes.push({key: 'auto-' + run[0].sequence, text: `Auto approved ${run.length} ${run.length === 1 ? 'call' : 'calls'}: ${shown}`});
    run = [];
  };
  for (const event of events) {
    if (event.type === 'auto.decision') {run.push(event); continue;}
    flush();
    const payload = eventObject(event.payload);
    if (event.type === 'plan.revised') {
      const feedback = typeof payload.feedback === 'string' ? payload.feedback.trim() : '';
      const shown = feedback.length > 200 ? feedback.slice(0, 200).trimEnd() + '…' : feedback;
      notes.push({key: 'revised-' + event.sequence, text: shown ? `You asked for changes: ${shown}` : 'You asked for changes to the plan'});
    } else if (event.type === 'auto.ceiling') {
      const name = String(payload.ceiling || '');
      notes.push({key: 'ceiling-' + event.sequence, text: `Auto paused at a previous ${ceilingNames[name] || 'run'} limit (${payload.used ?? '?'} of ${payload.limit ?? '?'}).`});
    }
  }
  flush();
  return notes;
}
