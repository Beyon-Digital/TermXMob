import {expect,it} from 'vitest';
import {assetFromMetadata,authorizedAssetSource,boundedContent,contentKind,decodeStructured,MAX_STRUCTURED_TEXT,mediaSignatureMatches,safeContentPath,safeWebLink,taskContentAssets} from './content-preview';
import type {Task} from './api';
it('accepts only owned artifact endpoint shapes and safe explicit web navigation',()=>{
 expect(authorizedAssetSource('/api/workspace/tasks/task_1/artifacts/file-2')).toBe('/api/workspace/tasks/task_1/artifacts/file-2');
 expect(authorizedAssetSource('/api/media/artifacts/file/export?format=original&version=2')).toBeTruthy();
 for(const url of ['javascript:alert(1)','//evil.test/image','https://evil.test/api/media/artifacts/a/export?format=original','/api/workspace/tasks/../artifacts/a','/api/media/artifacts/a/export?format=original&redirect=evil','data:image/svg+xml,<svg/>','/api/workspace/tasks/a/artifacts/b?token=secret','/api/workspace/tasks/a/artifacts/%2e%2e'])expect(authorizedAssetSource(url)).toBeNull();
 expect(safeWebLink('https://example.test/docs#setup')).toBe('https://example.test/docs#setup');
 for(const url of ['javascript:alert(1)','data:text/html,x','file:///etc/passwd','//evil.test','https://name:password@evil.test','https://evil.test/\nx','https://evil.test/%0aHeader','https:\\evil.test','/api/delete'])expect(safeWebLink(url)).toBeNull();
});
it('classifies only actual MIME/name metadata and rejects unsafe file actions',()=>{
 expect(contentKind('unknown','image/png')).toBe('image');expect(contentKind('movie','video/mp4')).toBe('video');expect(contentKind('voice','audio/mpeg')).toBe('audio');expect(contentKind('report.pdf')).toBe('pdf');expect(contentKind('sheet.csv')).toBe('spreadsheet');expect(contentKind('src/App.tsx')).toBe('code');expect(contentKind('script.svg','image/svg+xml')).toBe('code');
 for(const path of ['../secret','src/../../secret','file:///secret','//server/file','javascript:x','src\\file'])expect(safeContentPath(path)).toBeNull();
});
it('parses only bounded complete JSON and keeps partial, malformed and oversized values readable',()=>{
 expect(decodeStructured('{"ok":true}')).toEqual({ok:true});
 for(const value of ['{"ok":','not code <script>','['+'0,'.repeat(MAX_STRUCTURED_TEXT)+']'])expect(decodeStructured(value)).toBe(value);
 expect(decodeStructured('{"ok":true}',false)).toBe('{"ok":true}');
 expect(boundedContent('x'.repeat(20000))).toContain('Preview limited');
 expect(boundedContent({data:'x'.repeat(1_000_000)}).length).toBeLessThan(17000);
 expect(boundedContent(Array.from({length:1000},(_,index)=>({index})))).toContain('Preview limited');
});
it('binds artifact identity to the authorized task and merges live names without duplicating durable media',()=>{
 const raw={id:'a',task_id:'task',kind:'media',mime:'image/png',size:8};
 expect(assetFromMetadata('other',raw)).toBeNull();
 const task={id:'task',status:'completed',prompt:'Share',artifacts:[raw,{...raw,id:'upload',kind:'upload'}],events:[{sequence:1,type:'agent.media',payload:{artifact:raw,name:'diagram.png',caption:'An actual shared image'}}]} as Task;
 const shared=taskContentAssets(task,'assistant');expect(shared).toHaveLength(1);expect(shared[0]).toMatchObject({name:'diagram.png',source:'/api/workspace/tasks/task/artifacts/a',caption:'An actual shared image'});expect(taskContentAssets(task,'user')).toHaveLength(1);
});
it('rejects active content disguised as media and checks real media container signatures',()=>{
 expect(mediaSignatureMatches('image/png',new Uint8Array([137,80,78,71,13,10,26,10]))).toBe(true);
 const text=new TextEncoder();expect(mediaSignatureMatches('image/png',text.encode('<svg onload="alert(1)"/>'))).toBe(false);expect(mediaSignatureMatches('image/svg+xml',text.encode('<svg/>'))).toBe(false);expect(mediaSignatureMatches('video/mp4',text.encode('0000ftypisom'))).toBe(true);expect(mediaSignatureMatches('audio/wav',text.encode('RIFF0000WAVE'))).toBe(true);
});
