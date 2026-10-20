import type {Task} from './api';

export type ActivityEvent = NonNullable<Task['events']>[number];
type ObjectValue = Record<string, unknown>;
export const eventObject = (value: unknown): ObjectValue =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as ObjectValue : {};
const string = (value: unknown) => typeof value === 'string' ? value : '';
const eventCallId = (event: ActivityEvent) => {
  const payload = event.payload;
  return string(eventObject(payload.call).call_id || payload.call_id || payload.tool_call_id || payload.toolCallId || payload.itemId || eventObject(payload.item).id);
};

const labels: Record<string, string> = {
  browser_context: 'Inspect page', browser_capture: 'Capture page', browser_wait_for_handoff: 'Wait for browser handoff',
  browser_upload: 'Upload file', browser_download: 'Download file', read_file: 'Read file', write_file: 'Edit file',
  edit_file: 'Edit file', list_files: 'List files', search_files: 'Search files', run_shell: 'Run command',
  run_check: 'Run check', spawn_subagent: 'Delegate task', await_subagents: 'Collect agent results',
  subagent_status: 'Check agents', cancel_subagent: 'Stop agent', commandExecution: 'Run command',
  fileChange: 'Edit files', mcpToolCall: 'Use connected tool', webSearch: 'Search the web',
  dynamicToolCall: 'Use tool', collabToolCall: 'Coordinate agents', collabAgentToolCall: 'Coordinate agents', imageView: 'View image',
};
const activeStatuses = new Set(['queued', 'planning', 'running', 'recovering', 'cancelling']);
export const taskIsActive = (status = '') => activeStatuses.has(status);

export type ToolPhase = 'running' | 'pending' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export type ToolActivity = {
  key: string; title: string; phase: ToolPhase; kind: string; callId: string;
  state: 'input-available' | 'output-available' | 'output-error'; events: ActivityEvent[];
  input: unknown; output: unknown;
};

function toolTitle(name: string, args: ObjectValue, payload: ObjectValue, item: ObjectValue) {
  if (payload.title) return string(payload.title);
  if (name === 'browser_action') {
    const action = string(args.action), target = eventObject(args.args);
    const controls: Record<string, string> = {click: 'Click page control', type: 'Enter text', scroll: 'Scroll page', navigate: 'Open page', reload: 'Reload page', zoom: 'Adjust page zoom', select: 'Select option', key: 'Press key', wait: 'Wait for page'};
    const selector = string(target.selector);
    const control = string(target.name || target.label) || selector.match(/\[aria-label=["']([^"']+)["']\]/)?.[1] || (/^#[a-zA-Z][\w-]*$/.test(selector) ? selector.slice(1).replaceAll('-', ' ') : '');
    return action === 'click' && control ? 'Click ' + control[0].toUpperCase() + control.slice(1) : controls[action] || 'Use browser';
  }
  const label = labels[name] || (name ? name.replaceAll('_', ' ').replace(/^./, value => value.toUpperCase()) : 'Agent action');
  const path = string(args.path || args.filename);
  if (path && ['read_file', 'write_file', 'edit_file'].includes(name)) return label + ' · ' + path;
  if (name === 'mcpToolCall' && item.tool) return string(item.tool);
  return label;
}

/** Normalize real internal, ACP, Claude and Codex events without guessing completion. */
export function toolActivities(events: ActivityEvent[], taskStatus?: string): ToolActivity[] {
  const groups = new Map<string, ToolActivity>();
  for (const event of events) {
    const p = event.payload, call = eventObject(p.call), item = eventObject(p.item);
    if (p.parent_call_id) continue;
    const itemType = string(item.type);
    const isItem = event.type.startsWith('engine.item.') && Object.hasOwn(labels, itemType);
    const isOutput = event.type === 'engine.command.output' || event.type === 'process.output';
    if (!event.type.startsWith('tool.') && !event.type.startsWith('engine.tool.') && !isItem && !isOutput) continue;
    const id = eventCallId(event);
    // Output without a call ID cannot safely be attributed to a parallel command.
    if (isOutput && !id) continue;
    const key = id ? 'call:' + id : 'event:' + event.sequence;
    const current = groups.get(key);
    const name = string(call.name || p.name || p.tool || itemType);
    const input = call.arguments ?? p.arguments ?? p.input ?? p.rawInput ?? item.arguments ?? item.command;
    const args = eventObject(input);
    const result = eventObject(p.result);
    const status = string(p.status || item.status || result.status);
    const failed = /failed|error/.test(event.type) || result.ok === false || p.is_error === true || item.success === false || (itemType === 'mcpToolCall' && item.error != null) || ['failed', 'error', 'declined', 'denied'].includes(status);
    const completed = /finished|completed|reused/.test(event.type) || status === 'completed';
    let phase: ToolPhase = failed ? 'failed' : ['cancelled', 'canceled', 'interrupted'].includes(status) ? 'cancelled' : completed ? 'completed' : status === 'pending' ? 'pending' : 'running';
    // Stream output does not change a known terminal state.
    if (isOutput && current) phase = current.phase;
    const output = p.result ?? p.rawOutput ?? p.output ?? item.aggregatedOutput ?? item.result ?? item.error ?? item.contentItems ?? (completed || failed ? p.content ?? p.error : undefined);
    if (current) {
      current.events.push(event);
      current.phase = phase;
      current.state = phase === 'failed' ? 'output-error' : phase === 'completed' ? 'output-available' : 'input-available';
      if (name || p.title) current.title = toolTitle(name || current.kind, args, p, item);
      if (input !== undefined) current.input = input;
      if (output !== undefined) current.output = output;
      else if (isOutput) current.output = string(current.output) + string(p.delta || p.text || p.chunk || p.output);
    } else {
      groups.set(key, {key, title: toolTitle(name, args, p, item), kind: name, callId: id, phase,
        state: phase === 'failed' ? 'output-error' : phase === 'completed' ? 'output-available' : 'input-available',
        events: [event], input, output: output ?? (isOutput ? p.delta ?? p.text ?? p.chunk ?? p.output : undefined)});
    }
  }
  if (taskStatus && ['completed', 'failed', 'cancelled', 'interrupted'].includes(taskStatus)) {
    for (const entry of groups.values()) if (entry.phase === 'running' || entry.phase === 'pending') entry.phase = 'interrupted';
  }
  return [...groups.values()];
}

/** Keep streamed text and its later full-message snapshot from appearing twice. */
export function taskResponseText(task: Task): string {
  if (task.result) return task.result;
  const segments: {key: string; text: string}[] = [];
  let current: {key: string; text: string} | undefined;
  for (const event of task.events || []) {
    if (event.payload.parent_call_id) continue;
    if (['assistant.delta', 'engine.message.delta', 'message.delta', 'response.output_text.delta'].includes(event.type)) {
      const delta = string(event.payload.delta || event.payload.text);
      if (!delta) continue;
      const key = string(event.payload.stream_id || event.payload.itemId || event.payload.message_id) || 'engine';
      if (!current || current.key !== key) {current = {key, text: ''}; segments.push(current);}
      current.text += delta;
    } else if (event.type === 'assistant.message') {
      const text = string(event.payload.text);
      if (!text) continue;
      if (current) current.text = text;
      else segments.push({key: 'message:' + event.sequence, text});
      current = undefined;
    } else if (event.type === 'tool.started' || event.type === 'engine.tool.started' || event.type === 'engine.item.started') {
      current = undefined;
    }
  }
  return segments.map(segment => segment.text).filter(Boolean).join('\n\n');
}

export type AgentActivity = {id: string; taskId?: string; name: string; prompt: string; status: string; result: string; events: ActivityEvent[]; children: AgentActivity[]; truncated?: boolean};

/** Child events are already scoped and authorized by the task snapshot endpoint. */
export function delegatedActivities(events: ActivityEvent[], snapshots: Task[] = [], parentStatus?: string, depth = 0): AgentActivity[] {
  if (depth > 4) return [];
  const agents = new Map<string, AgentActivity>();
  const authoritative = new Map(snapshots.map(snapshot => [snapshot.id, snapshot]));
  for (const snapshot of snapshots) {
    const extra = snapshot as Task & {agent_label?: string; delegated_task?: string};
    agents.set(snapshot.id, {id: snapshot.id, taskId: snapshot.id, name: extra.agent_label || 'Agent', prompt: extra.delegated_task || snapshot.prompt,
      status: snapshot.status, result: snapshot.result || snapshot.error || '', events: snapshot.events || [],
      children: delegatedActivities(snapshot.events || [], snapshot.children || [], snapshot.status, depth + 1), truncated: snapshot.activity_truncated});
  }
  for (const event of events) {
    const nativeParent = string(event.payload.parent_call_id);
    if (nativeParent) {
      const id = 'engine:' + nativeParent;
      let child = agents.get(id);
      if (!child) {
        const start = events.find(candidate => eventCallId(candidate) === nativeParent && candidate.type.endsWith('.started'));
        const item = eventObject(start?.payload.item);
        const input = eventObject(start?.payload.input || eventObject(start?.payload.call).arguments || item.arguments);
        child = {id, name: string(input.description || input.name) || 'Delegated agent', prompt: string(input.prompt || input.task || item.prompt), status: 'running', result: '', events: [], children: []};
        agents.set(id, child);
      }
      const {parent_call_id: _parent, ...payload} = event.payload;
      child.events.push({...event, payload});
      continue;
    }
    if (!['subagent.started', 'subagent.event', 'subagent.finished', 'subagent.cancelled'].includes(event.type)) continue;
    const payload = event.payload, id = string(payload.child_id);
    if (!id) continue;
    let child = agents.get(id);
    if (!child) {child = {id, taskId: id, name: string(payload.agent) || 'Agent', prompt: '', status: 'running', result: '', events: [], children: []}; agents.set(id, child);}
    if (payload.agent) child.name = string(payload.agent);
    if (payload.task) child.prompt = string(payload.task);
    if (event.type === 'subagent.event' && typeof payload.type === 'string' && authoritative.get(id)?.events === undefined) {
      const inner = {type: payload.type, payload: eventObject(payload.payload), sequence: event.sequence};
      child.events.push(inner);
      if (inner.type.startsWith('task.') && typeof inner.payload.status === 'string') child.status = inner.payload.status;
      const statuses: Record<string, string> = {'task.completed': 'completed', 'task.failed': 'failed', 'task.cancelled': 'cancelled', 'approval.requested': 'awaiting_approval', 'task.running': 'running'};
      if (statuses[inner.type]) child.status = statuses[inner.type];
    }
    if (event.type === 'subagent.finished') {child.status = string(payload.status) || child.status; child.result = string(payload.result);}
    if (event.type === 'subagent.cancelled') child.status = 'cancelled';
  }
  for (const child of agents.values()) {
    const snapshot = authoritative.get(child.id);
    if (snapshot) {
      // Full task snapshots are newer than the retained parent event window.
      // Historical forwarding must not revive a completed worker or duplicate
      // the worker's already-complete event stream.
      child.status = snapshot.status;
      child.result = snapshot.result || snapshot.error || child.result;
    }
    if (!child.taskId) {
      const parentId = child.id.slice('engine:'.length);
      const terminal = events.filter(event => eventCallId(event) === parentId && /completed|failed|finished/.test(event.type)).at(-1);
      if (terminal) {
        const reported = toolActivities([terminal])[0]?.phase;
        if (reported && ['completed', 'failed', 'cancelled'].includes(reported)) child.status = reported;
      } else if (parentStatus && ['completed', 'failed', 'cancelled', 'interrupted'].includes(parentStatus)) {
        // A disconnected native session may never report its child tool's
        // result. Stop the spinner without fabricating a successful outcome.
        child.status = 'interrupted';
      }
      child.result = taskResponseText({id: child.id, status: child.status, prompt: child.prompt, events: child.events});
    }
    if (child.events.length) child.children = delegatedActivities(child.events, snapshots.find(snapshot => snapshot.id === child.id)?.children, child.status, depth + 1);
  }
  return [...agents.values()];
}

export function activityPreview(value: unknown, limit = 6000): string {
  if (value == null) return '';
  const object = eventObject(value);
  const text = object.truncated === true && typeof object.preview === 'string'
    ? object.preview + '\n… Output truncated by the engine.'
    : typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return text.length > limit ? text.slice(0, limit) + '\n… Preview truncated.' : text;
}
