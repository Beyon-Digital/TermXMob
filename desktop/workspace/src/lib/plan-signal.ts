import type {TaskStreamEvent} from './task-events';

/**
 * The orchestrator snapshot cursor only moves for delegated-chat events, so a plan change made by the main agent
 * (plan_nodes, start_ready, retry_node, cancel_node, reassign_node) never reaches the Delegated work header on its own.
 * The main chat already follows the task event stream; it forwards the events that mean "the plan graph may have
 * changed" here, and the header re-reads the board at once instead of waiting for its idle poll.
 */
const PLAN_TOOLS = new Set(['plan_nodes', 'start_ready', 'retry_node', 'cancel_node', 'reassign_node']);
const MAX_CALLS = 200;
const listeners = new Set<() => void>();
const names = new Map<string, string>();

type ToolStarted = {call?: {call_id?: unknown; name?: unknown}};
type ToolFinished = {call_id?: unknown};

/** Call for every event of the main chat's task stream. Notifies subscribers only for plan-affecting events. */
export function notePlanEvent(event: TaskStreamEvent) {
  if (event.type === 'tool.started') {
    const call = (event.payload as ToolStarted | undefined)?.call;
    if (typeof call?.call_id === 'string' && typeof call.name === 'string') {
      names.set(call.call_id, call.name);
      if (names.size > MAX_CALLS) names.delete(names.keys().next().value as string);
    }
  } else if (event.type === 'tool.finished') {
    const id = (event.payload as ToolFinished | undefined)?.call_id;
    const name = typeof id === 'string' ? names.get(id) : undefined;
    if (typeof id === 'string') names.delete(id);
    // A call whose start was missed (a reconnect) is unknown, so it counts: one extra board read is cheaper than a stale header.
    if (!name || PLAN_TOOLS.has(name)) listeners.forEach(listener => listener());
  } else if (event.type === 'approval.resolved') {
    listeners.forEach(listener => listener());
  }
}

/** Returns the unsubscribe function. */
export function subscribePlanEvents(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
