import {memo, useEffect, useMemo, useState} from 'react';
import {Bot, Check, ChevronRight, CircleAlert, Copy, GitBranch, LoaderCircle, Pause, Sparkles} from 'lucide-react';
import {Message, MessageContent, MessageResponse} from '../components/ai-elements/message';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {Button} from '../components/ui/button';
import {delegatedActivities, taskIsActive, taskResponseText, toolActivities, type AgentActivity, type ToolPhase} from '../lib/tool-activity';
import {taskState} from '../lib/task-presentation';
import {request, type Task} from '../lib/api';
import ToolActivities, {ActivityStatus} from './ToolActivities';
import BudgetApproval from './BudgetApproval';
import {GeneralApprovalCard} from './GeneralApprovalCard';
import './conversation-message.css';

type Props = {role: 'user' | 'assistant'; text: string; task?: Task; engine?: string; visible?: boolean;
  onSupervise: (id: string) => void; onRefresh: () => void; onError: (error: unknown) => void};

function phase(status: string): ToolPhase {
  return status === 'completed' ? 'completed' : status === 'failed' ? 'failed' : status === 'cancelled' ? 'cancelled' : status === 'interrupted' ? 'interrupted' : taskIsActive(status) ? 'running' : 'pending';
}

function AgentRow({agent, rootTaskId, onSupervise, depth = 0}: {agent: AgentActivity; rootTaskId: string; onSupervise: (id: string) => void; depth?: number}) {
  const [open, setOpen] = useState(false);
  const status = agent.status === 'interrupted' ? {label: 'No result reported'} : taskState(agent.status);
  return <li className="delegated-agent">
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="agent-activity-trigger">
        <span className="agent-avatar" aria-hidden="true"><Bot size={15}/></span>
        <span className="agent-activity-label"><strong>{agent.name}</strong>{agent.prompt && <span>{agent.prompt}</span>}</span>
        <span className={'agent-activity-status phase-' + phase(agent.status)}><ActivityStatus phase={phase(agent.status)}/><span>{status.label}</span></span>
        <ChevronRight size={14} className="disclosure-chevron" aria-hidden="true"/>
      </CollapsibleTrigger>
      <CollapsibleContent className="agent-activity-content">
        {open && <>{agent.prompt && <p className="agent-task-prompt">{agent.prompt}</p>}
          {agent.truncated && <p className="activity-loading">Showing recent activity for this agent.</p>}
          <ToolActivities events={agent.events} taskStatus={agent.status} limit={20}/>
          {agent.result && <div className="agent-result"><MessageResponse mode="static">{agent.result}</MessageResponse></div>}
          {agent.children.length > 0 && depth < 3 && <ul className="delegated-agents nested" aria-label={agent.name + ' delegated agents'}>{agent.children.slice(0, 12).map(child => <AgentRow key={child.id} agent={child} rootTaskId={rootTaskId} onSupervise={onSupervise} depth={depth + 1}/>)}</ul>}
          <Button variant="ghost" size="sm" onClick={() => onSupervise(agent.taskId || rootTaskId)}>{agent.taskId ? 'Open agent session' : 'Open parent task'} <GitBranch size={12}/></Button>
        </>}
      </CollapsibleContent>
    </Collapsible>
  </li>;
}

function RunActivity({task, onSupervise, visible = true}: {task: Task; onSupervise: (id: string) => void; visible?: boolean}) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<Task | null>(null), [loading, setLoading] = useState(false), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  useEffect(() => {
    // Recovery can resume the same task ID. Its previous terminal history is
    // no longer authoritative after the task begins producing live events.
    if (task.events !== undefined || taskIsActive(task.status)) setSaved(null);
  }, [task.id, task.status, task.events !== undefined]);
  // Completed turn projections intentionally omit events. Fetch their history
  // only when inspected, never once per message during conversation polling.
  useEffect(() => {
    if (!open || !visible || task.events !== undefined || saved?.id === task.id) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    request<Task>('/api/workspace/tasks/' + encodeURIComponent(task.id) + '?activity=true', {signal: controller.signal})
      .then(value => {if (!controller.signal.aborted && value.id === task.id) setSaved(value);})
      .catch(reason => {if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Activity could not be loaded');})
      .finally(() => {if (!controller.signal.aborted) setLoading(false);});
    return () => controller.abort();
  }, [open, visible, task.id, task.events, saved?.id, retry]);
  const details = task.events !== undefined ? task : saved?.id === task.id ? saved : task;
  const events = details.events || [];
  const tools = useMemo(() => toolActivities(events, task.status), [details.events, task.status]);
  const agents = useMemo(() => delegatedActivities(events, details.children, task.status), [details.events, details.children, task.status]);
  const current = [...tools].reverse().find(tool => tool.phase === 'running' || tool.phase === 'pending');
  const activeAgents = agents.filter(agent => taskIsActive(agent.status)).length;
  const needsAttention = task.status === 'awaiting_approval' || task.status === 'recovery_confirmation_required';
  const ongoing = taskIsActive(task.status);
  if (!tools.length && !agents.length && task.events !== undefined) return null;
  const summary = needsAttention ? 'Your input is needed' : activeAgents ? `${activeAgents} ${activeAgents === 1 ? 'agent is' : 'agents are'} working` : ongoing ? current?.title || 'Working on your request' : 'View activity';
  const count = [tools.length ? `${tools.length} ${tools.length === 1 ? 'tool' : 'tools'}` : '', agents.length ? `${agents.length} ${agents.length === 1 ? 'agent' : 'agents'}` : ''].filter(Boolean).join(' · ');
  return <Collapsible className={'run-activity' + (ongoing ? ' is-working' : '')} open={open} onOpenChange={setOpen}>
    <CollapsibleTrigger className="run-activity-trigger">
      {ongoing ? <LoaderCircle size={15} className="spin" aria-hidden="true"/> : needsAttention || task.status === 'failed' ? <CircleAlert size={15} aria-hidden="true"/> : <GitBranch size={15} aria-hidden="true"/>}
      <span className="run-activity-summary">{summary}</span><span className="run-activity-count">{count}</span><ChevronRight size={14} className="disclosure-chevron" aria-hidden="true"/>
    </CollapsibleTrigger>
    <CollapsibleContent className="run-activity-body">
      {open && <>{loading && <p className="activity-loading" role="status"><LoaderCircle size={13} className="spin"/> Loading activity…</p>}
        {error && <p className="activity-load-error" role="alert">{error} <Button size="sm" variant="ghost" onClick={() => setRetry(value => value + 1)}>Retry activity</Button></p>}
        {details.activity_truncated && <p className="activity-loading">Showing recent activity for this run.</p>}
        {!loading && !error && !tools.length && !agents.length && saved?.id === task.id && <p className="activity-loading">No tool or delegated-agent activity was found in this history.</p>}
        {agents.length > 0 && <section className="agent-team"><h3><Bot size={14}/> Agent team</h3><ul className="delegated-agents" aria-label="Delegated agents">{agents.slice(0, 24).map(agent => <AgentRow key={agent.id} agent={agent} rootTaskId={task.id} onSupervise={onSupervise}/>)}</ul></section>}
        <ToolActivities events={events} taskStatus={task.status}/>
        <Button className="run-inspect" variant="ghost" size="sm" onClick={() => onSupervise(task.id)}>Open task details <ChevronRight size={12}/></Button>
      </>}
    </CollapsibleContent>
  </Collapsible>;
}

function ResponseLoader({task}: {task: Task}) {
  const busy = taskIsActive(task.status);
  const labels: Record<string, string> = {queued: 'Waiting to start', planning: 'Planning the next steps', running: 'Preparing a response', recovering: 'Restoring this task', cancelling: 'Stopping this task', awaiting_approval: 'Waiting for your approval', paused: 'This task is paused', recovery_confirmation_required: 'Review recovery before continuing', cancelled: 'This task was stopped', failed: 'This task needs attention', completed: 'This turn finished without a text response'};
  return <div className={'response-loader' + (busy ? ' is-loading' : '')} role="status" aria-live="polite">
    <div className="response-loader-label">{busy ? <Sparkles size={15} aria-hidden="true"/> : task.status === 'failed' ? <CircleAlert size={15} aria-hidden="true"/> : <Pause size={15} aria-hidden="true"/>}<span>{labels[task.status] || 'Preparing this conversation'}</span></div>
    {busy && <div className="response-skeleton" aria-hidden="true"><span/><span/><span/></div>}
  </div>;
}

function ConversationMessage({role, text, task, visible, onSupervise, onRefresh, onError}: Props) {
  const [copied, setCopied] = useState(false);
  const content = task ? text || taskResponseText(task) : text;
  const ongoing = taskIsActive(task?.status);
  async function copy() {try {await navigator.clipboard.writeText(content); setCopied(true);} catch (error) {onError(error);}}
  if (role === 'user') return <Message from="user"><MessageContent><p className="user-message-text">{text}</p></MessageContent></Message>;
  return <Message from="assistant"><MessageContent>
    <div className="response-author"><span className="response-brand" aria-hidden="true">T</span><span>TermX</span></div>
    {task && <RunActivity key={task.id} task={task} onSupervise={onSupervise} visible={visible}/>}
    {content ? <MessageResponse mode={ongoing ? 'streaming' : 'static'} isAnimating={ongoing}>{content}</MessageResponse> : task ? <ResponseLoader task={task}/> : null}
    {task?.error && <div className="response-error" role="alert"><CircleAlert size={16} aria-hidden="true"/><div><strong>The task stopped with an error</strong><p>{task.error}</p><Button size="sm" variant="ghost" onClick={() => onSupervise(task.id)}>Review task</Button></div></div>}
    {task?.status === 'recovery_confirmation_required' && <p className="response-recovery">Review the task’s last action before resuming. <Button variant="ghost" size="sm" onClick={() => onSupervise(task.id)}>Review recovery</Button></p>}
    {(task?.approvals || []).filter(approval => approval.status === 'pending').map(approval => approval.kind === 'budget'
      ? <BudgetApproval key={approval.id} task={task!} approval={approval} onDone={onRefresh} onError={onError}/>
      : <GeneralApprovalCard key={approval.id} taskId={task!.id} approval={approval} onDone={onRefresh} onError={onError}/>)}
    {(content || task) && <div className="response-actions">
      {content && <Button size="icon" variant="ghost" aria-label={copied ? 'Answer copied' : 'Copy answer'} title={copied ? 'Copied' : 'Copy answer'} onClick={() => void copy()}>{copied ? <Check size={15}/> : <Copy size={15}/>}</Button>}
      {task && <Button variant="ghost" size="sm" onClick={() => onSupervise(task.id)} aria-label="Supervise task">Task details</Button>}
      {task && <span className={'response-status phase-' + phase(task.status)}>{ongoing && <LoaderCircle size={12} className="spin" aria-hidden="true"/>}{taskState(task.status).label}</span>}
    </div>}
  </MessageContent></Message>;
}

export default memo(ConversationMessage);
