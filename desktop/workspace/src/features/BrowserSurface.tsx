import BrowserSkillEditor,{type BrowserDraft} from './BrowserSkillEditor';
import {useCallback,useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {request} from '../lib/api';
import './browser.css';

type Profile={id:string;name:string;project_id:string;ephemeral:boolean};
type Tab={id:string;title:string;url:string;state:string;profile_id:string;project_id:string;document_revision:number;lease_revision:number;recording:boolean;grant_id:string|null};
type Element={index:number;tag:string;name:string;type:string|null;selector?:string;box:{x:number;y:number;width:number;height:number}};
type Context={tab_id:string;url:string;title:string;elements:Element[];document_revision:number;lease_revision:number};
type Review={id:string;status:string;decision:string;reason:string;target:string;effect:string;tool_id:string;run_id:string;expires_at:number};
type Annotation={id:string;comment:string;stale:boolean};
type Download={id:string;filename:string;size:number;approved:boolean};
export type BrowserSurfaceProps={projectId?:string|null;taskId?:string|null;onContext?:(context:unknown)=>void};
const post=(body:unknown):RequestInit=>({method:'POST',body:JSON.stringify(body)});

export function BrowserSurface({projectId,taskId,onContext}:BrowserSurfaceProps){
 const [profiles,setProfiles]=useState<Profile[]>([]),[tabs,setTabs]=useState<Tab[]>([]),[selected,setSelected]=useState('');
 const [url,setUrl]=useState(''),[profileId,setProfileId]=useState(''),[profileName,setProfileName]=useState(''),[showProfile,setShowProfile]=useState(false);
 const [error,setError]=useState(''),[busy,setBusy]=useState(false),[frame,setFrame]=useState(''),[connected,setConnected]=useState(false);
 const [context,setContext]=useState<Context|null>(null),[showContext,setShowContext]=useState(false),[grantOpen,setGrantOpen]=useState(false);
 const [sites,setSites]=useState(''),[expires,setExpires]=useState('600'),[editAllowed,setEditAllowed]=useState(true),[uploadsAllowed,setUploadsAllowed]=useState(false),[diagnosticsAllowed,setDiagnosticsAllowed]=useState(false);
 const [reviews,setReviews]=useState<Review[]>([]),[annotations,setAnnotations]=useState<Annotation[]>([]),[downloads,setDownloads]=useState<Download[]>([]),[comment,setComment]=useState(''),[find,setFind]=useState('');
 const [skillDraft,setSkillDraft]=useState<BrowserDraft|null>(null);
 const socket=useRef<WebSocket|null>(null),frameURL=useRef(''),surface=useRef<HTMLImageElement|null>(null),input=useRef<HTMLTextAreaElement|null>(null);
 const tab=tabs.find(t=>t.id===selected),profile=profiles.find(p=>p.id===tab?.profile_id);
 const activeTabs=tabs.filter(t=>t.project_id===(projectId||'') && !['closed','crashed'].includes(t.state));
 const load=useCallback(async()=>{
  const [p,t,r,d]=await Promise.all([request<Profile[]>('/api/browser/profiles'),request<Tab[]>('/api/browser/tabs'),request<Review[]>('/api/browser/reviews'),request<Download[]>('/api/browser/downloads')]);
  setProfiles(p);setTabs(t);setReviews(r);setDownloads(d);setProfileId(current=>p.some(x=>x.id===current&&x.project_id===(projectId||''))?current:p.find(x=>x.project_id===(projectId||''))?.id||'');
  setSelected(current=>t.some(x=>x.id===current&&x.project_id===(projectId||'')&&!['closed','crashed'].includes(x.state))?current:t.find(x=>x.project_id===(projectId||'')&&!['closed','crashed'].includes(x.state))?.id||'');
 },[projectId]);
 const run=useCallback(async(job:()=>Promise<unknown>)=>{setBusy(true);setError('');try{await job()}catch(e){setError(e instanceof Error?e.message:String(e))}finally{setBusy(false)}},[]);
 useEffect(()=>{void run(load);const timer=setInterval(()=>{void load().catch(()=>{})},2500);return()=>clearInterval(timer)},[load,run]);
 useEffect(()=>{setUrl(tab?.state==='private'?'':tab?.url||'');setContext(null);setAnnotations([])},[selected,tab?.url,tab?.state]);
 useEffect(()=>{
  if(!selected)return;
  let live=true;setConnected(false);setFrame('');
  const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/api/browser/tabs/${encodeURIComponent(selected)}/view`);socket.current=ws;ws.binaryType='blob';
  ws.onopen=()=>{if(live)setConnected(true)};
  ws.onmessage=(event)=>{if(!live)return;if(event.data instanceof Blob){const next=URL.createObjectURL(event.data);if(frameURL.current)URL.revokeObjectURL(frameURL.current);frameURL.current=next;setFrame(next)}else{try{const payload=JSON.parse(event.data);if(payload.type==='state')setTabs(items=>items.map(t=>t.id===payload.tab.id?payload.tab:t))}catch{}}};
  ws.onerror=()=>{if(live)setError('Browser view could not connect. Check the host connection and session.')};
  ws.onclose=()=>{if(live)setConnected(false)};
  return()=>{live=false;ws.close();socket.current=null;if(frameURL.current){URL.revokeObjectURL(frameURL.current);frameURL.current=''}};
 },[selected]);
 const human=(action:string,args:Record<string,unknown>={})=>{if(socket.current?.readyState===WebSocket.OPEN)socket.current.send(JSON.stringify({action,args}));else void run(async()=>{await request(`/api/browser/tabs/${selected}/human`,post({action,args}));await load()})};
 const observe=async()=>{const value=await request<Context>(`/api/browser/tabs/${selected}/context`);setContext(value);setShowContext(true);return value};
 const createTab=()=>run(async()=>{if(!profileId)throw new Error('Create or select a browser profile first.');const value=await request<Tab>('/api/browser/tabs',post({profile_id:profileId,url:url.trim()||'about:blank'}));await load();setSelected(value.id)});
 const handoff=()=>run(async()=>{if(!taskId)throw new Error('Start a chat task before handing over this tab.');await request(`/api/browser/tabs/${selected}/handoff`,post({run_id:taskId,origins:sites.split(/[\n,]/).map(s=>s.trim()).filter(Boolean),actions:['navigate','observe','capture','click','scroll','wait','find','zoom','history',...(editAllowed?['type']:[]),...(uploadsAllowed?['upload','download']:[]),...(diagnosticsAllowed?['diagnostics']:[])],expires_in:Number(expires)}));setGrantOpen(false);await load()});
 const pending=reviews.filter(r=>r.run_id===taskId&&r.status==='needs_user'&&r.expires_at>Date.now()/1000);
 const allowed=reviews.filter(r=>r.run_id===taskId&&r.status==='completed').length;
 const decision=(id:string,approve:boolean)=>run(async()=>{await request(`/api/browser/reviews/${id}/decision`,post({approve}));await load()});
 const uploadFile=(file:File)=>run(async()=>{if(file.size>10*1024*1024)throw new Error('Choose a file smaller than 10 MiB.');const bytes=new Uint8Array(await file.arrayBuffer());let text='';for(let i=0;i<bytes.length;i+=8192)text+=String.fromCharCode(...bytes.subarray(i,i+8192));const ref=await request(`/api/browser/tabs/${selected}/uploads`,post({filename:file.name,mime_type:file.type||'application/octet-stream',data_base64:btoa(text)}));onContext?.({type:'approved-browser-upload',file:ref,tab_id:selected})});
 return <section className="browser-workspace" aria-label="Built-in browser workspace">
  <div className="browser-tabstrip" role="tablist" aria-label="Built-in browser tabs">
   {activeTabs.map(t=><Button key={t.id} role="tab" aria-selected={selected===t.id} variant={selected===t.id?'secondary':'ghost'} onClick={()=>setSelected(t.id)}>{t.title||'New tab'}</Button>)}
   <Button variant="ghost" aria-label="Open built-in tab" onClick={()=>void createTab()} disabled={busy||!profileId}>+</Button>
   <select aria-label="Browser profile" value={profileId} onChange={e=>setProfileId(e.target.value)}>{!profileId&&<option value="">Choose profile</option>}{profiles.filter(p=>p.project_id===(projectId||'')).map(p=><option key={p.id} value={p.id}>{p.name}{p.ephemeral?' · Temporary':''}</option>)}</select>
   <Button variant="ghost" onClick={()=>setShowProfile(!showProfile)}>Profiles</Button>
  </div>
  {showProfile&&<form className="browser-profile-editor" onSubmit={e=>{e.preventDefault();void run(async()=>{const value=await request<Profile>('/api/browser/profiles',post({project_id:projectId||'',name:profileName,ephemeral:false}));await load();setProfileId(value.id);setProfileName('')})}}>
    <Input aria-label="New profile name" placeholder="New project browser profile" value={profileName} onChange={e=>setProfileName(e.target.value)}/><Button type="submit" disabled={busy||!profileName.trim()}>Create profile</Button>
    <Button type="button" variant="ghost" disabled={!profileId||busy} onClick={()=>void run(async()=>{await request(`/api/browser/profiles/${profileId}/clear`,post({}));await load()})}>Clear profile & history</Button>
  </form>}
  <form className="browser-address" onSubmit={e=>{e.preventDefault();if(tab)human('navigate',{url});else void createTab()}}>
   <Button type="button" variant="ghost" aria-label="Go back" disabled={!tab} onClick={()=>human('history',{direction:'back'})}>←</Button><Button type="button" variant="ghost" aria-label="Go forward" disabled={!tab} onClick={()=>human('history',{direction:'forward'})}>→</Button><Button type="button" variant="ghost" aria-label="Reload page" disabled={!tab} onClick={()=>human('history',{direction:'reload'})}>↻</Button>
   <img src="/assets/browser/lock.svg" alt=""/>
   <Input aria-label="Page address" placeholder={tab?.state==='private'?'Private login · address capture paused':'Enter a URL to open in TermX'} value={url} onChange={e=>setUrl(e.target.value)}/><Button type="submit" variant="ghost" disabled={busy}>Go</Button>
   <span className="browser-profile-label">{profile?.name}</span>
  </form>
  {tab&&<div className={`browser-control ${tab.state==='agent'?'active':tab.state==='private'?'private':''}`}>
   <img src="/assets/browser/shield.svg" alt=""/><span>{tab.state==='private'?'Private login · you are in control':pending.length?'Agent paused · approval required':tab.state==='agent'?'Agent is using this tab':'You are in control'}</span>
   {tab.state==='agent'?<Button variant="secondary" onClick={()=>void run(async()=>{await request(`/api/browser/tabs/${selected}/takeover`,post({private:false}));await load()})}>Take over</Button>:<Button variant="secondary" disabled={!taskId} onClick={()=>{setSites(tab.url==='about:blank'?'':new URL(tab.url).origin);setGrantOpen(true)}}>{tab.state==='private'?'Resume agent':'Let agent use'}</Button>}
   {tab.state!=='private'&&<Button variant="ghost" onClick={()=>void run(async()=>{await request(`/api/browser/tabs/${selected}/takeover`,post({private:true}));setContext(null);await load()})}>Private login</Button>}
   <Button variant="ghost" disabled={tab.state==='private'} onClick={()=>void run(async()=>{await observe()})}>Ask about page</Button>
   <Button variant="ghost" aria-label="Close selected tab" onClick={()=>void run(async()=>{await request(`/api/browser/tabs/${selected}`,{method:'DELETE'});await load()})}>Close tab</Button>
  </div>}
  {error&&<div className="browser-error" role="alert">{error}<Button variant="ghost" onClick={()=>void run(load)}>Retry</Button></div>}
  {grantOpen&&<div className="browser-grant" role="dialog" aria-label="Task browser handoff">
    <h3>Let this task use the tab</h3><p>{profile?.name} · Task {taskId?.slice(0,8)}. A handoff allows routine actions inside these exact sites. Consequential actions still require you.</p>
    <label>Approved origins<textarea value={sites} onChange={e=>setSites(e.target.value)} placeholder="https://example.com"/></label>
    <label>Duration<select value={expires} onChange={e=>setExpires(e.target.value)}><option value="300">5 minutes</option><option value="600">10 minutes</option><option value="1800">30 minutes</option><option value="3600">1 hour</option></select></label>
    <label><input type="checkbox" checked={editAllowed} onChange={e=>setEditAllowed(e.target.checked)}/> Edit non-sensitive fields</label><label><input type="checkbox" checked={uploadsAllowed} onChange={e=>setUploadsAllowed(e.target.checked)}/> Approved uploads and downloads</label><label><input type="checkbox" checked={diagnosticsAllowed} onChange={e=>setDiagnosticsAllowed(e.target.checked)}/> Read-only developer diagnostics</label>
    <div className="browser-actions"><Button disabled={busy||!sites.trim()||!taskId} onClick={()=>void handoff()}>Hand over & observe</Button><Button variant="ghost" onClick={()=>setGrantOpen(false)}>Cancel</Button></div>
  </div>}
  <div className="browser-main">
   <div className="browser-page">
    {frame?<><img ref={surface} src={frame} className="browser-live-view" alt="Live rendered browser page. Use Page controls below for keyboard accessible page content." onPointerDown={e=>{const r=e.currentTarget.getBoundingClientRect();input.current?.focus();human('click',{x:(e.clientX-r.left)*1440/r.width,y:(e.clientY-r.top)*900/r.height})}} onWheel={e=>human('scroll',{x:e.deltaX,y:e.deltaY})}/><textarea ref={input} className="browser-remote-input" aria-label="Type into the focused browser page field" autoComplete="off" onKeyDown={e=>{if(e.key.length>1||e.metaKey||e.ctrlKey){if(e.key==='Escape'){e.currentTarget.blur();return}e.preventDefault();const modifiers=[e.metaKey?'Meta':'',e.ctrlKey?'Control':'',e.altKey?'Alt':'',e.shiftKey?'Shift':''].filter(Boolean);human('key',{key:[...modifiers,e.key===' '?'Space':e.key].join('+')})}}} onInput={e=>{if(!(e.nativeEvent as InputEvent).isComposing){human('type',{text:e.currentTarget.value});e.currentTarget.value=''}}} onCompositionEnd={e=>{human('type',{text:e.currentTarget.value});e.currentTarget.value=''}}/></>:<div className="browser-empty"><h2>{tab?'Connecting browser…':'Your built-in browser'}</h2><p>{tab?'The host is preparing the live page.':'Choose a project profile and open a site to browse here.'}</p><p>Signed-in profiles belong to this workspace. Share page context or hand over a tab when you need the agent.</p>{!profiles.length&&<Button onClick={()=>setShowProfile(true)}>Create a browser profile</Button>}</div>}
    {tab&&<div className="browser-diagnostics"><span>{connected?'Live browser connected':'View disconnected'}</span><form onSubmit={e=>{e.preventDefault();human('find',{text:find})}}><Input aria-label="Find on page" value={find} onChange={e=>setFind(e.target.value)} placeholder="Find on page"/><Button type="submit" variant="ghost">Find</Button></form><label>Zoom<select aria-label="Page zoom" defaultValue="1" onChange={e=>human('zoom',{zoom:Number(e.target.value)})}><option value="0.75">75%</option><option value="1">100%</option><option value="1.25">125%</option><option value="1.5">150%</option><option value="2">200%</option></select></label></div>}
   </div>
   <aside className="browser-activity" aria-label="Browser control and review activity">
    {tab?.state==='private'?<><span className="browser-badge attention">Private login</span><h3>I’m paused while you sign in.</h3><p>The agent and Auto Review cannot inspect this page or your inputs during private login.</p><div className="browser-card"><strong>Capture paused</strong><p>Pending permits are revoked. Resume checks the current page again.</p></div></>:<><span className="browser-badge">{tab?.state==='agent'?'Auto Review active':'Human control'}</span><p>{tab?.state==='agent'?'Routine work stays inside this task, tab and site grant.':'Browse here, then choose exactly what to share with your chat.'}</p><div className="browser-card"><strong>{allowed} completed actions</strong><p>Within this task’s approved scope.</p></div></>}
    {pending.map(r=><article className="browser-card browser-approval" key={r.id}><span className="browser-badge attention">Needs your approval</span><h3>{r.effect} · {r.target}</h3><p>{r.reason.replaceAll('_',' ')}</p><code>{r.tool_id}</code><p>This decision applies to this exact current action.</p><Button disabled={busy} onClick={()=>void decision(r.id,true)}>Approve once</Button><Button variant="ghost" disabled={busy} onClick={()=>void decision(r.id,false)}>Deny</Button></article>)}
    <details><summary>Review activity</summary>{reviews.filter(r=>!taskId||r.run_id===taskId).slice(-20).reverse().map(r=><div className="browser-review-row" key={r.id}><span>{r.decision}</span><span>{r.tool_id}<small>{r.reason.replaceAll('_',' ')}</small></span></div>)}{!reviews.length&&<p>No browser decisions yet.</p>}</details>
    {tab&&<><Button variant="ghost" disabled={tab.state==='private'} onClick={()=>void run(async()=>{await observe()})}>Page controls</Button><label className="browser-upload">Approve a file for this tab<input type="file" disabled={tab.state==='private'} onChange={e=>{const file=e.target.files?.[0];if(file)void uploadFile(file);e.target.value=''}}/></label><Button variant="ghost" disabled={tab.state==='private'} onClick={()=>void run(async()=>{await request(`/api/browser/tabs/${selected}/recording`,post({enabled:!tab.recording}));await load()})}>{tab.recording?'Stop recording':'Record safe task steps'}</Button>{<Button variant="ghost" disabled={tab.state==='private'} onClick={()=>void run(async()=>{const draft=await request<BrowserDraft>(`/api/browser/tabs/${selected}/skill-draft`,post({name:'Browser task draft'}));setSkillDraft(draft)})}>Review recorded skill</Button>}</>}
    <details><summary>Downloads</summary>{downloads.map(d=><div key={d.id}>{d.approved?<a href={`/api/browser/downloads/${d.id}`} download>{d.filename}</a>:<span>{d.filename} · awaiting exact export approval</span>}<small>{Math.ceil(d.size/1024)} KiB</small></div>)}{!downloads.length&&<p>No downloads.</p>}</details>
   </aside>
  </div>
  {skillDraft&&<BrowserSkillEditor initial={skillDraft} tabs={activeTabs.filter(t=>t.id!==selected)} onError={e=>setError(e instanceof Error?e.message:String(e))} onClose={()=>setSkillDraft(null)}/>}
  {showContext&&context&&tab?.state!=='private'&&<section className="browser-context" aria-label="Page context preview"><div className="browser-actions"><h3>{context.title}</h3><Button onClick={()=>{onContext?.({type:'browser-context',context:{...context,elements:context.elements.filter(e=>e.name&&e.box.width>0&&e.box.height>0).slice(0,100)}});setShowContext(false)}}>Attach this context</Button><Button variant="ghost" onClick={()=>setShowContext(false)}>Close</Button></div><p>{context.url} · Document {context.document_revision}. Sharing this snapshot grants no ongoing browser control.</p><div className="browser-elements">{context.elements.filter(e=>e.name&&e.box.width>0&&e.box.height>0).slice(0,100).map(e=><div key={e.index}><span>{e.name}</span>{['a','button','input','select','textarea'].includes(e.tag)&&<Button variant="ghost" onClick={()=>human('click',e.selector?{selector:e.selector}:{x:e.box.x+e.box.width/2,y:e.box.y+e.box.height/2})}>Focus {e.tag}</Button>}</div>)}</div><form onSubmit={e=>{e.preventDefault();void run(async()=>{await request(`/api/browser/tabs/${selected}/annotations`,post({revision:context.document_revision,comment}));setComment('');setAnnotations(await request(`/api/browser/tabs/${selected}/annotations`))})}}><Input aria-label="Page annotation" value={comment} onChange={e=>setComment(e.target.value)} placeholder="Add a comment to this page revision"/><Button type="submit" disabled={!comment.trim()}>Annotate</Button></form>{annotations.map(a=><p key={a.id}>{a.comment}{a.stale?' · Stale after navigation':''}</p>)}</section>}
 </section>
}
export default BrowserSurface;
