import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import ComputerSurface from './ComputerSurface';
vi.mock('../lib/api',()=>({request:vi.fn()}));
class Socket{static OPEN=1;static instances:Socket[]=[];readyState=1;binaryType='';onopen?:()=>void;onmessage?:(event:{data:string})=>void;onclose?:(event:CloseEvent)=>void;send=vi.fn();close=vi.fn();constructor(){Socket.instances.push(this);setTimeout(()=>this.onopen?.(),0)}message(data:unknown){act(()=>this.onmessage?.({data:JSON.stringify(data)}))}}
beforeEach(()=>{Socket.instances=[];vi.stubGlobal('WebSocket',Socket)});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals();vi.restoreAllMocks()});
async function watch(){render(<ComputerSurface onError={vi.fn()}/>);fireEvent.click(screen.getByRole('button',{name:'Watch this machine'}));await waitFor(()=>expect(Socket.instances).toHaveLength(1));await screen.findByRole('button',{name:'Take control'});return Socket.instances[0]}
describe('acknowledged computer control',()=>{
 it('waits for the host acknowledgement and clears a denied authority request',async()=>{const ws=await watch();fireEvent.click(screen.getByRole('button',{name:'Take control'}));expect(screen.getByRole('button',{name:'Checking control permission…'})).toBeDisabled();expect(screen.getByRole('status')).toHaveTextContent('Watching');ws.message({type:'denied',view_only:true,message:'Control scope revoked'});expect(screen.getByRole('button',{name:'Take control'})).toBeEnabled();expect(screen.getByRole('alert')).toHaveTextContent('Control scope revoked');ws.message({type:'control',view_only:false});expect(screen.getByRole('button',{name:'Return to watch'})).toBeEnabled()});
 it('lets Tab leave the controlling canvas and Escape reach the safety controls without forwarding either key',async()=>{const ws=await watch();ws.message({type:'control',view_only:false});const canvas=screen.getByRole('region',{name:/Remote computer, keyboard control enabled/});canvas.focus();expect(fireEvent.keyDown(canvas,{key:'Tab'})).toBe(true);expect(ws.send.mock.calls.some(([value])=>JSON.parse(value).type==='key'&&JSON.parse(value).key==='Tab')).toBe(false);fireEvent.keyDown(canvas,{key:'Escape'});expect(screen.getByRole('button',{name:'Return to watch'})).toHaveFocus();expect(ws.send.mock.calls.some(([value])=>JSON.parse(value).type==='key'&&JSON.parse(value).key==='Escape')).toBe(false);fireEvent.click(screen.getByRole('button',{name:'Send Escape'}));expect(ws.send).toHaveBeenCalledWith(JSON.stringify({type:'key',key:'Escape',action:'up'}))});
 it('keeps a user pause on visibility return and names a revoked view instead of silently restarting it',async()=>{const ws=await watch();ws.message({type:'paused'});vi.spyOn(document,'hidden','get').mockReturnValue(false);document.dispatchEvent(new Event('visibilitychange'));expect(ws.send.mock.calls.some(([value])=>JSON.parse(value).type==='resume')).toBe(false);act(()=>ws.onclose?.({code:4403} as CloseEvent));expect(screen.getByRole('alert')).toHaveTextContent('Computer view permission was denied or revoked');expect(screen.getByRole('button',{name:'Take control'})).toBeDisabled();expect(screen.getByRole('button',{name:'Retry computer capture'})).toBeEnabled()});
});

it('closes capture and control on workspace lock and never starts another watcher without a user action',async()=>{const ws=await watch();ws.message({type:'control',view_only:false});fireEvent(window,new Event('termx-locked'));await screen.findByRole('button',{name:'Watch this machine'});expect(ws.close).toHaveBeenCalled();ws.message({type:'control',view_only:false});expect(screen.queryByRole('button',{name:'Return to watch'})).toBeNull();expect(Socket.instances).toHaveLength(1);fireEvent.click(screen.getByRole('button',{name:'Watch this machine'}));await waitFor(()=>expect(Socket.instances).toHaveLength(2));await waitFor(()=>expect(screen.getByRole('button',{name:'Take control'})).toBeEnabled())});

it('closes hidden dock panels, releases input, and ignores late callbacks after unmount',async()=>{
 const view=render(<ComputerSurface onError={vi.fn()} visible/>);
 fireEvent.click(screen.getByRole('button',{name:'Watch this machine'}));
 await waitFor(()=>expect(Socket.instances).toHaveLength(1));
 const ws=Socket.instances[0];ws.message({type:'control',view_only:false});
 view.rerender(<ComputerSurface onError={vi.fn()} visible={false}/>);
 expect(ws.close).toHaveBeenCalled();
 expect(ws.send).toHaveBeenCalledWith(JSON.stringify({type:'release_all'}));
 expect(ws.send).toHaveBeenCalledWith(JSON.stringify({type:'pause'}));
 ws.message({type:'control',view_only:false});
 expect(screen.queryByRole('button',{name:'Return to watch'})).toBeNull();
 view.unmount();act(()=>ws.onopen?.());expect(ws.close.mock.calls.length).toBeGreaterThan(1);
});

it('stops capture on window blur and requires an explicit restart',async()=>{
 const ws=await watch();fireEvent(window,new Event('blur'));
 expect(ws.close).toHaveBeenCalled();
 fireEvent(window,new Event('focus'));
 expect(screen.getByRole('button',{name:'Watch this machine'})).toBeEnabled();
 expect(Socket.instances).toHaveLength(1);
});
