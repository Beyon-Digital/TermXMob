import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import HostServiceSettings from './HostServiceSettings';
import {gql,request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),gql:vi.fn(),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)})}));
const api=vi.mocked(request),status={installed:true,running:true,ready:false,managed:true,supported:true,backend:'supervisor',requires_login:true};
afterEach(cleanup);beforeEach(()=>{api.mockReset();api.mockResolvedValue(status);vi.mocked(gql).mockReset();vi.mocked(gql).mockResolvedValue({tunnels:{profiles:[{id:'named',name:'Stable profile',provider:'cloudflare',kind:'named'},{id:'quick',name:'Quick profile',provider:'cloudflare',kind:'quick'}]}})});
it('distinguishes running from readiness and requires exact confirmation before shutting down',async()=>{
 render(<HostServiceSettings/>);await screen.findByText(/Running · waiting for readiness/);
 fireEvent.click(screen.getByRole('button',{name:'Stop background service'}));
 expect(api.mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
 const confirm=screen.getByRole('button',{name:'Confirm stop background service'});expect(screen.getByRole('dialog')).toHaveTextContent('disconnect all clients');fireEvent.click(confirm);
 await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/host/service/stop',expect.objectContaining({method:'POST',body:'{}'})));
 await screen.findByText(/Host shutdown requested/);
 expect(api.mock.calls.filter(([path])=>path==='/api/host/service')).toHaveLength(1);
});
it('sends reviewed start arguments and exposes service failures',async()=>{
 api.mockImplementation(async(path)=>{if(path.endsWith('/start'))throw new Error('Desktop sidecar already owns this profile');return status});
 render(<HostServiceSettings/>);await screen.findByRole('button',{name:'Start background service'});
 fireEvent.change(screen.getByLabelText('Installation port'),{target:{value:'9797'}});fireEvent.click(screen.getByRole('button',{name:'Start background service'}));
 expect(screen.getByRole('dialog')).toHaveTextContent('its saved local address');fireEvent.click(screen.getByRole('button',{name:'Confirm start background service'}));
 await screen.findByText('Desktop sidecar already owns this profile');expect(api).toHaveBeenCalledWith('/api/host/service/start',expect.objectContaining({body:'{}'}));
});


it('keeps service installation local unless the user explicitly selects a reviewed tunnel profile',async()=>{
 render(<HostServiceSettings/>);await screen.findByRole('option',{name:'Stable profile · cloudflare · named'});
 expect(screen.getByLabelText('Background service tunnel profile')).toHaveValue('');
 fireEvent.click(screen.getByRole('button',{name:'Install background service'}));expect(screen.getByRole('dialog')).toHaveTextContent('No tunnel will start automatically');fireEvent.click(screen.getByRole('button',{name:'Confirm install background service'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/host/service/install',expect.objectContaining({body:'{"port":8787,"host":"127.0.0.1"}'})));
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 fireEvent.change(screen.getByLabelText('Background service tunnel profile'),{target:{value:'named'}});fireEvent.click(screen.getByRole('button',{name:'Install background service'}));expect(screen.getByRole('dialog')).toHaveTextContent('Stable profile · cloudflare · named · exact profile named');fireEvent.click(screen.getByRole('button',{name:'Confirm install background service'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/host/service/install',expect.objectContaining({body:'{"port":8787,"host":"127.0.0.1","tunnel_profile_id":"named"}'})));
});
