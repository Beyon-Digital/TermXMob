import {useMemo,useState} from 'react';
import {ArrowUpRight,Plus,Search,Folder,CheckCircle2,Clock3,Inbox} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import type {Session,Project} from '../lib/api';
import {taskState} from '../lib/task-presentation';
import './task-hub.css';

export default function TaskHub({sessions,projects,onOpen,onNew,onRefresh,loading}:{sessions:Session[];projects:Project[];onOpen:(id:string)=>void;onNew:()=>void;onRefresh:()=>void;loading:boolean}){
 const [query,setQuery]=useState(''),[filter,setFilter]=useState('all'),[limit,setLimit]=useState(40);
 const available=sessions.filter(session=>!session.archived);
 const counts={running:available.filter(session=>taskState(session.latest_status||'').active).length,attention:available.filter(session=>taskState(session.latest_status||'').attention).length,completed:available.filter(session=>session.latest_status==='completed').length};
 const rows=useMemo(()=>available.filter(session=>{const state=taskState(session.latest_status||'');return (filter==='all'||filter==='running'&&state.active||filter==='attention'&&state.attention||filter==='completed'&&session.latest_status==='completed')&&[session.title,session.cwd,session.model,session.engine].join(' ').toLowerCase().includes(query.toLowerCase())}).sort((a,b)=>Number(b.pinned)-Number(a.pinned)||b.updated_at-a.updated_at),[sessions,query,filter]);
 return <section className="task-hub" aria-label="Task dashboard"><header className="task-hub-heading"><div><span className="task-eyebrow">YOUR WORKSPACE</span><h1>Move your work forward.</h1><p>Start a task, follow its progress, and review what changed.</p></div><Button onClick={onNew}><Plus size={16}/>New task</Button></header>
  <div className="task-metrics">{([{id:'running',label:'In progress',icon:Clock3},{id:'attention',label:'Needs attention',icon:Inbox},{id:'completed',label:'Completed',icon:CheckCircle2}] as const).map(item=><button key={item.id} onClick={()=>{setFilter(item.id);setLimit(40)}} aria-pressed={filter===item.id}><item.icon size={18}/><span>{item.label}</span><strong>{counts[item.id]}</strong></button>)}</div>
  <div className="task-hub-tools"><Search size={16}/><Input aria-label="Search tasks" placeholder="Search tasks, projects or models" value={query} onChange={event=>{setQuery(event.target.value);setLimit(40)}}/><Button variant="ghost" onClick={onRefresh}>Refresh</Button></div>
  <div className="task-filters" role="group" aria-label="Task filters">{[['all','All tasks'],['running','Running'],['attention','Needs attention'],['completed','Completed']].map(([id,label])=><Button key={id} variant={filter===id?'default':'ghost'} aria-pressed={filter===id} onClick={()=>{setFilter(id);setLimit(40)}}>{label}</Button>)}</div>
  {!rows.length&&<div className="task-hub-empty"><Folder size={28}/><h2>{loading?'Loading tasks…':query||filter!=='all'?'No matching tasks':'What are we working on?'}</h2><p>{query||filter!=='all'?'Try another filter or search.':'Choose a project and describe the outcome you want.'}</p>{!loading&&<Button onClick={onNew}>Create a task</Button>}</div>}
  <div className="task-cards">{rows.slice(0,limit).map(session=>{const state=taskState(session.latest_status||''),project=projects.find(item=>item.id===session.project_id);return <button className="task-card" key={session.id} onClick={()=>onOpen(session.id)}><div className="task-card-meta"><span className={'task-state '+state.tone}>{state.label}</span><span>{new Date(session.updated_at*1000).toLocaleDateString()}</span></div><h2>{session.title||'Untitled task'}</h2><p>{project?.name||session.cwd||'No project'} · {session.engine||'TermX'}</p><div className="task-card-footer"><span>{state.attention?'Review next action':session.latest_status==='completed'?'Review result':'Open conversation'}</span><ArrowUpRight size={16}/></div></button>})}</div>
  {rows.length>limit&&<Button variant="outline" onClick={()=>setLimit(value=>value+40)}>Show more tasks</Button>}
 </section>;
}
