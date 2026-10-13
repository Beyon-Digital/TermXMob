import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import WindowRecordingPanel from './WindowRecordingPanel';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,data?:unknown)=>({method,body:JSON.stringify(data)})}));
const api=vi.mocked(request),base='/api/desktop/recording';
const windows=[{id:'source',app:'Editor',title:'Source',width:800,height:600},{id:'test',app:'Editor',title:'Test',width:800,height:600}];
const capture={id:'source-capture',private:false,recording:false,window:windows[0],expires_at:1,steps:[]};
const draft={id:'draft',name:'workflow',content:'Click safely',parameters:[]};
beforeEach(()=>{api.mockReset()});
afterEach(cleanup);
it('revokes a capture that starts after the panel is closed',async()=>{
 let resolveCapture!:(value:unknown)=>void;
 api.mockImplementation(async(path:string,init?:RequestInit)=>{
  if(path.endsWith('/windows'))return {windows,reason:''};
  if(path.endsWith('/captures')&&init?.method==='POST')return await new Promise(resolve=>{resolveCapture=resolve});
  return {};
 });
 const view=render(<WindowRecordingPanel onError={vi.fn()}/>);
 await screen.findByRole('option',{name:/Editor · Source/});
 fireEvent.change(screen.getByLabelText('Application window'),{target:{value:'source'}});
 fireEvent.click(screen.getByRole('button',{name:'Start window capture'}));
 await waitFor(()=>expect(resolveCapture).toBeDefined());view.unmount();
 await act(async()=>resolveCapture(capture));
 await waitFor(()=>expect(api).toHaveBeenCalledWith(base+'/captures/source-capture',expect.objectContaining({method:'DELETE'})));
});
it.each([true,false])('stops the scoped test capture after a test succeeds: %s',async success=>{
 const errors=vi.fn();
 api.mockImplementation(async(path:string,init?:RequestInit)=>{
  if(path.endsWith('/windows'))return {windows,reason:''};
  if(path.endsWith('/captures')&&init?.method==='POST')return JSON.parse(String(init.body)).window_id==='source'?capture:{...capture,id:'test-capture',window:windows[1]};
  if(path.endsWith('/preview'))return {image:''};
  if(path.endsWith('/draft'))return draft;
  if(path.endsWith('/test')){if(!success)throw new Error('Replay failed');return {...draft,test:{steps_executed:1}}}
  if(init?.method==='PATCH')return capture;
  return {};
 });
 render(<WindowRecordingPanel onError={errors}/>);
 await screen.findByRole('option',{name:/Editor · Source/});
 fireEvent.change(screen.getByLabelText('Application window'),{target:{value:'source'}});
 fireEvent.click(screen.getByRole('button',{name:'Start window capture'}));
 const create=await screen.findByRole('button',{name:'Create editable skill draft'});
 await waitFor(()=>expect(create).toBeEnabled());fireEvent.click(create);
 await screen.findByLabelText('Safe test window');
 fireEvent.change(screen.getByLabelText('Safe test window'),{target:{value:'test'}});
 fireEvent.click(screen.getByRole('checkbox',{name:/I own this safe test window/}));
 const run=screen.getByRole('button',{name:'Run scoped test'});await waitFor(()=>expect(run).toBeEnabled());fireEvent.click(run);
 await waitFor(()=>expect(api).toHaveBeenCalledWith(base+'/captures/test-capture',expect.objectContaining({method:'DELETE'})));
 if(success)await screen.findByText(/Executed 1 steps/);else expect(errors).toHaveBeenCalledWith(expect.objectContaining({message:'Replay failed'}));
 expect(api.mock.calls.some(([path,init])=>path===base+'/captures/source-capture'&&init?.method==='DELETE')).toBe(false);
});

it('keeps an externally stopped capture closed when an older settings save finishes',async()=>{
 let resolveSave!:(value:unknown)=>void;
 api.mockImplementation(async(path:string,init?:RequestInit)=>{
  if(path.endsWith('/windows'))return {windows,reason:''};
  if(path.endsWith('/captures')&&init?.method==='POST')return capture;
  if(path.endsWith('/preview'))return {image:''};
  if(init?.method==='PATCH')return await new Promise(resolve=>{resolveSave=resolve});
  return {};
 });
 render(<WindowRecordingPanel onError={vi.fn()}/>);
 await screen.findByRole('option',{name:/Editor · Source/});
 fireEvent.change(screen.getByLabelText('Application window'),{target:{value:'source'}});
 fireEvent.click(screen.getByRole('button',{name:'Start window capture'}));
 const privacy=await screen.findByRole('button',{name:'Private mode'});
 await waitFor(()=>expect(privacy).toBeEnabled());fireEvent.click(privacy);
 await waitFor(()=>expect(resolveSave).toBeDefined());
 act(()=>window.dispatchEvent(new CustomEvent('termx-capture-changed',{detail:{stopped:['source-capture']}})));
 expect(screen.queryByRole('button',{name:'Stop capture'})).toBeNull();
 await act(async()=>resolveSave({...capture,private:true}));
 expect(screen.queryByRole('button',{name:'Stop capture'})).toBeNull();
});
