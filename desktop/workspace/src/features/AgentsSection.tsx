import {useState} from 'react';
import {AgentPresetEditor} from './AgentPresetEditor';
import type {AgentPreset,AgentPresetInput,EngineDescriptor,HostIdentity} from '../lib/host-types';
import {ActionButton,Item,Json,ManagerPage,rowMatcher} from './manager-ui';
import {gql,useManagerLoad,useManagerMutation} from './use-manager';

interface AgentsData { engines?: EngineDescriptor[]; custom_agents?: AgentPreset[] }
const loadAgents=()=>gql<AgentsData>('{ engines { id label installed } custom_agents { id name description instructions engine model enabled tools tools_mode toolsets deny_tools limits approval_mode sandbox_profile file_revision mcp_connections } }');

export default function AgentsSection({admin,me,projectId}:{admin:boolean;me:HostIdentity;projectId:string|null}){
  const {data,loading,error:loadError,reload}=useManagerLoad(loadAgents);
  const {busy,error,notice,mutate}=useManagerMutation(reload);
  const [search,setSearch]=useState(''),[preview,setPreview]=useState<AgentPreset|null>(null),[editing,setEditing]=useState<AgentPreset|null>(null);
  const presets=data?.custom_agents||[],engines=(data?.engines||[]).filter(row=>row.installed);
  const matches=rowMatcher(search,me.session_id);
  return <ManagerPage section="agents" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={{label:"Agents",value:search,onChange:setSearch,count:presets.length}}>
    <>
      <h3>Presets for future sessions</h3>
      {presets.filter(matches).map(agent=><Item key={agent.id} title={agent.name} meta={agent.description}>
        <ActionButton busy={busy} onClick={()=>setPreview(agent)}>Inspect</ActionButton>
        {admin&&<ActionButton busy={busy} onClick={()=>setEditing(agent)}>Edit preset</ActionButton>}
        {admin&&<ActionButton busy={busy} onClick={()=>void mutate(()=>gql('mutation($id:String!){delete_custom_agent(agent_id:$id){deleted}}',{id:agent.id}),'Agent preset removed')}>Remove</ActionButton>}
      </Item>)}
      {preview&&<Json value={preview}/>}
      {admin&&editing&&<AgentPresetEditor engines={engines} key={editing.id} preset={editing} onCancel={()=>setEditing(null)} onSave={async input=>{const result=await mutate(()=>gql('mutation($id:String!,$input:CustomAgentPatchInput!){patch_custom_agent(agent_id:$id,input:$input){id}}',{id:editing.id,input:{...input}}),'Preset updated for future runs');if(result!==null)setEditing(null);return result}}/>}
      {admin&&<details open><summary>Create agent preset</summary><AgentPresetEditor engines={engines} onSave={(input:AgentPresetInput)=>mutate(()=>gql('mutation($input:CustomAgentInput!){create_custom_agent(input:$input){id}}',{input:{...input}}),'Preset saved')}/></details>}
    </>
  </ManagerPage>;
}
