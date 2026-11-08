import {useState} from 'react';
import {Badge} from '../components/ui/badge';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {SettingsRow,SettingsSection} from '../components/ui/settings-section';
import {Textarea} from '../components/ui/textarea';
import {BOT_MESSAGE_FIELDS,TRIGGER_FIELDS,describeTrigger,groupThreads,kindLabel,partyName,reportOptions,reportValue,type BotMessage,type Thread,type TriggerSchedule} from '../lib/bot-messages';
import {usePolling} from '../lib/use-polling';
import type {BotTabProps} from './bot-api';
import {ActionButton,Empty,date} from './manager-ui';
import {gql} from './use-manager';

const listQuery=`query($b:String!,$c:String!){bot_messages(bot_id:$b){ ${BOT_MESSAGE_FIELDS} } schedules(conversation_id:$c){ ${TRIGGER_FIELDS} }}`;
const markMutation='mutation($message_id:String!,$status:String!){mark_message(message_id:$message_id,status:$status){ id status }}';
const sendMutation=`mutation($i:SendBotMessageInput!){send_bot_message(input:$i){ ${BOT_MESSAGE_FIELDS} }}`;
const editMutation='mutation($c:String!,$s:String!,$i:BotScheduleEditInput!){edit_schedule(conversation_id:$c,schedule_id:$s,input:$i){ id revision report_to }}';
const short=(text:string,size=80)=>text.length>size?text.slice(0,size)+'…':text;

function Message({message,busy,onMark,showSubject}:{message:BotMessage;busy:boolean;onMark:(message:BotMessage,status:'read'|'done')=>void;showSubject:boolean}){
  const toOwner=message.recipient.kind==='owner';
  return <li className="bot-message">
    <div className="bot-message-head">
      <Badge variant="outline">{partyName(message.sender)}</Badge>
      <span className="bot-message-to">to</span>
      <Badge variant="outline">{partyName(message.recipient)}</Badge>
      <small>{kindLabel[message.kind]}{message.automatic?' · automatic report':''} · {date(message.created_at)}</small>
      {message.status==='unread'&&toOwner&&<Badge>Unread</Badge>}
      {message.status==='parked'&&<Badge variant="secondary">Held</Badge>}
    </div>
    {showSubject&&<strong>{message.subject}</strong>}
    <p className="bot-message-body">{message.body}</p>
    {message.status==='parked'&&message.reason&&<p className="manager-help">{message.reason} It was delivered to nobody.</p>}
    <div className="manager-item-actions">
      {message.status==='unread'&&toOwner&&<ActionButton busy={busy} onClick={()=>onMark(message,'read')}>Mark as read</ActionButton>}
      {message.status==='parked'&&<ActionButton busy={busy} onClick={()=>onMark(message,'done')}>Dismiss message</ActionButton>}
    </div>
  </li>;
}

function Threads({threads,busy,onMark}:{threads:Thread[];busy:boolean;onMark:(message:BotMessage,status:'read'|'done')=>void}){
  const [opened,setOpened]=useState<string[]>([]);
  const toggle=(id:string)=>setOpened(current=>current.includes(id)?current.filter(value=>value!==id):[...current,id]);
  return <ul className="bot-threads" aria-label="Message threads">
    {threads.map(thread=>{
      const isOpen=opened.includes(thread.id),held=thread.messages.find(message=>message.status==='parked');
      return <li key={thread.id}>
        <button type="button" className="bot-thread-head" aria-expanded={isOpen} aria-controls={`bot-thread-${thread.id}`} onClick={()=>toggle(thread.id)}>
          <span className="bot-thread-subject">{thread.subject}</span>
          <small>{thread.messages.length===1?'1 message':`${thread.messages.length} messages`} · {date(thread.last)}</small>
        </button>
        <ol className="bot-path" aria-label={`Route of ${thread.subject}`}>{thread.path.map((name,index)=><li key={index}><Badge variant="outline">{name}</Badge></li>)}</ol>
        {thread.unread>0&&<Badge>{thread.unread===1?'1 unread':`${thread.unread} unread`}</Badge>}
        {held&&!isOpen&&<p className="manager-help">Held: {held.reason}</p>}
        {isOpen&&<ol className="bot-message-list" id={`bot-thread-${thread.id}`}>
          {thread.messages.map(message=><Message key={message.id} message={message} busy={busy} onMark={onMark} showSubject={message.subject!==thread.subject}/>)}
        </ol>}
      </li>;
    })}
  </ul>;
}

function Compose({bot,mutation,onSent}:BotTabProps&{onSent:()=>void}){
  const {busy,mutate}=mutation;
  const [subject,setSubject]=useState(''),[body,setBody]=useState(''),[tried,setTried]=useState(false);
  const error=tried&&!body.trim()?'Write a message to send.':'';
  async function submit(){
    setTried(true);
    if(!body.trim())return;
    const result=await mutate(()=>gql(sendMutation,{i:{to:'bot:'+bot.id,kind:'request',subject:subject.trim(),body:body.trim()}}),'Message sent');
    if(result!==null){setSubject('');setBody('');setTried(false);onSent()}
  }
  return <form className="manager-form" noValidate aria-label="Send a message" onSubmit={event=>{event.preventDefault();void submit()}}>
    <FormField label="Subject" hint="Optional. One line.">{control=><Input {...control} value={subject} maxLength={120} onChange={event=>setSubject(event.target.value)}/>}</FormField>
    <FormField label="Your message" required error={error} hint={bot.state==='paused'?'This bot is paused. It keeps the message until you resume it.':'The bot reads it as information, inside its own permissions.'}>{control=>
      <Textarea {...control} value={body} maxLength={8000} onChange={event=>setBody(event.target.value)}/>}</FormField>
    <div className="manager-item-actions"><ActionButton busy={busy} primary type="submit">Send message</ActionButton></div>
  </form>;
}

function Reports({bot,bots,rows,mutation,onChanged}:BotTabProps&{rows:TriggerSchedule[];onChanged:()=>void}){
  const options=reportOptions(bots,bot.id),name=(id:string)=>bots.find(row=>row.id===id)?.name||'a deleted bot';
  return <SettingsSection level={3} title="Where reports go" description="When a run finishes, its result is posted here. Nothing asks the model again.">
    {rows.length===0?<li className="settings-row"><Empty>No schedules yet. Add one under Schedules &amp; triggers. Its reports go to the main agent unless you choose otherwise.</Empty></li>
    :rows.map(row=><SettingsRow key={row.id} label={short(row.prompt)} description={describeTrigger(row.trigger||{kind:'time'},name)}>{control=>
      <Select {...control} value={reportValue(row.report_to||{kind:'orchestrator'})} disabled={mutation.busy}
        onChange={event=>void mutation.mutate(()=>gql(editMutation,{c:bot.conversation_id,s:row.id,i:{revision:row.revision,report_to:event.target.value}}),'Report destination saved').then(result=>{if(result!==null)onChanged()})}>
        {options.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}
      </Select>}</SettingsRow>)}
  </SettingsSection>;
}

export default function BotMessages(props:BotTabProps){
  const {bot,mutation}=props;
  const [messages,setMessages]=useState<BotMessage[]>([]),[rows,setRows]=useState<TriggerSchedule[]>([]),[version,setVersion]=useState(0),[error,setError]=useState(''),[loaded,setLoaded]=useState(false);
  usePolling(()=>gql<{bot_messages:BotMessage[];schedules:TriggerSchedule[]}>(listQuery,{b:bot.id,c:bot.conversation_id}),{
    interval:10000,resetKey:version,
    onResult:value=>{setMessages(value.bot_messages??[]);setRows(value.schedules??[]);setError('');setLoaded(true)},
    onError:reason=>setError(reason instanceof Error?reason.message:String(reason))});
  const refresh=()=>setVersion(value=>value+1);
  const mark=(message:BotMessage,status:'read'|'done')=>void mutation.mutate(()=>gql(markMutation,{message_id:message.id,status}),status==='done'?'Message dismissed':'Message marked as read').then(result=>{if(result!==null)refresh()});
  const threads=groupThreads(messages);
  return <>
    {error&&<p role="alert" className="manager-error">{error}</p>}
    <SettingsSection level={3} title="Messages" description="What this bot, the main agent and you have sent each other. Messages are written by agents or by you, so they are shown as text and never followed as orders.">
      <li className="settings-row bot-form-row">
        {!loaded?<p className="manager-help">Loading messages…</p>
        :threads.length===0?<Empty>No messages yet. Reports, requests and replies between this bot, the main agent and you appear here.</Empty>
        :<Threads threads={threads} busy={mutation.busy} onMark={mark}/>}
      </li>
    </SettingsSection>
    <SettingsSection level={3} title="Send a message" description="The bot reads it the next time it runs, or right away if a message trigger is on.">
      <li className="settings-row bot-form-row"><Compose {...props} onSent={refresh}/></li>
    </SettingsSection>
    <Reports {...props} rows={rows.filter(row=>row.state!=='archived')} onChanged={refresh}/>
  </>;
}
