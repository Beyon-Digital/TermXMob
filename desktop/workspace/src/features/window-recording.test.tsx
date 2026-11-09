import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import WindowRecordingPanel from './WindowRecordingPanel';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,data?:unknown)=>({method,body:JSON.stringify(data)})}));
const api=vi.mocked(request),base='/api/desktop/recording';
const windows=[{id:'source',app:'Editor',title:'Source',width:800,height:600},{id:'test',app:'Editor',title:'Test',width:800,height:600}];
const capture={id:'source-capture',private:false,window:windows[0],expires_at:1};
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
it('offers a human preview only, with no recording, skill draft, replay or publish controls',async()=>{
 api.mockImplementation(async(path:string,init?:RequestInit)=>{
  if(path.endsWith('/windows'))return {windows,reason:''};
  if(path.endsWith('/captures')&&init?.method==='POST')return capture;
  if(path.endsWith('/preview'))return {image:'data:image/jpeg;base64,AA=='};
  return {};
 });
 render(<WindowRecordingPanel onError={vi.fn()}/>);
 await screen.findByRole('option',{name:/Editor · Source/});
 fireEvent.change(screen.getByLabelText('Application window'),{target:{value:'source'}});
 fireEvent.click(screen.getByRole('button',{name:'Start window capture'}));
 await screen.findByRole('button',{name:'Stop capture'});
 for(const name of [/Record my actions/,/Stop recording/,/Create editable skill draft/,/Save draft/,/Run scoped test/,/Approve & activate/])expect(screen.queryByRole('button',{name})).toBeNull();
 for(const label of ['Recorded skill name','Recorded parameter name','Recorded skill version','Safe test window'])expect(screen.queryByLabelText(label)).toBeNull();
 expect(screen.getByRole('region',{name:'Window preview'})).toBeTruthy();
 expect(screen.queryByText(/recorded|frontmost/i)).toBeNull();
 const preview=await screen.findByRole('img');
 expect(preview.getAttribute('alt')).not.toMatch(/recorded/i);
});

it('sends only fields the host accepts: private and redactions for settings, an event alone for input',async()=>{
 api.mockImplementation(async(path:string,init?:RequestInit)=>{
  if(path.endsWith('/windows'))return {windows,reason:''};
  if(path.endsWith('/captures')&&init?.method==='POST')return capture;
  if(path.endsWith('/preview'))return {image:''};
  if(init?.method==='PATCH')return {...capture,private:true};
  return {};
 });
 render(<WindowRecordingPanel onError={vi.fn()}/>);
 await screen.findByRole('option',{name:/Editor · Source/});
 fireEvent.change(screen.getByLabelText('Application window'),{target:{value:'source'}});
 fireEvent.click(screen.getByRole('button',{name:'Start window capture'}));
 fireEvent.change(await screen.findByLabelText('Window text'),{target:{value:'hello'}});
 fireEvent.click(screen.getByRole('button',{name:'Type text'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith(base+'/captures/source-capture/input',expect.objectContaining({method:'POST'})));
 const sent=api.mock.calls.find(([path])=>String(path).endsWith('/input'))!;
 expect(JSON.parse(String((sent[1] as RequestInit).body))).toEqual({event:{type:'text',data:'hello'}});
 fireEvent.click(screen.getByRole('button',{name:'Private mode'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith(base+'/captures/source-capture',expect.objectContaining({method:'PATCH'})));
 const patch=api.mock.calls.find(([,init])=>(init as RequestInit|undefined)?.method==='PATCH')!;
 expect(JSON.parse(String((patch[1] as RequestInit).body))).toEqual({private:true});
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
