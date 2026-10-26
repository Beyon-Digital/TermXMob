import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import ExtensionManager,{matchesExtensionMetadata} from './ExtensionManager';
import Managers from './Managers';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));const mocked=vi.mocked(request);afterEach(cleanup);beforeEach(()=>{mocked.mockReset()});
const source={scope:'project',folder:'inspect',digest:'disk-one',markdown:'Original instructions',name:'Inspect',bytes:21};
const projectRoot={scope:'project',project_id:'p',path:'/projects/p/.agents',editable:true};
function defaults(path:string){if(path.includes('/compatibility'))return {sources:[]};if(path.endsWith('/skill-sources?project_id=p'))return {sources:[source],roots:[projectRoot],diagnostics:[]};if(path.includes('/versions?'))return {versions:[]};return source}
describe('extension sources and registries',()=>{
 it('opens an advertised editable host source without a project picker and saves its exact revision',async()=>{
  const hostSource={...source,scope:'user',effective_for_publish:true};
  mocked.mockImplementation(async(path,init)=>{
   if(init?.method==='PUT')return {...hostSource,digest:'host-saved'};
   if(path==='/api/workspace/skill-sources')return {sources:[hostSource],roots:[{scope:'user',project_id:null,path:'/host/.agents',editable:true}],precedence:'Project overrides user',diagnostics:[]};
   if(path.includes('/compatibility'))return {sources:[]};
   if(path.includes('/versions?'))return {versions:[]};
   return hostSource;
  });
  render(<ExtensionManager data={{}} projectId={null} onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Original instructions');
  expect(screen.getByText('Add a skill').closest('details')).toHaveAttribute('open');
  expect(screen.queryByRole('combobox',{name:'Skill scope'})).not.toBeInTheDocument();
  expect(screen.queryByText('Project overrides user')).not.toBeInTheDocument();
  expect(screen.getByText('/host/.agents').closest('details')).not.toHaveAttribute('open');
  fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'Host worker instructions'}});
  fireEvent.click(screen.getByRole('button',{name:'Save source'}));
  await waitFor(()=>expect(mocked.mock.calls.some(([,init])=>init?.method==='PUT')).toBe(true));
  expect(JSON.parse(String(mocked.mock.calls.find(([,init])=>init?.method==='PUT')![1]?.body))).toMatchObject({scope:'user',project_id:null,markdown:'Host worker instructions',expected_digest:'disk-one'});
 });
 it('keeps source locations and the add editor collapsed until requested without discarding a collapsed draft',async()=>{
  mocked.mockImplementation(async(path)=>path.includes('/compatibility')?{sources:[]}:{sources:[],roots:[{scope:'user',project_id:null,path:'/host/.agents',editable:true}],diagnostics:[]});
  render(<ExtensionManager data={{}} projectId={null} onChanged={vi.fn()}/>);
  const add=await screen.findByText('Add a skill');expect(add.closest('details')).not.toHaveAttribute('open');
  expect(screen.getByText('Source locations').closest('details')).not.toHaveAttribute('open');
  fireEvent.click(add);await waitFor(()=>expect(add.closest('details')).toHaveAttribute('open'));
  fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'Keep this draft'}});
  fireEvent.click(add);await waitFor(()=>expect(add.closest('details')).not.toHaveAttribute('open'));
  fireEvent.click(add);await waitFor(()=>expect(add.closest('details')).toHaveAttribute('open'));
  expect(screen.getByLabelText('Skill instructions')).toHaveValue('Keep this draft');
 });
 it.each([
  {name:'missing',roots:[]},
  {name:'read-only',roots:[{...projectRoot,editable:false,read_only_reason:'Project source is read-only'}]},
  {name:'different project',roots:[{...projectRoot,project_id:'other'}]},
 ])('does not infer source write authority from a $name root or the selected project ID',async({roots})=>{
  mocked.mockImplementation(async(path)=>path.endsWith('/skill-sources?project_id=p')?{sources:[source],roots,diagnostics:[]}:defaults(path) as any);
  render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);
  expect(await screen.findByRole('heading',{name:'Inspect'})).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Edit source'})).not.toBeInTheDocument();
  expect(screen.queryByText('Add a skill')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'View source'}));await screen.findByDisplayValue('Original instructions');
  expect(screen.getByLabelText('Skill instructions')).toHaveAttribute('readonly');
  expect(screen.getByRole('button',{name:'Save source'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Review skill publication'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Export source'})).toBeEnabled();
  expect(mocked.mock.calls.some(([,init])=>init?.method==='PUT')).toBe(false);
 });
 it('refreshes revoked root authority without dropping the draft or borrowing a host root for a project write',async()=>{
  let editable=true;
  mocked.mockImplementation(async(path,init)=>{
   if(init?.method==='PUT')return {...source,digest:'saved'};
   if(path.endsWith('/skill-sources?project_id=p'))return {sources:[source],roots:[{...projectRoot,editable,read_only_reason:editable?null:'Project permission was revoked'},{scope:'user',path:'/host/.agents',project_id:null,editable:true}],diagnostics:[]};
   return defaults(path) as any;
  });
  render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Original instructions');
  fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'Keep the project draft'}});
  await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh sources'})).toBeEnabled());
  editable=false;fireEvent.click(screen.getByRole('button',{name:'Refresh sources'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Refresh sources'})).toBeEnabled());
  expect(screen.getByLabelText('Skill instructions')).toHaveValue('Keep the project draft');
  expect(screen.getByLabelText('Skill instructions')).toHaveAttribute('readonly');
  expect(screen.getByRole('button',{name:'Save source'})).toBeDisabled();
  expect(mocked.mock.calls.some(([,init])=>init?.method==='PUT')).toBe(false);
  editable=true;fireEvent.click(screen.getByRole('button',{name:'Refresh sources'}));
  await waitFor(()=>expect(screen.getByRole('button',{name:'Save source'})).toBeEnabled());
  fireEvent.click(screen.getByRole('button',{name:'Save source'}));
  await waitFor(()=>expect(mocked.mock.calls.some(([,init])=>init?.method==='PUT')).toBe(true));
  expect(JSON.parse(String(mocked.mock.calls.find(([,init])=>init?.method==='PUT')![1]?.body))).toMatchObject({scope:'project',project_id:'p',expected_digest:'disk-one',markdown:'Keep the project draft'});
 });
 it('ignores a late source read after switching to another project editor',async()=>{
  let completeOld!:(value:unknown)=>void;
  mocked.mockImplementation(async(path)=>{
   if(path.includes('/compatibility'))return {sources:[]};
   if(path.includes('/skill-sources?')){const id=new URLSearchParams(path.split('?')[1]).get('project_id');return {sources:[{...source,name:'Inspect '+id}],roots:[{...projectRoot,project_id:id}],diagnostics:[]}}
   if(path.includes('/versions?'))return {versions:[]};
   if(path.includes('/source?')&&new URLSearchParams(path.split('?')[1]).get('project_id')==='p')return new Promise(resolve=>{completeOld=resolve});
   return {...source,markdown:'Q source instructions',digest:'q-digest'};
  });
  const view=render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));
  view.rerender(<ExtensionManager data={{}} projectId="q" onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Q source instructions');
  await act(async()=>completeOld({...source,markdown:'Old project instructions',digest:'old'}));
  expect(screen.getByLabelText('Skill instructions')).toHaveValue('Q source instructions');
  expect(screen.queryByDisplayValue('Old project instructions')).not.toBeInTheDocument();
  expect(mocked.mock.calls.some(([,init])=>init?.method==='PUT')).toBe(false);
 });
 it('cannot apply an earlier project save acknowledgement to a newer draft target',async()=>{
  let completeOld!:(value:unknown)=>void;
  mocked.mockImplementation(async(path,init)=>{
   if(init?.method==='PUT'){const body=JSON.parse(String(init.body));if(body.project_id==='p')return new Promise(resolve=>{completeOld=resolve});return {...source,markdown:body.markdown,digest:'q-saved'}}
   if(path.includes('/compatibility'))return {sources:[]};
   if(path.includes('/skill-sources?')){const id=new URLSearchParams(path.split('?')[1]).get('project_id');return {sources:[{...source,name:'Inspect '+id}],roots:[{...projectRoot,project_id:id}],diagnostics:[]}}
   if(path.includes('/versions?'))return {versions:[]};
   if(path.includes('/source?')&&new URLSearchParams(path.split('?')[1]).get('project_id')==='q')return {...source,markdown:'Q source instructions',digest:'q-digest'};
   return source;
  });
  const view=render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Original instructions');
  fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'P saved instructions'}});
  fireEvent.click(screen.getByRole('button',{name:'Save source'}));
  view.rerender(<ExtensionManager data={{}} projectId="q" onChanged={vi.fn()}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Q source instructions');
  fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'Q unsaved instructions'}});
  await act(async()=>completeOld({...source,markdown:'P saved instructions',digest:'p-saved'}));
  expect(screen.getByLabelText('Skill instructions')).toHaveValue('Q unsaved instructions');
  fireEvent.click(screen.getByRole('button',{name:'Save source'}));
  await waitFor(()=>expect(mocked.mock.calls.filter(([,init])=>init?.method==='PUT')).toHaveLength(2));
  expect(JSON.parse(String(mocked.mock.calls.filter(([,init])=>init?.method==='PUT')[1][1]?.body))).toMatchObject({project_id:'q',expected_digest:'q-digest',markdown:'Q unsaved instructions'});
 });
 it('retains the actual reviewed skill installation path for an editable authorized root',async()=>{
  const preview={id:'review',digest:'review-digest',bundle:{id:'inspect',version:'1'},permission_diff:{added:[],removed:[]},dependencies:[]};
  const changed=vi.fn();
  mocked.mockImplementation(async(path)=>path.endsWith('/publish-preview')?preview:path==='/api/workspace/extension-previews/review/install'?{id:'installed'}:defaults(path) as any);
  render(<ExtensionManager data={{}} projectId="p" onChanged={changed}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Original instructions');
  fireEvent.click(screen.getByRole('button',{name:'Review skill publication'}));
  await screen.findByRole('heading',{name:'Review inspect · 1'});
  fireEvent.click(screen.getByRole('button',{name:'Install reviewed version'}));
  await waitFor(()=>expect(changed).toHaveBeenCalledOnce());
  expect(JSON.parse(String(mocked.mock.calls.find(([path])=>path==='/api/workspace/extension-previews/review/install')![1]?.body))).toEqual({digest:'review-digest'});
 });
 it('searches safe inventory metadata with all terms while excluding credentials and source drafts',()=>{
  expect(matchesExtensionMetadata({name:'Inspect Code',scope:'project'},'PROJECT inspect')).toBe(true);
  expect(matchesExtensionMetadata({name:'Inspect',scope:'user'},'project inspect')).toBe(false);
  expect(matchesExtensionMetadata({id:'bundle',permissions:['filesystem.read']},'BUNDLE filesystem')).toBe(true);
  expect(matchesExtensionMetadata({folder:'utility',source:'codex',path:'/compatible/utility'},'CODEX utility')).toBe(true);
  expect(matchesExtensionMetadata({name:'Registry',credential:'private-secret',markdown:'private instructions'},'private')).toBe(false);
 });
 it('preserves the selected unsaved conflict draft and exact revision through a no-match search',async()=>{
  mocked.mockImplementation(async(path,init)=>{
   if(init?.method==='PUT')throw new Error('Skill changed on disk');
   if(path.includes('/source?'))return {...source,markdown:'External current file',digest:'disk-two'};
   return defaults(path) as any;
  });
  const data={};
  const props={data,projectId:'p',onChanged:vi.fn()};const view=render(<ExtensionManager {...props}/>);
  fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('External current file');
  fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'My unsaved instruction'}});fireEvent.click(screen.getByText('Save source',{exact:true}));await screen.findByRole('heading',{name:'Source changed on disk'});
  view.rerender(<ExtensionManager {...props} search="nothing matches"/>);
  expect(screen.queryByRole('heading',{name:'Inspect'})).not.toBeInTheDocument();
  expect(screen.getByText(/Search matches: 0 skill sources, 0 compatibility sources, 0 bundles, 0 registries and 0 loaded packages/)).toBeInTheDocument();expect(screen.getByLabelText('Skill instructions')).toHaveValue('My unsaved instruction');expect(screen.getByRole('region',{name:'Current skill source'})).toHaveTextContent('External current file');
  view.rerender(<ExtensionManager {...props} search="project inspect"/>);
  expect(screen.getByRole('heading',{name:'Inspect'})).toBeInTheDocument();
  fireEvent.click(screen.getByText('Use current revision with my draft',{exact:true}));fireEvent.click(screen.getByText('Save source',{exact:true}));await waitFor(()=>expect(mocked.mock.calls.filter(([,init])=>init?.method==='PUT')).toHaveLength(2));
  expect(JSON.parse(String(mocked.mock.calls.filter(([,init])=>init?.method==='PUT')[1][1]?.body))).toMatchObject({markdown:'My unsaved instruction',expected_digest:'disk-two'});
 });
 it('filters installed bundles, registries and loaded packages without dropping their selected inventory',async()=>{
  mocked.mockImplementation(async(path)=>path.endsWith('/packages')?{packages:[{id:'package-one',version:'2',filename:'package.json',permissions:['network.fetch']}]} as any:defaults(path) as any);
  const props={data:{extensions:[{id:'installed-one',version:'1',permissions:['filesystem.read']}],registries:[{id:'registry-one',name:'Catalog registry',kind:'public_https',url:'https://registry.example/index'}]},projectId:'p',onChanged:vi.fn()};
  const view=render(<ExtensionManager {...props}/>);fireEvent.click(screen.getByText('Installed immutable bundles'));fireEvent.click(screen.getByText('Public & private registries'));fireEvent.click(screen.getByRole('button',{name:'Browse packages'}));await screen.findByRole('heading',{name:'package-one · 2'});
  for(const [term,title] of [['installed filesystem','installed-one · 1'],['public_https catalog','Catalog registry'],['network package','package-one · 2']]){view.rerender(<ExtensionManager {...props} search={term}/>);expect(screen.getByText(title,{exact:true}).tagName).toBe('H3')}
  view.rerender(<ExtensionManager {...props} search="absent inventory"/>);expect(screen.queryByText('installed-one · 1',{exact:true})).not.toBeInTheDocument();expect(screen.queryByText('Catalog registry',{exact:true})).not.toBeInTheDocument();expect(screen.queryByText('package-one · 2',{exact:true})).not.toBeInTheDocument();
 });
 it('preserves the unsaved skill after a real revision conflict and requires an explicit current-revision decision',async()=>{
  mocked.mockImplementation(async(path,init)=>{if(init?.method==='PUT')throw new Error('Skill changed on disk');if(path.includes('/source?'))return {...source,markdown:'External edit',digest:'disk-two'};return defaults(path) as any});
  render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('External edit');fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'My unsaved changes'}});fireEvent.click(screen.getByRole('button',{name:'Save source'}));await screen.findByRole('heading',{name:'Source changed on disk'});expect(screen.getByLabelText('Skill instructions')).toHaveValue('My unsaved changes');expect(screen.getByRole('region',{name:'Current skill source'})).toHaveTextContent('External edit');fireEvent.click(screen.getByRole('button',{name:'Use current revision with my draft'}));fireEvent.click(screen.getByRole('button',{name:'Save source'}));await waitFor(()=>expect(mocked.mock.calls.filter(([,init])=>init?.method==='PUT')).toHaveLength(2));expect(JSON.parse(String(mocked.mock.calls.filter(([,init])=>init?.method==='PUT')[1][1]?.body))).toMatchObject({markdown:'My unsaved changes',expected_digest:'disk-two',project_id:'p'});
 });
 it('does not publish an edited unsaved draft and restores history into the editor only',async()=>{
  mocked.mockImplementation(async(path)=>path.includes('/versions?')?{versions:[{id:'version',digest:'old',created_at:1}]}:path.endsWith('/versions/version')?{markdown:'Old history'}:defaults(path) as any);
  render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Original instructions');expect(screen.getByRole('button',{name:'Review skill publication'})).toBeEnabled();fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'Unsaved'}});expect(screen.getByRole('button',{name:'Review skill publication'})).toBeDisabled();fireEvent.click(screen.getByText('Immutable version history'));fireEvent.click(screen.getByRole('button',{name:'Restore as draft'}));await screen.findByDisplayValue('Old history');expect(screen.getByRole('button',{name:'Review skill publication'})).toBeDisabled();expect(mocked.mock.calls.some(([p])=>p.includes('/publish-preview'))).toBe(false);
 });
 it('keeps the actual manager source editor and save acknowledgement mounted through inventory refresh',async()=>{
  mocked.mockImplementation(async(path,init)=>{if(path==='/auth/me')return {principal:{scopes:['host-admin']}} as any;if(path==='/api/workspace/extensions')return {extensions:[],registries:[],formats:['termx-bundle/v1']} as any;if(init?.method==='PUT')return {...source,markdown:'Saved instructions',digest:'saved'};return defaults(path) as any});
  render(<Managers section="extensions" projectId="p" sessionId={null}/>);fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Original instructions');fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'Saved instructions'}});fireEvent.click(screen.getByRole('button',{name:'Save source'}));await screen.findByText('Skill source saved');await waitFor(()=>expect(mocked.mock.calls.filter(([p])=>p==='/api/workspace/extensions').length).toBeGreaterThan(1));expect(screen.getByLabelText('Skill instructions')).toHaveValue('Saved instructions');expect(screen.getByLabelText('Skill folder name')).toHaveValue('inspect');expect(screen.getByRole('button',{name:'Review skill publication'})).toBeEnabled();
 });
 it('prevents native file selection during a pending source save instead of discarding the import',async()=>{
  let release!:(value:any)=>void;mocked.mockImplementation(async(path,init)=>init?.method==='PUT'?new Promise(resolve=>{release=resolve}):defaults(path) as any);
  render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);fireEvent.click(await screen.findByRole('button',{name:'Edit source'}));await screen.findByDisplayValue('Original instructions');fireEvent.change(screen.getByLabelText('Skill instructions'),{target:{value:'Saved draft'}});fireEvent.click(screen.getByRole('button',{name:'Save source'}));await waitFor(()=>expect(screen.getByLabelText('Import SKILL.md')).toBeDisabled());release({...source,markdown:'Saved draft',digest:'saved'});await waitFor(()=>expect(screen.getByLabelText('Import SKILL.md')).toBeEnabled());expect(screen.getByLabelText('Skill instructions')).toHaveValue('Saved draft');
 });
 it('sends private registry credentials once without exposing a provider reference or retaining the secret in the form',async()=>{
  mocked.mockImplementation(async(path)=>defaults(path) as any);render(<ExtensionManager data={{}} projectId="p" onChanged={vi.fn()}/>);fireEvent.click(screen.getByText('Public & private registries'));fireEvent.change(screen.getByLabelText('Registry adapter'),{target:{value:'private_https'}});fireEvent.change(screen.getByLabelText('Registry name'),{target:{value:'Private'}});fireEvent.change(screen.getByLabelText('HTTPS index URL'),{target:{value:'https://registry.example/index'}});fireEvent.change(screen.getByLabelText('Registry credential'),{target:{value:'private-fixture-secret'}});fireEvent.click(screen.getByRole('button',{name:'Add registry'}));await waitFor(()=>expect(mocked.mock.calls.some(([p,init])=>p==='/api/workspace/registries'&&init?.method==='POST')).toBe(true));const payload=JSON.parse(String(mocked.mock.calls.find(([p])=>p==='/api/workspace/registries')![1]?.body));expect(payload).toEqual({name:'Private',kind:'private_https',url:'https://registry.example/index',credential:'private-fixture-secret'});expect(screen.getByLabelText('Registry credential')).toHaveValue('');
 });
});
