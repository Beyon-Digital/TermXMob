import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import ForkContextDialog from './ForkContextDialog';
import {request,type Session} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();vi.resetAllMocks()});
const session={id:'source',engine:'internal',cwd:'/project',provider_id:'account',turns:[{id:'first',prompt:'Keep this context'},{id:'second',prompt:'Exclude this context'}]} as Session;
it('reviews only selected turns, explicit files and summary and commits the exact digest',async()=>{
 vi.mocked(request).mockImplementation(async path=>path.endsWith('/fork-preview')?{id:'review',digest:'exact',transfer:{messages:['Keep this context'],files:['src/main.ts'],summary:'Decision'}} as never:{id:'fork'} as never);
 const created=vi.fn();render(<ForkContextDialog session={session} engine="codex" onClose={vi.fn()} onCreated={created} onError={vi.fn()}/>);
 fireEvent.click(screen.getByLabelText('Exclude this context'));
 fireEvent.change(screen.getByLabelText('Project files to transfer'),{target:{value:'src/main.ts\nsrc/main.ts'}});
 fireEvent.change(screen.getByLabelText('Transfer summary'),{target:{value:'Decision'}});
 fireEvent.click(screen.getByRole('button',{name:'Preview transferred context'}));
 await screen.findByLabelText('Context transfer preview');
 expect(JSON.parse(vi.mocked(request).mock.calls[0][1]!.body as string)).toEqual({engine:'codex',turn_ids:['first'],files:['src/main.ts'],summary:'Decision'});
 fireEvent.click(screen.getByRole('button',{name:'Edit transferred context'}));expect(screen.getByLabelText('Transfer summary')).toHaveValue('Decision');
 fireEvent.click(screen.getByRole('button',{name:'Preview transferred context'}));await screen.findByLabelText('Context transfer preview');
 fireEvent.click(screen.getByRole('button',{name:'Create linked fork'}));await waitFor(()=>expect(created).toHaveBeenCalledWith('fork'));
 expect(vi.mocked(request).mock.calls.at(-1)?.[0]).toBe('/api/workspace/fork-previews/review/commit');
 expect(JSON.parse(vi.mocked(request).mock.calls.at(-1)![1]!.body as string)).toEqual({digest:'exact',provider_id:'account'});
});
it('fences pending previews on Lock and keeps the selected draft for renewed review',async()=>{
 let resolve!:(value:unknown)=>void;vi.mocked(request).mockImplementation(async()=>await new Promise<unknown>(done=>{resolve=done}) as never);
 render(<ForkContextDialog session={session} engine="codex" onClose={vi.fn()} onCreated={vi.fn()} onError={vi.fn()}/>);
 fireEvent.change(screen.getByLabelText('Transfer summary'),{target:{value:'Preserved'}});fireEvent.click(screen.getByRole('button',{name:'Preview transferred context'}));
 act(()=>window.dispatchEvent(new Event('termx-locked')));await act(async()=>resolve({id:'stale',digest:'stale',transfer:{}}));
 expect(screen.queryByLabelText('Context transfer preview')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Preview transferred context'})).toBeDisabled();
 act(()=>window.dispatchEvent(new Event('termx-session-unlocked')));expect(screen.getByLabelText('Transfer summary')).toHaveValue('Preserved');expect(screen.getByRole('button',{name:'Preview transferred context'})).not.toBeDisabled();
});

it('suspends its dialog while hidden and retains unsent transfer choices without creating a preview',()=>{
 const props={session,engine:'codex',onClose:vi.fn(),onCreated:vi.fn(),onError:vi.fn()};
 const view=render(<ForkContextDialog {...props}/>);
 fireEvent.click(screen.getByLabelText('Exclude this context'));
 fireEvent.change(screen.getByLabelText('Project files to transfer'),{target:{value:'src/main.ts'}});
 fireEvent.change(screen.getByLabelText('Transfer summary'),{target:{value:'Retain these decisions'}});
 view.rerender(<ForkContextDialog {...props} visible={false}/>);
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(request).not.toHaveBeenCalled();
 view.rerender(<ForkContextDialog {...props} visible/>);
 expect(screen.getByLabelText('Exclude this context')).not.toBeChecked();
 expect(screen.getByLabelText('Project files to transfer')).toHaveValue('src/main.ts');
 expect(screen.getByLabelText('Transfer summary')).toHaveValue('Retain these decisions');
 expect(request).not.toHaveBeenCalled();expect(props.onClose).not.toHaveBeenCalled();
});

it('retains a successful fork receipt completed while hidden without navigating away from Studio',async()=>{
 let finish!:(value:unknown)=>void;
 vi.mocked(request).mockImplementation(async path=>path.endsWith('/fork-preview')?{id:'review',digest:'exact',transfer:{}} as never:await new Promise<unknown>(resolve=>{finish=resolve}) as never);
 const props={session,engine:'codex',onClose:vi.fn(),onCreated:vi.fn(),onError:vi.fn()};
 const view=render(<ForkContextDialog {...props}/>);
 fireEvent.click(screen.getByRole('button',{name:'Preview transferred context'}));
 await screen.findByLabelText('Context transfer preview');
 fireEvent.click(screen.getByRole('button',{name:'Create linked fork'}));
 view.rerender(<ForkContextDialog {...props} visible={false}/>);
 await act(async()=>finish({id:'accepted-fork'}));
 expect(props.onCreated).not.toHaveBeenCalled();expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 view.rerender(<ForkContextDialog {...props} visible/>);
 expect(screen.getByRole('status')).toHaveTextContent('Linked conversation created');
 expect(screen.queryByRole('button',{name:'Create linked fork'})).not.toBeInTheDocument();
 expect(vi.mocked(request).mock.calls.filter(([path])=>path.endsWith('/commit'))).toHaveLength(1);
 fireEvent.click(screen.getByRole('button',{name:'Open linked conversation'}));
 expect(props.onCreated).toHaveBeenCalledOnce();expect(props.onCreated).toHaveBeenCalledWith('accepted-fork');
});

it('chooses project files from the exact session checkout and lets the user remove them',async()=>{
 vi.mocked(request).mockResolvedValue({path:'',parent:null,breadcrumbs:[{name:'Project',path:''}],home:null,entries:[{name:'main.ts',path:'src/main.ts',dir:false}],next_offset:null,total:1} as never);
 render(<ForkContextDialog session={{...session,project_id:'project',worktree_id:'tree'} as Session} engine="codex" onClose={vi.fn()} onCreated={vi.fn()} onError={vi.fn()}/>);
 expect(screen.getByLabelText('Project files to transfer')).not.toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Browse files'}));
 fireEvent.click(await screen.findByRole('button',{name:'Select file main.ts'}));
 fireEvent.click(screen.getByRole('button',{name:'Choose file'}));
 expect(screen.getByLabelText('Project files to transfer')).toHaveValue('src/main.ts');
 const target=new URL('http://fixture'+vi.mocked(request).mock.calls[0][0]);
 expect(Object.fromEntries(target.searchParams)).toMatchObject({project_id:'project',session_id:'source',worktree_id:'tree',kind:'file'});
 fireEvent.click(screen.getByRole('button',{name:'Remove file src/main.ts'}));
 expect(screen.getByLabelText('Project files to transfer')).toHaveValue('');
 expect(screen.getByText('No files selected.')).toBeVisible();
});
