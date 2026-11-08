import {useRef,useState,type KeyboardEvent} from 'react';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {SettingsRow,SettingsSection} from '../components/ui/settings-section';
import {Textarea} from '../components/ui/textarea';
import {stateLabel,taskStatusLabel,type Bot,type ProjectChoice} from '../lib/bots';
import {reportOptions} from '../lib/bot-messages';
import BotMessages from './BotMessages';
import BotPermissions from './BotPermissions';
import BotSchedules from './BotSchedules';
import NeedsYouInbox,{type BotTab} from './NeedsYouInbox';
import {BotActivity,BotSkills,BotTasks} from './BotWork';
import {ActionButton,date} from './manager-ui';
import {useDirtyForm} from './settings-forms';
import {gql} from './use-manager';
import {patchBot,type BotTabProps} from './bot-api';
import type {NeedsYouItem} from '../lib/bot-schedules';

function Overview({bot,bots,mutation,projects,onGone,inbox,onTab}:BotTabProps&{onGone:()=>void;inbox:NeedsYouItem[];onTab:(tab:BotTab)=>void}){
  const {busy,mutate}=mutation;
  const [confirming,setConfirming]=useState(false),[reportTo,setReportTo]=useState('owner');
  const paused=bot.state==='paused';
  const project=projects.find(row=>row.id===bot.project_id);
  const setState=(state:'active'|'paused')=>mutate(()=>gql('mutation($id:String!,$state:String!,$revision:Int){set_bot_state(bot_id:$id,state:$state,revision:$revision){id}}',{id:bot.id,state,revision:bot.revision}),state==='paused'?'Bot paused':'Bot resumed');
  const run=()=>mutate(()=>gql('mutation($id:String!,$report_to:String){bot_run_now(bot_id:$id,report_to:$report_to){task_id}}',{id:bot.id,report_to:reportTo}),'Run started. Its result appears under Tasks.');
  const destinations=[{value:'owner',label:'You'},...reportOptions(bots,bot.id).filter(option=>option.value!=='owner')];
  return <>
    <NeedsYouInbox items={inbox} bots={bots} mutation={mutation} showBot={false} onOpen={(_id,tab)=>onTab(tab)}/>
    <SettingsSection level={3} title="Status" description="Where this bot stands and what it is doing.">
      <SettingsRow label="State" description={paused?'Paused bots do not start new runs. Their schedules turn off and come back when you resume.':'Pausing stops new runs. A run already in progress finishes.'}>
        <Badge variant={paused?'secondary':'default'}>{stateLabel[bot.state]}</Badge>
        <ActionButton busy={busy} onClick={()=>void setState(paused?'active':'paused')}>{paused?'Resume bot':'Pause bot'}</ActionButton>
      </SettingsRow>
      <SettingsRow label="Run now" description="Runs its standing instructions once. The result appears under Tasks and is posted where you choose.">
        <Select aria-label="Send the result to" value={reportTo} onChange={event=>setReportTo(event.target.value)}>
          {destinations.map(option=><option key={option.value} value={option.value}>{option.label}</option>)}
        </Select>
        <ActionButton busy={busy} primary disabled={paused||bot.violations.length>0} onClick={()=>void run()}>Run now</ActionButton>
      </SettingsRow>
      <SettingsRow label="Last run" description="Its most recent task.">
        <span>{bot.last_task?`${taskStatusLabel(bot.last_task.status)} · ${date(bot.last_task.updated_at)}`:'No runs yet'}</span>
      </SettingsRow>
      <SettingsRow label="Works in" description="The folder it reads and, if allowed, changes.">
        <span>{bot.project_id?project?.name||'A registered project':'Its own scratch folder'}</span>
      </SettingsRow>
      <SettingsRow label="Model" description="Chosen when it runs unless set here."><span>{bot.model||'Default model'}</span></SettingsRow>
    </SettingsSection>
    <SettingsSection level={3} title="Delete" description="Remove this bot for good.">
      <SettingsRow label="Delete bot" description="Stops it, revokes its schedules and permissions and removes its agent file. Its conversation history is kept.">
        <ActionButton busy={busy} onClick={()=>setConfirming(true)}>Delete bot</ActionButton>
      </SettingsRow>
    </SettingsSection>
    <Dialog open={confirming} onOpenChange={setConfirming}>
      <DialogContent aria-describedby="delete-bot-description">
        <DialogTitle>Delete bot</DialogTitle>
        <DialogDescription id="delete-bot-description">“{bot.name}” stops now and cannot be resumed. Its schedules and remembered approvals are revoked.</DialogDescription>
        <div className="bot-dialog-actions">
          <Button variant="ghost" onClick={()=>setConfirming(false)}>Keep bot</Button>
          <ActionButton busy={busy} primary onClick={()=>void mutate(()=>gql('mutation($id:String!){delete_bot(bot_id:$id){id}}',{id:bot.id}),'Bot deleted').then(result=>{setConfirming(false);if(result!==null)onGone()})}>Delete bot</ActionButton>
        </div>
      </DialogContent>
    </Dialog>
  </>;
}

function Prompt({bot,mutation}:BotTabProps){
  const {formProps,markClean}=useDirtyForm('bot-prompt');
  const {busy,mutate}=mutation;
  const [name,setName]=useState(bot.name),[description,setDescription]=useState(bot.description),[model,setModel]=useState(bot.model||''),[instructions,setInstructions]=useState(bot.instructions);
  const [tried,setTried]=useState(false);
  const nameError=tried&&!name.trim()?'The bot needs a name.':'',instructionsError=tried&&!instructions.trim()?'Say what the bot should do each time it runs.':'';
  async function submit(){
    setTried(true);
    if(!name.trim()||!instructions.trim())return;
    const result=await mutate(()=>patchBot(bot.id,{revision:bot.revision,name:name.trim(),description:description.trim(),model:model.trim(),instructions:instructions.trim()}),'Prompt saved');
    if(result!==null)markClean();
  }
  return <form {...formProps} noValidate className="manager-form" aria-label="Bot prompt" onSubmit={event=>{event.preventDefault();void submit()}}>
    <p className="manager-help">Changes apply from the next run. Any schedule for this bot has to be re-authorized after you change its prompt.</p>
    <FormField label="Name" required error={nameError}>{control=><Input {...control} value={name} maxLength={80} onChange={event=>setName(event.target.value)}/>}</FormField>
    <FormField label="Description" hint="Shown in the bot list.">{control=><Input {...control} value={description} maxLength={300} onChange={event=>setDescription(event.target.value)}/>}</FormField>
    <FormField label="Model" hint="Leave empty to use your default model.">{control=><Input {...control} value={model} maxLength={200} onChange={event=>setModel(event.target.value)}/>}</FormField>
    <FormField label="Instructions" required error={instructionsError}>{control=><Textarea {...control} className="bot-instructions" value={instructions} onChange={event=>setInstructions(event.target.value)}/>}</FormField>
    <div className="manager-item-actions"><ActionButton busy={busy} primary type="submit">Save prompt</ActionButton></div>
  </form>;
}

const tabs=[
  {id:'overview',label:'Overview'},{id:'prompt',label:'Prompt'},{id:'permissions',label:'Permissions'},
  {id:'schedules',label:'Schedules & triggers'},{id:'messages',label:'Messages'},{id:'skills',label:'Skills'},
  {id:'tasks',label:'Tasks'},{id:'activity',label:'Activity'},
] as const;
type TabId=typeof tabs[number]['id'];

export default function BotDetail({bot,bots,mutation,projects,inbox,initialTab='overview',onBack,onGone}:BotTabProps&{inbox:NeedsYouItem[];initialTab?:TabId;onBack:()=>void;onGone:()=>void}){
  const [tab,setTab]=useState<TabId>(initialTab);
  const list=useRef<HTMLDivElement>(null);
  const props={bot,bots,mutation,projects};
  function move(event:KeyboardEvent,index:number){
    const next=event.key==='ArrowRight'?(index+1)%tabs.length:event.key==='ArrowLeft'?(index+tabs.length-1)%tabs.length:event.key==='Home'?0:event.key==='End'?tabs.length-1:-1;
    if(next<0)return;
    event.preventDefault();setTab(tabs[next].id);
    list.current?.querySelector<HTMLElement>(`#bot-tab-${tabs[next].id}`)?.focus();
  }
  return <div className="bot-detail">
    <div className="bot-detail-head">
      <Button variant="ghost" onClick={onBack}>All bots</Button>
      <h3>{bot.name}</h3>
      <Badge variant={bot.state==='active'?'default':'secondary'}>{stateLabel[bot.state]}</Badge>
      {bot.needs_you>0&&<Badge variant="outline">{bot.needs_you===1?'1 needs you':`${bot.needs_you} need you`}</Badge>}
    </div>
    <div className="bot-tabs" role="tablist" aria-label="Bot sections" ref={list}>
      {tabs.map((item,index)=><button key={item.id} type="button" role="tab" id={`bot-tab-${item.id}`} aria-controls={`bot-panel-${item.id}`} aria-selected={tab===item.id} tabIndex={tab===item.id?0:-1}
        className="bot-tab" onClick={()=>setTab(item.id)} onKeyDown={event=>move(event,index)}>{item.label}</button>)}
    </div>
    <div role="tabpanel" id={`bot-panel-${tab}`} aria-labelledby={`bot-tab-${tab}`} className="bot-panel">
      {tab==='overview'?<Overview {...props} inbox={inbox} onTab={setTab} onGone={onGone}/>
      :tab==='prompt'?<Prompt {...props}/>
      :tab==='permissions'?<BotPermissions {...props}/>
      :tab==='schedules'?<BotSchedules {...props}/>
      :tab==='messages'?<BotMessages {...props}/>
      :tab==='skills'?<BotSkills {...props}/>
      :tab==='tasks'?<BotTasks {...props}/>
      :<BotActivity {...props}/>}
    </div>
  </div>;
}
