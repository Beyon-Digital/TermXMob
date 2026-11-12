import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor} from '@testing-library/react';
import WorkflowRunDetail from './WorkflowRunDetail';
import {ApiError, request} from '../lib/api';
import {workflowTemplates, type WorkflowRun} from '../lib/workflows';
vi.mock('../lib/api',async()=>{const actual=await vi.importActual('../lib/api');return {...actual,request:vi.fn()}});
const run:WorkflowRun={id:'run',workflow_id:'flow',workflow_revision:2,name:'Project review',status:'awaiting_approval',args:{topic:'Security'},project_id:'p1',parent_session_id:'parent',snapshot:{...workflowTemplates[0],language:'javascript',limits:{max_agents:4,concurrency:2,timeout_s:90}},limits:{max_agents:4,concurrency:2,timeout_s:90},calls:[{id:'call',sequence:1,operation:'agent',name:'Reviewer',status:'awaiting_approval',session_id:'child',task_id:'task',output:null,error:null,started_at:1,finished_at:null}],result:null,error:null,allowed_actions:['cancel'],created_at:1,updated_at:2,finished_at:null};
const props=()=>({runId:'run',ownerId:'owner',projectId:'p1',visible:true,onBack:vi.fn(),onOpenSession:vi.fn(),onAgain:vi.fn(),onAccessLost:vi.fn()});
beforeEach(()=>{vi.mocked(request).mockReset();vi.mocked(request).mockResolvedValue(run)});afterEach(cleanup);

it('shows actual child approval and routes Review to that owned conversation',async()=>{
 const callbacks=props();render(<WorkflowRunDetail {...callbacks}/>);
 await screen.findByRole('heading',{name:'Project review'});
 expect(screen.getByRole('button',{name:/Reviewer Needs approval/})).toHaveAttribute('data-state','closed');
 fireEvent.click(screen.getByRole('button',{name:'Review'}));
 expect(callbacks.onOpenSession).toHaveBeenCalledWith('child');
 expect(screen.queryByText('Raw details')).not.toBeInTheDocument();
});

it('keeps stopping state until host acknowledges a terminal cancellation',async()=>{
 let release!:(value:WorkflowRun)=>void;
 vi.mocked(request).mockImplementation(async(_path,init)=>init?.method==='POST'?new Promise(resolve=>{release=resolve}):run);
 render(<WorkflowRunDetail {...props()}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Stop'}));
 expect(screen.getByRole('button',{name:'Stopping…'})).toBeDisabled();
 await act(async()=>release({...run,status:'cancelling',allowed_actions:['cancel']}));
 expect(screen.getByRole('button',{name:'Stopping…'})).toBeDisabled();
 expect(screen.queryByRole('button',{name:'Run again'})).not.toBeInTheDocument();
});

it('shows interrupted history without a resume claim and starts another run only explicitly',async()=>{
 const interrupted={...run,status:'needs_attention',calls:[],allowed_actions:['retry'],error:{code:'restart',message:'Host restarted'}};
 vi.mocked(request).mockResolvedValue(interrupted);const callbacks=props();render(<WorkflowRunDetail {...callbacks}/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('Host restarted');
 expect(screen.getByText(/This run was interrupted/)).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:/resume/i})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Run again'}));
 expect(callbacks.onAgain).toHaveBeenCalledWith(interrupted);
 expect(vi.mocked(request).mock.calls.every(([,init])=>!init?.method)).toBe(true);
});

it('clears inaccessible run data and ignores cancellation replies after a scope switch',async()=>{
 const callbacks=props();let release!:(value:WorkflowRun)=>void;
 vi.mocked(request).mockImplementation(async(path,init)=>{if(init?.method==='POST')return new Promise(resolve=>{release=resolve});if(path.endsWith('/other'))throw new ApiError(403,'Run unavailable');return run});
 const view=render(<WorkflowRunDetail {...callbacks}/>);
 fireEvent.click(await screen.findByRole('button',{name:'Stop'}));
 view.rerender(<WorkflowRunDetail {...callbacks} ownerId="other-owner" runId="other"/>);
 await waitFor(()=>expect(callbacks.onAccessLost).toHaveBeenCalled());
 await act(async()=>release({...run,status:'cancelled'}));
 expect(screen.queryByRole('heading',{name:'Project review'})).not.toBeInTheDocument();
});


it('identifies a recent activity window without implying it is the complete durable history',async()=>{
 vi.mocked(request).mockResolvedValue({...run,calls_truncated:true,call_count:900});render(<WorkflowRunDetail {...props()}/>);
 expect(await screen.findByText('Recent activity · 1 of 900 entries')).toBeInTheDocument();
 expect(screen.getByRole('list',{name:'Workflow activity'})).toBeInTheDocument();
});
