import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import CloudAccounts from './CloudAccounts';
import CloudMachines from './CloudMachines';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,body:unknown)=>({method,body:JSON.stringify(body)})}));
const mocked=vi.mocked(request);
const account={id:'account',name:'Development',provider:'aws',identity:{account:'123456789012'},authenticated:true};
const catalog={regions:[{id:'us-east-1',label:'US East'}],zones:[{id:'us-east-1a',label:'Zone A'}],sizes:[{id:'t3.small',label:'Small'}],images:[{id:'ami-fixture',label:'Ubuntu'}],volume_types:[{id:'gp3',label:'SSD'}]};
afterEach(cleanup);
beforeEach(()=>{
 mocked.mockReset();mocked.mockImplementation(async(path,init)=>{
  if(path==='/api/cloud/accounts')return {accounts:[account],secure_storage:true} as any;
  if(path.includes('/catalog?'))return catalog as any;
  if(path==='/api/cloud/preview'){const plan=JSON.parse(String(init?.body));return {id:'review',plan,identity:account.identity,billing:'Provider charges apply',cleanup_resources:['VM','disks']} as any}
  return [] as any;
 });
});
it('verifies provider credentials in Settings, clears secrets after save, and never stores them in browser storage',async()=>{
 render(<CloudAccounts search=""/>);
 fireEvent.change(screen.getByLabelText('Account name'),{target:{value:'Build account'}});
 fireEvent.change(screen.getByLabelText('AWS access key ID'),{target:{value:'fixture-key'}});
 fireEvent.change(screen.getByLabelText('AWS secret access key'),{target:{value:'fixture-secret'}});
 fireEvent.click(screen.getByRole('button',{name:'Verify and save account'}));
 expect(await screen.findByRole('status')).toHaveTextContent('Authentication verified and saved');
 const call=mocked.mock.calls.find(([path,init])=>path==='/api/cloud/accounts'&&init?.method==='POST');
 expect(JSON.parse(String(call?.[1]?.body))).toEqual({name:'Build account',provider:'aws',credentials:{access_key_id:'fixture-key',secret_access_key:'fixture-secret'}});
 expect(screen.getByLabelText('AWS secret access key')).toHaveValue('');
 expect(JSON.stringify(localStorage)).not.toContain('fixture-secret');
});
async function configure(){
 render(<CloudMachines projectId="project" projects={[{id:'project',name:'Project'} as any]} onChange={async()=>{}}/>);
 fireEvent.click(screen.getByRole('button',{name:'Create cloud machine'}));
 await screen.findByRole('option',{name:'Development · AWS'});
 fireEvent.change(screen.getByLabelText('Provider account'),{target:{value:'account'}});
 await screen.findByRole('option',{name:'US East'});
 for(const [label,value] of [['Cloud runner name','Build runner'],['Region','us-east-1'],['Availability zone','us-east-1a'],['Machine size','t3.small'],['Operating system','ami-fixture'],['Boot volume type','gp3'],['Allowed SSH source CIDR','203.0.113.1/32']]){
  await waitFor(()=>expect(screen.getByLabelText(label)).toBeEnabled());
  fireEvent.change(screen.getByLabelText(label),{target:{value}});
 }
 fireEvent.click(screen.getByRole('checkbox',{name:/Allow agents to use/}));
 fireEvent.click(screen.getByRole('checkbox',{name:/I authorize provider usage charges/}));
}
it('invalidates a review when configuration changes and retries a lost launch response with the same reviewed request',async()=>{
 await configure();
 fireEvent.click(screen.getByRole('button',{name:'Review provisioning'}));
 await screen.findByRole('button',{name:'Create reviewed machine'});
 expect(mocked.mock.calls.filter(([path,init])=>path==='/api/cloud/deployments'&&init?.method==='POST')).toHaveLength(0);
 fireEvent.change(screen.getByLabelText('Boot volume size (GiB)'),{target:{value:'64'}});
 expect(screen.queryByRole('button',{name:'Create reviewed machine'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Review provisioning'}));
 await screen.findByRole('button',{name:'Create reviewed machine'});
 const base=mocked.getMockImplementation()!;let attempts=0;
 mocked.mockImplementation(async(path,init)=>{if(path==='/api/cloud/deployments'&&init?.method==='POST'&&attempts++===0)throw new Error('Response lost');return base(path,init)});
 fireEvent.click(screen.getByRole('button',{name:'Create reviewed machine'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Response lost');
 fireEvent.click(screen.getByRole('button',{name:'Create reviewed machine'}));
 await waitFor(()=>expect(screen.queryByRole('button',{name:'Create reviewed machine'})).not.toBeInTheDocument());
 const calls=mocked.mock.calls.filter(([path,init])=>path==='/api/cloud/deployments'&&init?.method==='POST');
 expect(calls).toHaveLength(2);expect(calls[0][1]?.body).toBe(calls[1][1]?.body);
 const reviews=mocked.mock.calls.filter(([path])=>path==='/api/cloud/preview').map(([,init])=>JSON.parse(String(init?.body)));
 expect(reviews[1].request_id).not.toBe(reviews[0].request_id);expect(reviews[1].boot.size_gb).toBe(64);
});
it('reviews destructive cleanup and leaves failed cleanup visible for a retry',async()=>{
 const base=mocked.getMockImplementation()!;
 mocked.mockImplementation(async(path,init)=>{
  if(path==='/api/cloud/deployments')return [{id:'deployment',provider:'aws',status:'cleanup-failed',cleanup_error:'Credentials expired',resources:[],plan:{name:'Build runner',region:'us-east-1',size:'t3.small',boot:{size_gb:32},volumes:[]}}] as any;
  return base(path,init);
 });
 render(<CloudMachines projectId="project" projects={[]} onChange={async()=>{}}/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('Credentials expired');
 fireEvent.click(screen.getByRole('button',{name:'Retry cleanup'}));
 expect(mocked.mock.calls.filter(([path])=>path.endsWith('/cleanup'))).toHaveLength(0);
 expect(screen.getByRole('group',{name:'Review cloud cleanup'})).toHaveTextContent('Existing networks and subnets');
 fireEvent.click(screen.getByRole('button',{name:'Delete managed resources'}));
 await waitFor(()=>expect(mocked).toHaveBeenCalledWith('/api/cloud/deployments/deployment/cleanup',expect.objectContaining({method:'POST'})));
 expect(screen.getByRole('alert')).toHaveTextContent('may still incur charges');
});
