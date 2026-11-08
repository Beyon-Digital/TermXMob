import {useEffect,useState} from 'react';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Select} from '../components/ui/select';
import {BOT_SCHEDULE_FIELDS,authorizationDays,type BotSchedule} from '../lib/bot-schedules';
import {botTools} from '../lib/bots';
import {usePolling} from '../lib/use-polling';
import {loadBotSkills,type BotTabProps} from './bot-api';
import {ActionButton} from './manager-ui';
import BotTriggers from './BotTriggers';
import ScheduleManager from './ScheduleManager';
import {gql} from './use-manager';

const listQuery=`query($c:String!){schedules(conversation_id:$c){ ${BOT_SCHEDULE_FIELDS} }}`;
const reauthorizeMutation=`mutation($c:String!,$days:Int!){reauthorize_schedules(conversation_id:$c,grant_days:$days){ ${BOT_SCHEDULE_FIELDS} }}`;
const count=(n:number)=>n===1?'1 schedule':`${n} schedules`;

/**
 * The banner explains why schedules are off and offers the one fix. Re-authorizing is a grant of unattended
 * authority, so it states what is granted and for how long before it does anything.
 */
function ReauthorizeBanner({bot,rows,onDone}:{bot:BotTabProps['bot'];rows:BotSchedule[];onDone:(rows:BotSchedule[])=>void}){
  const [open,setOpen]=useState(false),[days,setDays]=useState(30),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const parked=rows.filter(row=>row.needs_reauthorization);
  const waiting=bot.state==='paused'?rows.filter(row=>row.paused_by_bot):[];
  async function confirm(){
    setBusy(true);setError('');
    try{
      const result=await gql<{reauthorize_schedules:BotSchedule[]}>(reauthorizeMutation,{c:bot.conversation_id,days});
      onDone(result.reauthorize_schedules);setOpen(false);
    }catch(reason){setError(reason instanceof Error?reason.message:String(reason))}
    finally{setBusy(false)}
  }
  return <>
    {waiting.length>0&&<p className="manager-help" role="status">{count(waiting.length)} will turn back on when you resume this bot.</p>}
    {parked.length>0&&<section className="bot-banner" aria-labelledby="bot-reauthorize-title">
      <h4 id="bot-reauthorize-title">{parked.length===1?'1 schedule is paused until you re-authorize it':`${parked.length} schedules are paused until you re-authorize them`}</h4>
      <p>{parked[0].reason||"This bot's prompt or permissions changed after you authorized its schedules."}</p>
      <p>A bot cannot widen its own authority. Re-authorize to let {parked.length===1?'it':'them'} run again under the bot's current prompt and permissions.</p>
      {parked.some(row=>row.computer_permission)&&<p>Computer control is not carried over. Set it again on each schedule after you re-authorize.</p>}
      <div className="manager-item-actions">
        <Button disabled={bot.state!=='active'} onClick={()=>{setError('');setOpen(true)}}>Re-authorize schedules</Button>
        {bot.state!=='active'&&<small>Resume the bot first.</small>}
      </div>
    </section>}
    <Dialog open={open} onOpenChange={next=>{if(!busy)setOpen(next)}}>
      <DialogContent className="bot-dialog" aria-describedby="bot-reauthorize-description">
        <DialogTitle>Re-authorize schedules</DialogTitle>
        <DialogDescription id="bot-reauthorize-description">
          “{bot.name}” will run {count(parked.length)} on its own as you, with the prompt and permissions it has now. Anything it needs approval for still waits for you.
        </DialogDescription>
        <ul className="bot-changes">{parked.map(row=><li key={row.id}>{row.prompt.length>120?row.prompt.slice(0,120)+'…':row.prompt}</li>)}</ul>
        <FormField label="Valid for">{control=><Select {...control} value={String(days)} onChange={event=>setDays(Number(event.target.value))}>
          {authorizationDays.map(value=><option key={value} value={value}>{value} days</option>)}</Select>}</FormField>
        {error&&<p role="alert" className="manager-error">{error}</p>}
        <div className="bot-dialog-actions">
          <Button variant="ghost" disabled={busy} onClick={()=>setOpen(false)}>Cancel</Button>
          <ActionButton busy={busy} primary onClick={()=>void confirm()}>Re-authorize schedules</ActionButton>
        </div>
      </DialogContent>
    </Dialog>
  </>;
}

/** Apps named by the skills this bot has switched on. They are suggestions for a schedule's computer permission, never a grant. */
function useSkillApps(bot:BotTabProps['bot']){
  const [state,setState]=useState<{apps:string[];failed:boolean}>({apps:[],failed:false});
  useEffect(()=>{
    let live=true;
    loadBotSkills(bot).then(({session,extensions})=>{
      const chosen=session?.extension_ids||[];
      const found=extensions.filter(row=>chosen.includes(row.id)).flatMap(row=>row.computer_apps||[]);
      if(live)setState({apps:[...new Set(found)],failed:false});
    }).catch(()=>{if(live)setState({apps:[],failed:true})});
    return()=>{live=false};
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[bot.conversation_id,bot.project_id]);
  return state;
}

export default function BotSchedules(props:BotTabProps&{onTab?:(tab:'permissions')=>void}){
  const {bot,onTab}=props;
  const [rows,setRows]=useState<BotSchedule[]>([]),[version,setVersion]=useState(0),[error,setError]=useState('');
  const {apps:suggestions,failed:suggestionsFailed}=useSkillApps(bot);
  usePolling(()=>gql<{schedules:BotSchedule[]}>(listQuery,{c:bot.conversation_id}),{
    interval:10000,resetKey:version,
    onResult:value=>{setRows(value.schedules??[]);setError('')},
    onError:reason=>setError(reason instanceof Error?reason.message:String(reason))});
  // Default deny: a schedule may drive apps only when the bot's ceiling lists the tool, does not block computer control, and the bot uses the tool.
  const tools=botTools(bot);
  const allowed=bot.ceiling.tools.includes('computer')&&!bot.ceiling.deny_capabilities.includes('computer.control')&&tools.includes('computer');
  const blocked=<p className="manager-help">This bot cannot control the computer, so its schedules cannot either. To change that, allow “Control this computer” and stop blocking computer control under <button type="button" className="bot-link" onClick={()=>onTab?.('permissions')}>Permissions</button>.</p>;
  return <>
    {error&&<p role="alert" className="manager-error">{error}</p>}
    <ReauthorizeBanner bot={bot} rows={rows} onDone={next=>{setRows(rows.map(row=>next.find(item=>item.id===row.id)||row));setVersion(value=>value+1);
      // The bot list carries the "needs you" count shown in the header, so it is read again now rather than at the next poll.
      props.mutation.reload()}}/>
    <ScheduleManager projectId={bot.project_id??null} sessionId={bot.conversation_id} conversationId={bot.conversation_id} version={version} computer={{allowed,suggestions,suggestionsFailed,blocked}}/>
    <BotTriggers {...props} rows={rows} onChanged={()=>setVersion(value=>value+1)}/>
  </>;
}
