import type {Task} from './api';
import {toolActivities} from './tool-activity';

export type ContentKind='image'|'video'|'audio'|'pdf'|'document'|'spreadsheet'|'code'|'json'|'archive'|'file'|'folder'|'website';
export type ContentAsset={key:string;source:string;name:string;mime:string;size?:number;caption?:string;kind:ContentKind};
const identifier=/^[a-zA-Z0-9_-]{1,128}$/;
export const MAX_STRUCTURED_TEXT=64_000;
export const PREVIEW_TEXT_LIMIT=16_000;
export const contentObject=(value:unknown):Record<string,unknown>=>value&&typeof value==='object'&&!Array.isArray(value)?value as Record<string,unknown>:{};
export const contentText=(value:unknown)=>typeof value==='string'?value:'';
export function safeContentPath(value:unknown):string|null{
 if(typeof value!=='string'||!value||value.length>2048||/[\\\x00-\x1f<>]/.test(value)||/^(?:[a-z][a-z\d+.-]*:|\/\/|#)/i.test(value)||/[?#]/.test(value)||/(?:^|\/)\.\.(?:\/|$)/.test(value))return null;
 return value;
}
/** These are resource endpoints, never arbitrary same-origin URLs or local file paths. */
export function authorizedAssetSource(value:unknown):string|null{
 if(typeof value!=='string'||value.length>1024)return null;
 if(/^\/api\/(?:workspace|agent)\/tasks\/[A-Za-z0-9_-]{1,128}\/artifacts\/[A-Za-z0-9_-]{1,128}$/.test(value))return value;
 if(/^\/api\/media\/artifacts\/[A-Za-z0-9_-]{1,128}\/export\?format=original(?:&version=[1-9]\d{0,8})?$/.test(value))return value;
 return null;
}
/** Navigation only: no preview metadata, favicon or remote image requests are made. */
export function safeWebLink(value:unknown):string|null{
 if(typeof value!=='string'||value.length>2048||/[\s\\\x00-\x1f<>]/.test(value)||/%(?:00|0[ad])/i.test(value)||!/^https?:\/\//i.test(value))return null;
 try{const url=new URL(value);return ['http:','https:'].includes(url.protocol)&&!!url.hostname&&!url.username&&!url.password?url.href:null}catch{return null}
}
export function contentKind(name:string,mime=''):ContentKind{
 const type=mime.toLowerCase().split(';')[0],extension=name.split('.').at(-1)?.toLowerCase();
 if(type.startsWith('image/')&&type!=='image/svg+xml')return 'image';
 if(type.startsWith('video/'))return 'video';
 if(type.startsWith('audio/'))return 'audio';
 if(type==='application/pdf'||extension==='pdf')return 'pdf';
 if(type==='application/json'||['json','jsonc'].includes(extension||''))return 'json';
 if(/spreadsheet|csv|excel/.test(type)||['csv','xlsx','xls','ods'].includes(extension||''))return 'spreadsheet';
 if(/zip|tar|gzip|compressed/.test(type)||['zip','tar','gz','7z'].includes(extension||''))return 'archive';
 if(['ts','tsx','js','jsx','mjs','py','rs','go','java','swift','css','html','svg','sh','sql','yaml','yml','toml'].includes(extension||''))return 'code';
 if(type.startsWith('text/')||/document|word|rtf/.test(type)||['md','txt','docx','doc','rtf'].includes(extension||''))return 'document';
 return 'file';
}
export function assetFromMetadata(taskId:string,value:unknown,extra:Record<string,unknown>={}):ContentAsset|null{
 const item=contentObject(value),id=contentText(item.id);
 if(!identifier.test(taskId)||!identifier.test(id)||item.task_id&&item.task_id!==taskId)return null;
 const mime=contentText(item.mime).toLowerCase().split(';')[0];
 if(!mime||mime.length>128)return null;
 const name=contentText(extra.name)||contentText(item.name)||contentText(item.title)||({'upload':'Uploaded file','media':'Shared file','log':'Command output'}[contentText(item.kind)]||'Attached file');
 const caption=contentText(extra.caption)||contentText(item.caption);
 return {key:taskId+':'+id,source:'/api/workspace/tasks/'+taskId+'/artifacts/'+id,name:name.slice(0,240),mime,size:typeof item.size==='number'&&Number.isFinite(item.size)&&item.size>=0?item.size:undefined,caption:caption.slice(0,2000)||undefined,kind:contentKind(name,mime)};
}
export function taskContentAssets(task:Task,role:'user'|'assistant'):ContentAsset[]{
 const assets=new Map<string,ContentAsset>();
 const records=(task as Task&{artifacts?:unknown[]}).artifacts;
 for(const raw of Array.isArray(records)?records.slice(0,100):[]){const item=contentObject(raw);if((item.kind==='upload')!==(role==='user'))continue;const asset=assetFromMetadata(task.id,item);if(asset)assets.set(asset.key,asset)}
 for(const event of task.events||[]){
  if(event.payload.parent_call_id)continue;
  const rows=role==='user'&&event.type==='user.media'&&Array.isArray(event.payload.artifacts)?event.payload.artifacts:role==='assistant'&&event.type==='agent.media'?[event.payload.artifact]:[];
  for(const row of rows){const asset=assetFromMetadata(task.id,row,event.payload);if(asset)assets.set(asset.key,{...assets.get(asset.key),...asset})}
 }
 return [...assets.values()].slice(0,100);
}
export function decodeStructured(value:unknown,complete=true):unknown{
 if(!complete||typeof value!=='string'||value.length>MAX_STRUCTURED_TEXT||!/^\s*[\[{]/.test(value))return value;
 try{return JSON.parse(value)}catch{return value}
}
export function boundedContent(value:unknown,limit=PREVIEW_TEXT_LIMIT):string{
 let text:string,limited=false,remaining=limit+1024,nodes=0;const seen=new WeakSet<object>();
 try{text=typeof value==='string'?value:JSON.stringify(value,(_key,item:unknown)=>{
  if(typeof item==='string'){const length=Math.max(0,remaining);remaining-=Math.min(item.length,length);if(item.length>length){limited=true;return item.slice(0,length)+'…'}return item}
  if(item&&typeof item==='object'){
   if(seen.has(item))return '[Circular reference]';seen.add(item);
   if(++nodes>200){limited=true;return '[Additional data omitted from preview]'}
   if(Array.isArray(item)&&item.length>50){limited=true;return [...item.slice(0,50),'Additional items omitted from preview']}
   if(!Array.isArray(item)){const keys=Object.keys(item);if(keys.length>50){limited=true;return Object.fromEntries(keys.slice(0,50).map(key=>[key,(item as Record<string,unknown>)[key]]))}}
  }
  return item;
 },2)??''}catch{return 'This value cannot be displayed as JSON.'}
 return text.length>limit||limited?text.slice(0,limit)+'\n… Preview limited. Download raw data for the full value.':text;
}
export function contentSize(value?:number){if(value===undefined||!Number.isFinite(value)||value<0)return '';return value<1024?value+' B':value<1024*1024?(value/1024).toFixed(1)+' KB':(value/1024/1024).toFixed(1)+' MB'}
export const inlineMediaType=(mime:string)=>/^(?:image\/(?:png|jpeg|gif|webp|avif)|audio\/(?:mpeg|mp3|wav|x-wav|ogg|flac|mp4|webm)|video\/(?:mp4|webm|ogg))$/.test(mime);
/** Check container signatures before a reported media MIME reaches a browser decoder. */
export function mediaSignatureMatches(mime:string,bytes:Uint8Array){
 const ascii=(start:number,length:number)=>String.fromCharCode(...bytes.slice(start,start+length));
 if(mime==='image/png')return bytes.length>=8&&[137,80,78,71,13,10,26,10].every((value,index)=>bytes[index]===value);
 if(mime==='image/jpeg')return bytes[0]===255&&bytes[1]===216&&bytes[2]===255;
 if(mime==='image/gif')return /^GIF8[79]a$/.test(ascii(0,6));
 if(mime==='image/webp')return ascii(0,4)==='RIFF'&&ascii(8,4)==='WEBP';
 if(mime==='image/avif')return ascii(4,4)==='ftyp'&&/avif|avis/.test(ascii(8,24));
 if(/(?:audio|video)\/mp4/.test(mime))return ascii(4,4)==='ftyp';
 if(/(?:audio|video)\/webm/.test(mime))return bytes[0]===26&&bytes[1]===69&&bytes[2]===223&&bytes[3]===163;
 if(/(?:audio|video)\/ogg/.test(mime))return ascii(0,4)==='OggS';
 if(/audio\/(?:x-)?wav/.test(mime))return ascii(0,4)==='RIFF'&&ascii(8,4)==='WAVE';
 if(mime==='audio/flac')return ascii(0,4)==='fLaC';
 if(/audio\/(?:mpeg|mp3)/.test(mime))return ascii(0,3)==='ID3'||bytes[0]===255&&(bytes[1]&224)===224;
 return false;
}

export type ResponseContentAction={kind:'file';path:string;line?:number;label:string}|{kind:'website';url:string;label:string};
/** Response CTAs come only from completed tool metadata, never from prose instructions. */
export function responseContentActions(task:Task):ResponseContentAction[]{
 const actions=new Map<string,ResponseContentAction>();
 for(const activity of toolActivities(task.events||[],task.status).slice(-20).reverse()){
  if(activity.phase!=='completed')continue;
  const result=contentObject(decodeStructured(activity.output));if(result.ok===false)continue;
  const addFile=(path:unknown,line?:unknown)=>{const safe=safeContentPath(path);if(safe)actions.set('file:'+safe,{kind:'file',path:safe,line:typeof line==='number'&&Number.isSafeInteger(line)&&line>0?line:undefined,label:safe.split('/').at(-1)||safe})};
  const addWebsite=(url:unknown,title?:unknown)=>{const href=safeWebLink(url);if(href)actions.set('web:'+href,{kind:'website',url:href,label:contentText(title).slice(0,100)||new URL(href).host})};
  if(['read_file','write_file','edit_file','Read'].includes(activity.kind))addFile(result.path,result.start_line);
  if(['apply_patch','fileChange'].includes(activity.kind)&&Array.isArray(result.changes))for(const row of result.changes.slice(0,10))addFile(contentObject(row).path);
  if(activity.kind==='browser_context')addWebsite(result.url,result.title);
  if(activity.kind==='webSearch'&&Array.isArray(result.results))for(const row of result.results.slice(0,3)){const item=contentObject(row);addWebsite(item.url,item.title)}
  if(actions.size>=3)break;
 }
 return [...actions.values()].slice(0,3);
}
