import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import ConversationMessage from './ConversationMessage';
import {delegatedActivities, taskResponseText, toolActivities, type ActivityEvent} from '../lib/tool-activity';
import {request, type Task} from '../lib/api';

vi.mock('../lib/api', () => ({request: vi.fn(), json: (method: string, body: unknown) => ({method, body: JSON.stringify(body)})}));
const mocked = vi.mocked(request);
const event = (sequence: number, type: string, payload: Record<string, unknown>): ActivityEvent => ({sequence, type, payload});
const task = (changes: Partial<Task> = {}): Task => ({id: 'task-one', status: 'completed', prompt: 'Inspect the project', ...changes});
const callbacks = () => ({onSupervise: vi.fn(), onRefresh: vi.fn(), onError: vi.fn()});
beforeEach(() => mocked.mockReset());
afterEach(cleanup);

it('correlates ACP camel-case tool updates and recognizes the reported completion state', () => {
  const events = [event(1, 'engine.tool.started', {toolCallId: 'acp-one', title: 'Inspect files', status: 'pending', rawInput: {path: 'src'}}), event(2, 'engine.tool.progress', {toolCallId: 'acp-one', status: 'in_progress'}), event(3, 'engine.tool.progress', {toolCallId: 'acp-one', status: 'completed', content: [{type: 'content', content: {type: 'text', text: 'Three files'}}]})];
  expect(toolActivities(events)).toMatchObject([{key: 'call:acp-one', title: 'Inspect files', phase: 'completed'}]);
  expect(toolActivities(events)[0].output).toEqual(events[2].payload.content);
});

it('retains parallel command output by ID, excludes prose items, and never invents success', () => {
  const events = [event(1, 'engine.item.started', {item: {id: 'cmd', type: 'commandExecution', command: 'pwd'}}), event(2, 'engine.command.output', {itemId: 'cmd', delta: '/project'}), event(3, 'engine.item.completed', {item: {id: 'cmd', type: 'commandExecution', status: 'completed', aggregatedOutput: '/project'}}), event(4, 'engine.item.completed', {item: {id: 'prose', type: 'agentMessage', text: 'Ready'}}), event(5, 'tool.started', {call: {call_id: 'unfinished', name: 'run_shell', arguments: {command: 'inspect'}}}), event(6, 'process.output', {call_id: 'unfinished', chunk: 'progress'})];
  const actions = toolActivities(events, 'failed');
  expect(actions).toHaveLength(2);
  expect(actions[0]).toMatchObject({phase: 'completed', output: '/project'});
  expect(actions[1]).toMatchObject({phase: 'interrupted', output: 'progress'});
});

it('renders native connected-tool results, dynamic-tool failures, and collaboration calls', () => {
  const events = [event(1, 'engine.item.completed', {item: {id: 'mcp', type: 'mcpToolCall', tool: 'Inspect project', status: 'completed', result: {content: [{type: 'text', text: 'Three files'}]}}}), event(2, 'engine.item.completed', {item: {id: 'dynamic', type: 'dynamicToolCall', success: false, contentItems: [{type: 'inputText', text: 'Tool rejected'}]}}), event(3, 'engine.item.completed', {item: {id: 'collab', type: 'collabToolCall', tool: 'spawnAgent', status: 'completed', newThreadId: 'native-child'}})];
  expect(toolActivities(events)).toMatchObject([{phase: 'completed', title: 'Inspect project', output: {content: [{text: 'Three files'}]}}, {phase: 'failed', output: [{text: 'Tool rejected'}]}, {phase: 'completed', title: 'Coordinate agents'}]);
});

it('does not duplicate streamed text when the complete message arrives and separates later responses', () => {
  const value = task({status: 'running', events: [event(1, 'assistant.delta', {text: 'Let me ', stream_id: 'first'}), event(2, 'assistant.delta', {text: 'check.', stream_id: 'first'}), event(3, 'assistant.message', {text: 'Let me check.'}), event(4, 'tool.started', {call: {call_id: 'one', name: 'read_file'}}), event(5, 'assistant.delta', {text: 'Found **it**.', stream_id: 'second'})]});
  expect(taskResponseText(value)).toBe('Let me check.\n\nFound **it**.');
  expect(taskResponseText({...value, result: 'Canonical answer'})).toBe('Canonical answer');
});

it('keeps native child prose and tools in a delegated session rather than the parent answer', () => {
  const events = [event(1, 'engine.tool.started', {call_id: 'delegate', tool: 'Agent', input: {description: 'Reviewer', prompt: 'Inspect auth'}}), event(2, 'engine.message.delta', {delta: 'Child analysis', parent_call_id: 'delegate'}), event(3, 'engine.tool.started', {call_id: 'read', tool: 'read_file', input: {path: 'auth.py'}, parent_call_id: 'delegate'}), event(4, 'engine.tool.completed', {call_id: 'read', output: 'File read', parent_call_id: 'delegate'}), event(5, 'engine.tool.completed', {call_id: 'delegate', output: 'Review done'}), event(6, 'engine.message.delta', {delta: 'Parent answer'})];
  expect(taskResponseText(task({events}))).toBe('Parent answer');
  expect(toolActivities(events)).toHaveLength(1);
  const children = delegatedActivities(events);
  expect(children).toMatchObject([{name: 'Reviewer', status: 'completed', result: 'Child analysis'}]);
  expect(children[0].taskId).toBeUndefined();
  expect(toolActivities(children[0].events)[0]).toMatchObject({title: 'Read file · auth.py', phase: 'completed', output: 'File read'});
});

it('reconstructs actual nested internal agents and preserves child failures', () => {
  const events = [event(1, 'subagent.started', {child_id: 'reviewer', agent: 'Reviewer', task: 'Review auth'}), event(2, 'subagent.event', {child_id: 'reviewer', type: 'subagent.started', payload: {child_id: 'tester', agent: 'Tester', task: 'Verify refresh'}}), event(3, 'subagent.event', {child_id: 'reviewer', type: 'subagent.finished', payload: {child_id: 'tester', status: 'failed', result: 'Connection lost'}}), event(4, 'subagent.finished', {child_id: 'reviewer', status: 'completed', result: 'Review complete'})];
  expect(delegatedActivities(events)).toMatchObject([{id: 'reviewer', taskId: 'reviewer', status: 'completed', children: [{id: 'tester', taskId: 'tester', status: 'failed', result: 'Connection lost'}]}]);
});

it('does not leave native child agents spinning after a lost parent or infer success from failed tool results', () => {
  const events = [event(1, 'engine.tool.started', {call_id: 'delegate', tool: 'Agent'}), event(2, 'engine.message.delta', {delta: 'Partial child answer', parent_call_id: 'delegate'})];
  expect(delegatedActivities(events, [], 'failed')).toMatchObject([{status: 'interrupted', result: 'Partial child answer'}]);
  const reported = [...events, event(3, 'tool.finished', {call: {call_id: 'delegate'}, result: {ok: false, error: 'Lost connection'}})];
  expect(delegatedActivities(reported, [], 'completed')).toMatchObject([{status: 'failed'}]);
  const codex = [event(1, 'engine.item.started', {item: {id: 'worker', type: 'collabToolCall', prompt: 'Inspect auth'}}), event(2, 'engine.message.delta', {delta: 'Worker result', parent_call_id: 'worker'}), event(3, 'engine.item.completed', {item: {id: 'worker', type: 'collabToolCall', status: 'completed'}})];
  expect(delegatedActivities(codex, [], 'completed')).toMatchObject([{prompt: 'Inspect auth', status: 'completed', result: 'Worker result'}]);
});

it('prefers authoritative child snapshots over older forwarded statuses and events', () => {
  const events = [event(4, 'subagent.event', {child_id: 'reviewer', type: 'task.running', payload: {status: 'running'}}), event(5, 'subagent.event', {child_id: 'reviewer', type: 'assistant.delta', payload: {text: 'Once'}})];
  const snapshots = [task({id: 'reviewer', status: 'completed', result: 'Complete', events: [event(2, 'assistant.delta', {text: 'Once'})]})];
  const child = delegatedActivities(events, snapshots)[0];
  expect(child.status).toBe('completed'); expect(child.result).toBe('Complete'); expect(child.events).toHaveLength(1);
});

it('reloads saved activity after recovery resumes and finishes the same task ID', async () => {
  const props = callbacks();
  mocked.mockResolvedValueOnce(task({status: 'failed', events: [event(1, 'tool.started', {call: {call_id: 'old', name: 'read_file', arguments: {path: 'old.txt'}}})]}));
  const {rerender} = render(<ConversationMessage role="assistant" text="" task={task({status: 'failed'})} {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: /View activity/}));
  await screen.findByText('Read file · old.txt');
  rerender(<ConversationMessage role="assistant" text="" task={task({status: 'running', events: []})} {...props}/>);
  mocked.mockResolvedValueOnce(task({events: [event(2, 'tool.started', {call: {call_id: 'new', name: 'read_file', arguments: {path: 'new.txt'}}})]}));
  rerender(<ConversationMessage role="assistant" text="Done" task={task()} {...props}/>);
  await screen.findByText('Read file · new.txt');
  expect(mocked).toHaveBeenCalledTimes(2);
});

it('loads saved tool history only after expansion and keeps failures retryable', async () => {
  mocked.mockRejectedValueOnce(new Error('Host temporarily unavailable'));
  const value = task({result: 'Saved answer'});
  render(<ConversationMessage role="assistant" text="Saved answer" task={value} {...callbacks()}/>);
  expect(mocked).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', {name: /View activity/}));
  await screen.findByText(/Host temporarily unavailable/);
  mocked.mockResolvedValueOnce({...value, events: [event(1, 'tool.started', {call: {call_id: 'read', name: 'read_file', arguments: {path: 'README.md'}}}), event(2, 'tool.finished', {call_id: 'read', result: {ok: true, text: 'Project description'}})]});
  fireEvent.click(screen.getByRole('button', {name: 'Retry activity'}));
  await screen.findByText('Read file · README.md');
  expect(mocked).toHaveBeenCalledTimes(2);
  expect(mocked.mock.calls[0][0]).toBe('/api/workspace/tasks/task-one?activity=true');
  expect(screen.getByText('Saved answer')).toBeVisible();
});

it('discards delayed saved-history responses after the selected task changes', async () => {
  let finish!: (value: Task) => void;
  mocked.mockReturnValueOnce(new Promise(resolve => {finish = resolve}) as ReturnType<typeof request>);
  const props = callbacks();
  const {rerender} = render(<ConversationMessage role="assistant" text="First" task={task()} {...props}/>);
  fireEvent.click(screen.getByRole('button', {name: /View activity/}));
  const signal = mocked.mock.calls[0][1]?.signal;
  rerender(<ConversationMessage role="assistant" text="Second" task={task({id: 'other-task'})} {...props}/>);
  expect(signal?.aborted).toBe(true);
  finish(task({events: [event(1, 'tool.started', {call: {name: 'old_private_tool', call_id: 'old'}})]}));
  await waitFor(() => expect(screen.queryByText('Old private tool')).not.toBeInTheDocument());
  expect(screen.getByText('Second')).toBeVisible();
});

it('renders structured Markdown and a real waiting state without treating user text as markup', () => {
  const props = callbacks();
  const {rerender} = render(<ConversationMessage role="assistant" text={'## Findings\n\n- **Refresh** is stable\n\n| Check | Result |\n| --- | --- |\n| Login | Pass |\n\n```python\nprint("ready")\n```'} {...props}/>);
  expect(screen.getByRole('heading', {name: 'Findings', level: 2})).toBeVisible();
  expect(screen.getByRole('table')).toHaveTextContent('Login');
  expect(screen.getByRole('table')).toHaveAttribute('tabindex', '0');
  expect(screen.getByRole('region', {name: 'Code block'})).toHaveAttribute('tabindex', '0');
  expect(screen.getByText('print("ready")')).toBeVisible();
  rerender(<ConversationMessage role="assistant" text="" task={task({status: 'awaiting_approval', events: []})} {...props}/>);
  expect(screen.getAllByRole('status').some(element => element.textContent?.includes('Waiting for your approval'))).toBe(true);
  expect(screen.getByRole('button', {name: 'Review task'})).toBeVisible();
  expect(document.querySelector('.response-skeleton')).toBeNull();
  rerender(<ConversationMessage role="user" text="<b>literal</b> **text**" {...props}/>);
  expect(screen.getByText('<b>literal</b> **text**')).toBeVisible();
});

it('keeps a long tool stream collapsed and makes failures visible before inspection', () => {
 const events = Array.from({length: 12}, (_, index) => event(index + 1, index === 11 ? 'tool.failed' : 'tool.finished', {call: {name: 'read_file', call_id: 'read-' + index, arguments: {path: 'src/file-' + index + '.ts'}}, result: {ok: index !== 11}}));
 render(<ConversationMessage role="assistant" text="Review complete" task={task({events})} {...callbacks()}/>);
 const summary = screen.getByRole('button', {name: /View activity/});
 expect(summary).toHaveAttribute('aria-expanded', 'false');expect(summary).toHaveTextContent('12 tools');expect(summary).toHaveTextContent('1 failed');
 expect(screen.queryByRole('list', {name: 'Tool activity'})).not.toBeInTheDocument();
 fireEvent.click(summary);expect(screen.getByRole('button', {name: /Read file · src\/file-0.ts/})).toBeVisible();
});

it('opens file references in parent and lazily loaded delegated answers using the current session callback', async () => {
 const open = vi.fn(), value = task({result: 'Update `src/App.tsx:12`.'});
 mocked.mockResolvedValue({...value, events: [], children: [task({id: 'reviewer', prompt: 'Inspect configuration', result: 'See [configuration](config.json#L3).', events: []})]});
 const props = callbacks();render(<ConversationMessage role="assistant" text={value.result!} task={value} onOpenFile={open} {...props}/>);
 fireEvent.click(screen.getByRole('button', {name: 'Open file src/App.tsx:12'}));expect(open).toHaveBeenLastCalledWith('src/App.tsx', 12);
 expect(mocked).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button', {name: /View activity/}));
 fireEvent.click(await screen.findByRole('button', {name: /Agent Inspect configuration/}));
 fireEvent.click(screen.getByRole('button', {name: 'Open file config.json:3'}));expect(open).toHaveBeenLastCalledWith('config.json', 3);
 fireEvent.click(screen.getByRole('button', {name: /Open agent session/}));expect(props.onSupervise).toHaveBeenCalledWith('reviewer');
 expect(mocked).toHaveBeenCalledTimes(1);
});
