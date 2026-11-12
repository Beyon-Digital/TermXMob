import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
const mock=vi.hoisted(()=>({instances:[] as any[],gql:vi.fn().mockResolvedValue({sessions:[]})}));
vi.mock('@xterm/xterm',()=>({Terminal:class{options:any;dispose=vi.fn();reset=vi.fn();constructor(options:any){this.options=options;mock.instances.push(this)}loadAddon(){}open(){}onData(){}attachCustomKeyEventHandler(){}}}));
vi.mock('@xterm/addon-fit',()=>({FitAddon:class{fit(){}}}));
vi.mock('../lib/api',()=>({gql:mock.gql,request:vi.fn()}));
import TerminalPanel from './TerminalPanel';
afterEach(()=>{cleanup();vi.unstubAllGlobals();mock.instances.length=0;document.documentElement.removeAttribute('style');delete document.documentElement.dataset.theme;delete document.documentElement.dataset.appWorkspace;mock.gql.mockClear()});
it('updates the existing terminal appearance without resetting it or replacing its connection',async()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});
 const root=document.documentElement;root.style.setProperty('--background','#151716');root.style.setProperty('--foreground','#f0f3ed');root.style.setProperty('--accent','#92d4a3');root.dataset.theme='dark';
 render(<TerminalPanel project={null} onError={vi.fn()}/>);
 const instance=mock.instances[0];expect(instance.options.fontFamily).toContain('Geist Mono');expect(instance.options.theme.background).toBe('#151716');
 root.style.setProperty('--background','#fafaf9');root.style.setProperty('--foreground','#191f1c');root.style.setProperty('--accent','#116c46');root.dataset.theme='light';
 await waitFor(()=>expect(instance.options.theme).toEqual({background:'#fafaf9',foreground:'#191f1c',cursor:'#116c46'}));
 expect(mock.instances).toHaveLength(1);expect(instance.reset).not.toHaveBeenCalled();expect(instance.dispose).not.toHaveBeenCalled();
 cleanup();expect(instance.dispose).toHaveBeenCalledOnce();
});

it('updates the palette when the workspace changes while keeping the mounted PTY connected',async()=>{
 class Socket {
  static OPEN=1;static items:Socket[]=[];readyState=1;binaryType='';onopen:any;onclose:any;onerror:any;onmessage:any;
  send=vi.fn();close=vi.fn();constructor(_url:string|URL){Socket.items.push(this)}
 }
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});vi.stubGlobal('WebSocket',Socket);
 mock.gql.mockResolvedValueOnce({sessions:[{id:'pty-a',title:'Retained terminal',cwd:'/project-a',exited:false}]});
 const root=document.documentElement;root.dataset.theme='dark';root.dataset.appWorkspace='studio';
 root.style.setProperty('--background','#17191a');root.style.setProperty('--foreground','#ecefee');root.style.setProperty('--accent','#a3cdb5');
 render(<TerminalPanel project={{id:'project-a',name:'Project A',path:'/project-a'}} onError={vi.fn()}/>);
 await screen.findByRole('option',{name:'Retained terminal'});
 fireEvent.change(screen.getByRole('combobox',{name:'Terminal session'}),{target:{value:'pty-a'}});
 await waitFor(()=>expect(Socket.items).toHaveLength(1));const socket=Socket.items[0];act(()=>socket.onopen());
 const instance=mock.instances[0],resets=instance.reset.mock.calls.length,sent=socket.send.mock.calls.length;
 expect(instance.options.theme).toEqual({background:'#17191a',foreground:'#ecefee',cursor:'#a3cdb5'});
 act(()=>{root.style.setProperty('--background','#1a1917');root.style.setProperty('--foreground','#f2f0eb');root.style.setProperty('--accent','#de7e56');root.dataset.appWorkspace='agent'});
 await waitFor(()=>expect(instance.options.theme).toEqual({background:'#1a1917',foreground:'#f2f0eb',cursor:'#de7e56'}));
 act(()=>{root.style.setProperty('--background','#17191a');root.style.setProperty('--foreground','#ecefee');root.style.setProperty('--accent','#a3cdb5');root.dataset.appWorkspace='studio'});
 await waitFor(()=>expect(instance.options.theme).toEqual({background:'#17191a',foreground:'#ecefee',cursor:'#a3cdb5'}));
 expect(root.dataset.theme).toBe('dark');expect(mock.instances).toEqual([instance]);expect(Socket.items).toEqual([socket]);
 expect(instance.reset).toHaveBeenCalledTimes(resets);expect(instance.dispose).not.toHaveBeenCalled();expect(socket.close).not.toHaveBeenCalled();expect(socket.send).toHaveBeenCalledTimes(sent);
 expect(screen.getByRole('region',{name:'Terminal'})).toHaveAttribute('data-connected-session','pty-a');
});
