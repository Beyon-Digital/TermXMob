import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import Chat from './Chat';
import {request,type Session,type Provider} from '../lib/api';
import {draftSubmissionOwners,draftWrites,sessionDrafts,submittingDrafts} from '../lib/drafts';

vi.mock('../lib/api',()=>({gql:vi.fn(async()=>({engine_models:[]})),request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();vi.clearAllMocks()});
const provider:Provider={id:'account',name:'Configured account',model:'',secret_configured:true,capabilities:['functions']};
const session:Session={id:'main',title:'Main agent',engine:'internal',provider_id:'account',mode:'agent',pinned:false,archived:false,draft_text:'Keep this draft',revision:1,updated_at:1,scroll:0,turns:[]};
const props={engines:[],onSelect:vi.fn(),onRefresh:vi.fn(),onError:vi.fn(),coordinating:true};
function response(path:string,current:Session){if(path==='/api/runners')return [];if(path.includes('runner-agents'))return {accounts:[]};if(path.endsWith('/queue'))return {items:[]};return {...current}}

it.each([
 {name:'host models',account:{...provider,models:['discovered-one','discovered-two']}},
 {name:'catalog-only metadata',account:{...provider,model_catalog:{'discovered-one':{},'discovered-two':{}}}}
])('renders $name when the selected provider has an empty raw model allowlist',async({account})=>{
 vi.mocked(request).mockImplementation(async(path)=>response(path,session) as never);
 render(<Chat session={session} {...props} providers={[account]}/>);
 fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));
 const list=screen.getByRole('listbox',{name:'Session model'});
 expect(await within(list).findByRole('option',{name:'discovered-one'})).toBeVisible();expect(within(list).getByRole('option',{name:'discovered-two'})).toBeVisible();
 expect(screen.queryByRole('button',{name:'Reset effort'})).not.toBeInTheDocument();expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Keep this draft');
 expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH'||init?.method==='POST')).toBe(false);
});

it('keeps an explicitly configured allowlist ahead of a wider discovered list',()=>{
 vi.mocked(request).mockImplementation(async(path)=>response(path,session) as never);
 render(<Chat session={session} {...props} providers={[{...provider,model:'chosen-one, chosen-two',models:['unselected-discovered']}]}/>);
 fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));const list=screen.getByRole('listbox',{name:'Session model'});
 expect(within(list).getByRole('option',{name:'chosen-one'})).toBeVisible();expect(screen.queryByRole('option',{name:'unselected-discovered'})).not.toBeInTheDocument();
});

it('saves the chosen main agent provider and model together through the composer without a send',async()=>{
 const current={...session,provider_id:undefined};
 vi.mocked(request).mockImplementation(async(path,init)=>{if(init?.method==='PATCH'){Object.assign(current,JSON.parse(String(init.body)).changes);current.revision++}return response(path,current) as never});
 render(<Chat session={{...current}} {...props} providers={[{...provider,models:['shared-model']},{...provider,id:'second',name:'Second account',models:['shared-model']}]}/>);
 fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));
 expect(screen.getByRole('option',{name:'shared-model · Configured account'})).toBeVisible();expect(screen.getByRole('option',{name:'shared-model · Second account'})).toBeVisible();
 expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='PATCH')).toBe(false);
 fireEvent.click(screen.getByRole('option',{name:'shared-model · Second account'}));
 await waitFor(()=>expect(current.model).toBe('shared-model'));
 const writes=vi.mocked(request).mock.calls.filter(([,init])=>init?.method==='PATCH');expect(writes).toHaveLength(1);expect(writes[0][0]).toBe('/api/workspace/sessions/main');
 expect(JSON.parse(String(writes[0][1]?.body))).toEqual({revision:1,changes:{provider_id:'second',model:'shared-model',reasoning_config:{}}});
 expect(screen.getByRole('textbox',{name:'Message'})).toHaveValue('Keep this draft');expect(vi.mocked(request).mock.calls.some(([,init])=>init?.method==='POST')).toBe(false);
});
