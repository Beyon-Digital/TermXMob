import {render,screen,fireEvent,cleanup,act} from '@testing-library/react';
import {afterEach,beforeEach,it,expect,vi} from 'vitest';
import Chat from './Chat';
import {sessionDrafts,draftWrites,submittingDrafts,draftSubmissionOwners} from '../lib/drafts';
import {acceptTransferredDraft} from '../lib/redock-state';
import {persistWindowDraft} from '../lib/window-journal';
import {request,type Session,type Task} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';
vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({engine_models:[]})),request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
vi.mock('../lib/window-journal',()=>({persistWindowDraft:vi.fn(async()=>{})}));
beforeEach(()=>{vi.mocked(request).mockResolvedValue({} as never)});
afterEach(()=>{cleanup();vi.useRealTimers();sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();vi.clearAllMocks()});
const props={engines:[],providers:[],onSelect:()=>{},onRefresh:()=>{},onError:vi.fn()};
const row=(id:string,task?:Partial<Task>):Session=>({id,title:id,engine:'internal',mode:'ask',pinned:false,archived:false,draft_text:'',revision:1,updated_at:1,scroll:0,turns:task?[{id:'turn-1',prompt:'Do the work',task:{id:'task-1',prompt:'Do the work',status:'running',...task}}]:[]} as Session);

it('keeps the transcript silent and routes status changes through one polite announcer',()=>{
 const view=render(<Chat session={row('live',{status:'completed',result:'Earlier answer'})} {...props}/>);
 const log=screen.getByRole('log');
 expect(log).toHaveAttribute('aria-live','off');
 const announcer=screen.getByRole('status');
 expect(announcer).toHaveAttribute('aria-live','polite');
 expect(announcer).toBeEmptyDOMElement();
 expect(screen.getAllByRole('status')).toHaveLength(1);
 view.rerender(<Chat session={row('live',{id:'task-2',status:'running',result:''})} {...props}/>);
 expect(screen.getByRole('status')).toHaveTextContent('Response started');
 view.rerender(<Chat session={row('live',{id:'task-2',status:'running',result:'Partial text that keeps streaming'})} {...props}/>);
 expect(screen.getByRole('status')).toHaveTextContent('Response started');
 view.rerender(<Chat session={row('live',{id:'task-2',status:'awaiting_approval',approvals:[{id:'a1',kind:'tool',status:'pending',title:'Run tests'}] as never})} {...props}/>);
 expect(screen.getByRole('status')).toHaveTextContent('Approval needed');
 view.rerender(<Chat session={row('live',{id:'task-2',status:'completed',result:'Done'})} {...props}/>);
 expect(screen.getByRole('status')).toHaveTextContent('Response complete');
 expect(screen.getAllByRole('status')).toHaveLength(1);
});

it('does not announce an already finished conversation when it is opened or switched to',()=>{
 const view=render(<Chat session={row('first',{status:'running'})} {...props}/>);
 view.rerender(<Chat session={row('second',{id:'task-9',status:'completed',result:'Old'})} {...props}/>);
 expect(screen.getByRole('status')).toBeEmptyDOMElement();
});

it('has no accessibility violations with the announcer, transcript and composer mounted',async()=>{
 const {container}=render(<Chat session={row('axe',{status:'completed',result:'Finished the change.'})} {...props}/>);
 await expectNoAxeViolations(container);
});

it('writes the window-journal draft after a pause in typing, and immediately when leaving the conversation',()=>{
 vi.useFakeTimers({toFake:['setTimeout','clearTimeout']});
 const view=render(<Chat session={row('debounced')} ownerId="owner" {...props}/>);
 act(()=>{vi.advanceTimersByTime(400)});
 vi.mocked(persistWindowDraft).mockClear();
 const box=screen.getByLabelText('Message');
 for(const text of ['h','he','hel'])fireEvent.change(box,{target:{value:text}});
 expect(persistWindowDraft).not.toHaveBeenCalled();
 act(()=>{vi.advanceTimersByTime(299)});
 expect(persistWindowDraft).not.toHaveBeenCalled();
 act(()=>{vi.advanceTimersByTime(1)});
 expect(persistWindowDraft).toHaveBeenCalledTimes(1);
 expect(vi.mocked(persistWindowDraft).mock.calls[0][2]).toMatchObject({text:'hel'});
 vi.mocked(persistWindowDraft).mockClear();
 fireEvent.change(box,{target:{value:'help'}});
 view.unmount();
 expect(persistWindowDraft).toHaveBeenCalledTimes(1);
 expect(vi.mocked(persistWindowDraft).mock.calls[0][2]).toMatchObject({text:'help'});
});

it('adopts a draft handed over from another window only for the matching open conversation',()=>{
 const session=row('handoff');
 render(<Chat session={session} {...props}/>);
 act(()=>acceptTransferredDraft({version:1,sessionId:'someone-else',buffers:[],draft:{text:'not mine',saved:'',dirty:true}}));
 expect(screen.getByLabelText('Message')).toHaveValue('');
 act(()=>acceptTransferredDraft({version:1,sessionId:'handoff',buffers:[],draft:{text:'from the detached window',saved:'',dirty:true}}));
 expect(screen.getByLabelText('Message')).toHaveValue('from the detached window');
});

it('does not replay an earlier handoff in a conversation mounted afterwards',()=>{
 act(()=>acceptTransferredDraft({version:1,sessionId:'late',buffers:[],draft:{text:'old handoff',saved:'',dirty:true}}));
 sessionDrafts.clear();
 render(<Chat session={row('late')} {...props}/>);
 expect(screen.getByLabelText('Message')).toHaveValue('');
});

it('shows the stopping state in every panel through the shared draft store',()=>{
 const running=row('stop',{status:'running'});
 const first=render(<Chat session={running} {...props}/>);
 const second=render(<Chat session={running} {...props}/>);
 let release!:()=>void;
 vi.mocked(request).mockImplementation(((path:string)=>path.endsWith('/cancel')?new Promise<void>(resolve=>{release=resolve}):Promise.resolve({})) as never);
 fireEvent.click(first.getAllByRole('button',{name:'Stop task'})[0]);
 expect(second.getAllByRole('button',{name:'Stop task'})[0]).toBeDisabled();
 act(()=>release());
});
