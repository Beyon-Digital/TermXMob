import {render,cleanup,waitFor,screen,fireEvent,act} from '@testing-library/react';
import {afterEach,beforeEach,it,expect,vi} from 'vitest';
import Chat from './Chat';
import type {Session} from '../lib/api';
vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({engine_models:[]})),request:vi.fn(async()=>({extensions:[]})),json:()=>({})}));
beforeEach(()=>{Object.defineProperty(HTMLElement.prototype,'getBoundingClientRect',{configurable:true,value(){return {width:800,height:this.classList?.contains('message-row')?140:600,top:0,left:0,right:800,bottom:600}}});Object.defineProperty(HTMLElement.prototype,'offsetHeight',{configurable:true,get(){return this.classList?.contains('message-row')?140:600}});Object.defineProperty(HTMLElement.prototype,'offsetWidth',{configurable:true,get(){return 800}});Object.defineProperty(HTMLElement.prototype,'clientHeight',{configurable:true,get(){return 600}});vi.stubGlobal('ResizeObserver',class{constructor(private callback:ResizeObserverCallback){}observe(target:Element){queueMicrotask(()=>{if(this.active)this.callback([{target,contentRect:target.getBoundingClientRect(),borderBoxSize:[{inlineSize:800,blockSize:target.getBoundingClientRect().height}]}] as unknown as ResizeObserverEntry[],this as unknown as ResizeObserver)})}active=true;unobserve(){}disconnect(){this.active=false}})});
afterEach(()=>{cleanup();vi.unstubAllGlobals()});
it('keeps DOM message rows bounded with10,000 retained turns',async()=>{const session:Session={pinned:false,archived:false,updated_at:1,scroll:0,id:'large',title:'Long conversation',engine:'internal',provider_id:'fixture',model:'test',revision:1,mode:'ask',draft_text:'',turns:Array.from({length:10000},(_,i)=>({id:'turn-'+i,sequence:i+1,prompt:'Message '+i,task:{id:'task-'+i,status:'completed',prompt:'Message '+i,result:'**Response** '+i}}))};const {container}=render(<Chat session={session} engines={[]} providers={[]} onSelect={()=>{}} onRefresh={()=>{}} onError={()=>{}}/>);await waitFor(()=>expect(container.querySelectorAll('.message-row').length).toBeGreaterThan(0));expect(container.querySelectorAll('.message-row').length).toBeLessThan(50)});

it('keeps recovery and pending approvals visible while secondary controls stay out of the transcript',async()=>{
 const session:Session={pinned:false,archived:false,updated_at:1,scroll:0,id:'attention',title:'Review work',engine:'internal',mode:'agent',revision:1,draft_text:'',turns:[
  {id:'recovery',sequence:1,prompt:'Recover this task',task:{prompt:'Recover this task',id:'recover-task',status:'recovery_confirmation_required',result:'',events:[],approvals:[]}},
  {id:'approval',sequence:2,prompt:'Run this command',task:{prompt:'Run this command',id:'approval-task',status:'awaiting_approval',result:'',events:[],approvals:[{id:'approval-exact',kind:'tool',status:'pending',payload:{reason:'Run a command'}}]}}
 ]};
 const {container}=render(<Chat session={session} engines={[]} providers={[]} onSelect={()=>{}} onRefresh={()=>{}} onError={()=>{}}/>);
 expect(await screen.findByRole('button',{name:'Review task'})).toBeVisible();
 expect(screen.getByText('Recovery needs your confirmation.')).toBeVisible();expect(screen.getByRole('button',{name:'Review action'})).toBeVisible();
 expect(container.querySelector('.message-author')).not.toBeInTheDocument();expect(container.querySelector('.task-meta')).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Queue next'})).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Running task actions'})).toBeVisible();expect(screen.getByRole('button',{name:'Stop task'})).toBeVisible();
});

it('brings a virtualized pending approval into view and focuses its actual review without deciding',async()=>{
 const original=Object.getOwnPropertyDescriptor(HTMLElement.prototype,'scrollTo');
 Object.defineProperty(HTMLElement.prototype,'scrollTo',{configurable:true,value(this:HTMLElement,options?:ScrollToOptions|number,y?:number){this.scrollTop=typeof options==='number'?y||0:options?.top||0;queueMicrotask(()=>this.dispatchEvent(new Event('scroll')))}});
 try{
  const session:Session={pinned:false,archived:false,updated_at:1,scroll:0,id:'approval-focus',title:'Pending operation',engine:'internal',mode:'agent',revision:1,draft_text:'',turns:[
   ...Array.from({length:60},(_,index)=>({id:'history-'+index,sequence:index+1,prompt:'Earlier request '+index,task:{id:'history-task-'+index,prompt:'Earlier request '+index,status:'completed',result:'Earlier response '+index}})),
   {id:'latest-approval',sequence:61,prompt:'Run this command',task:{id:'latest-approval-task',prompt:'Run this command',status:'awaiting_approval',approvals:[{id:'review-only',kind:'tool',status:'pending',payload:{reason:'Run the selected command'}}]}}
  ]};
  const {container}=render(<Chat session={session} engines={[]} providers={[]} onSelect={()=>{}} onRefresh={()=>{}} onError={()=>{}}/>);
  const log=screen.getByRole('log');Object.defineProperty(log,'scrollHeight',{configurable:true,get:()=>Number.parseFloat(log.querySelector<HTMLElement>('.message-list')?.style.height||'0')});await waitFor(()=>expect(container.querySelectorAll('.message-row').length).toBeGreaterThan(0));
  fireEvent.wheel(log);log.scrollTop=0;fireEvent.scroll(log);
  await waitFor(()=>expect(screen.queryByRole('button',{name:'Review action'})).not.toBeInTheDocument());
  fireEvent.click(screen.getByRole('button',{name:'Review current task'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Review action'})).toHaveFocus());
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:'Allow once'})).not.toBeInTheDocument();
 }finally{cleanup();if(original)Object.defineProperty(HTMLElement.prototype,'scrollTo',original);else Reflect.deleteProperty(HTMLElement.prototype,'scrollTo')}
});

it('cancels approval focus when the conversation changes before the requested frame',async()=>{
 const session:Session={pinned:false,archived:false,updated_at:1,scroll:0,id:'old-approval',title:'Old approval',engine:'internal',mode:'agent',revision:1,draft_text:'',turns:[{id:'old-turn',sequence:1,prompt:'Old operation',task:{id:'old-task',prompt:'Old operation',status:'awaiting_approval',approvals:[{id:'old-pending',kind:'budget',status:'pending',payload:{suggested_limits:{max_steps:256}}}]}}]};
 const props={engines:[],providers:[],onSelect:()=>{},onRefresh:()=>{},onError:()=>{}};
 const view=render(<Chat session={session} {...props}/>);
 await screen.findByRole('button',{name:'Review budget extension'});
 fireEvent.click(screen.getByRole('button',{name:'Review current task'}));
 view.rerender(<Chat session={{...session,id:'new-conversation',title:'New conversation',turns:[]}} {...props}/>);
 const draft=screen.getByRole('textbox',{name:'Message'});draft.focus();
 await act(async()=>{await new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))) });
 expect(draft).toHaveFocus();expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:'Approve budget extension'})).not.toBeInTheDocument();
});
