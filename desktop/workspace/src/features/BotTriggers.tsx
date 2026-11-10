import {useRef,useState} from 'react';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Textarea} from '../components/ui/textarea';
import {describeTrigger,reportOptions,reportValue,type Trigger} from '../lib/bot-messages';
import {TRIGGER_SCHEDULE_FIELDS,authorizationDays,type BotSchedule} from '../lib/bot-schedules';
import type {BotTabProps} from './bot-api';
import {ActionButton,Empty,date} from './manager-ui';
import {gql} from './use-manager';

const createMutation=`mutation($c:String!,$i:BotScheduleInput!){create_schedule(conversation_id:$c,input:$i){ ${TRIGGER_SCHEDULE_FIELDS} webhook_secret }}`;
const editMutation=`mutation($c:String!,$s:String!,$i:BotScheduleEditInput!){edit_schedule(conversation_id:$c,schedule_id:$s,input:$i){ ${TRIGGER_SCHEDULE_FIELDS} }}`;
const enableMutation='mutation($c:String!,$s:String!,$enabled:Boolean!,$r:Int!){set_schedule_enabled(conversation_id:$c,schedule_id:$s,enabled:$enabled,revision:$r){ id }}';
const archiveMutation='mutation($c:String!,$s:String!,$r:Int!){archive_schedule(conversation_id:$c,schedule_id:$s,revision:$r){ id }}';
const rotateMutation=`mutation($c:String!,$s:String!){rotate_webhook_secret(conversation_id:$c,schedule_id:$s){ ${TRIGGER_SCHEDULE_FIELDS} webhook_secret }}`;

type Kind='message'|'bot_finished'|'file_change'|'webhook';
interface Draft { kind:Kind; botId:string; on:'completed'|'any'; glob:string; debounce:string; prompt:string; report:string; days:string; runs:string; consent:boolean }
const kinds:{id:Kind;label:string}[]=[{id:'message',label:'A message arrives'},{id:'bot_finished',label:'Another bot finishes'},{id:'file_change',label:'Files change'},{id:'webhook',label:'A webhook is called'}];
const globProblem=(glob:string)=>!glob.trim()||glob.length>200||/^[/~]/.test(glob)||glob.includes('\\')||/^[a-z]:/i.test(glob)||glob.split('/').includes('..');
const urlOf=(row:BotSchedule)=>row.webhook_path?location.origin+row.webhook_path:'';

function draftOf(row:BotSchedule|null):Draft{
  const trigger=row?.trigger&&row.trigger.kind!=='time'?row.trigger:null;
  return {kind:(trigger?.kind as Kind)||'message',botId:trigger?.kind==='bot_finished'?trigger.bot_id:'',on:trigger?.kind==='bot_finished'?trigger.on:'completed',
    glob:trigger?.kind==='file_change'?trigger.glob:'',debounce:String(trigger?.kind==='file_change'?trigger.debounce_s:30),
    prompt:row?.prompt||'',report:row?.report_to?reportValue(row.report_to):'orchestrator',days:'30',runs:'30',consent:false};
}
function triggerOf(draft:Draft):Trigger{
  return draft.kind==='bot_finished'?{kind:'bot_finished',bot_id:draft.botId,on:draft.on}
    :draft.kind==='file_change'?{kind:'file_change',glob:draft.glob.trim(),debounce_s:Number(draft.debounce)}:{kind:draft.kind};
}

/** A secret is shown here and nowhere else: not in the list, not after this dialog closes. */
function SecretReveal({title,url,secret,onDone}:{title:string;url:string;secret:string;onDone:()=>void}){
  const [copied,setCopied]=useState<'idle'|'copied'|'failed'>('idle');
  const copy=async()=>{try{await navigator.clipboard.writeText(secret);setCopied('copied')}catch{setCopied('failed')}};
  return <Dialog open onOpenChange={next=>{if(!next)onDone()}}>
    <DialogContent className="bot-dialog" aria-describedby="bot-secret-description">
      <DialogTitle>{title}</DialogTitle>
      <DialogDescription id="bot-secret-description">Copy the secret now. It is shown once and cannot be read again; rotate it if you lose it.</DialogDescription>
      <dl className="bot-secret">
        <dt>Address</dt><dd><code>{url}</code></dd>
        <dt>Secret</dt><dd><code>{secret}</code></dd>
      </dl>
      <p className="manager-help">Sign each request: send <code>X-Termx-Timestamp</code> (seconds) and <code>X-Termx-Signature</code> as <code>sha256=</code> and the HMAC-SHA256 of <code>timestamp.body</code> with the secret. Requests older than five minutes, repeats and bodies over 64 KB are refused. The address only works if you make this host reachable by the sender.</p>
      <div className="bot-dialog-actions">
        <Button variant="secondary" onClick={()=>void copy()}>Copy secret</Button>
        <Button onClick={onDone}>Done</Button>
      </div>
      <p role="status" className="manager-help">{copied==='copied'?'Secret copied':copied==='failed'?'Could not copy. Select the secret and copy it yourself.':''}</p>
    </DialogContent>
  </Dialog>;
}

function TriggerDialog({bot,bots,row,mutation,onClose,onSaved}:BotTabProps&{row:BotSchedule|null;onClose:()=>void;onSaved:(created:(BotSchedule&{webhook_secret?:string|null})|null)=>void}){
  const {busy,mutate}=mutation;
  const [draft,setDraft]=useState(()=>draftOf(row)),[tried,setTried]=useState(false);
  const requestId=useRef(crypto.randomUUID());
  const set=<K extends keyof Draft>(key:K,value:Draft[K])=>setDraft(current=>({...current,[key]:value}));
  const others=bots.filter(candidate=>candidate.id!==bot.id&&candidate.state!=='archived'),creating=!row;
  const errors={
    prompt:!draft.prompt.trim()?'Say what the bot should do when this happens.':'',
    bot:draft.kind==='bot_finished'&&!draft.botId?'Choose the bot to watch.':'',
    glob:draft.kind==='file_change'&&globProblem(draft.glob)?'Enter a glob inside the project, such as src/**/*.py.':'',
    debounce:draft.kind==='file_change'&&!(Number.isInteger(Number(draft.debounce))&&Number(draft.debounce)>=5&&Number(draft.debounce)<=3600)?'Enter 5 to 3600 seconds.':'',
    consent:creating&&!draft.consent?'Confirm that the bot may run on its own.':'',
  };
  const shown=(key:keyof typeof errors)=>tried?errors[key]:'';
  async function submit(){
    setTried(true);
    if(Object.values(errors).some(Boolean))return;
    const trigger=triggerOf(draft);
    if(row){
      const result=await mutate(()=>gql<{edit_schedule:BotSchedule}>(editMutation,{c:bot.conversation_id,s:row.id,i:{revision:row.revision,prompt:draft.prompt.trim(),trigger,report_to:draft.report}}),'Trigger saved');
      if(result!==null)onSaved(null);
      return;
    }
    const result=await mutate(()=>gql<{create_schedule:BotSchedule&{webhook_secret?:string|null}}>(createMutation,{c:bot.conversation_id,i:{
      request_id:requestId.current,prompt:draft.prompt.trim(),trigger,report_to:draft.report,grant_days:Number(draft.days),max_runs:Number(draft.runs)||30}}),'Trigger created');
    if(result!==null)onSaved(result.create_schedule);
  }
  const verb=creating?'Create trigger':'Save trigger';
  return <Dialog open onOpenChange={next=>{if(!next&&!busy)onClose()}}>
    <DialogContent className="bot-dialog" aria-describedby="bot-trigger-description">
      <DialogTitle>{creating?'Add trigger':'Edit trigger'}</DialogTitle>
      <DialogDescription id="bot-trigger-description">A trigger starts this bot when something happens, instead of at a set time. What woke it is given to the bot as information, never as orders.</DialogDescription>
      <form className="manager-form" noValidate aria-label={creating?'Add trigger':'Edit trigger'} onSubmit={event=>{event.preventDefault();void submit()}}>
        <FormField label="Starts when" hint={bot.project_id?undefined:'Watching files needs a bot that works in a project.'}>{control=>
          <Select {...control} value={draft.kind} disabled={!creating} onChange={event=>set('kind',event.target.value as Kind)}>
            {kinds.map(kind=><option key={kind.id} value={kind.id} disabled={kind.id==='file_change'&&!bot.project_id}>{kind.label}</option>)}
          </Select>}</FormField>
        {draft.kind==='bot_finished'&&<>
          <FormField label="Bot to watch" required error={shown('bot')}>{control=>
            <Select {...control} value={draft.botId} onChange={event=>set('botId',event.target.value)}>
              <option value="">Choose a bot</option>{others.map(other=><option key={other.id} value={other.id}>{other.name}</option>)}
            </Select>}</FormField>
          <FormField label="Run when it" hint="Its result is passed on as information.">{control=>
            <Select {...control} value={draft.on} onChange={event=>set('on',event.target.value as Draft['on'])}>
              <option value="completed">finishes successfully</option><option value="any">finishes, however it ends</option>
            </Select>}</FormField>
        </>}
        {draft.kind==='file_change'&&<>
          <FormField label="Files to watch" required error={shown('glob')} hint="A pattern inside the project. Links are never followed.">{control=>
            <Input {...control} value={draft.glob} placeholder="src/**/*.py" maxLength={200} onChange={event=>set('glob',event.target.value)}/>}</FormField>
          <FormField label="Wait until changes settle for" required error={shown('debounce')} hint="Seconds of quiet before the bot runs, so a burst of edits starts one run.">{control=>
            <Input {...control} type="number" min={5} max={3600} value={draft.debounce} onChange={event=>set('debounce',event.target.value)}/>}</FormField>
        </>}
        {draft.kind==='webhook'&&<p className="manager-help">A webhook starts off. You get its address and secret once, then turn it on when the sender is ready.</p>}
        <FormField label="What the bot does when this happens" required error={shown('prompt')}>{control=>
          <Textarea {...control} value={draft.prompt} onChange={event=>set('prompt',event.target.value)}/>}</FormField>
        <FormField label="Send each result to">{control=>
          <Select {...control} value={draft.report} onChange={event=>set('report',event.target.value)}>
            {reportOptions(bots,bot.id).map(option=><option key={option.value} value={option.value}>{option.label}</option>)}
          </Select>}</FormField>
        {creating&&<>
          <div className="bot-fields">
            <FormField label="Valid for">{control=>
              <Select {...control} value={draft.days} onChange={event=>set('days',event.target.value)}>{authorizationDays.map(value=><option key={value} value={value}>{value} days</option>)}</Select>}</FormField>
            <FormField label="Most runs">{control=><Input {...control} type="number" min={1} max={10000} value={draft.runs} onChange={event=>set('runs',event.target.value)}/>}</FormField>
          </div>
          <div className="form-field">
            <label className="bot-check"><input type="checkbox" checked={draft.consent} aria-invalid={shown('consent')?true:undefined} onChange={event=>set('consent',event.target.checked)}/>
              <span>“{bot.name}” may run on its own when this happens, as me, inside its permissions. Anything it needs approval for waits for me.</span></label>
            <p className="form-field-error" role="alert">{shown('consent')}</p>
          </div>
        </>}
        <div className="bot-dialog-actions">
          <Button type="button" variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button>
          <ActionButton busy={busy} primary type="submit">{verb}</ActionButton>
        </div>
      </form>
    </DialogContent>
  </Dialog>;
}

export default function BotTriggers({bot,bots,mutation,rows,onChanged}:BotTabProps&{rows:BotSchedule[];onChanged:()=>void}){
  const {busy,mutate}=mutation;
  const [dialog,setDialog]=useState<'new'|BotSchedule|null>(null),[reveal,setReveal]=useState<{title:string;url:string;secret:string}|null>(null);
  const [rotating,setRotating]=useState<BotSchedule|null>(null),[archiving,setArchiving]=useState<BotSchedule|null>(null);
  // An archived trigger takes its Archive button with it, so focus goes to the list's heading instead.
  const heading=useRef<HTMLHeadingElement>(null),archived=useRef(false);
  const triggers=rows.filter(row=>row.trigger&&row.trigger.kind!=='time'&&row.state!=='archived');
  const nameOf=(id:string)=>bots.find(row=>row.id===id)?.name||'a deleted bot';
  const toggle=(row:BotSchedule)=>mutate(()=>gql(enableMutation,{c:bot.conversation_id,s:row.id,enabled:!row.enabled,r:row.revision}),row.enabled?'Trigger turned off':'Trigger turned on').then(result=>{if(result!==null)onChanged()});
  async function rotate(row:BotSchedule){
    const result=await mutate(()=>gql<{rotate_webhook_secret:BotSchedule}>(rotateMutation,{c:bot.conversation_id,s:row.id}),'Secret rotated');
    setRotating(null);
    if(result?.rotate_webhook_secret.webhook_secret)setReveal({title:'New webhook secret',url:urlOf(row),secret:result.rotate_webhook_secret.webhook_secret});
    if(result!==null)onChanged();
  }
  async function archive(row:BotSchedule){
    const result=await mutate(()=>gql(archiveMutation,{c:bot.conversation_id,s:row.id,r:row.revision}),'Trigger archived');
    archived.current=result!==null;
    setArchiving(null);
    if(result!==null)onChanged();
  }
  return <section className="bot-triggers" aria-labelledby="bot-triggers-title">
    <div className="schedule-heading">
      <div><h3 id="bot-triggers-title" tabIndex={-1} ref={heading}>Triggers</h3><p>Start this bot when something happens instead of at a set time. A run that needs approval waits for you.</p></div>
      <Button disabled={busy||bot.state!=='active'} onClick={()=>setDialog('new')}>Add trigger</Button>
    </div>
    {triggers.length===0?<Empty>No triggers yet. Add one to start this bot when a message arrives, another bot finishes, files change or a webhook is called.</Empty>
    :<ul className="bot-trigger-list" aria-label="Triggers">
      {triggers.map(row=><li key={row.id}>
        <div className="bot-trigger-text">
          <strong id={'trigger-'+row.id}>{describeTrigger(row.trigger!,nameOf)}</strong>
          <p>{row.prompt}</p>
          {row.trigger?.kind==='webhook'&&row.webhook_path&&<p><code>{urlOf(row)}</code></p>}
          <small>{row.runs_started} of {row.max_runs} runs used{row.grant_expires_at?` · Authorized until ${date(row.grant_expires_at)}`:''}</small>
          {row.reason&&<small className="manager-help">{row.reason}</small>}
        </div>
        <div className="bot-trigger-actions">
          <Badge variant={row.enabled?'default':'secondary'}>{row.enabled?'On':'Off'}</Badge>
          <ActionButton busy={busy} aria-describedby={'trigger-'+row.id} onClick={()=>void toggle(row)}>{row.enabled?'Turn off':'Turn on'}</ActionButton>
          <Button variant="ghost" disabled={busy} aria-describedby={'trigger-'+row.id} onClick={()=>setDialog(row)}>Edit</Button>
          {row.trigger?.kind==='webhook'&&<Button variant="ghost" disabled={busy} aria-describedby={'trigger-'+row.id} onClick={()=>setRotating(row)}>Rotate secret</Button>}
          <Button variant="ghost" disabled={busy} aria-describedby={'trigger-'+row.id} onClick={()=>setArchiving(row)}>Archive trigger</Button>
        </div>
      </li>)}
    </ul>}
    {dialog&&<TriggerDialog key={dialog==='new'?'new':dialog.id} bot={bot} bots={bots} mutation={mutation} projects={[]} row={dialog==='new'?null:dialog} onClose={()=>setDialog(null)}
      onSaved={created=>{
        setDialog(null);onChanged();
        if(created?.webhook_secret)setReveal({title:'Webhook created',url:urlOf(created),secret:created.webhook_secret});
      }}/>}
    {reveal&&<SecretReveal {...reveal} onDone={()=>setReveal(null)}/>}
    <Dialog open={!!rotating} onOpenChange={next=>{if(!next)setRotating(null)}}>
      <DialogContent aria-describedby="bot-rotate-description">
        <DialogTitle>Rotate secret</DialogTitle>
        <DialogDescription id="bot-rotate-description">The old secret stops working at once. Update the sender with the new one, which is shown once.</DialogDescription>
        <div className="bot-dialog-actions">
          <Button variant="ghost" onClick={()=>setRotating(null)}>Keep secret</Button>
          <ActionButton busy={busy} primary onClick={()=>rotating&&void rotate(rotating)}>Rotate secret</ActionButton>
        </div>
      </DialogContent>
    </Dialog>
    <Dialog open={!!archiving} onOpenChange={next=>{if(!next)setArchiving(null)}}>
      <DialogContent aria-describedby="bot-archive-trigger-description" onCloseAutoFocus={event=>{if(archived.current){archived.current=false;event.preventDefault();heading.current?.focus()}}}>
        <DialogTitle>Archive trigger</DialogTitle>
        <DialogDescription id="bot-archive-trigger-description">The bot stops starting from this trigger. Runs already in progress finish. This cannot be undone; add a new trigger to bring it back.</DialogDescription>
        <div className="bot-dialog-actions">
          <Button variant="ghost" onClick={()=>setArchiving(null)}>Keep trigger</Button>
          <ActionButton busy={busy} primary onClick={()=>archiving&&void archive(archiving)}>Archive trigger</ActionButton>
        </div>
      </DialogContent>
    </Dialog>
  </section>;
}
