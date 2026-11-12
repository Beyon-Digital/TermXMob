import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {ContentAssetPreview,RawContentDetails,StructuredToolResult,TaskContentAttachments,WebLinkPreview} from './ContentPreview';
import {transfer} from '../lib/transfers';
import type {ContentAsset} from '../lib/content-preview';
vi.mock('../lib/transfers',()=>({transfer:vi.fn(),download:vi.fn()}));
const png=new Uint8Array([137,80,78,71,13,10,26,10]);
function blob(mime:string,bytes=png):Blob{return {type:mime,size:bytes.length,slice:()=>({arrayBuffer:async()=>bytes.buffer})} as unknown as Blob}
const asset:ContentAsset={key:'task:image',source:'/api/workspace/tasks/task/artifacts/image',name:'diagram.png',mime:'image/png',size:png.length,kind:'image',caption:'A shared diagram'};
const create=vi.fn(()=> 'blob:owned-preview'),revoke=vi.fn();
beforeEach(()=>{vi.mocked(transfer).mockReset();Object.defineProperty(URL,'createObjectURL',{configurable:true,value:create});Object.defineProperty(URL,'revokeObjectURL',{configurable:true,value:revoke});create.mockClear();revoke.mockClear()});
afterEach(cleanup);
it('loads only an authenticated owned image and revokes its object URL on unmount',async()=>{
 vi.mocked(transfer).mockResolvedValue(blob('image/png'));
 const view=render(<ContentAssetPreview asset={asset}/>);
 expect(await screen.findByRole('img',{name:'A shared diagram'})).toHaveAttribute('src','blob:owned-preview');
 expect(transfer).toHaveBeenCalledWith(asset.source,expect.objectContaining({signal:expect.any(AbortSignal)}));
 const click=vi.spyOn(HTMLAnchorElement.prototype,'click').mockImplementation(()=>{});fireEvent.click(screen.getByRole('button',{name:'Download diagram.png'}));await waitFor(()=>expect(transfer).toHaveBeenCalledTimes(2));await waitFor(()=>expect(create).toHaveBeenLastCalledWith(expect.objectContaining({type:'application/octet-stream'})));click.mockRestore();
 view.unmount();expect(revoke).toHaveBeenCalledWith('blob:owned-preview');
});
it.each([['video/mp4','video','0000ftypisom'],['audio/mpeg','audio','ID3fixture']])('uses actual %s metadata for a native controlled player without autoplay',async(mime,kind,signature)=>{
 vi.mocked(transfer).mockResolvedValue(blob(mime,new TextEncoder().encode(signature)));
 render(<ContentAssetPreview asset={{...asset,mime,kind:kind as 'video'|'audio',name:'Owned recording'}}/>);
 const player=await screen.findByLabelText('Owned recording');expect(player.tagName.toLowerCase()).toBe(kind);expect(player).toHaveAttribute('controls');expect(player).not.toHaveAttribute('autoplay');expect(player).toHaveAttribute('preload','metadata');
});
it('never executes SVG, HTML or PDF as an inline document and never fetches external previews',()=>{
 for(const mime of ['image/svg+xml','text/html','application/pdf']){
  const view=render(<ContentAssetPreview asset={{...asset,mime,kind:'file'}}/>);
  expect(view.container.querySelector('iframe,object,embed,img,svg script')).toBeNull();expect(screen.queryByRole('button',{name:/Load .* preview/})).toBeNull();view.unmount();
 }
 render(<WebLinkPreview url="https://example.test/docs" title="Documentation"/>);
 const link=screen.getByRole('link',{name:/Documentation/});expect(link).toHaveAttribute('href','https://example.test/docs');expect(link).toHaveAttribute('rel','noopener noreferrer');expect(screen.queryByRole('img')).toBeNull();expect(transfer).not.toHaveBeenCalled();
});
it('rejects a disguised active payload, keeps download available and supports an explicit retry',async()=>{
 vi.mocked(transfer).mockResolvedValueOnce(blob('image/png',new TextEncoder().encode('<svg onload="alert(1)"/>'))).mockResolvedValueOnce(blob('image/png'));
 render(<ContentAssetPreview asset={asset}/>);
 expect(await screen.findByRole('alert')).toHaveTextContent('A preview is unavailable');expect(screen.queryByRole('img')).toBeNull();expect(create).not.toHaveBeenCalled();
 expect(screen.getByRole('button',{name:'Download diagram.png'})).toBeEnabled();fireEvent.click(screen.getByRole('button',{name:'Retry preview'}));expect(await screen.findByRole('img')).toBeVisible();
});
it('does not load oversized assets or fetch while hidden and discards late responses on workspace changes',async()=>{
 const large=render(<ContentAssetPreview asset={{...asset,size:9*1024*1024}}/>);expect(transfer).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:/Load image preview/})).toBeNull();large.unmount();
 let finish!:(value:Blob)=>void;vi.mocked(transfer).mockReturnValue(new Promise(resolve=>finish=resolve));
 const view=render(<ContentAssetPreview asset={asset}/>);const signal=vi.mocked(transfer).mock.calls[0][1]?.signal;
 view.rerender(<ContentAssetPreview asset={asset} visible={false}/>);expect(signal?.aborted).toBe(true);await act(async()=>finish(blob('image/png')));expect(create).not.toHaveBeenCalled();expect(screen.queryByRole('img')).toBeNull();
});
it('clears an existing preview on lock and requires explicit reload after unlocking',async()=>{
 vi.mocked(transfer).mockResolvedValue(blob('image/png'));render(<ContentAssetPreview asset={asset}/>);await screen.findByRole('img');
 act(()=>window.dispatchEvent(new Event('termx-session-locked')));expect(screen.queryByRole('img')).toBeNull();expect(revoke).toHaveBeenCalledWith('blob:owned-preview');expect(transfer).toHaveBeenCalledOnce();
 fireEvent.click(screen.getByRole('button',{name:'Load image preview'}));await screen.findByRole('img');expect(transfer).toHaveBeenCalledTimes(2);
});
it('renders typed file, search, changes, command and JSON results with contextual actions',()=>{
 const open=vi.fn();const view=render(<StructuredToolResult kind="read_file" value={{ok:true,path:'src/main.ts',content:'export const ready=true;',start_line:8}} onOpenFile={open}/>);
 fireEvent.click(screen.getByRole('button',{name:'Open file src/main.ts:8'}));expect(open).toHaveBeenCalledWith('src/main.ts',8);expect(screen.getByRole('region',{name:'src/main.ts content'})).toHaveTextContent('export const ready');
 view.rerender(<StructuredToolResult kind="search_project" value={{matches:[{path:'src/other.ts',line:3,text:'matching source'}]}} onOpenFile={open}/>);expect(screen.getByText('matching source')).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'Open file src/other.ts:3'}));expect(open).toHaveBeenLastCalledWith('src/other.ts',3);
 view.rerender(<StructuredToolResult kind="apply_patch" value={{changes:[{path:'src/main.ts',added:2,removed:1,preview:['--- old','+++ new','+added']}]}} onOpenFile={open}/>);expect(screen.getByText('+2 added')).toBeVisible();expect(screen.getByRole('region',{name:'src/main.ts changes',hidden:true})).not.toBeVisible();fireEvent.click(screen.getByText('View changes'));expect(screen.getByRole('region',{name:'src/main.ts changes'})).toHaveTextContent('+added');
 view.rerender(<StructuredToolResult kind="run_check" value={{exit_code:1,duration_ms:1250,output:'Check failed',error:'Permission denied'}}/>);expect(screen.getByRole('alert')).toHaveTextContent('Permission denied');expect(screen.getByText('Exit 1')).toBeVisible();expect(screen.getByRole('region',{name:'Command output'})).toHaveTextContent('Check failed');
 view.rerender(<StructuredToolResult value={{ok:true,count:3,details:{reason:'Observed metadata'}}}/>);expect(screen.getByText('count')).toBeVisible();expect(screen.getByText('3')).toBeVisible();expect(screen.queryByRole('button',{name:/Run|Execute/})).toBeNull();
});
it('keeps streaming or malformed JSON as bounded text until a complete result exists',()=>{
 const json='{"path":"src/main.ts","content":"safe text"}',open=vi.fn();const view=render(<StructuredToolResult kind="read_file" value={json} complete={false} onOpenFile={open}/>);expect(screen.getByText(json)).toBeVisible();expect(screen.queryByRole('button',{name:/Open file/})).toBeNull();
 view.rerender(<StructuredToolResult kind="read_file" value={json} complete onOpenFile={open}/>);expect(screen.getByRole('button',{name:'Open file src/main.ts'})).toBeVisible();
 view.rerender(<StructuredToolResult value={'{"broken":'} onOpenFile={open}/>);expect(screen.getByText('{"broken":')).toBeVisible();
 view.rerender(<StructuredToolResult value={'x'.repeat(100_000)}/>);expect(screen.getByText(/Preview limited/).textContent!.length).toBeLessThan(17000);
});
it('preserves exact raw identifiers, arguments and errors behind an explicit disclosure',()=>{
 render(<RawContentDetails title="Raw details" value={{call_id:'actual-call',input:{path:'a.ts'},error:'Denied',state:'output-error'}}/>);const raw=screen.getByLabelText('Raw details');expect(raw).not.toBeVisible();fireEvent.click(screen.getByText('Raw details',{selector:'summary'}));expect(raw).toBeVisible();expect(raw).toHaveTextContent('actual-call');expect(raw).toHaveTextContent('output-error');expect(raw).toHaveTextContent('a.ts');expect(raw).toHaveTextContent('Denied');
});
it('keeps persisted upload and assistant files in their actual message roles',()=>{
 const task={id:'task',status:'completed',prompt:'Inspect',artifacts:[{id:'upload',task_id:'task',kind:'upload',mime:'application/pdf',size:200},{id:'shared',task_id:'task',kind:'media',mime:'text/plain',size:20}]};
 const view=render(<TaskContentAttachments task={task} role="user"/>);expect(screen.getByRole('region',{name:'Message attachments'})).toHaveTextContent('Uploaded file');expect(screen.queryByText('Shared file')).toBeNull();
 view.rerender(<TaskContentAttachments task={task} role="assistant"/>);expect(screen.getByRole('region',{name:'Shared files'})).toHaveTextContent('Shared file');expect(screen.queryByText('Uploaded file')).toBeNull();expect(transfer).not.toHaveBeenCalled();
});
