import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import {gql} from '../lib/api';
import DesktopControl from './DesktopControl';
import {DESKTOP_FIELDS,type DesktopStatus} from './desktop-control-api';

vi.mock('../lib/api',()=>({gql:vi.fn()}));
const graph=vi.mocked(gql);
const NOTE='Checked against the checksum built into this version of TermX. The release’s build provenance (GitHub attestation) is verified when TermX publishes its checksums, not on your computer.';

const base:DesktopStatus={installed:false,source:null,path:null,version:null,pinned_version:'1.2.0',verified:false,helper_ok:null,platform:'macos',arch:'arm64',
 platform_supported:true,platform_reason:null,envelope_version:null,ready:false,permission:{state:'unknown',guidance:'',grant_to:null},supported_surfaces:[],issues:[],
 install:{available:true,asset:'agent-desktop-v1.2.0-aarch64-apple-darwin.tar.gz',size_bytes:2230641,host:'github.com',note:NOTE},configured_path:null,setting_locked:false};
const ready:DesktopStatus={...base,installed:true,source:'managed',path:'/Users/me/.config/termx/desktop-control/bin/1.2.0/agent-desktop',version:'1.2.0',verified:true,helper_ok:true,
 envelope_version:'2.6',ready:true,permission:{state:'granted',guidance:'',grant_to:null},supported_surfaces:['window','menu','sheet']};
const denied:DesktopStatus={...ready,ready:false,permission:{state:'denied',guidance:'Allow Terminal in System Settings, Privacy & Security, Accessibility, then choose Check again.',grant_to:'Terminal'},
 issues:[{code:'permission_denied',message:'This computer has not allowed TermX to read other applications.',action:'Grant the permission, then check again.'}]};

let current:DesktopStatus;
const calls=()=>graph.mock.calls.map(([query,variables])=>({query:String(query),variables:variables as Record<string,unknown>|undefined}));
// The path field's own error region is always mounted (and empty), so look for alerts that say something.
const spoken=()=>screen.queryAllByRole('alert').filter(node=>node.textContent);
const alertText=async()=>{await waitFor(()=>expect(spoken()).toHaveLength(1));return spoken()[0]};
const named=(name:string)=>calls().filter(call=>call.query.includes(name));
function serve(status:DesktopStatus,after?:Partial<Record<'install'|'remove'|'path',unknown>>){
 current=status;
 graph.mockImplementation((async(query:string,variables?:Record<string,unknown>)=>{
  if(query.includes('desktop_control_install')){if(after?.install instanceof Error)throw after.install;current=(after?.install as DesktopStatus)||ready;return {desktop_control_install:current}}
  if(query.includes('desktop_control_remove')){current=(after?.remove as DesktopStatus)||base;return {desktop_control_remove:current}}
  if(query.includes('desktop_control_set_path')){if(after?.path instanceof Error)throw after.path;current={...current,source:variables?.path?'configured':'managed',path:String(variables?.path||current.path),verified:!variables?.path};return {desktop_control_set_path:current}}
  if(query.includes('desktop_control_status'))return {desktop_control_status:current};
  if(query.includes('machine'))return {machine:{capabilities:{agent_desktop:{ready:false,version:null,issue:'not_installed'}}}};
  throw new Error('unexpected query '+query);
 }) as never);
}
beforeEach(()=>{graph.mockReset()});
afterEach(cleanup);

it('checks the status first and says so',async()=>{
 serve(base);render(<DesktopControl admin/>);
 expect(screen.getByRole('status')).toHaveTextContent('Checking desktop control…');
 await screen.findByRole('button',{name:'Install desktop control'});
 expect(named('desktop_control_status')[0].variables).toEqual({refresh:false});
});

it('explains what desktop control is and that it takes no screenshots',async()=>{
 serve(base);render(<DesktopControl admin/>);
 expect(await screen.findByText(/Lets your agents read and operate other applications through their accessibility controls, in the background\. It does not take screenshots\./)).toBeVisible();
 expect(screen.getByRole('heading',{name:'Desktop control'})).toBeVisible();
});

it('says plainly when this computer is not supported, with no actions',async()=>{
 serve({...base,platform:'linux',arch:'riscv64',platform_supported:false,platform_reason:'pin_incomplete',install:{...base.install,available:false}});
 render(<DesktopControl admin/>);
 expect(await screen.findByText('Desktop control is not available for linux riscv64 in this version of TermX.')).toBeVisible();
 expect(screen.queryByRole('button',{name:/Install desktop control/})).toBeNull();
});

it('offers the install with the version, size and source before anything is downloaded',async()=>{
 serve(base);render(<DesktopControl admin/>);
 expect(await screen.findByText(/Not installed\. TermX can download version 1\.2\.0 \(2\.2 MB\) from github\.com and check it against a checksum built into this version before running it\./)).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Install desktop control'}));
 const dialog=await screen.findByRole('dialog',{name:'Install desktop control'});
 expect(named('desktop_control_install')).toHaveLength(0);
 const facts=within(dialog).getByRole('list',{name:'What will be installed'});
 expect(facts).toHaveTextContent('Version1.2.0');expect(facts).toHaveTextContent('Sourcegithub.com');expect(facts).toHaveTextContent('vercel-labs/agent-desktop');
 expect(facts).toHaveTextContent('LicenseApache-2.0');expect(facts).toHaveTextContent('Size2.2 MB');expect(facts).toHaveTextContent('agent-desktop-v1.2.0-aarch64-apple-darwin.tar.gz');
 expect(dialog).toHaveTextContent('build provenance (GitHub attestation) is verified when TermX publishes its checksums, not on your computer');
 expect(dialog).toHaveTextContent('Checked against the checksum built into TermX before it runs.');
 expect(dialog).toHaveTextContent('Agents can then read and operate the applications you allow, in the background.');
 expect(dialog).toHaveTextContent('Text read from an application is sent to your model provider.');
});

it('installs only after confirmation, sending the version that was shown and consent',async()=>{
 serve(base);render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByRole('button',{name:'Install desktop control'}));
 const dialog=await screen.findByRole('dialog',{name:'Install desktop control'});
 fireEvent.click(within(dialog).getByRole('button',{name:'Install desktop control'}));
 await waitFor(()=>expect(named('desktop_control_install')).toHaveLength(1));
 expect(named('desktop_control_install')[0].variables).toEqual({input:{version:'1.2.0',consent:true}});
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 expect(await screen.findByText('Ready')).toBeVisible();
 expect(screen.getByText(/Version 1\.2\.0/)).toBeVisible();
});

it('cancelling the install changes nothing',async()=>{
 serve(base);render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByRole('button',{name:'Install desktop control'}));
 fireEvent.click(within(await screen.findByRole('dialog')).getByRole('button',{name:'Cancel'}));
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
 expect(named('desktop_control_install')).toHaveLength(0);
});

it('shows the install in progress and blocks a second click',async()=>{
 serve(base);let finish!:(value:unknown)=>void;
 graph.mockImplementation((async(query:string)=>query.includes('desktop_control_install')?new Promise(resolve=>{finish=resolve}):{desktop_control_status:base}) as never);
 render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByRole('button',{name:'Install desktop control'}));
 const dialog=await screen.findByRole('dialog');const confirm=within(dialog).getByRole('button',{name:'Install desktop control'});
 fireEvent.click(confirm);
 expect(within(dialog).getByRole('status')).toHaveTextContent('Installing…');
 expect(confirm).toBeDisabled();expect(dialog).toHaveAttribute('aria-busy','true');
 fireEvent.click(confirm);expect(named('desktop_control_install')).toHaveLength(1);
 finish({desktop_control_install:ready});
 await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull());
});

it('keeps the dialog open and says what went wrong when the install fails',async()=>{
 serve(base,{install:new Error('The download did not match the checksum, so nothing was installed.')});render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByRole('button',{name:'Install desktop control'}));
 const dialog=await screen.findByRole('dialog');
 fireEvent.click(within(dialog).getByRole('button',{name:'Install desktop control'}));
 expect(await within(dialog).findByRole('alert')).toHaveTextContent('The download did not match the checksum, so nothing was installed.');
 expect(within(dialog).getByRole('button',{name:'Install desktop control'})).toBeEnabled();
});

it('shows a file that does not match as an alert with reinstall and remove, not as ready',async()=>{
 serve({...ready,ready:false,verified:false,issues:[{code:'hash_mismatch',message:'The file does not match what this version of TermX expects, so it was not run.',action:'Reinstall desktop control.'}]});
 render(<DesktopControl admin/>);
 expect(await alertText()).toHaveTextContent('The file does not match what this version of TermX expects, so it was not run.');
 expect(screen.getByRole('button',{name:'Reinstall desktop control'})).toBeEnabled();
 expect(screen.getByRole('button',{name:'Remove desktop control'})).toBeEnabled();
 expect(screen.queryByText('Ready')).toBeNull();
});

it('walks through the macOS permission steps and checks again on request',async()=>{
 serve(denied);render(<DesktopControl admin/>);
 const alert=await alertText();
 expect(alert).toHaveTextContent('Allow Terminal in System Settings, Privacy & Security, Accessibility, then choose Check again.');
 const steps=screen.getByRole('list',{name:'Steps to allow desktop control'});
 expect(within(steps).getAllByRole('listitem')).toHaveLength(4);
 expect(steps).toHaveTextContent('Open System Settings and go to Privacy & Security.');
 expect(steps).toHaveTextContent('called Device Control and Data Access on macOS 27 and later');
 expect(within(steps).getByText('Terminal',{selector:'strong'})).toBeVisible();
 expect(steps).toHaveTextContent('If System Settings lists agent-desktop separately, switch that on too.');
 expect(screen.getByText(/start TermX from the application that has the permission/i)).toBeVisible();
 current={...ready};
 fireEvent.click(screen.getByRole('button',{name:'Check again'}));
 await waitFor(()=>expect(screen.getByText('Ready')).toBeVisible());
 expect(named('desktop_control_status').at(-1)!.variables).toEqual({refresh:true});
});

it('names the application that started TermX when the host cannot tell which one it is',async()=>{
 serve({...denied,permission:{...denied.permission,grant_to:null}});render(<DesktopControl admin/>);
 const steps=await screen.findByRole('list',{name:'Steps to allow desktop control'});
 expect(steps).toHaveTextContent('the application that started TermX');
});

it('explains the missing accessibility service on Linux and the elevation limit on Windows',async()=>{
 serve({...base,installed:true,source:'managed',version:'1.2.0',verified:true,platform:'linux',arch:'x86_64',permission:{state:'unavailable',guidance:'Desktop control needs a graphical session and the accessibility service.',grant_to:null},
  issues:[{code:'no_accessibility_bus',message:'No accessibility service is reachable from this session.',action:'Start TermX from a graphical session with at-spi2-core.'}]});
 const view=render(<DesktopControl admin/>);
 const steps=await screen.findByRole('list',{name:'Steps to allow desktop control'});
 expect(steps).toHaveTextContent('at-spi2-core');expect(steps).toHaveTextContent('Ubuntu 24.04 or newer');expect(steps).toHaveTextContent('Wayland-only sessions cannot scroll or focus');
 view.unmount();
 serve({...ready,platform:'windows',arch:'x86_64',permission:{state:'not_required',guidance:'',grant_to:null}});render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByText('Details',{selector:'summary'}));
 expect(screen.getByText(/Elevated windows cannot be controlled from a normal TermX process/)).toBeInTheDocument();
});

it('shows a ready state with version, source and details',async()=>{
 serve(ready);render(<DesktopControl admin/>);
 expect(await screen.findByText('Ready')).toBeVisible();
 expect(screen.getByText(/Version 1\.2\.0 · Installed by TermX/)).toBeVisible();
 const details=screen.getByText('Details',{selector:'summary'}).closest('details')!;
 expect(details).not.toHaveAttribute('open');
 fireEvent.click(screen.getByText('Details',{selector:'summary'}));
 expect(details).toHaveTextContent('/Users/me/.config/termx/desktop-control/bin/1.2.0/agent-desktop');
 expect(details).toHaveTextContent('Checked against the checksum built into this version of TermX');
 expect(details).toHaveTextContent('Helper programOK');expect(details).toHaveTextContent('window, menu, sheet');
 expect(details).toHaveTextContent('Reading an application can switch on its accessibility support');
 expect(details).toHaveTextContent('sent to your model provider');
 expect(screen.getByRole('button',{name:'Check again'})).toBeEnabled();
});

it('removes the managed copy only after confirmation that says what is deleted',async()=>{
 serve(ready);render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByRole('button',{name:'Remove desktop control'}));
 const dialog=await screen.findByRole('dialog',{name:'Remove desktop control'});
 expect(dialog).toHaveTextContent('Deletes the downloaded program for version 1.2.0.');
 expect(dialog).toHaveTextContent('Agents cannot control applications until you install it again.');
 expect(named('desktop_control_remove')).toHaveLength(0);
 fireEvent.click(within(dialog).getByRole('button',{name:'Remove desktop control'}));
 await waitFor(()=>expect(named('desktop_control_remove')).toHaveLength(1));
 expect(await screen.findByRole('button',{name:'Install desktop control'})).toBeVisible();
});

it('lets an admin point at a program they built, validating the path before sending it',async()=>{
 serve(ready);render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByText('Use a program you built yourself',{selector:'summary'}));
 const field=screen.getByLabelText('Program path');
 expect(field).toHaveAccessibleDescription(/cannot check it against a known checksum/);
 fireEvent.change(field,{target:{value:'bin/agent-desktop'}});
 fireEvent.click(screen.getByRole('button',{name:'Use this path'}));
 expect(field).toHaveAttribute('aria-invalid','true');
 expect(field).toHaveAccessibleDescription(/Enter the full path/);
 expect(named('desktop_control_set_path')).toHaveLength(0);
 fireEvent.change(field,{target:{value:'/opt/agent-desktop/bin/agent-desktop'}});
 expect(field).not.toHaveAttribute('aria-invalid');
 fireEvent.click(screen.getByRole('button',{name:'Use this path'}));
 await waitFor(()=>expect(named('desktop_control_set_path')).toHaveLength(1));
 expect(named('desktop_control_set_path')[0].variables).toEqual({path:'/opt/agent-desktop/bin/agent-desktop'});
 expect(await screen.findByText(/Version 1\.2\.0 · Your own program, not checked/)).toBeVisible();
});

it('accepts Windows paths and refuses control characters',async()=>{
 serve(ready);render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByText('Use a program you built yourself',{selector:'summary'}));
 const field=screen.getByLabelText('Program path');
 for(const bad of ['agent-desktop','./agent-desktop','~/agent-desktop','/opt/a\u0000b','  ']){
  fireEvent.change(field,{target:{value:bad}});fireEvent.click(screen.getByRole('button',{name:'Use this path'}));
  expect(field).toHaveAttribute('aria-invalid','true');
 }
 expect(named('desktop_control_set_path')).toHaveLength(0);
 fireEvent.change(field,{target:{value:'C:\\Tools\\agent-desktop.exe'}});fireEvent.click(screen.getByRole('button',{name:'Use this path'}));
 await waitFor(()=>expect(named('desktop_control_set_path')).toHaveLength(1));
});

it('shows the host’s reason beside the path field when it refuses the program',async()=>{
 serve(ready,{path:new Error('That file is writable by other users.')});render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByText('Use a program you built yourself',{selector:'summary'}));
 fireEvent.change(screen.getByLabelText('Program path'),{target:{value:'/opt/ad'}});fireEvent.click(screen.getByRole('button',{name:'Use this path'}));
 await waitFor(()=>expect(screen.getByLabelText('Program path')).toHaveAccessibleDescription(/That file is writable by other users\./));
 expect(screen.getByLabelText('Program path')).toHaveValue('/opt/ad');
});

it('warns that a configured program is not checked and can go back to the managed copy',async()=>{
 serve({...ready,source:'configured',verified:false,path:'/opt/ad',configured_path:'/opt/ad'});render(<DesktopControl admin/>);
 expect(await screen.findByText(/Your own program, not checked/)).toBeVisible();
 expect(screen.getByText(/An unchecked program runs with the permission you gave TermX/)).toBeVisible();
 expect(screen.queryByRole('button',{name:'Remove desktop control'})).toBeNull();
 fireEvent.click(screen.getByText('Use a program you built yourself',{selector:'summary'}));
 fireEvent.click(screen.getByRole('button',{name:'Stop using this path'}));
 await waitFor(()=>expect(named('desktop_control_set_path')[0].variables).toEqual({path:''}));
});

it('tells the person the status could not be read and lets them retry',async()=>{
 graph.mockRejectedValueOnce(new Error('Host unavailable')).mockResolvedValue({desktop_control_status:ready} as never);
 render(<DesktopControl admin/>);
 expect(await alertText()).toHaveTextContent('Desktop control status could not be read. Host unavailable');
 fireEvent.click(screen.getByRole('button',{name:'Check again'}));
 expect(await screen.findByText('Ready')).toBeVisible();
});

it('gives people who are not host admins one line from the machine status, and never asks for the path',async()=>{
 serve(ready);render(<DesktopControl admin={false}/>);
 expect(await screen.findByText('Desktop control is not set up on this computer. A host admin can set it up under Settings, Tools & connections.')).toBeVisible();
 expect(named('desktop_control_status')).toHaveLength(0);
 expect(screen.queryByRole('button')).toBeNull();
});

it('has no accessibility violations in its states or dialogs',async()=>{
 for(const status of [base,ready,denied]){
  serve(status);const view=render(<DesktopControl admin/>);
  await screen.findByRole('button',{name:/Check again|Install desktop control/});
  await expectNoAxeViolations(view.container);view.unmount();
 }
 serve(base);render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByRole('button',{name:'Install desktop control'}));
 const dialog=await screen.findByRole('dialog');
 await expectNoAxeViolations(dialog);
});

it('treats a saved path that cannot be used as a path problem: no reinstall, the path form open, a way to stop using it',async()=>{
 const issue={code:'configured_invalid',message:'The configured program path is not usable.',action:'Fix TERMX_AGENT_DESKTOP.'};
 // The host falls back to the managed copy but keeps the issue, so the source is not "configured".
 serve({...ready,ready:false,configured_path:'/opt/gone/agent-desktop',issues:[issue]});render(<DesktopControl admin/>);
 const alert=await alertText();
 expect(alert).toHaveTextContent('The program path saved in Settings is not usable');
 expect(alert).not.toHaveTextContent('TERMX_AGENT_DESKTOP');
 expect(screen.queryByRole('button',{name:'Reinstall desktop control'})).toBeNull();
 expect(screen.getByLabelText('Program path')).toBeVisible();
 fireEvent.click(screen.getByRole('button',{name:'Stop using this path'}));
 await waitFor(()=>expect(named('desktop_control_set_path')[0].variables).toEqual({path:''}));
});

it('keeps the host’s own wording when the environment variable decides the unusable path',async()=>{
 const issue={code:'configured_invalid',message:'The configured program path is not usable.',action:'Fix TERMX_AGENT_DESKTOP.'};
 serve({...ready,ready:false,setting_locked:true,configured_path:null,issues:[issue]});render(<DesktopControl admin/>);
 expect(await alertText()).toHaveTextContent('Fix TERMX_AGENT_DESKTOP.');
 expect(screen.queryByRole('button',{name:'Stop using this path'})).toBeNull();
});

it('disables the path form when the environment variable decides the program',async()=>{
 serve({...ready,setting_locked:true});render(<DesktopControl admin/>);
 fireEvent.click(await screen.findByText('Use a program you built yourself',{selector:'summary'}));
 expect(screen.getByLabelText('Program path')).toBeDisabled();expect(screen.getByRole('button',{name:'Use this path'})).toBeDisabled();
 expect(screen.getByText(/TERMX_AGENT_DESKTOP environment variable/)).toBeVisible();
});

it('selects only fields and arguments that schema.graphql declares',async()=>{
 const {readFileSync}=await import('node:fs');const {resolve}=await import('node:path');
 const schema=readFileSync(resolve(process.cwd(),'..','..','schema.graphql'),'utf8');
 const block=(name:string)=>(new RegExp(`(?:type|input) ${name} \\{([^}]*)\\}`).exec(schema)?.[1]||'').split('\n').map(line=>line.trim().split(/[:(]/)[0]).filter(Boolean);
 const status=block('DesktopControlStatus'),nested:Record<string,string[]>={permission:block('DesktopControlPermission'),issues:block('DesktopControlIssue'),install:block('DesktopControlInstallInfo')};
 expect(status).toContain('ready');
 const flat=DESKTOP_FIELDS.replace(/(\w+) \{([^}]*)\}/g,(_all,name:string,inner:string)=>{for(const field of inner.trim().split(/\s+/))expect(nested[name],name+'.'+field).toContain(field);return name});
 for(const field of flat.trim().split(/\s+/))expect(status,field).toContain(field);
 for(const operation of ['desktop_control_status(refresh: $refresh)','desktop_control_install(input: $input)','desktop_control_remove','desktop_control_set_path(path: $path)']){
  expect(schema).toContain(operation.split('(')[0]+(operation.includes('(')?'(':':'));
 }
 expect(block('DesktopControlInstallInput')).toEqual(['version','consent']);
});
