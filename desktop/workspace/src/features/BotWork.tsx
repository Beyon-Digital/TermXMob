import {useState} from 'react';
import {Badge} from '../components/ui/badge';
import {FormField} from '../components/ui/form-field';
import {Textarea} from '../components/ui/textarea';
import {request,type Session} from '../lib/api';
import {taskStatusLabel} from '../lib/bots';
import {usePolling} from '../lib/use-polling';
import type {BotTabProps} from './bot-api';
import {ActionButton,Empty,date} from './manager-ui';
import {useDirtyForm} from './settings-forms';
import {gql,useManagerLoad} from './use-manager';

const outcome=(text?:string)=>{const value=(text||'').trim();return value.length>400?value.slice(0,400)+'…':value};

export function BotTasks({bot,mutation}:BotTabProps){
  const {formProps,markClean}=useDirtyForm('bot-task');
  const {busy,mutate}=mutation;
  const {data,loading,error,reload}=useManagerLoad(()=>request<Session>(`/api/workspace/sessions/${bot.conversation_id}`));
  const [task,setTask]=useState('');
  const turns=[...(data?.turns||[])].reverse().slice(0,20);
  const live=bot.active_tasks>0||turns.some(turn=>turn.task&&['running','awaiting_approval','planning'].includes(turn.task.status));
  usePolling(()=>request<Session>(`/api/workspace/sessions/${bot.conversation_id}`),{interval:4000,immediate:false,enabled:live,onResult:()=>reload()});
  const paused=bot.state!=='active';
  async function run(){
    const result=await mutate(()=>gql('mutation($id:String!,$prompt:String){bot_run_now(bot_id:$id,prompt:$prompt){task_id}}',{id:bot.id,prompt:task.trim()||null}),'Task started',{refresh:true});
    if(result!==null){setTask('');markClean();reload()}
  }
  return <>
    <form {...formProps} className="manager-form" aria-label="Give this bot a task" onSubmit={event=>{event.preventDefault();void run()}}>
      <FormField label="Task" hint={paused?'Resume the bot to give it a task.':'Leave empty to run its standing instructions.'}>{control=>
        <Textarea {...control} value={task} disabled={paused} onChange={event=>setTask(event.target.value)}/>}</FormField>
      <div className="manager-item-actions"><ActionButton busy={busy} primary type="submit" disabled={paused||bot.violations.length>0}>Run task</ActionButton></div>
    </form>
    <section aria-label="Recent tasks" className="bot-history">
      <h4>Recent tasks</h4>
      {error&&<div role="alert" className="manager-error">{error}</div>}
      {loading&&!data?<Empty>Loading tasks…</Empty>
        :!turns.length?<Empty>No tasks yet. Run this bot once to see what it did.</Empty>
        :<ul className="bot-history-list">{turns.map(turn=><li key={turn.id}>
          <div><strong>{turn.prompt.length>140?turn.prompt.slice(0,140)+'…':turn.prompt}</strong><Badge variant="secondary">{taskStatusLabel(turn.task?.status)}</Badge></div>
          {turn.task?.error?<p className="bot-history-error">{outcome(turn.task.error)}</p>:turn.task?.result?<p>{outcome(turn.task.result)}</p>:null}
        </li>)}</ul>}
    </section>
  </>;
}

const eventText:Record<string,string>={created:'Created',updated:'Settings changed',paused:'Paused',resumed:'Resumed',ceiling_changed:'Ceiling changed',rule_revoked:'Remembered approval revoked',archived:'Deleted'};

export function BotActivity({bot}:BotTabProps){
  const {data,loading,error,reload}=useManagerLoad(()=>request<{events:{seq:number;kind:string;resource:string;event:string;payload:{by?:string};created:number}[]}>('/api/workspace/audit'));
  const rows=(data?.events||[]).filter(row=>row.kind==='bot'&&row.resource===bot.id);
  if(error)return <div role="alert" className="manager-error">{error} <button type="button" className="bot-link" onClick={reload}>Retry</button></div>;
  if(loading&&!data)return <Empty>Loading activity…</Empty>;
  if(!rows.length)return <Empty>No changes recorded for this bot yet. Edits, pauses and permission changes show up here.</Empty>;
  return <ul className="bot-history-list" aria-label="Bot activity">{rows.map(row=><li key={row.seq}>
    <div><strong>{eventText[row.event]||row.event.replace(/_/g,' ')}</strong><small>{date(row.created)}{row.payload?.by==='orchestrator'?' · by the main agent':''}</small></div>
  </li>)}</ul>;
}
