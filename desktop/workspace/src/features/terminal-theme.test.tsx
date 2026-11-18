import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
const mock=vi.hoisted(()=>({instances:[] as any[],gql:vi.fn().mockResolvedValue({sessions:[]})}));
vi.mock('@xterm/xterm',()=>({Terminal:class{options:any;dispose=vi.fn();reset=vi.fn();write=vi.fn();focus=vi.fn();cols=80;rows=24;constructor(options:any){this.options=options;mock.instances.push(this)}loadAddon(){}open(){}onData(){}attachCustomKeyEventHandler(){}}}));
vi.mock('@xterm/addon-fit',()=>({FitAddon:class{fit(){}}}));
vi.mock('@xterm/addon-search',()=>({SearchAddon:class{findNext=vi.fn();findPrevious=vi.fn();clearDecorations=vi.fn();onDidChangeResults=vi.fn()}}));
vi.mock('@xterm/addon-web-links',()=>({WebLinksAddon:class{}}));
vi.mock('../lib/api',()=>({gql:mock.gql,request:vi.fn()}));
import TerminalPanel from './TerminalPanel';
import {ANSI_COLOURS} from './terminal/terminal-theme';
const project={id:'project-a',name:'Project A',path:'/project-a'};
class Socket {
 static OPEN=1;static items:Socket[]=[];readyState=1;binaryType='';onopen:any;onclose:any;onerror:any;onmessage:any;
 send=vi.fn();close=vi.fn();constructor(_url:string|URL){Socket.items.push(this)}
}
const root=document.documentElement;
const cleared=['--bg','--text','--accent','--selection',...ANSI_COLOURS.map(([,name])=>name)];
afterEach(()=>{cleanup();vi.unstubAllGlobals();mock.instances.length=0;Socket.items=[];for(const name of cleared)root.style.removeProperty(name);delete root.dataset.theme;delete root.dataset.appWorkspace;root.className='';mock.gql.mockReset().mockResolvedValue({sessions:[]})});
const light:Record<string,string>=Object.fromEntries([['--bg','rgb(250, 249, 247)'],['--text','rgb(31, 30, 27)'],['--accent','rgb(176, 80, 45)'],['--selection','rgba(176, 80, 45, 0.24)'],...ANSI_COLOURS.map(([,name],index)=>[name,`rgb(${10+index}, 90, 40)`])]);
const dark:Record<string,string>=Object.fromEntries([['--bg','rgb(26, 25, 23)'],['--text','rgb(242, 240, 235)'],['--accent','rgb(222, 126, 86)'],['--selection','rgba(222, 126, 86, 0.24)'],...ANSI_COLOURS.map(([,name],index)=>[name,`rgb(${200+index}, 160, 120)`])]);
const apply=(values:Record<string,string>)=>{for(const [name,value] of Object.entries(values))root.style.setProperty(name,value)};
async function openFirstSession(){
 mock.gql.mockResolvedValue({sessions:[{id:'pty-a',title:'zsh',cwd:'/project-a',exited:false}]});
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});vi.stubGlobal('WebSocket',Socket);
 render(<TerminalPanel project={project} onError={vi.fn()}/>);
 const tab=await screen.findByRole('tab',{name:'zsh'});fireEvent.mouseDown(tab,{button:0,ctrlKey:false});
 await waitFor(()=>expect(mock.instances).toHaveLength(1));return mock.instances[0];
}
it('starts from the token palette and mono 12px type',async()=>{
 apply(light);root.dataset.theme='light';
 const instance=await openFirstSession();
 expect(instance.options.fontFamily).toContain('JetBrains Mono');expect(instance.options.fontSize).toBe(12);expect(instance.options.lineHeight).toBe(1);
 ANSI_COLOURS.forEach(([key],index)=>expect(instance.options.theme[key],key).toBe(`rgb(${10+index}, 90, 40)`));
 expect(instance.options.theme).toMatchObject({background:'rgb(250, 249, 247)',foreground:'rgb(31, 30, 27)',cursor:'rgb(176, 80, 45)',selectionBackground:'rgba(176, 80, 45, 0.24)'});
});
it('re-applies all 16 colours when the theme flips, without resetting, replacing or disconnecting the terminal',async()=>{
 apply(light);root.dataset.theme='light';
 const instance=await openFirstSession();await waitFor(()=>expect(Socket.items).toHaveLength(1));const socket=Socket.items[0];act(()=>socket.onopen());
 const sent=socket.send.mock.calls.length;
 act(()=>{apply(dark);root.dataset.theme='dark'});
 await waitFor(()=>expect(instance.options.theme.background).toBe('rgb(26, 25, 23)'));
 ANSI_COLOURS.forEach(([key],index)=>expect(instance.options.theme[key],key).toBe(`rgb(${200+index}, 160, 120)`));
 act(()=>{apply(light);root.dataset.theme='light'});
 await waitFor(()=>expect(instance.options.theme.background).toBe('rgb(250, 249, 247)'));
 act(()=>{apply(dark);root.className='dark'});
 await waitFor(()=>expect(instance.options.theme.foreground).toBe('rgb(242, 240, 235)'));
 expect(mock.instances).toEqual([instance]);expect(Socket.items).toEqual([socket]);
 expect(instance.reset).toHaveBeenCalledTimes(1);expect(instance.dispose).not.toHaveBeenCalled();expect(socket.close).not.toHaveBeenCalled();expect(socket.send).toHaveBeenCalledTimes(sent);
 expect(screen.getByRole('region',{name:'Terminal'})).toHaveAttribute('data-connected-session','pty-a');
 cleanup();expect(instance.dispose).toHaveBeenCalledOnce();
});
it('still follows a workspace-mode palette change on the same document',async()=>{
 apply(dark);root.dataset.theme='dark';root.dataset.appWorkspace='studio';
 const instance=await openFirstSession();
 act(()=>{root.style.setProperty('--bg','rgb(23, 25, 26)');root.dataset.appWorkspace='agent'});
 await waitFor(()=>expect(instance.options.theme.background).toBe('rgb(23, 25, 26)'));
 expect(mock.instances).toHaveLength(1);expect(instance.reset).toHaveBeenCalledTimes(1);
});
