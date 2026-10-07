import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import CaptureStatus from './CaptureStatus';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn(),json:(method:string,body:unknown)=>({method,body:JSON.stringify(body)})}));
const api=vi.mocked(request);
afterEach(()=>{cleanup();vi.resetAllMocks()});
it('opens active window controls and clears successful stops even when another capture cannot stop',async()=>{
 let rows=[{id:'window',kind:'window',state:'private',stop_allowed:true},{id:'tab',kind:'tab',state:'recording',stop_allowed:true}];
 const changed=vi.fn(),onOpen=vi.fn(),onError=vi.fn();window.addEventListener('termx-capture-changed',changed);
 api.mockImplementation(async(path,init)=>{if(path==='/api/desktop/recording/status')return {captures:rows};if(path.endsWith('/window')&&init?.method==='DELETE'){rows=rows.filter(row=>row.id!=='window');return {stopped:true}}if(path.endsWith('/tab/recording'))throw Error('Control revoked');throw Error(path)});
 render(<CaptureStatus onOpen={onOpen} onError={onError}/>);
 await screen.findByText('1 private · models paused');fireEvent.click(screen.getByRole('button',{name:'Open capture controls'}));expect(onOpen).toHaveBeenCalledWith('computer');
 fireEvent.click(screen.getByRole('button',{name:'Stop capture'}));await screen.findByText('1 recording');expect(onError).toHaveBeenCalled();await waitFor(()=>expect(changed).toHaveBeenCalled());expect((changed.mock.calls[0][0] as CustomEvent).detail.stopped).toEqual(['window']);
 window.removeEventListener('termx-capture-changed',changed);
});
it('does not poll or display capture authority while disabled',()=>{render(<CaptureStatus enabled={false} onOpen={vi.fn()} onError={vi.fn()}/>);expect(api).not.toHaveBeenCalled();expect(screen.queryByRole('group',{name:'Active capture controls'})).not.toBeInTheDocument()});
