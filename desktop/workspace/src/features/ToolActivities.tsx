import {useMemo, useState} from 'react';
import {Button} from '../components/ui/button';
import {Check, ChevronRight, Circle, CircleAlert, FileText, Globe, LoaderCircle, Monitor, Terminal, Wrench} from 'lucide-react';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {StructuredToolResult,RawContentDetails,isWorkflowReceiptTool,workflowRunReceipt} from './ContentPreview';
import {workflowStatus} from '../lib/workflows';
import {eventObject, toolActivities, type ActivityEvent, type ToolActivity, type ToolPhase} from '../lib/tool-activity';

const phases: Record<ToolPhase, string> = {running: 'Running', pending: 'Waiting', completed: 'Completed', failed: 'Failed', cancelled: 'Stopped', interrupted: 'No result reported'};

export function ActivityStatus({phase}: {phase: ToolPhase}) {
  return phase === 'running' ? <LoaderCircle size={13} className="spin" aria-hidden="true"/> : phase === 'completed' ? <Check size={13} aria-hidden="true"/> : phase === 'failed' ? <CircleAlert size={13} aria-hidden="true"/> : <Circle size={12} aria-hidden="true"/>;
}

function ToolIcon({kind}: {kind: string}) {
  if (/^desktop_/.test(kind)) return <Monitor size={15}/>;
  if (/shell|command|check/.test(kind)) return <Terminal size={15}/>;
  if (/file|File/.test(kind)) return <FileText size={15}/>;
  if (/browser|Search/.test(kind)) return <Globe size={15}/>;
  return <Wrench size={15}/>;
}

function DesktopDetails({activity}: {activity: ToolActivity}) {
  const view = activity.desktop!;
  const name = activity.title + ' details';
  return <>
    {view.facts.length > 0 && <ul className="desktop-facts" aria-label={name}>{view.facts.map(([label, value]) => <li key={label}><span>{label}</span><strong>{value}</strong></li>)}</ul>}
    {view.notes.map(note => <p className="desktop-note" key={note}>{note}</p>)}
    {!view.facts.length && !view.notes.length && <p className="tool-detail-empty">{activity.phase === 'running' ? 'Waiting for the application to answer.' : 'No details were reported.'}</p>}
  </>;
}

function Row({activity, status, notePhase, statusIcon, children}: {activity: ToolActivity; status: string; notePhase?: boolean; statusIcon?: React.ReactNode; children: React.ReactNode}) {
  return <li className={'tool-timeline-item phase-' + (notePhase ? 'note' : activity.phase)}>
    <span className="tool-timeline-icon" aria-hidden="true"><ToolIcon kind={activity.kind}/></span>
    <Collapsible className="tool-action">
      <CollapsibleTrigger className="tool-action-trigger">
        <span className="tool-action-title">{activity.title}</span>
        <span className="tool-action-status">{statusIcon ?? <ActivityStatus phase={activity.phase}/>}<span>{status}</span></span>
        <ChevronRight size={13} className="disclosure-chevron" aria-hidden="true"/>
      </CollapsibleTrigger>
      <CollapsibleContent className="tool-action-content">{children}</CollapsibleContent>
    </Collapsible>
  </li>;
}

/** Desktop calls show only the host's short summary: no input, raw result, call id or event dump. */
function DesktopActivityRow({activity}: {activity: ToolActivity}) {
  const quiet = !!activity.desktop?.quiet;
  return <Row activity={activity} status={quiet ? 'Screen changed' : phases[activity.phase]} notePhase={quiet} statusIcon={quiet ? <Circle size={12} aria-hidden="true"/> : undefined}>
    <DesktopDetails activity={activity}/>
  </Row>;
}

function workflowActivityKind(activity:ToolActivity){
 if(isWorkflowReceiptTool(activity.kind))return activity.kind;
 if(!['dynamicToolCall','mcpToolCall'].includes(activity.kind))return activity.kind;
 for(const event of activity.events){
  const item=eventObject(event.payload.item),tool=typeof item.tool==='string'?item.tool:'';
  if(isWorkflowReceiptTool(tool)&&(activity.kind==='dynamicToolCall'&&!item.namespace||item.server==='termx-workflows'))return tool;
 }
 return activity.kind;
}

export function ToolActivityRow({activity,taskId,onOpenFile,visible=true}: {activity: ToolActivity;taskId?:string;onOpenFile?:(path:string,line?:number)=>void;visible?:boolean}) {
  if (activity.desktop) return <DesktopActivityRow activity={activity}/>;
  const hasInput=activity.input!==undefined&&activity.input!==null,hasOutput=activity.output!==undefined&&activity.output!==null;
  const kind=workflowActivityKind(activity),complete=['completed','failed'].includes(activity.phase),receipt=workflowRunReceipt(activity.output,kind,complete);
  return <Row activity={activity} status={receipt?workflowStatus(receipt.status).label:phases[activity.phase]} statusIcon={receipt?<Circle size={12} aria-hidden="true"/>:undefined}>
    {hasOutput&&<div className="tool-value"><h4>Result</h4><div role="region" tabIndex={0} aria-label={activity.title+' result'}><StructuredToolResult value={activity.output} kind={kind} taskId={taskId} onOpenFile={onOpenFile} visible={visible} complete={complete}/></div></div>}
    {!hasInput&&!hasOutput&&<p className="tool-detail-empty">{activity.phase==='running'?'Waiting for the tool to return a result.':'This tool did not provide a text result.'}</p>}
    {hasInput&&<RawContentDetails title={activity.title+' arguments'} value={activity.input}/>}
    <RawContentDetails title="Raw details" value={{call_id:activity.callId,state:activity.state,phase:activity.phase,input:activity.input,result:activity.output,events:activity.events.map(event=>({event:event.type,...event.payload}))}}/>
  </Row>;
}

export default function ToolActivities({events, taskStatus, limit = 60,taskId,onOpenFile,visible=true}: {events: ActivityEvent[]; taskStatus?: string; limit?: number;taskId?:string;onOpenFile?:(path:string,line?:number)=>void;visible?:boolean}) {
  const [shown, setShown] = useState(limit);
  const activities = useMemo(() => toolActivities(events, taskStatus), [events, taskStatus]);
  if (!activities.length) return null;
  return <>{activities.length > shown && <p className="activity-overflow">Showing the latest {shown} of {activities.length} actions. <Button variant="ghost" size="sm" onClick={() => setShown(value => value + limit)}>Show earlier actions</Button></p>}<ol className="tool-timeline" aria-label="Tool activity">{activities.slice(-shown).map(activity => <ToolActivityRow key={activity.key} activity={activity} taskId={taskId} onOpenFile={onOpenFile} visible={visible}/>)}</ol></>;
}
