import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import NeedsYouInbox from './NeedsYouInbox';
import {request} from '../lib/api';
import type {Mutation} from './use-manager';
import type {NeedsYouItem} from '../lib/bot-schedules';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(cleanup);
beforeEach(()=>{vi.mocked(request).mockReset().mockResolvedValue({data:{acknowledge_schedule_run:{id:'r'}}} as never)});

const item=(id:string):NeedsYouItem=>({id,kind:'run',bot_id:'b1',bot_name:'Triage',title:'Run '+id,detail:'',scheduled:true,run_id:id});
const mutation={busy:false,mutate:async(work:() => Promise<unknown>)=>{await work();return {}}} as unknown as Mutation;
const show=(items:NeedsYouItem[])=><NeedsYouInbox items={items} bots={[{id:'b1',conversation_id:'c1'} as never]} mutation={mutation} onOpen={vi.fn()}/>;

it('moves focus to a line saying nothing needs you only after the last item was answered',async()=>{
 const view=render(show([item('a')]));
 fireEvent.click(screen.getByRole('button',{name:'Dismiss run'}));
 await waitFor(()=>expect(request).toHaveBeenCalled());
 view.rerender(show([]));
 const line=await screen.findByText('Nothing needs you now.');
 await waitFor(()=>expect(line).toHaveFocus());
});

it('does not take focus when the list empties some other way, such as another tab clearing the last item',async()=>{
 const view=render(show([item('a'),item('b')]));
 fireEvent.click(screen.getAllByRole('button',{name:'Dismiss run'})[0]);
 await waitFor(()=>expect(request).toHaveBeenCalled());
 view.rerender(show([item('b')]));
 expect(screen.getByRole('heading',{name:/Needs you/})).toHaveFocus();  // the answered item left, so focus waits on the heading
 view.rerender(show([]));
 expect(screen.queryByText('Nothing needs you now.')).toBeNull();
 expect(document.body).toHaveFocus();
});
