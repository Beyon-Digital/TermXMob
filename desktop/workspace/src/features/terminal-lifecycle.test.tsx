import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,render,screen,waitFor} from '@testing-library/react';
const mock=vi.hoisted(()=>({instances:[] as any[],remembered:new Map<string,string>(),gql:vi.fn(),request:vi.fn()}));
vi.mock('@xterm/xterm',()=>({Terminal:class{options:any;reset=vi.fn();write=vi.fn();dispose=vi.fn();cols=80;rows=24;constructor(options:any){this.options=options;mock.instances.push(this)}loadAddon(){}open(){}onData(){}attachCustomKeyEventHandler(){}}}));
vi.mock('@xterm/addon-fit',()=>({FitAddon:class{fit(){}}}));
vi.mock('../lib/api',()=>({gql:mock.gql,request:mock.request}));
vi.mock('../lib/window-state',()=>({rememberTerminal:(id:string,terminal:string)=>mock.remembered.set(id,terminal),recalledTerminal:(id:string)=>mock.remembered.get(id)||''}));
import TerminalPanel from './TerminalPanel';
import {saveTerminalSelection} from '../lib/terminal-selection';
class Socket {
 static OPEN=1;static items:Socket[]=[];readyState=1;binaryType='';onopen:any;onclose:any;onerror:any;onmessage:any;
 url:string;send=vi.fn();close=vi.fn();constructor(url:string|URL){this.url=String(url);Socket.items.push(this)}
}
const project={id:'project-a',name:'Project A',path:'/project-a'};
const row={id:'pty-a',title:'Retained terminal',cwd:'/project-a',exited:false};
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve}}
beforeEach(()=>{vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});vi.stubGlobal('WebSocket',Socket);mock.gql.mockReset().mockResolvedValue({sessions:[row]});mock.request.mockReset().mockResolvedValue({});mock.remembered.clear();mock.remembered.set('conversation-a','pty-a');Socket.items=[];mock.instances=[]});
afterEach(()=>{cleanup();vi.unstubAllGlobals();sessionStorage.clear()});
it('restores the existing PTY when its canonical workspace session arrives after the project',async()=>{
 const view=render(<TerminalPanel project={project} onError={vi.fn()}/>);
 await waitFor(()=>expect(mock.gql).toHaveBeenCalledOnce());expect(Socket.items).toHaveLength(0);
 view.rerender(<TerminalPanel project={project} workspaceSession="conversation-a" onError={vi.fn()}/>);
 await waitFor(()=>expect(Socket.items).toHaveLength(1));expect(Socket.items[0].url).toContain('/api/sessions/pty-a/pty');expect(mock.request).not.toHaveBeenCalled();
 act(()=>Socket.items[0].onopen());expect(screen.getByRole('region',{name:'Terminal'})).toHaveAttribute('data-connected-session','pty-a');
});
it('locks observation and reconnects the same PTY after unlock without replaying any input',async()=>{
 const onError=vi.fn();render(<TerminalPanel project={project} workspaceSession="conversation-a" onError={onError}/>);
 await waitFor(()=>expect(Socket.items).toHaveLength(1));const first=Socket.items[0];act(()=>first.onopen());
 act(()=>window.dispatchEvent(new Event('termx-locked')));expect(first.close).toHaveBeenCalled();expect(screen.getByText('Locked')).toBeVisible();expect(mock.instances[0].options.disableStdin).toBe(true);
 const count=mock.gql.mock.calls.length;act(()=>first.onclose({code:4401}));expect(mock.request).not.toHaveBeenCalled();expect(Socket.items).toHaveLength(1);expect(mock.gql).toHaveBeenCalledTimes(count);
 act(()=>window.dispatchEvent(new Event('termx-session-unlocked')));await waitFor(()=>expect(Socket.items).toHaveLength(2));const second=Socket.items[1];act(()=>second.onopen());
 act(()=>{first.onclose({code:4401});first.onmessage({data:'old output'});first.onerror()});expect(screen.getByText('Connected')).toBeVisible();expect(mock.instances[0].write).not.toHaveBeenCalled();expect(mock.instances[0].options.disableStdin).toBe(false);
 expect(second.url).toBe(first.url);expect(second.send.mock.calls.map(([data])=>JSON.parse(data))).toEqual([{type:'resize',cols:80,rows:24}]);expect(onError).not.toHaveBeenCalled();
});
it('rejects a late terminal inventory for the previous canonical conversation',async()=>{
 const old=deferred<{sessions:typeof row[]}>();mock.gql.mockReturnValueOnce(old.promise);mock.remembered.set('conversation-b','pty-b');
 const view=render(<TerminalPanel project={project} workspaceSession="conversation-a" onError={vi.fn()}/>);
 mock.gql.mockResolvedValue({sessions:[{...row,id:'pty-b'}]});view.rerender(<TerminalPanel project={project} workspaceSession="conversation-b" onError={vi.fn()}/>);
 await waitFor(()=>expect(Socket.items).toHaveLength(1));await act(async()=>old.resolve({sessions:[row]}));expect(Socket.items).toHaveLength(1);expect(Socket.items[0].url).toContain('/api/sessions/pty-b/pty');
});
it('does not reconnect or replay input after current terminal authority is denied',async()=>{
 const onError=vi.fn();render(<TerminalPanel project={project} workspaceSession="conversation-a" onError={onError}/>);await waitFor(()=>expect(Socket.items).toHaveLength(1));const socket=Socket.items[0];act(()=>{socket.onopen();socket.onclose({code:4403})});expect(onError).toHaveBeenCalledWith(expect.objectContaining({message:'Terminal authority was revoked.'}));expect(mock.request).not.toHaveBeenCalled();expect(Socket.items).toHaveLength(1);expect(mock.instances[0].options.disableStdin).toBe(true);
});
it('ignores a handoff response after switching to another canonical conversation',async()=>{
 const view=render(<TerminalPanel project={project} workspaceSession="conversation-a" onError={vi.fn()}/>);await waitFor(()=>expect(Socket.items).toHaveLength(1));
 const pending=deferred<{sessions:typeof row[]}>();mock.gql.mockReturnValueOnce(pending.promise);act(()=>window.dispatchEvent(new CustomEvent('termx-terminal-handoff',{detail:{sessionId:'conversation-a',id:'pty-returned'}})));
 mock.remembered.set('conversation-b','pty-b');mock.gql.mockResolvedValue({sessions:[{...row,id:'pty-b'}]});view.rerender(<TerminalPanel project={project} workspaceSession="conversation-b" onError={vi.fn()}/>);await waitFor(()=>expect(Socket.items.at(-1)?.url).toContain('/api/sessions/pty-b/pty'));
 const count=Socket.items.length;await act(async()=>pending.resolve({sessions:[{...row,id:'pty-returned'}]}));expect(Socket.items).toHaveLength(count);expect(screen.getByRole('combobox',{name:'Terminal session'})).toHaveValue('pty-b');
});
it('recovers a reloaded window selection only after the host authorizes the exact PTY',async()=>{
 mock.remembered.clear();saveTerminalSelection('owner-a','conversation-a','pty-a');const result=deferred<{sessions:typeof row[]}>();mock.gql.mockReturnValueOnce(result.promise);
 render(<TerminalPanel ownerId="owner-a" project={project} workspaceSession="conversation-a" onError={vi.fn()}/>);expect(Socket.items).toHaveLength(0);await act(async()=>result.resolve({sessions:[row]}));await waitFor(()=>expect(Socket.items).toHaveLength(1));expect(Socket.items[0].url).toContain('/api/sessions/pty-a/pty');
});
it('never connects a stored PTY that is absent from the current authorized inventory',async()=>{
 mock.remembered.clear();saveTerminalSelection('owner-a','conversation-a','pty-a');mock.gql.mockResolvedValue({sessions:[]});render(<TerminalPanel ownerId="owner-a" project={project} workspaceSession="conversation-a" onError={vi.fn()}/>);await waitFor(()=>expect(mock.gql).toHaveBeenCalledOnce());expect(Socket.items).toHaveLength(0);expect(screen.getByRole('combobox',{name:'Terminal session'})).toHaveValue('');
});
