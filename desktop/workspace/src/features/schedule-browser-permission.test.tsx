import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import ScheduleBrowserPermission from './ScheduleBrowserPermission';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
const api=vi.mocked(request),tab={id:'exact-tab',title:'Approved page',url:'https://example.com/tasks',state:'human'};
afterEach(cleanup);beforeEach(()=>{api.mockReset();api.mockResolvedValue({tabs:[tab]})});
it('requires opt-in and binds exact tabs, origins and individually chosen actions',async()=>{
 const changed=vi.fn();render(<ScheduleBrowserPermission conversationId="conversation" grantDays={7} disabled={false} onChange={changed}/>);
 expect(api).not.toHaveBeenCalled();expect(changed).toHaveBeenLastCalledWith({enabled:false,permission:null});
 fireEvent.click(screen.getByRole('checkbox',{name:/Allow this schedule/}));fireEvent.click(await screen.findByRole('checkbox',{name:/Approved page · tab exact-tab/}));
 await waitFor(()=>expect(changed).toHaveBeenLastCalledWith({enabled:true,permission:expect.objectContaining({tabs:[{tab_id:'exact-tab',origins:['https://example.com'],actions:['observe']}],max_seconds:600})}));
 fireEvent.click(screen.getByRole('checkbox',{name:'Navigate within approved origins'}));
 await waitFor(()=>expect(changed.mock.calls.at(-1)?.[0].permission.tabs[0].actions).toEqual(['observe','navigate']));
 expect(screen.getByRole('region',{name:'Exact unattended browser permission'})).toHaveTextContent('exact-tab');
 expect(api).toHaveBeenCalledWith('/api/workspace/scheduled-browser/tabs?conversation_id=conversation');
 fireEvent.click(screen.getByRole('checkbox',{name:/Allow this schedule/}));await waitFor(()=>expect(changed).toHaveBeenLastCalledWith({enabled:false,permission:null}));
});
it('rejects non-origin URLs, omitted current origins and authority beyond the schedule grant',async()=>{
 const changed=vi.fn();render(<ScheduleBrowserPermission conversationId="conversation" grantDays={1} disabled={false} onChange={changed}/>);
 fireEvent.click(screen.getByRole('checkbox',{name:/Allow this schedule/}));fireEvent.click(await screen.findByRole('checkbox',{name:/Approved page/}));
 fireEvent.change(screen.getByLabelText('Approved origins for tab exact-tab'),{target:{value:'https://example.com/path'}});
 await waitFor(()=>expect(changed).toHaveBeenLastCalledWith({enabled:true,permission:null}));
 fireEvent.change(screen.getByLabelText('Approved origins for tab exact-tab'),{target:{value:'https://other.example'}});
 expect(screen.queryByRole('region',{name:'Exact unattended browser permission'})).toBeNull();
 fireEvent.change(screen.getByLabelText('Approved origins for tab exact-tab'),{target:{value:'https://example.com'}});
 fireEvent.change(screen.getByLabelText('Browser permission expires after hours'),{target:{value:'25'}});
 await waitFor(()=>expect(changed).toHaveBeenLastCalledWith({enabled:true,permission:null}));expect(screen.getByRole('alert')).toHaveTextContent('1-day grant');
});
