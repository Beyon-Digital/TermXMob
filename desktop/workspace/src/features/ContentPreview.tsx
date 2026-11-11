import {useEffect,useMemo,useState} from 'react';
import {ArrowUpRight,Download,LoaderCircle,Workflow} from 'lucide-react';
import {Button} from '../components/ui/button';
import {transfer} from '../lib/transfers';
import {assetFromMetadata,authorizedAssetSource,boundedContent,contentKind,contentObject,contentSize,contentText,decodeStructured,inlineMediaType,mediaSignatureMatches,responseContentActions,safeContentPath,safeWebLink,taskContentAssets,type ContentAsset,type ContentKind} from '../lib/content-preview';
import type {Task} from '../lib/api';
import {workflowStatus} from '../lib/workflows';
import ContentTypeIcon from './ContentTypeIcon';
import './content-preview.css';

type OpenFile=(path:string,line?:number)=>void;
const fileName=(value:string)=>value.split(/[\\/]/).at(-1)?.replace(/[\x00-\x1f<>]/g,'').slice(0,180)||'attachment';
const failure=(reason:unknown)=>reason instanceof Error?reason.message:'The file could not be loaded. Try again.';

export function WebLinkPreview({url,title,description,source='Link in response'}:{url:string;title?:string;description?:string;source?:string}){
 const href=safeWebLink(url);
 if(!href)return <p className="content-text">{title||url}</p>;
 const host=new URL(href).host;
 return <a className="content-link-preview" href={href} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"><ContentTypeIcon kind="website"/><span><strong>{(title||host).slice(0,400)}</strong><small>{host} · {source}</small>{description&&<span className="content-link-description">{description.slice(0,400)}</span>}</span><ArrowUpRight size={16} aria-hidden="true"/></a>;
}

export function FileContentTile({path,name,line,size,kind,onOpenFile}:{path?:string;name?:string;line?:number;size?:number;kind?:ContentKind;onOpenFile?:OpenFile}){
 const safe=safeContentPath(path),label=name||path||'File';
 return <div className="content-file-tile"><ContentTypeIcon kind={kind||contentKind(label)}/><span><strong>{label}</strong>{(size!==undefined||line)&&<small>{[contentSize(size),line?'Line '+line:''].filter(Boolean).join(' · ')}</small>}</span>{safe&&onOpenFile&&<Button variant="ghost" size="sm" aria-label={'Open file '+safe+(line?':'+line:'')} onClick={()=>onOpenFile(safe,line)}>Open file<ArrowUpRight size={16} aria-hidden="true"/></Button>}</div>;
}

/** Only authenticated owned resources are fetched. Active HTML/SVG is download-only. */
export function ContentAssetPreview({asset,visible=true}:{asset:ContentAsset;visible?:boolean}){
 const [url,setURL]=useState(''),[error,setError]=useState(''),[loading,setLoading]=useState(false),[requested,setRequested]=useState(false),[retry,setRetry]=useState(0),[blocked,setBlocked]=useState(false),[downloading,setDownloading]=useState(false);
 const source=authorizedAssetSource(asset.source),canPreview=inlineMediaType(asset.mime)||!asset.mime&&asset.kind==='image';
 const limit=asset.kind==='image'?8*1024*1024:24*1024*1024;
 const eligible=canPreview&&(asset.size===undefined||asset.size<=limit),automatic=asset.size!==undefined&&asset.size<=limit;
 useEffect(()=>{const pause=()=>{setBlocked(true);setRequested(false);setError('');setURL('')};const events=['termx-session-locked','termx-locked','termx-signed-out'];for(const event of events)window.addEventListener(event,pause);return()=>{for(const event of events)window.removeEventListener(event,pause)}},[]);
 useEffect(()=>{
  setURL('');setError('');
  if(!visible||blocked||!source||!eligible||!automatic&&!requested){setLoading(false);return}
  let cancelled=false,created='';const controller=new AbortController();setLoading(true);
  void transfer(source,{signal:controller.signal}).then(async blob=>{
   if(cancelled)return;
   const mime=blob.type.toLowerCase().split(';')[0];
   if(blob.size>limit)throw Error('This file is too large for an inline preview. Download the original to view it.');
   if((asset.mime&&mime!==asset.mime)||contentKind(asset.name,mime)!==asset.kind||!inlineMediaType(mime)||!mediaSignatureMatches(mime,new Uint8Array(await blob.slice(0,40).arrayBuffer())))throw Error('A preview is unavailable for this file. You can download the original.');
   if(cancelled)return;
   created=URL.createObjectURL(blob);setURL(created);
  }).catch(reason=>{if(!cancelled)setError(failure(reason))}).finally(()=>{if(!cancelled)setLoading(false)});
  return()=>{cancelled=true;controller.abort();if(created)URL.revokeObjectURL(created)};
 },[source,asset.mime,asset.kind,limit,automatic,eligible,requested,retry,visible,blocked]);
 async function save(){if(!source||downloading)return;setDownloading(true);setError('');try{const blob=await transfer(source),url=URL.createObjectURL(new Blob([blob],{type:'application/octet-stream'})),anchor=document.createElement('a');anchor.href=url;anchor.download=fileName(asset.name);anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(reason){setError(failure(reason))}finally{setDownloading(false)}}
 return <figure className={'content-asset content-asset-'+asset.kind}>
  <div className="content-file-tile"><ContentTypeIcon kind={asset.kind}/><span><strong>{asset.name}</strong><small>{[asset.mime,contentSize(asset.size)].filter(Boolean).join(' · ')}</small></span><Button variant="ghost" size="sm" aria-label={'Download '+asset.name} disabled={!source||downloading||!visible} onClick={()=>void save()}>{downloading?<LoaderCircle size={16} className="spin" aria-hidden="true"/>:<Download size={16} aria-hidden="true"/>}<span>Download</span></Button></div>
  {source&&eligible&&!url&&!loading&&!error&&<Button className="content-load-preview" variant="ghost" size="sm" disabled={!visible} onClick={()=>{setBlocked(false);setRequested(true)}}>Load {asset.kind} preview</Button>}
  {loading&&<p className="content-preview-note" role="status">Loading {asset.kind} preview…</p>}
  {url&&!error&&(asset.kind==='image'?<img className="content-image-preview" src={url} alt={asset.caption||asset.name} loading="lazy" onError={()=>setError('The image could not be displayed. Download the original to view it.')}/>:asset.kind==='video'?<video className="content-video-preview" src={url} controls preload="metadata" aria-label={asset.name} onError={()=>setError('The video could not be played. Download the original to view it.')}/>:<audio className="content-audio-preview" src={url} controls preload="metadata" aria-label={asset.name} onError={()=>setError('The audio could not be played. Download the original to listen.')}/>)}
  {error&&<p className="content-preview-error" role="alert">{error}{canPreview&&<Button variant="ghost" size="sm" disabled={!visible} onClick={()=>{setError('');setRequested(true);setRetry(value=>value+1)}}>Retry preview</Button>}</p>}
  {asset.caption&&<figcaption>{asset.caption}</figcaption>}
 </figure>;
}

export function TaskContentAttachments({task,role,visible=true}:{task:Task;role:'user'|'assistant';visible?:boolean}){
 const assets=useMemo(()=>taskContentAssets(task,role),[task.id,task.events,(task as Task&{artifacts?:unknown[]}).artifacts,role]);
 const [all,setAll]=useState(false);
 if(!assets.length)return null;
 return <section className="content-attachments" aria-label={role==='user'?'Message attachments':'Shared files'}>{(task as Task&{artifacts_truncated?:boolean}).artifacts_truncated&&<p className="content-preview-note">Some attachment metadata is omitted from this view.</p>}{assets.slice(0,all?100:8).map(asset=><ContentAssetPreview key={asset.key} asset={asset} visible={visible}/>)}{!all&&assets.length>8&&<Button variant="ghost" size="sm" onClick={()=>setAll(true)}>Show {assets.length-8} more attachments</Button>}</section>;
}

function DataFields({value}:{value:Record<string,unknown>}){
 const entries=Object.entries(value).slice(0,20);
 return <><dl className="content-data-fields">{entries.map(([key,item])=><div key={key}><dt>{key.replaceAll('_',' ')}</dt><dd>{item!==null&&typeof item==='object'?<details><summary>{Array.isArray(item)?item.length+' items':Object.keys(item).length+' fields'}</summary><pre tabIndex={0}>{boundedContent(item,6000)}</pre></details>:<span>{boundedContent(item,2000)||String(item)}</span>}</dd></div>)}</dl>{Object.keys(value).length>20&&<p className="content-preview-note">Showing 20 fields. The full value is in Raw details.</p>}</>;
}
function SourceText({text,label}:{text:string;label:string}){return <pre className="content-source" tabIndex={0} role="region" aria-label={label}><code>{boundedContent(text)}</code></pre>}
function Changes({rows,onOpenFile}:{rows:unknown[];onOpenFile?:OpenFile}){
 return <div className="content-changes">{rows.slice(0,30).map((raw,index)=>{const row=contentObject(raw),path=contentText(row.path),diff=Array.isArray(row.preview)?row.preview.filter(line=>typeof line==='string').join('\n'):contentText(row.diff);return <section key={index}><FileContentTile path={path} name={path||'Changed file'} onOpenFile={onOpenFile}/>{(typeof row.added==='number'||typeof row.removed==='number')&&<p className="content-change-count">{typeof row.added==='number'&&<span>+{row.added} added</span>}{typeof row.removed==='number'&&<span>−{row.removed} removed</span>}</p>}{diff&&<details><summary>View changes</summary><SourceText text={diff} label={(path||'File')+' changes'}/></details>}</section>})}</div>;
}

type WorkflowReceipt={id:string;name:string;status:string;phases?:number;calls?:number};
const workflowRunStatuses=new Set(['queued','running','awaiting_approval','cancelling','completed','failed','cancelled','needs_attention']);
export const isWorkflowReceiptTool=(kind:string)=>/^(?:mcp__termx-workflows__)?(?:run_workflow|get_workflow_run|cancel_workflow_run)$/.test(kind);

/** Only typed workflow receipts become navigation. IDs never become URLs or execution requests. */
export function workflowRunReceipt(value:unknown,kind='',complete=true,depth=0):WorkflowReceipt|null{
 if(!complete||depth>4)return null;
 const decoded=decodeStructured(value,complete);
 if(Array.isArray(decoded)){
  const receipts=decoded.slice(0,30).map(item=>workflowRunReceipt(item,kind,complete,depth+1)).filter((item):item is WorkflowReceipt=>!!item);
  return receipts.length===1?receipts[0]:null;
 }
 const row=contentObject(decoded);
 if(row.ok===false||row.refused===true||row.isError===true||row.is_error===true)return null;
 const typed=row.type==='workflow_run'||row.kind==='workflow_run',nested=contentObject(row.run);
 const run=Object.keys(nested).length&&(isWorkflowReceiptTool(kind)||typed)?nested:typed?row:null;
 if(run){
  const id=contentText(run.id),status=contentText(run.status),name=contentText(run.name).trim();
  if(!/^[a-f0-9]{32}$/.test(id)||!workflowRunStatuses.has(status)||!name||row.run_id!==undefined&&row.run_id!==id)return null;
  const calls=Array.isArray(run.calls)?run.calls.map(contentObject):null;
  return {id,name:name.slice(0,120),status,...(calls?{phases:calls.filter(call=>call.operation==='phase').length,calls:calls.filter(call=>call.operation==='agent').length}:{})};
 }
 const content=row.content??row.contentItems??row.structuredContent;
 if(content!==undefined)return workflowRunReceipt(content,kind,complete,depth+1);
 if(['text','output_text','inputText'].includes(contentText(row.type))&&typeof row.text==='string')return workflowRunReceipt(row.text,kind,complete,depth+1);
 return null;
}

function WorkflowRunReceipt({receipt,visible}:{receipt:WorkflowReceipt;visible:boolean}){
 const counts=receipt.phases===undefined?[]:[receipt.phases+' '+(receipt.phases===1?'phase':'phases'),receipt.calls+' agent '+(receipt.calls===1?'call':'calls')];
 return <div className="content-workflow-receipt" role="group" aria-label="Workflow run receipt"><Workflow size={16} strokeWidth={1.5} aria-hidden="true"/><div className="content-workflow-summary"><strong>{receipt.name}</strong><span className="content-workflow-meta"><span>{workflowStatus(receipt.status).label}</span>{counts.map(count=><span key={count}>{count}</span>)}</span></div><Button variant="ghost" size="sm" disabled={!visible} onClick={()=>{if(visible)window.dispatchEvent(new CustomEvent('termx-open-workflow',{detail:{runId:receipt.id}}))}}>View run<ArrowUpRight size={16} strokeWidth={1.5} aria-hidden="true"/></Button></div>;
}

/** Metadata determines presentation. Plain text is never promoted to executable content. */
export function StructuredToolResult({value,kind='',taskId,onOpenFile,visible=true,complete=true,depth=0}:{value:unknown;kind?:string;taskId?:string;onOpenFile?:OpenFile;visible?:boolean;complete?:boolean;depth?:number}){
 const decoded=decodeStructured(value,complete),row=contentObject(decoded);
 if(depth>4)return <SourceText text={boundedContent(decoded)} label="Nested result"/>;
 if(isWorkflowReceiptTool(kind)&&(row.ok===false||row.refused===true||row.isError===true||row.is_error===true))return <>{contentText(row.error)&&<p className="content-preview-error" role="alert">{contentText(row.error)}</p>}<DataFields value={row}/></>;
 const receipt=workflowRunReceipt(decoded,kind,complete);
 if(receipt)return <WorkflowRunReceipt receipt={receipt} visible={visible}/>;
 if(decoded===null||decoded===undefined)return <p className="content-preview-note">No result was reported.</p>;
 if(typeof decoded!=='object')return <p className="content-text">{boundedContent(decoded)}</p>;
 if(Array.isArray(decoded))return <ul className="content-result-list">{decoded.slice(0,30).map((item,index)=><li key={index}><StructuredToolResult value={item} kind={kind} taskId={taskId} onOpenFile={onOpenFile} visible={visible} complete={complete} depth={depth+1}/></li>)}{decoded.length>30&&<li className="content-preview-note">Showing 30 of {decoded.length} items. The full value is in Raw details.</li>}</ul>;
 const error=contentText(row.error),resource=authorizedAssetSource(row.url??row.source),mime=contentText(row.mime??row.mime_type),name=contentText(row.name??row.title)||'Attached file';
 const asset=taskId&&row.artifact?assetFromMetadata(taskId,row.artifact,{name:row.shared||row.name,caption:row.caption}):resource&&mime?{key:resource,source:resource,name,mime,kind:contentKind(name,mime),size:typeof row.size==='number'?row.size:undefined}:null;
 let content;
 if(asset)content=<ContentAssetPreview asset={asset} visible={visible}/>;
 else if(['read_file','Read'].includes(kind)&&safeContentPath(row.path)&&typeof row.content==='string')content=<><FileContentTile path={contentText(row.path)} line={typeof row.start_line==='number'?row.start_line:undefined} onOpenFile={onOpenFile}/><SourceText text={row.content} label={contentText(row.path)+' content'}/>{row.truncated===true&&<p className="content-preview-note">The tool returned part of this file.</p>}</>;
 else if(kind==='list_files'&&Array.isArray(row.entries))content=<ul className="content-file-list">{row.entries.slice(0,50).map((item,index)=>{const entry=contentObject(item);return <li key={index}><FileContentTile path={contentText(entry.path)} kind={entry.kind==='dir'?'folder':undefined} size={typeof entry.size==='number'?entry.size:undefined} onOpenFile={entry.kind==='dir'?undefined:onOpenFile}/></li>})}</ul>;
 else if(['search_project','search_files'].includes(kind)&&Array.isArray(row.matches))content=<ul className="content-search-results">{row.matches.slice(0,30).map((item,index)=>{const match=contentObject(item);return <li key={index}><FileContentTile path={contentText(match.path)} line={typeof match.line==='number'&&match.line>0?match.line:undefined} onOpenFile={onOpenFile}/>{typeof match.text==='string'&&<p>{match.text.slice(0,2000)}</p>}</li>})}</ul>;
 else if(['apply_patch','fileChange','edit_file'].includes(kind)&&Array.isArray(row.changes))content=<Changes rows={row.changes} onOpenFile={onOpenFile}/>;
 else if(['write_file','edit_file'].includes(kind)&&safeContentPath(row.path))content=<FileContentTile path={contentText(row.path)} size={typeof row.size==='number'?row.size:undefined} onOpenFile={onOpenFile}/>;
 else if((kind==='webSearch'||row.type==='search_results')&&Array.isArray(row.results))content=<ul className="content-web-results">{row.results.slice(0,20).map((item,index)=>{const result=contentObject(item);return <li key={index}><WebLinkPreview url={contentText(result.url)} title={contentText(result.title)} description={contentText(result.snippet||result.description)} source="Tool result"/></li>})}</ul>;
 else if((['website','webpage','link'].includes(contentText(row.type))||kind==='browser_context')&&safeWebLink(row.url))content=<WebLinkPreview url={contentText(row.url)} title={contentText(row.title)} description={contentText(row.description)} source="Tool result"/>;
 else if(row.type==='code'&&typeof row.code==='string')content=<><p className="content-preview-note">{contentText(row.language)||'Code'}</p><SourceText text={row.code} label="Code result"/></>;
 else if(['run_shell','run_check','commandExecution'].includes(kind)&&typeof (row.output??row.stdout)==='string')content=<><div className="content-log-status">{typeof row.exit_code==='number'&&<span>Exit {row.exit_code}</span>}{typeof row.duration_ms==='number'&&<span>{(row.duration_ms/1000).toFixed(1)} seconds</span>}{row.truncated===true&&<span>Partial output</span>}</div><SourceText text={contentText(row.output??row.stdout)} label="Command output"/>{typeof row.stderr==='string'&&<SourceText text={row.stderr} label="Standard error"/>}</>;
 else if(Array.isArray(row.content))content=<StructuredToolResult value={row.content} kind={kind} taskId={taskId} onOpenFile={onOpenFile} visible={visible} complete={complete} depth={depth+1}/>;
 else if(['text','output_text','inputText'].includes(contentText(row.type))&&typeof row.text==='string')content=<StructuredToolResult value={row.text} kind={kind} taskId={taskId} onOpenFile={onOpenFile} visible={visible} complete={complete} depth={depth+1}/>;
 else if(typeof row.text==='string'&&Object.keys(row).every(key=>['text','ok','type'].includes(key)))content=<p className="content-text">{boundedContent(row.text)}</p>;
 else content=<DataFields value={row}/>;
 return <>{error&&<p className="content-preview-error" role="alert">{error}</p>}{content}{row.truncated===true&&!['read_file','run_shell','run_check','commandExecution'].includes(kind)&&<p className="content-preview-note">The tool returned a partial result.</p>}</>;
}

export function RawContentDetails({title,value}:{title:string;value:unknown}){
 const [error,setError]=useState('');
 function save(){try{const text=typeof value==='string'?value:JSON.stringify(value,null,2),url=URL.createObjectURL(new Blob([text??''],{type:'application/octet-stream'})),anchor=document.createElement('a');anchor.href=url;anchor.download='tool-details.json';anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}catch(reason){setError(failure(reason))}}
 return <details className="content-raw-details"><summary>{title}</summary><pre tabIndex={0} aria-label={title}>{boundedContent(value)}</pre><Button size="sm" variant="ghost" onClick={save}><Download size={16} aria-hidden="true"/>Download raw data</Button>{error&&<p role="alert" className="content-preview-error">{error}</p>}</details>;
}

export function ResponseContentActions({task,onOpenFile}:{task:Task;onOpenFile?:OpenFile}){
 const actions=useMemo(()=>responseContentActions(task),[task.events,task.status]);
 const available=actions.filter(action=>action.kind==='website'||!!onOpenFile);
 if(!available.length)return null;
 return <div className="response-content-actions" role="group" aria-label="Content actions">{available.map(action=>action.kind==='file'?<Button key={action.path} variant="outline" onClick={()=>onOpenFile?.(action.path,action.line)}><ContentTypeIcon kind={contentKind(action.path)} size={16}/>Open {action.label}</Button>:<Button key={action.url} variant="outline" asChild><a href={action.url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"><ContentTypeIcon kind="website" size={16}/>Open {action.label}<ArrowUpRight size={16} aria-hidden="true"/></a></Button>)}</div>;
}
