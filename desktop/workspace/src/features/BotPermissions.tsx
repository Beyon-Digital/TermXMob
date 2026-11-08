import {useState} from 'react';
import {Button} from '../components/ui/button';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
import {FormField} from '../components/ui/form-field';
import {Select} from '../components/ui/select';
import {SettingsRow,SettingsSection} from '../components/ui/settings-section';
import {BOT_FIELDS,approvalModes,approvalRank,denied,denyGroups,fallbackToolIds,sandboxProfiles,sandboxRank,toolGroups,toolLabel,type BotCeiling} from '../lib/bots';
import {patchBot,useHostToolNames,type BotTabProps} from './bot-api';
import {ActionButton,Empty} from './manager-ui';
import {useDirtyForm} from './settings-forms';
import {gql} from './use-manager';

const known=new Set(fallbackToolIds);
const toggle=(list:string[],id:string,on:boolean)=>on?[...new Set([...list,id])]:list.filter(item=>item!==id);

/** Checkboxes for `allowed` tools, grouped by what they let a bot do. Tools the screen has no wording for appear under their id. */
function ToolChoices({allowed,selected,onChange,legend}:{allowed:string[];selected:string[];onChange:(next:string[])=>void;legend:string}){
  const groups=[...toolGroups.map(group=>({label:group.label,tools:group.tools.filter(tool=>allowed.includes(tool.id))})),
    {label:'Other',tools:allowed.filter(id=>!known.has(id)).map(id=>({id,label:id}))}].filter(group=>group.tools.length);
  return <fieldset className="bot-tools"><legend>{legend}</legend>
    {groups.map(group=><div key={group.label} role="group" aria-label={group.label} className="bot-tool-group">
      <h4>{group.label}</h4>
      {group.tools.map(tool=><label key={tool.id} className="bot-check"><input type="checkbox" checked={selected.includes(tool.id)} onChange={event=>onChange(toggle(selected,tool.id,event.target.checked))}/><span>{tool.label}</span></label>)}
    </div>)}
  </fieldset>;
}

function Permissions({bot,mutation}:BotTabProps){
  const {formProps,markClean}=useDirtyForm('bot-permissions');
  const {busy,mutate}=mutation;
  const effective=bot.effective;
  const [tools,setTools]=useState(effective?.tools||bot.tools),[approval,setApproval]=useState(effective?.approval_mode||bot.ceiling.approval_mode),[sandbox,setSandbox]=useState(effective?.sandbox_profile||bot.ceiling.sandbox_profile);
  const [tried,setTried]=useState(false);
  const toolsError=tried&&!tools.length?'Choose at least one tool.':'';
  async function submit(){
    setTried(true);
    if(!tools.length)return;
    const result=await mutate(()=>patchBot(bot.id,{revision:bot.revision,tools,approval_mode:approval,sandbox_profile:sandbox}),'Permissions saved');
    if(result!==null)markClean();
  }
  return <form {...formProps} className="manager-form" aria-label="Bot permissions" onSubmit={event=>{event.preventDefault();void submit()}}>
    <p className="manager-help">Only what the ceiling allows is listed. Saving changes the bot&rsquo;s configuration, so any schedule has to be re-authorized.</p>
    <ToolChoices legend="What this bot can use" allowed={bot.ceiling.tools} selected={tools} onChange={setTools}/>
    {toolsError&&<p className="form-field-error" role="alert">{toolsError}</p>}
    <FormField label="Approvals" hint="How often it asks you before a risky action.">{control=>
      <Select {...control} value={approval} onChange={event=>setApproval(event.target.value)}>
        {approvalModes.filter(row=>approvalRank(row.id)<=approvalRank(bot.ceiling.approval_mode)).map(row=><option key={row.id} value={row.id}>{row.label}</option>)}
      </Select>}</FormField>
    <FormField label="Sandbox" hint="How far its commands can reach.">{control=>
      <Select {...control} value={sandbox} onChange={event=>setSandbox(event.target.value)}>
        {sandboxProfiles.filter(row=>sandboxRank(row.id)<=sandboxRank(bot.ceiling.sandbox_profile)).map(row=><option key={row.id} value={row.id}>{row.label}</option>)}
      </Select>}</FormField>
    <div className="manager-item-actions"><ActionButton busy={busy} primary type="submit">Save permissions</ActionButton></div>
  </form>;
}

function describe(before:BotCeiling,after:BotCeiling){
  const lines:string[]=[];
  const added=after.tools.filter(id=>!before.tools.includes(id)),removed=before.tools.filter(id=>!after.tools.includes(id));
  if(added.length)lines.push('Allows: '+added.map(toolLabel).join(', '));
  if(removed.length)lines.push('No longer allows: '+removed.map(toolLabel).join(', '));
  for(const group of denyGroups){
    const was=denied(before,group),now=denied(after,group);
    if(was&&!now)lines.push('Allows '+group.label.toLowerCase());
    if(!was&&now)lines.push('Blocks '+group.label.toLowerCase());
  }
  if(before.approval_mode!==after.approval_mode)lines.push('Approvals: '+(approvalModes.find(row=>row.id===after.approval_mode)?.label||after.approval_mode));
  if(before.sandbox_profile!==after.sandbox_profile)lines.push('Sandbox: '+(sandboxProfiles.find(row=>row.id===after.sandbox_profile)?.label||after.sandbox_profile));
  return lines;
}

function Ceiling({bot,mutation}:BotTabProps){
  const {formProps,markClean}=useDirtyForm('bot-ceiling');
  const {busy,mutate}=mutation;
  const [tools,setTools]=useState(bot.ceiling.tools),[approval,setApproval]=useState(bot.ceiling.approval_mode),[sandbox,setSandbox]=useState(bot.ceiling.sandbox_profile),[deny,setDeny]=useState(bot.ceiling.deny_capabilities);
  const [pending,setPending]=useState<BotCeiling|null>(null),[tried,setTried]=useState(false);
  const hostTools=useHostToolNames();
  // Everything the host has, plus anything this bot's ceiling already names, so a ceiling is never shown with a tool missing.
  const everything=[...new Set([...hostTools,...bot.ceiling.tools])];
  const toolsError=tried&&!tools.length?'Choose at least one tool.':'';
  function review(){
    setTried(true);
    if(!tools.length)return;
    setPending({tools,approval_mode:approval,sandbox_profile:sandbox,deny_capabilities:deny});
  }
  async function save(){
    if(!pending)return;
    const result=await mutate(()=>gql(`mutation($id:String!,$revision:Int!,$ceiling:BotCeilingInput!){set_bot_ceiling(bot_id:$id,revision:$revision,ceiling:$ceiling){ ${BOT_FIELDS} }}`,
      {id:bot.id,revision:bot.revision,ceiling:pending}),'Ceiling saved');
    setPending(null);
    if(result!==null)markClean();
  }
  const changes=pending?describe(bot.ceiling,pending):[];
  return <>
    <form {...formProps} className="manager-form" aria-label="Bot ceiling" onSubmit={event=>{event.preventDefault();review()}}>
      <ToolChoices legend="Tools it may ever use" allowed={everything} selected={tools} onChange={setTools}/>
      {toolsError&&<p className="form-field-error" role="alert">{toolsError}</p>}
      <fieldset className="bot-tools"><legend>Block these completely</legend>
        {denyGroups.map(group=><label key={group.id} className="bot-check"><input type="checkbox" checked={group.capabilities.every(name=>deny.includes(name))}
          onChange={event=>setDeny(current=>event.target.checked?[...new Set([...current,...group.capabilities])]:current.filter(name=>!group.capabilities.includes(name)))}/>
          <span>{group.label}<small>{group.description}</small></span></label>)}
      </fieldset>
      <FormField label="Most it may skip approvals">{control=>
        <Select {...control} value={approval} onChange={event=>setApproval(event.target.value)}>{approvalModes.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</Select>}</FormField>
      <FormField label="Furthest its commands may reach">{control=>
        <Select {...control} value={sandbox} onChange={event=>setSandbox(event.target.value)}>{sandboxProfiles.map(row=><option key={row.id} value={row.id}>{row.label}</option>)}</Select>}</FormField>
      <div className="manager-item-actions"><ActionButton busy={busy} primary type="submit">Save ceiling</ActionButton></div>
    </form>
    <Dialog open={!!pending} onOpenChange={open=>{if(!open)setPending(null)}}>
      <DialogContent aria-describedby="ceiling-description">
        <DialogTitle>Save ceiling</DialogTitle>
        <DialogDescription id="ceiling-description">{changes.length?'This changes what the bot can do without asking you:':'Nothing changes.'}</DialogDescription>
        {changes.length>0&&<ul className="bot-changes">{changes.map(line=><li key={line}>{line}</li>)}</ul>}
        <div className="bot-dialog-actions">
          <Button variant="ghost" onClick={()=>setPending(null)}>Cancel</Button>
          <ActionButton busy={busy} primary onClick={()=>void save()}>Save ceiling</ActionButton>
        </div>
      </DialogContent>
    </Dialog>
  </>;
}

function Peers({bot,bots,mutation}:BotTabProps){
  const {formProps,markClean}=useDirtyForm('bot-peers');
  const {busy,mutate}=mutation;
  const [peers,setPeers]=useState(bot.peers);
  const others=bots.filter(row=>row.id!==bot.id&&row.state!=='archived');
  async function submit(){
    const result=await mutate(()=>patchBot(bot.id,{revision:bot.revision,peers}),'Peers saved');
    if(result!==null)markClean();
  }
  if(!others.length)return <Empty>No other bots yet. Create another bot to let this one name it as a peer.</Empty>;
  return <form {...formProps} className="manager-form" aria-label="Bot peers" onSubmit={event=>{event.preventDefault();void submit()}}>
    <fieldset className="bot-tools"><legend>Bots it may message</legend>
      {others.map(row=><label key={row.id} className="bot-check"><input type="checkbox" checked={peers.includes(row.id)} onChange={event=>setPeers(toggle(peers,row.id,event.target.checked))}/><span>{row.name}</span></label>)}
    </fieldset>
    <div className="manager-item-actions"><ActionButton busy={busy} primary type="submit">Save peers</ActionButton></div>
  </form>;
}

export default function BotPermissions(props:BotTabProps){
  const {bot,mutation}=props;
  return <>
    {bot.violations.length>0&&<div role="alert" className="manager-error bot-violation">
      <strong>This bot will not run.</strong> Its agent file asks for more than the ceiling allows: {bot.violations.join('; ')}. Save permissions below to bring it back inside the ceiling.
    </div>}
    <SettingsSection level={3} title="Permissions" description="What this bot is allowed to use right now.">
      <li className="settings-row bot-form-row"><Permissions key={bot.revision} {...props}/></li>
    </SettingsSection>
    <SettingsSection level={3} title="Ceiling" description="The most this bot can ever do. It runs as you, so it never has more access than your own account, and the main agent cannot raise it.">
      <li className="settings-row bot-form-row"><Ceiling key={bot.revision} {...props}/></li>
    </SettingsSection>
    <SettingsSection level={3} title="Remembered approvals" description="Choices you made with “always allow” or “always deny” for this bot.">
      {bot.rules.length?bot.rules.map(rule=><SettingsRow key={rule.id} label={rule.display||rule.tool} description={`${rule.effect==='allow'?'Allowed':'Denied'}${rule.times_used?` · used ${rule.times_used} time${rule.times_used===1?'':'s'}`:''}`}>
        <ActionButton busy={mutation.busy} onClick={()=>void mutation.mutate(()=>gql('mutation($id:String!,$rule:String!){revoke_bot_rule(bot_id:$id,rule_id:$rule){id}}',{id:bot.id,rule:rule.id}),'Approval revoked')} aria-label={`Revoke ${rule.display||rule.tool}`}>Revoke</ActionButton>
      </SettingsRow>)
      :<li className="settings-row"><Empty>No remembered approvals. When you choose “always allow” for this bot during a run, it appears here.</Empty></li>}
    </SettingsSection>
    <SettingsSection level={3} title="Peers" description="Other bots this one may message. Leave empty to allow none.">
      <li className="settings-row bot-form-row"><Peers key={bot.revision} {...props}/></li>
    </SettingsSection>
  </>;
}
