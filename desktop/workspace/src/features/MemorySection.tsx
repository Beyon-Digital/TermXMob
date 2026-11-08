import {useState} from 'react';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {Textarea} from '../components/ui/textarea';
import {request} from '../lib/api';
import type {HostIdentity,MemoryRow} from '../lib/host-types';
import {MemoryEditor,appliesToLabel,appliesToText,memoryPayload,type MemoryScope} from './MemoryEditor';
import {ActionButton,Empty,ManagerPage,date,rowMatcher} from './manager-ui';
import {gql,scoped,useManagerLoad,useManagerMutation,type Mutation} from './use-manager';
import {useDirtyForm} from './settings-forms';

function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url);
}

const listPath = (projectId: string | null, agentId: string | null) =>
  `/api/workspace/memory${scoped(projectId)}${agentId ? `${projectId ? '&' : '?'}agent_id=${encodeURIComponent(agentId)}` : ''}`;

function MemoryItem({memory,agentId,children}:{memory:MemoryRow;agentId:string|null;children:React.ReactNode}){
  return <article className="manager-memory">
    <p>{memory.content}</p>
    <small>{memory.provenance} · {appliesToText(memory,agentId)} · expires {date(memory.expires_at)}{memory.state!=='proposed'&&memory.author==='agent'?' · Written by an agent, read back as untrusted':''}{memory.excluded?' · Excluded from execution':''}</small>
    <div>{children}</div>
  </article>;
}

function AddMemoryForm({projectId,agentId,mutation}:{projectId:string|null;agentId:string|null;mutation:Mutation}){
  const {formProps,markClean}=useDirtyForm('add-memory');
  const choices: MemoryScope[] = [...(agentId ? ['agent' as const] : []), ...(projectId ? ['project' as const] : []), 'user'];
  const [scope,setScope]=useState<MemoryScope>(choices[0]);
  const [content,setContent]=useState(''),[provenance,setProvenance]=useState(''),[retention,setRetention]=useState('30');
  const {busy,post,mutate}=mutation;
  async function submit(){
    const result=await post('/api/workspace/memory',{content,provenance,scope,project_id:scope==='project'?projectId:null,agent_id:scope==='agent'?agentId:null,retention_days:Number(retention||30)},'Memory saved');
    if(result!==null){setContent('');setProvenance('');setRetention('30');markClean()}
  }
  return <form {...formProps} className="manager-form" aria-label="Add memory" onSubmit={event=>{event.preventDefault();void submit()}}>
    <FormField label="Applies to" hint="Fixed once saved.">{control=><Select {...control} name="scope" value={scope} onChange={event=>setScope(event.target.value as MemoryScope)}>
      {choices.map(choice=><option key={choice} value={choice}>{appliesToLabel[choice]}</option>)}
    </Select>}</FormField>
    <FormField label="Fact or decision" required>{control=><Textarea {...control} name="content" value={content} onChange={event=>setContent(event.target.value)}/>}</FormField>
    <FormField label="Source or reason" required>{control=><Input {...control} name="provenance" value={provenance} onChange={event=>setProvenance(event.target.value)}/>}</FormField>
    <FormField label="Retention in days">{control=><Input {...control} name="retention_days" type="number" min={1} max={3650} value={retention} onChange={event=>setRetention(event.target.value)}/>}</FormField>
    <div className="manager-item-actions">
      <ActionButton busy={busy} primary type="submit">Save memory</ActionButton>
      <ActionButton busy={busy} type="button" onClick={()=>void mutate(async()=>{download('termx-memory.json',await request(`/api/workspace/memory/export${scoped(projectId)}`))},'Memory exported',{refresh:false})}>Export memory</ActionButton>
    </div>
  </form>;
}

/** `agentId` limits the list to what that agent sees and makes "This agent only" available when saving. */
export default function MemorySection({me,projectId,agentId=null}:{me:HostIdentity;projectId:string|null;agentId?:string|null}){
  const {data,loading,error:loadError,reload}=useManagerLoad(()=>request<{memory?:MemoryRow[]}>(listPath(projectId,agentId)));
  const mutation=useManagerMutation(reload);
  const {busy,error,notice,post,remove,mutate,setNotice}=mutation;
  const [search,setSearch]=useState(''),[editing,setEditing]=useState<MemoryRow|null>(null);
  const memories=data?.memory||[];
  const matches=rowMatcher(search,me.session_id);
  const proposed=memories.filter(memory=>memory.state==='proposed'&&matches(memory));
  const saved=memories.filter(memory=>memory.state!=='proposed'&&matches(memory));
  const resolve=(memory:MemoryRow,keep:boolean)=>void mutate(()=>gql('mutation($id:String!,$keep:Boolean!){resolve_memory_proposal(memory_id:$id,keep:$keep){id}}',{id:memory.id,keep}),keep?'Memory kept':'Memory discarded');
  return <ManagerPage section="memory" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={{label:"Memory",value:search,onChange:setSearch,count:memories.length}}>
    <>
      {proposed.length>0&&<section aria-label="Proposed memory">
        <h3>Proposed</h3>
        <p className="manager-help">An agent suggested these during a run nobody was watching. A proposal is not used until you keep it.</p>
        {proposed.map(memory=><MemoryItem key={memory.id} memory={memory} agentId={agentId}>
          <ActionButton busy={busy} primary onClick={()=>resolve(memory,true)}>Keep memory</ActionButton>
          <ActionButton busy={busy} onClick={()=>resolve(memory,false)}>Discard memory</ActionButton>
        </MemoryItem>)}
      </section>}
      <section aria-label="Saved memory">
        <h3>Memory with provenance</h3>
        {!saved.length&&<Empty>{proposed.length?'Nothing kept yet. Keep a proposal above or add a note below.':'No memory saved. Add facts and decisions that should carry into future sessions.'}</Empty>}
        {saved.map(memory=><MemoryItem key={memory.id} memory={memory} agentId={agentId}>
          <ActionButton busy={busy} onClick={()=>setEditing(memory)}>Edit memory</ActionButton>
          <ActionButton busy={busy} onClick={()=>void post('/api/workspace/memory',memoryPayload(memory,{excluded:!memory.excluded}),'Memory scope updated')}>{memory.excluded?'Include in context':'Exclude from context'}</ActionButton>
          <ActionButton busy={busy} onClick={()=>void remove(`/api/workspace/memory/${memory.id}`,'Memory deleted')}>Delete</ActionButton>
        </MemoryItem>)}
      </section>
      {editing&&<MemoryEditor key={editing.id} memory={editing} onCancel={()=>setEditing(null)} onSaved={()=>{setEditing(null);setNotice('Memory updated');reload()}}/>}
      <details open><summary>Add memory</summary><AddMemoryForm projectId={projectId} agentId={agentId} mutation={mutation}/></details>
    </>
  </ManagerPage>;
}
