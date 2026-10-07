import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {BrowserSurface} from './BrowserSurface';
import {SafetyManager} from './SafetyManager';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
const api=vi.mocked(request);
class Socket {static OPEN=1;readyState=1;onopen?:()=>void;onmessage?:()=>void;onerror?:()=>void;onclose?:()=>void;binaryType='';constructor(){setTimeout(()=>this.onopen?.(),0)}send=vi.fn();close=vi.fn()}
const profile={id:'profile',name:'Project account',project_id:'project',ephemeral:false};
const baseTab={id:'tab',title:'Site fixture',url:'https://site.test/page',state:'human',profile_id:'profile',project_id:'project',document_revision:1,lease_revision:1,recording:false,grant_id:null};
let tab={...baseTab};
beforeEach(()=>{tab={...baseTab};vi.stubGlobal('WebSocket',Socket);api.mockImplementation(async(path:string,init?:RequestInit)=>{
 if(path==='/api/browser/profiles')return [profile];if(path==='/api/browser/tabs')return [tab];if(path==='/api/browser/reviews'||path==='/api/browser/downloads'||path==='/api/browser/rules')return [];
 if(path==='/api/browser/tabs/tab/takeover'){const body=JSON.parse(String(init?.body));tab={...tab,state:body.private?'private':'human'};return tab;}
 if(path==='/api/browser/tabs/tab/context')return {tab_id:'tab',title:'Site fixture',url:'https://site.test/page',document_revision:1,lease_revision:1,elements:[{index:1,tag:'p',name:'Visible selected context',type:null,box:{x:1,y:1,width:100,height:20}}]};
 if(path==='/api/browser/tabs/tab/handoff'){tab={...tab,state:'agent'};return {tab};}
 if(path==='/api/browser/reviewer')return {configuration:null,evaluations:[],active:false};if(path==='/auth/me')return {principal:{scopes:[]}};
 throw new Error('Unexpected API '+path)
 })});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals()});
describe('Browser workspace',()=>{
 it('shares only the explicit preview and grants no ongoing control',async()=>{const share=vi.fn();render(<BrowserSurface projectId="project" taskId="task" onContext={share}/>);await screen.findByRole('button',{name:'Ask about page'});fireEvent.click(screen.getByRole('button',{name:'Ask about page'}));await screen.findByText('Visible selected context');expect(share).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'Attach this context'}));expect(share).toHaveBeenCalledWith(expect.objectContaining({type:'browser-context'}));expect(api).not.toHaveBeenCalledWith('/api/browser/tabs/tab/handoff',expect.anything())});
 it('private mode removes context and disables page observations',async()=>{render(<BrowserSurface projectId="project" taskId="task"/>);fireEvent.click(await screen.findByRole('button',{name:'Private login'}));await screen.findByText('I’m paused while you sign in.');expect(screen.getByRole('button',{name:'Ask about page'})).toBeDisabled();expect(screen.getByRole('button',{name:'Page controls'})).toBeDisabled();expect(screen.queryByText('Visible selected context')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Resume agent'})).toBeEnabled()});
 it('requires an exact site and explicit task handoff',async()=>{render(<BrowserSurface projectId="project" taskId="task"/>);fireEvent.click(await screen.findByRole('button',{name:'Let agent use'}));expect(screen.getByRole('dialog',{name:'Task browser handoff'})).toBeInTheDocument();expect(screen.getByLabelText('Approved origins')).toHaveValue('https://site.test');expect(api).not.toHaveBeenCalledWith('/api/browser/tabs/tab/handoff',expect.anything());fireEvent.click(screen.getByRole('button',{name:'Hand over & observe'}));await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/browser/tabs/tab/handoff',expect.objectContaining({body:expect.stringContaining('"origins":["https://site.test"]')})))});
 it('safety manager shows real empty state and hides administrator configuration',async()=>{render(<SafetyManager/>);await screen.findByText('No actions have been reviewed yet.');expect(screen.queryByRole('button',{name:'Configure'})).not.toBeInTheDocument();expect(screen.getByText('No model evaluations recorded.')).toBeInTheDocument()});
});
