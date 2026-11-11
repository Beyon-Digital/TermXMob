import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import {StructuredToolResult} from './ContentPreview';
import ToolActivities from './ToolActivities';

afterEach(cleanup);
const run={id:'0123456789abcdef0123456789abcdef',name:'Review project',status:'running',calls:[]};

it('shows the durable running receipt instead of claiming workflow completion or following a result URL',()=>{
 render(<StructuredToolResult kind="run_workflow" value={{ok:true,status:'completed',run_id:run.id,run:{...run,url:'https://untrusted.test/run'}}}/>);
 expect(screen.getByRole('group',{name:'Workflow run receipt'})).toHaveTextContent('Review project');
 expect(screen.getByText('Running')).toBeVisible();expect(screen.queryByText('Completed')).toBeNull();
 expect(screen.getByText('0 phases')).toBeVisible();expect(screen.getByText('0 agent calls')).toBeVisible();
 expect(screen.getByRole('button',{name:'View run'})).toBeEnabled();expect(screen.queryByRole('link')).toBeNull();
});

it.each(['run_workflow','get_workflow_run','cancel_workflow_run','mcp__termx-workflows__run_workflow'])('decodes %s native text receipts and opens only the bounded owned run identifier',kind=>{
 const open=vi.fn();window.addEventListener('termx-open-workflow',open);
 const calls=[{operation:'phase'},{operation:'agent'},{operation:'agent'},{operation:'log'}];
 render(<StructuredToolResult kind={kind} value={{content:[{type:'text',text:JSON.stringify({ok:true,run:{...run,status:'awaiting_approval',calls}})}]}}/>);
 expect(screen.getByText('Needs approval')).toBeVisible();expect(screen.getByText('1 phase')).toBeVisible();expect(screen.getByText('2 agent calls')).toBeVisible();
 expect(open).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'View run'}));
 expect(open).toHaveBeenCalledOnce();expect((open.mock.calls[0][0] as CustomEvent).detail).toEqual({runId:run.id});
 window.removeEventListener('termx-open-workflow',open);
});

it('keeps viewing disabled while hidden and does not create an action from an incomplete receipt',()=>{
 const open=vi.fn();window.addEventListener('termx-open-workflow',open);
 const value={ok:true,run};const view=render(<StructuredToolResult kind="run_workflow" value={value} visible={false}/>);
 const button=screen.getByRole('button',{name:'View run'});expect(button).toBeDisabled();fireEvent.click(button);expect(open).not.toHaveBeenCalled();
 view.rerender(<StructuredToolResult kind="run_workflow" value={value} complete={false}/>);expect(screen.queryByRole('button',{name:'View run'})).toBeNull();
 view.rerender(<StructuredToolResult kind="run_workflow" value={JSON.stringify(value)} complete={false}/>);expect(screen.queryByRole('button',{name:'View run'})).toBeNull();
 window.removeEventListener('termx-open-workflow',open);
});

it.each([
 {kind:'',value:run},
 {kind:'read_file',value:{run}},
 {kind:'some_run_workflow',value:{run}},
 {kind:'mcp__other__run_workflow',value:{run}},
 {kind:'run_workflow',value:{ok:false,run}},
 {kind:'run_workflow',value:{refused:true,run}},
 {kind:'run_workflow',value:{isError:true,content:[{type:'text',text:JSON.stringify({ok:true,run})}]}},
 {kind:'run_workflow',value:{run:{...run,id:'short-run-id'}}},
 {kind:'run_workflow',value:{run_id:'different',run}},
 {kind:'run_workflow',value:{run:{...run,id:'https://untrusted.test/run'}}},
 {kind:'run_workflow',value:{run:{...run,id:'x'.repeat(129)}}},
 {kind:'run_workflow',value:{run:{...run,status:'accepted'}}},
])('does not promote arbitrary or invalid JSON to workflow navigation: $kind',({kind,value})=>{
 render(<StructuredToolResult kind={kind} value={value}/>);expect(screen.queryByRole('button',{name:'View run'})).toBeNull();
});

it('recognizes explicitly typed run objects without inventing activity counts',()=>{
 render(<StructuredToolResult value={{type:'workflow_run',id:'abcdef0123456789abcdef0123456789',name:'Review project',status:'cancelled'}}/>);
 expect(screen.getByText('Stopped')).toBeVisible();expect(screen.queryByText(/phases|agent calls/)).toBeNull();expect(screen.getByRole('button',{name:'View run'})).toBeEnabled();
});

it.each([
 {kind:'run_workflow',output:{ok:true,run}},
 {kind:'mcp__termx-workflows__get_workflow_run',output:{content:[{type:'text',text:JSON.stringify({ok:true,run})}]}},
])('keeps a returned async $kind tool row consistent with its running workflow',({kind,output})=>{
 const events=[{type:'tool.started',sequence:1,payload:{call:{name:kind,call_id:'actual-call',arguments:{}}}},{type:'tool.finished',sequence:2,payload:{call_id:'actual-call',result:output}}];
 render(<ToolActivities events={events}/>);expect(screen.getByText('Running')).toBeVisible();expect(screen.queryByText('Completed')).toBeNull();
 fireEvent.click(screen.getByRole('button',{expanded:false}));expect(screen.getByRole('button',{name:'View run'})).toBeVisible();
});

it('reads a real Codex dynamic tool result while leaving unrelated native tool rows unchanged',()=>{
 const events=[{type:'engine.item.completed',sequence:1,payload:{item:{type:'dynamicToolCall',id:'native-call',tool:'run_workflow',status:'completed',success:true,contentItems:[{type:'inputText',text:JSON.stringify({ok:true,run})}]}}}];
 const view=render(<ToolActivities events={events}/>);expect(screen.getByText('Running')).toBeVisible();expect(screen.queryByText('Completed')).toBeNull();
 fireEvent.click(screen.getByRole('button',{expanded:false}));expect(screen.getByRole('button',{name:'View run'})).toBeVisible();
 view.rerender(<ToolActivities events={[{...events[0],payload:{item:{...events[0].payload.item,tool:'other_tool'}}}]}/>);
 expect(screen.getByText('Completed')).toBeVisible();expect(screen.queryByRole('button',{name:'View run'})).toBeNull();
});

it('recognizes only the owned workflow MCP server and namespace-free dynamic tool',()=>{
 const item={type:'mcpToolCall',id:'native-mcp',server:'termx-workflows',tool:'get_workflow_run',status:'completed',result:{content:[{type:'text',text:JSON.stringify({ok:true,run})}]}};
 const event={type:'engine.item.completed',sequence:1,payload:{item}};
 const view=render(<ToolActivities events={[event]}/>);expect(screen.getByText('Running')).toBeVisible();
 fireEvent.click(screen.getByRole('button',{expanded:false}));expect(screen.getByRole('button',{name:'View run'})).toBeVisible();
 view.rerender(<ToolActivities events={[{...event,payload:{item:{...item,server:'another-server'}}}]}/>);expect(screen.getByText('Completed')).toBeVisible();expect(screen.queryByRole('button',{name:'View run'})).toBeNull();
 view.rerender(<ToolActivities events={[{...event,payload:{item:{...item,type:'dynamicToolCall',namespace:'another-namespace',server:undefined}}}]}/>);expect(screen.getByText('Completed')).toBeVisible();expect(screen.queryByRole('button',{name:'View run'})).toBeNull();
});
