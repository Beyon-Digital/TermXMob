import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import LanTlsSettings from './LanTlsSettings';
import {request} from '../lib/api';
const {beginSave,saved}=vi.hoisted(()=>({beginSave:vi.fn(),saved:vi.fn()}));
vi.mock('./SettingsSurface',()=>({useSettingsSave:()=>beginSave}));
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,data:unknown)=>({method,body:JSON.stringify(data)})}));
const api=vi.mocked(request);
const status={configuration:{enabled:false,host:'0.0.0.0',port:8788,names:[]},active:{enabled:false},restart_required:true};
afterEach(()=>{cleanup();vi.restoreAllMocks()});
beforeEach(()=>{api.mockReset();api.mockResolvedValue(status);beginSave.mockReset();saved.mockReset();beginSave.mockReturnValue(saved)});

it('keeps network and trust details disclosed and never enables or saves during loading',async()=>{
 render(<LanTlsSettings/>);
 await screen.findByText('Local network HTTPS is off · Restart required');
 expect(screen.getByRole('checkbox',{name:'Enable LAN HTTPS after restart'})).not.toBeChecked();
 expect(screen.getByText('Network settings',{selector:'summary'}).closest('details')).not.toHaveAttribute('open');
 expect(screen.getByText('Device trust and certificate rotation',{selector:'summary'}).closest('details')).not.toHaveAttribute('open');
 expect(api).toHaveBeenCalledOnce();expect(api).toHaveBeenCalledWith('/auth/lan-tls');expect(beginSave).not.toHaveBeenCalled();
});

it('reviews exact interface, port and additional names before enabling and cancels without a request',async()=>{
 const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);
 render(<LanTlsSettings/>);await screen.findByRole('checkbox');
 fireEvent.click(screen.getByRole('checkbox',{name:'Enable LAN HTTPS after restart'}));
 fireEvent.click(screen.getByText('Network settings',{selector:'summary'}));
 fireEvent.change(screen.getByLabelText('HTTPS port'),{target:{value:'9443'}});
 fireEvent.change(screen.getByLabelText('Bind address'),{target:{value:'192.168.64.10'}});
 fireEvent.change(screen.getByLabelText('Additional .local names or private addresses'),{target:{value:'workstation.local\n192.168.64.11'}});
 fireEvent.click(screen.getByRole('button',{name:'Save LAN HTTPS settings'}));
 expect(confirm).toHaveBeenCalledWith('After the next host restart, allow authenticated HTTPS/WSS clients to reach this host at 192.168.64.10:9443? No firewall rule or trust root will be installed. Additional names: workstation.local, 192.168.64.11.');
 expect(api.mock.calls.some(([,init])=>init?.method==='PUT')).toBe(false);expect(beginSave).not.toHaveBeenCalled();
 expect(screen.getByLabelText('Bind address')).toHaveValue('192.168.64.10');
});

it('saves only reviewed settings and acknowledges success while retaining explicit restart guidance',async()=>{
 vi.spyOn(window,'confirm').mockReturnValue(true);
 api.mockImplementation(async(_path,init)=>init?.method==='PUT'?{message:'Saved. Restart the host to apply LAN exposure changes. No trust root was installed.'}:status);
 render(<LanTlsSettings/>);await screen.findByRole('checkbox');
 fireEvent.click(screen.getByRole('checkbox',{name:'Enable LAN HTTPS after restart'}));
 fireEvent.click(screen.getByText('Network settings',{selector:'summary'}));
 fireEvent.change(screen.getByLabelText('Additional .local names or private addresses'),{target:{value:'workstation.local  192.168.64.11'}});
 fireEvent.click(screen.getByRole('button',{name:'Save LAN HTTPS settings'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith('/auth/lan-tls',{method:'PUT',body:JSON.stringify({enabled:true,host:'0.0.0.0',port:8788,names:['workstation.local','192.168.64.11']})}));
 expect(await screen.findByText('Saved. Restart the host to apply LAN exposure changes. No trust root was installed.')).toBeInTheDocument();
 expect(saved).toHaveBeenCalledOnce();expect(api.mock.calls.filter(([,init])=>init?.method==='PUT')).toHaveLength(1);
});

it('preserves failed drafts without acknowledging a save and keeps active public certificate diagnostics available',async()=>{
 api.mockImplementation(async(_path,init)=>{if(init?.method==='PUT')throw new Error('Host declined the settings');return {...status,active:{enabled:true,urls:['https://workstation.local:8788'],ca_fingerprint:'synthetic-public-fingerprint',expires_at:'fixture expiry',ca_expires_at:'fixture CA expiry'}}});
 render(<LanTlsSettings/>);await screen.findByRole('checkbox');
 const certificate=screen.getByText('Addresses and certificates',{selector:'summary'}).closest('details');expect(certificate).not.toHaveAttribute('open');
 fireEvent.click(screen.getByText('Addresses and certificates',{selector:'summary'}));
 expect(screen.getByText('https://workstation.local:8788')).toBeInTheDocument();expect(screen.getByText('synthetic-public-fingerprint')).toBeInTheDocument();
 fireEvent.click(screen.getByText('Network settings',{selector:'summary'}));
 fireEvent.change(screen.getByLabelText('HTTPS port'),{target:{value:'9443'}});
 fireEvent.click(screen.getByRole('button',{name:'Save LAN HTTPS settings'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Host declined the settings');expect(saved).not.toHaveBeenCalled();expect(screen.getByLabelText('HTTPS port')).toHaveValue(9443);
});
