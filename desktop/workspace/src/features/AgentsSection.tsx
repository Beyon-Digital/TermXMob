import {useState} from 'react';
import {Button} from '../components/ui/button';
import {AgentPresetEditor,type AgentPresetSeed} from './AgentPresetEditor';
import {DescribeToDraft} from './authoring/DescribeToDraft';
import {asList,asText} from './authoring/kinds';
import type {AgentPreset,AgentPresetInput,EngineDescriptor,HostIdentity} from '../lib/host-types';
import {ActionButton,Empty,Item,Json,ManagerPage,rowMatcher} from './manager-ui';
import {gql,useManagerLoad,useManagerMutation} from './use-manager';

interface AgentsData { engines?: EngineDescriptor[]; custom_agents?: AgentPreset[] }
const loadAgents=()=>gql<AgentsData>('{ engines { id label installed } custom_agents { id name description instructions engine model enabled tools tools_mode toolsets deny_tools limits approval_mode sandbox_profile file_revision mcp_connections } }');

/** A draft only fills in the form. Permissions always start at the least-privileged defaults, whatever the draft says. */
const seedFrom=(draft:Record<string,unknown>):AgentPresetSeed=>({name:asText(draft.name),description:asText(draft.description),instructions:asText(draft.instructions),tools:asList(draft.tools),approval_mode:'standard',sandbox_profile:'agent'});

export default function AgentsSection({admin,me,projectId,onSection}:{admin:boolean;me:HostIdentity;projectId:string|null;onSection?:(name:string)=>void}){
  const {data,loading,error:loadError,reload}=useManagerLoad(loadAgents);
  const {busy,error,notice,mutate}=useManagerMutation(reload);
  const [search,setSearch]=useState(''),[preview,setPreview]=useState<AgentPreset|null>(null),[editing,setEditing]=useState<AgentPreset|null>(null);
  // New presets start from a description. `creating` shows the form, pre-filled when a draft was accepted.
  const [describing,setDescribing]=useState(false),[creating,setCreating]=useState(false),[seed,setSeed]=useState<AgentPresetSeed|undefined>(undefined),[seedKey,setSeedKey]=useState(0);
  const openForm=(next?:AgentPresetSeed)=>{setSeed(next);setSeedKey(key=>key+1);setDescribing(false);setCreating(true)};
  const presets=data?.custom_agents||[],engines=(data?.engines||[]).filter(row=>row.installed);
  const matches=rowMatcher(search,me.session_id);
  return <ManagerPage section="agents" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={{label:"Agents",value:search,onChange:setSearch,count:presets.length}}>
    <>
      <h3>Presets for future sessions</h3>
      {admin&&<div className="authoring-toolbar"><Button onClick={()=>setDescribing(true)}>New agent</Button></div>}
      {data&&!presets.length&&<Empty>No agent presets yet. Describe what you want and review the draft before it is saved.</Empty>}
      {presets.filter(matches).map(agent=><Item key={agent.id} title={agent.name} meta={agent.description}>
        <ActionButton busy={busy} onClick={()=>setPreview(agent)}>Inspect</ActionButton>
        {admin&&<ActionButton busy={busy} onClick={()=>setEditing(agent)}>Edit preset</ActionButton>}
        {admin&&<ActionButton busy={busy} onClick={()=>void mutate(()=>gql('mutation($id:String!){delete_custom_agent(agent_id:$id){deleted}}',{id:agent.id}),'Agent preset removed')}>Remove</ActionButton>}
      </Item>)}
      {preview&&<Json value={preview}/>}
      {admin&&editing&&<AgentPresetEditor engines={engines} key={editing.id} preset={editing} onCancel={()=>setEditing(null)} onSave={async input=>{const result=await mutate(()=>gql('mutation($id:String!,$input:CustomAgentPatchInput!){patch_custom_agent(agent_id:$id,input:$input){id}}',{id:editing.id,input:{...input}}),'Preset updated for future runs');if(result!==null)setEditing(null);return result}}/>}
      {admin&&creating&&<section aria-label="New agent preset">
        <h3>{seed?'Review your agent':'New agent preset'}</h3>
        {seed&&<p className="manager-help">Drafted from your description. Check each field, change what you need, then save.</p>}
        <AgentPresetEditor key={seedKey} engines={engines} initial={seed} onCancel={()=>{setCreating(false);setSeed(undefined)}} onSave={async(input:AgentPresetInput)=>{const result=await mutate(()=>gql('mutation($input:CustomAgentInput!){create_custom_agent(input:$input){id}}',{input:{...input}}),'Preset saved');if(result!==null){setCreating(false);setSeed(undefined)}return result}}/>
      </section>}
      {admin&&<DescribeToDraft kind="agent" open={describing} onClose={()=>setDescribing(false)} onManual={()=>openForm()} onReview={draft=>openForm(seedFrom(draft))} onSetupProvider={onSection?()=>onSection('models'):undefined}/>}
    </>
  </ManagerPage>;
}
