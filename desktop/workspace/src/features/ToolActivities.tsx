import {Tool,ToolHeader,ToolContent} from '../components/ai-elements/tool';
import {toolActivities} from '../lib/tool-activity';
import type {Task} from '../lib/api';
export default function ToolActivities({events}:{events:NonNullable<Task['events']>}){return <>{toolActivities(events).map(activity=><Tool key={activity.key}><ToolHeader type="dynamic-tool" toolName={activity.title} state={activity.state}/><ToolContent><pre>{JSON.stringify(activity.events.map(event=>({event:event.type,...event.payload})),null,2)}</pre></ToolContent></Tool>)}</>}
