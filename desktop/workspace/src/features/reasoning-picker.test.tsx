import {afterEach,it,expect,vi} from 'vitest';
import {act,cleanup,render,screen,fireEvent,waitFor} from '@testing-library/react';
import ReasoningPicker from './ReasoningPicker';
import {request,type Session} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),gql:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();vi.clearAllMocks()});
const session:Session={id:'conversation-a',title:'A',engine:'codex',model:'fast',mode:'ask',pinned:false,archived:false,draft_text:'',revision:7,scroll:0,updated_at:1};
const option=(values:string[])=>({id:'reasoning_effort',name:'Reasoning',category:'thought_level',type:'select',currentValue:'low',options:values.map(value=>({value,name:value}))});
const catalogue={config_options:[option(['low','high'])],model_configurations:{fast:{config_options:[option(['low'])]},slow:{config_options:[option(['low','high'])]}},stale:false};
const slider=()=>screen.getByRole('slider',{name:'Reasoning'});
const setEffort=(index:number)=>{fireEvent.change(slider(),{target:{value:String(index)}});fireEvent.pointerUp(slider())};
function transport(current=session){vi.mocked(request).mockImplementation(async(path,init)=>path.endsWith('/reasoning')?catalogue as any:init?.method==='PATCH'?{...current,reasoning_config:JSON.parse(init.body as string).changes.reasoning_config} as any:{...current} as any)}

it('uses only exact model values and fetches the current draft-safe revision before committing a slider value',async()=>{
 transport({...session,revision:12});const changed=vi.fn();render(<ReasoningPicker session={session} onUpdated={changed} onError={()=>{}}/>);
 await waitFor(()=>expect(slider()).toBeEnabled());expect(slider()).toHaveAttribute('max','1');expect(screen.queryByText('High')).not.toBeInTheDocument();
 fireEvent.change(slider(),{target:{value:'1'}});expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
 fireEvent.pointerUp(slider());await waitFor(()=>expect(changed).toHaveBeenCalled());
 const call=vi.mocked(request).mock.calls.find(([,init])=>init?.method==='PATCH')!;
 expect(call[0]).toBe('/api/workspace/sessions/conversation-a');expect(JSON.parse(call[1]!.body as string)).toEqual({revision:12,changes:{reasoning_config:{reasoning_effort:'low'}}});
});

it('offers an explicit Default position and commits keyboard Home without inventing a default value',async()=>{
 const current={...session,reasoning_config:{reasoning_effort:'low'}};transport(current);const changed=vi.fn();
 render(<ReasoningPicker session={current} onUpdated={changed} onError={()=>{}}/>);await waitFor(()=>expect(slider()).toBeEnabled());
 expect(slider()).toHaveValue('1');fireEvent.change(slider(),{target:{value:'0'}});fireEvent.keyUp(slider(),{key:'Home'});
 await waitFor(()=>expect(changed).toHaveBeenCalled());const call=vi.mocked(request).mock.calls.find(([,init])=>init?.method==='PATCH')!;
 expect(JSON.parse(call[1]!.body as string).changes.reasoning_config).toEqual({});
});

it('keeps active-turn sliders disabled and shows no invented missing-model or internal engine choices',async()=>{
 transport();const {rerender}=render(<ReasoningPicker session={session} disabled onUpdated={()=>{}} onError={()=>{}}/>);
 await waitFor(()=>expect(slider()).toBeDisabled());setEffort(1);expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
 rerender(<ReasoningPicker session={{...session,model:'unknown'}} onUpdated={()=>{}} onError={()=>{}}/>);
 await waitFor(()=>expect(screen.getByText('Engine default effort')).toBeInTheDocument());expect(screen.queryByRole('slider')).not.toBeInTheDocument();
 rerender(<ReasoningPicker session={{...session,engine:'internal'}} onUpdated={()=>{}} onError={()=>{}}/>);
 expect(screen.queryByRole('slider')).not.toBeInTheDocument();
});

it('does not expose stale capabilities or replace an existing choice when discovery fails',async()=>{
 vi.mocked(request).mockResolvedValue({...catalogue,stale:true,refresh_error:'offline'});
 render(<ReasoningPicker session={{...session,reasoning_config:{reasoning_effort:'low'}}} onUpdated={()=>{}} onError={()=>{}}/>);
 expect(await screen.findByText('Effort temporarily unavailable')).toBeInTheDocument();expect(screen.queryByRole('slider')).not.toBeInTheDocument();expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
});

it('refuses a change if the current conversation moved to a different model after the slider opened',async()=>{
 transport({...session,model:'slow'});const error=vi.fn();render(<ReasoningPicker session={session} onUpdated={()=>{}} onError={error}/>);
 await waitFor(()=>expect(slider()).toBeEnabled());setEffort(1);await waitFor(()=>expect(error).toHaveBeenCalled());expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
});

it('does not apply an old session response after switching conversations',async()=>{
 let resolve!:(value:Session)=>void;const pending=new Promise<Session>(done=>resolve=done);
 vi.mocked(request).mockImplementation(async(path,init)=>path.endsWith('/reasoning')?catalogue as any:init?.method==='PATCH'?pending:{...session} as any);
 const changed=vi.fn();const {rerender}=render(<ReasoningPicker session={session} onUpdated={changed} onError={()=>{}}/>);
 await waitFor(()=>expect(slider()).toBeEnabled());setEffort(1);await waitFor(()=>expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(true));
 rerender(<ReasoningPicker session={{...session,id:'conversation-b'}} onUpdated={changed} onError={()=>{}}/>);
 await act(async()=>{resolve(session);await pending});expect(changed).not.toHaveBeenCalled();
});

it('fences A→B→A responses and prevents duplicate commits from pointer release and blur',async()=>{
 let settle!:(value:Session)=>void;const pending=new Promise<Session>(resolve=>settle=resolve);
 vi.mocked(request).mockImplementation(async(path,init)=>path.endsWith('/reasoning')?catalogue as any:init?.method==='PATCH'?pending:{...session} as any);
 const changed=vi.fn(),error=vi.fn();const props={onUpdated:changed,onError:error};
 const {rerender}=render(<ReasoningPicker session={session} {...props}/>);await waitFor(()=>expect(slider()).toBeEnabled());
 setEffort(1);fireEvent.blur(slider());await waitFor(()=>expect(vi.mocked(request).mock.calls.filter(([,init])=>init?.method==='PATCH')).toHaveLength(1));expect(slider()).toBeDisabled();
 rerender(<ReasoningPicker session={{...session,id:'conversation-b'}} {...props}/>);await waitFor(()=>expect(slider()).toBeEnabled());
 rerender(<ReasoningPicker session={session} {...props}/>);await waitFor(()=>expect(slider()).toBeEnabled());
 await act(async()=>{settle({...session,reasoning_config:{reasoning_effort:'low'}});await pending});
 expect(changed).not.toHaveBeenCalled();expect(error).not.toHaveBeenCalled();
});
