import {useMemo,useState} from 'react';
import {ChevronRight,LoaderCircle} from 'lucide-react';
import {Tool,ToolHeader,ToolContent} from '../components/ai-elements/tool';
import {toolActivities} from '../lib/tool-activity';
import type {Task} from '../lib/api';

export default function ToolActivities({events}:{events:NonNullable<Task['events']>}){
 const activities=useMemo(()=>toolActivities(events),[events]);
 const [expanded,setExpanded]=useState(false);
 if(!activities.length)return null;
 const running=activities.filter(activity=>activity.state==='input-available');
 const failed=activities.filter(activity=>activity.state==='output-error');
 const last=running.at(-1)||activities.at(-1)!;
 const status=failed.length?`${failed.length} failed`:running.length?'Working':'Completed';
 const payload=(activity:typeof last)=><pre>{JSON.stringify(activity.events.map(event=>({event:event.type,...event.payload})),null,2)}</pre>;
 return <details className="task-activity" onToggle={event=>setExpanded(event.currentTarget.open)}>
  <summary><ChevronRight className="activity-chevron" size={13}/>{running.length>0&&<LoaderCircle size={12} className="spin"/>}<span>{activities.length===1?last.title:`${activities.length} actions`}</span><small className={failed.length?'activity-error':''}>{status}</small>{activities.length>1&&<span className="activity-latest">{last.title}</span>}</summary>
  {expanded&&<div className="task-activity-content">{activities.length===1?payload(last):activities.map(activity=><Tool key={activity.key}><ToolHeader type="dynamic-tool" toolName={activity.title} state={activity.state}/><ToolContent>{payload(activity)}</ToolContent></Tool>)}</div>}
 </details>
}
