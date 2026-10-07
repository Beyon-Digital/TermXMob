import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {GeneralApprovalCard} from './GeneralApprovalCard';
const api=vi.hoisted(()=>({request:vi.fn()}));vi.mock('../lib/api',()=>api);
beforeEach(()=>api.request.mockReset());afterEach(cleanup);
const preview={source:'host-rendered-target/v1',document_hash:'exact-rendered-hash',origin:'https://merchant.test',profile_name:'Account profile',profile_id:'profile',operation:'click',target_label:'Buy order',consequence:'Places this exact order.',reason:'Page labels are untrusted evidence.',manual_required:false,merchant:'<script>Untrusted merchant</script>',amount:'12.50',currency:'EUR',fields:[{label:'Item',type:'text',value:'Selected item'}]};
it('shows host-observed transaction and payload before exact consent without treating page text as markup',async()=>{
 api.request.mockResolvedValue({});const done=vi.fn();const view=render(<GeneralApprovalCard taskId="task" approval={{id:'a',payload:{browser_review:{human_preview:preview}}}} onDone={done} onError={vi.fn()}/>);
 expect(screen.getByRole('region',{name:'Host-observed browser operation'})).toBeTruthy();expect(screen.getByText('12.50 EUR')).toBeTruthy();expect(screen.getByText('<script>Untrusted merchant</script>')).toBeTruthy();expect(view.container.querySelector('script')).toBeNull();expect(screen.getByText('Selected item')).toBeTruthy();
 fireEvent.click(screen.getByRole('button',{name:'Allow once'}));await waitFor(()=>expect(done).toHaveBeenCalledOnce());expect(JSON.parse(api.request.mock.calls[0][1].body)).toEqual({decision:'approved'});
});
it('native wrapped approval with incomplete or sensitive evidence cannot dispatch Allow',async()=>{
 render(<GeneralApprovalCard taskId="task" approval={{id:'a',payload:{request:{browser_review:{human_preview:{...preview,manual_required:true,fields:[{label:'Payment card',type:'text',value:'[redacted]'}]}}}}}} onDone={vi.fn()} onError={vi.fn()}/>);
 expect(screen.getByRole('button',{name:'Allow once'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'Allow once'}));expect(api.request).not.toHaveBeenCalled();expect(screen.getByRole('status').textContent).toContain('Take over / Private login');expect(screen.getByText('[redacted]')).toBeTruthy();expect(screen.getByRole('button',{name:'Deny'})).toBeEnabled();
});
