import {useCallback,useEffect,useRef,useState} from 'react';
import {Badge} from '../components/ui/badge';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Textarea} from '../components/ui/textarea';
import {BOT_FIELDS,TEMPLATE_FIELDS,stateLabel,taskStatusLabel,toolLabel,type Bot,type BotTemplate,type ProjectChoice} from '../lib/bots';
import type {HostIdentity} from '../lib/host-types';
import {usePolling} from '../lib/use-polling';
import {NEEDS_YOU_FIELDS,type NeedsYouItem} from '../lib/bot-schedules';
import BotDetail from './BotDetail';
import NeedsYouInbox,{type BotTab} from './NeedsYouInbox';
import {ActionButton,Empty,ManagerPage,date,rowMatcher} from './manager-ui';
import {gql,useManagerLoad,useManagerMutation} from './use-manager';
import './bots.css';

interface BotsData { bots: Bot[]; bot_templates: BotTemplate[]; projects: ProjectChoice[]; needs_you?: NeedsYouItem[] }
const listQuery = `query { bots { ${BOT_FIELDS} } bot_templates { ${TEMPLATE_FIELDS} } projects { id name path } needs_you { ${NEEDS_YOU_FIELDS} } }`;
const refreshQuery = `query { bots { ${BOT_FIELDS} } needs_you { ${NEEDS_YOU_FIELDS} } }`;

/** The selected bot lives in `?bot=` so a reload or a shared link opens the same bot. */
function useBotParam() {
  const read = () => new URLSearchParams(location.search).get('bot');
  const [id, setId] = useState(read);
  useEffect(() => {
    const sync = () => setId(read());
    addEventListener('popstate', sync);
    return () => removeEventListener('popstate', sync);
  }, []);
  const select = useCallback((next: string | null) => {
    const url = new URL(location.href);
    if (next) url.searchParams.set('bot', next); else url.searchParams.delete('bot');
    history.pushState(history.state, '', url);
    setId(next);
  }, []);
  return [id, select] as const;
}

const CUSTOM = 'custom';

function NewBotDialog({open,onClose,templates,projects,defaultProject,busy,onCreate}:{
  open:boolean;onClose:()=>void;templates:BotTemplate[];projects:ProjectChoice[];defaultProject:string|null;busy:boolean;
  onCreate:(input:Record<string,unknown>)=>Promise<unknown>;
}){
  const [choice,setChoice]=useState(templates[0]?.id||CUSTOM),[name,setName]=useState(''),[instructions,setInstructions]=useState(''),[project,setProject]=useState(defaultProject||''),[tried,setTried]=useState(false);
  const template=templates.find(row=>row.id===choice)||null;
  const nameError=tried&&!name.trim()?'Give the bot a name.':'';
  const projectError=tried&&template?.requires_project&&!project?'This template watches a project. Choose one.':'';
  const instructionsError=tried&&!template&&!instructions.trim()?'Say what the bot should do each time it runs.':'';
  async function submit(){
    setTried(true);
    if(!name.trim()||(template?.requires_project&&!project)||(!template&&!instructions.trim()))return;
    const result=await onCreate({name:name.trim(),template:template?.id??null,instructions:template?null:instructions.trim(),project_id:project||null});
    if(result!==null)onClose();
  }
  return <Dialog open={open} onOpenChange={next=>{if(!next)onClose()}}>
    <DialogContent className="bot-dialog" aria-describedby="new-bot-description">
      <DialogTitle>New bot</DialogTitle>
      <DialogDescription id="new-bot-description">A bot works in the background as you, inside limits you set. It starts read-only: it cannot run commands, use the network or publish.</DialogDescription>
      <form className="manager-form" aria-label="New bot" noValidate onSubmit={event=>{event.preventDefault();void submit()}}>
        <fieldset className="bot-templates">
          <legend>Start from</legend>
          {[...templates,{id:CUSTOM,name:'Custom',description:'Write your own instructions.',requires_project:false,tools:[],instructions:''}].map(row=>
            <label key={row.id} className="bot-template">
              <input type="radio" name="template" value={row.id} checked={choice===row.id} onChange={()=>setChoice(row.id)}/>
              <span><strong>{row.name}</strong><small>{row.description}</small></span>
            </label>)}
        </fieldset>
        <FormField label="Name" required error={nameError}>{control=><Input {...control} value={name} maxLength={80} onChange={event=>setName(event.target.value)}/>}</FormField>
        <FormField label="Project" hint={template?.requires_project?'Required for this template.':'Optional. Without one the bot works in its own scratch folder.'} error={projectError}>{control=>
          <Select {...control} value={project} onChange={event=>setProject(event.target.value)}>
            <option value="">No project (scratch folder)</option>
            {projects.map(row=><option key={row.id} value={row.id}>{row.name}</option>)}
          </Select>}</FormField>
        {template?<p className="manager-help">Can use: {template.tools.map(toolLabel).join(', ')}.</p>
          :<FormField label="Instructions" required hint="You can change these later. Tools stay read-only until you allow more." error={instructionsError}>{control=><Textarea {...control} value={instructions} onChange={event=>setInstructions(event.target.value)}/>}</FormField>}
        <div className="bot-dialog-actions">
          <Button type="button" variant="ghost" onClick={onClose}>Cancel</Button>
          <ActionButton busy={busy} primary type="submit">Create bot</ActionButton>
        </div>
      </form>
    </DialogContent>
  </Dialog>;
}

function needsYou(count:number){return count===1?'1 needs you':`${count} need you`}

export default function BotsSection({me,projectId}:{admin?:boolean;me:HostIdentity;projectId:string|null}){
  const {data,loading,error:loadError,reload}=useManagerLoad(()=>gql<BotsData>(listQuery));
  const mutation=useManagerMutation(reload);
  const {busy,error,notice,mutate}=mutation;
  const [selected,select]=useBotParam(),[creating,setCreating]=useState(false),[search,setSearch]=useState('');
  const [fresh,setFresh]=useState<{bots:Bot[];needs_you?:NeedsYouItem[]}|null>(null),[target,setTarget]=useState<{id:string|null;tab:BotTab}>({id:null,tab:'overview'});
  useEffect(()=>setFresh(null),[data]);
  // Counts like "needs you" change while the screen is open, so the list refreshes itself quietly.
  usePolling(()=>gql<{bots:Bot[];needs_you?:NeedsYouItem[]}>(refreshQuery),{interval:15000,immediate:false,enabled:!!data,onResult:value=>setFresh(value)});
  const bots=fresh?.bots??data?.bots??[],inbox=(fresh?fresh.needs_you:data?.needs_you)||[],templates=data?.bot_templates||[],projects=data?.projects||[];
  const matches=rowMatcher(search,me.session_id);
  const current=selected?bots.find(row=>row.id===selected)||null:null;
  // A bot opens on its Overview unless the inbox asked for another tab. The request belongs to that bot, so any other way
  // of changing the selection (browser back, a deleted bot) falls back to Overview.
  const tab=target.id===selected?target.tab:'overview';
  const open=(id:string|null,next:BotTab='overview')=>{if(id)opened.current=id;setTarget({id,tab:next});select(id)};
  // Going back to the list returns to the bot that was open; if it is gone, to the New bot button.
  const opened=useRef<string|null>(null),newBot=useRef<HTMLButtonElement>(null);
  useEffect(()=>{
    if(selected||!opened.current||!data)return;
    const id=opened.current;opened.current=null;
    const row=Array.from(document.querySelectorAll<HTMLElement>('[data-bot-row]')).find(node=>node.dataset.botRow===id);
    (row||newBot.current)?.focus();
  },[selected,data]);
  const create=(input:Record<string,unknown>)=>mutate(async()=>{
    const result=await gql<{create_bot:Bot}>(`mutation($input:BotInput!){create_bot(input:$input){ ${BOT_FIELDS} }}`,{input});
    open(result.create_bot.id);
    return result;
  },'Bot created');
  const header=<div className="bot-toolbar"><Button ref={newBot} onClick={()=>setCreating(true)}>New bot</Button></div>;
  return <ManagerPage section="bots" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={current?undefined:{label:'Bots',value:search,onChange:setSearch,count:bots.length}}>
    <>
      {current?<BotDetail key={current.id+tab} bot={current} bots={bots} mutation={mutation} projects={projects} inbox={inbox.filter(item=>item.bot_id===current.id)} initialTab={tab} onBack={()=>open(null)} onGone={()=>open(null)}/>
      :selected?(loading?<Empty>Loading bot…</Empty>:<Empty>This bot no longer exists. <Button variant="ghost" onClick={()=>open(null)}>Show all bots</Button></Empty>)
      :<>
        {header}
        <NeedsYouInbox items={inbox} bots={bots} mutation={mutation} onOpen={open}/>
        {data&&!bots.length&&<Empty>No bots yet. Create one from a template to give recurring work a home that runs in the background, then check what it did here.</Empty>}
        {bots.length>0&&<ul className="bot-list" aria-label="Bots">
          {bots.filter(matches).map(bot=><li key={bot.id}>
            <button type="button" className="bot-row" data-bot-row={bot.id} onClick={()=>open(bot.id)}>
              <span className="bot-row-main"><strong>{bot.name}</strong><small>{bot.description||'No description'}</small></span>
              <span className="bot-row-meta">
                {bot.needs_you>0&&<Badge variant="outline">{needsYou(bot.needs_you)}</Badge>}
                <Badge variant={bot.state==='active'?'default':'secondary'}>{stateLabel[bot.state]}</Badge>
                <small>{bot.last_task?`${taskStatusLabel(bot.last_task.status)} · ${date(bot.last_task.updated_at)}`:'No runs yet'}</small>
              </span>
            </button>
          </li>)}
        </ul>}
        {search&&bots.length>0&&!bots.some(matches)&&<Empty>No bot matches “{search}”. Clear the search to see all bots.</Empty>}
      </>}
      <NewBotDialog key={String(creating)} open={creating} onClose={()=>setCreating(false)} templates={templates} projects={projects} defaultProject={projectId} busy={busy} onCreate={create}/>
    </>
  </ManagerPage>;
}
