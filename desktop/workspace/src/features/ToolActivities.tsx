import {useMemo, useState} from 'react';
import {Button} from '../components/ui/button';
import {Check, ChevronRight, Circle, CircleAlert, FileText, Globe, LoaderCircle, Terminal, Wrench} from 'lucide-react';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {activityPreview, eventObject, toolActivities, type ActivityEvent, type ToolActivity, type ToolPhase} from '../lib/tool-activity';

const phases: Record<ToolPhase, string> = {running: 'Running', pending: 'Waiting', completed: 'Completed', failed: 'Failed', cancelled: 'Stopped', interrupted: 'No result reported'};

export function ActivityStatus({phase}: {phase: ToolPhase}) {
  return phase === 'running' ? <LoaderCircle size={13} className="spin" aria-hidden="true"/> : phase === 'completed' ? <Check size={13} aria-hidden="true"/> : phase === 'failed' ? <CircleAlert size={13} aria-hidden="true"/> : <Circle size={12} aria-hidden="true"/>;
}

function ToolIcon({kind}: {kind: string}) {
  if (/shell|command|check/.test(kind)) return <Terminal size={15}/>;
  if (/file|File/.test(kind)) return <FileText size={15}/>;
  if (/browser|Search/.test(kind)) return <Globe size={15}/>;
  return <Wrench size={15}/>;
}

function outputText(value: unknown) {
  const object = eventObject(value);
  const content = object.stdout ?? object.output ?? object.text ?? object.error ?? object.content ?? object.value ?? value;
  if (Array.isArray(content)) return content.map(item => {
    const row = eventObject(item), nested = eventObject(row.content);
    return row.text ?? nested.text ?? item;
  }).map(item => activityPreview(item)).join('\n');
  return activityPreview(content);
}

export function ToolActivityRow({activity}: {activity: ToolActivity}) {
  const input = activityPreview(activity.input), output = outputText(activity.output);
  return <li className={'tool-timeline-item phase-' + activity.phase}>
    <span className="tool-timeline-icon" aria-hidden="true"><ToolIcon kind={activity.kind}/></span>
    <Collapsible className="tool-action">
      <CollapsibleTrigger className="tool-action-trigger">
        <span className="tool-action-title">{activity.title}</span>
        <span className="tool-action-status"><ActivityStatus phase={activity.phase}/><span>{phases[activity.phase]}</span></span>
        <ChevronRight size={13} className="disclosure-chevron" aria-hidden="true"/>
      </CollapsibleTrigger>
      <CollapsibleContent className="tool-action-content">
        {input && <div className="tool-value"><h4>Input</h4><pre tabIndex={0} aria-label={activity.title + ' input'}>{input}</pre></div>}
        {output && <div className="tool-value"><h4>Result</h4><pre tabIndex={0} aria-label={activity.title + ' result'}>{output}</pre></div>}
        {!input && !output && <p className="tool-detail-empty">{activity.phase === 'running' ? 'Waiting for the tool to return a result.' : 'This tool did not provide a text result.'}</p>}
        {activity.callId && <code className="tool-call-id">{activity.callId}</code>}
        <details className="tool-event-details"><summary>Event details</summary><pre tabIndex={0}>{activityPreview(activity.events.map(event => ({event: event.type, ...event.payload})), 16000)}</pre></details>
      </CollapsibleContent>
    </Collapsible>
  </li>;
}

export default function ToolActivities({events, taskStatus, limit = 60}: {events: ActivityEvent[]; taskStatus?: string; limit?: number}) {
  const [shown, setShown] = useState(limit);
  const activities = useMemo(() => toolActivities(events, taskStatus), [events, taskStatus]);
  if (!activities.length) return null;
  return <>{activities.length > shown && <p className="activity-overflow">Showing the latest {shown} of {activities.length} actions. <Button variant="ghost" size="sm" onClick={() => setShown(value => value + limit)}>Show earlier actions</Button></p>}<ol className="tool-timeline" aria-label="Tool activity">{activities.slice(-shown).map(activity => <ToolActivityRow key={activity.key} activity={activity}/>)}</ol></>;
}
