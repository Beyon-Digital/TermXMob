import {useState} from 'react';
import {FormField} from '../components/ui/form-field';
import {Input} from '../components/ui/input';
import {Textarea} from '../components/ui/textarea';
import {request} from '../lib/api';
import type {HostIdentity,MemoryRow} from '../lib/host-types';
import {MemoryEditor} from './MemoryEditor';
import {ActionButton,Empty,ManagerPage,date,rowMatcher} from './manager-ui';
import {scoped,useManagerLoad,useManagerMutation,type Mutation} from './use-manager';
import {useDirtyForm} from './settings-forms';

function download(name: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], {type: 'application/json'}));
  const anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); URL.revokeObjectURL(url);
}

function AddMemoryForm({projectId,mutation}:{projectId:string|null;mutation:Mutation}){
  const {formProps,markClean}=useDirtyForm('add-memory');
  const [content,setContent]=useState(''),[provenance,setProvenance]=useState(''),[retention,setRetention]=useState('30');
  const {busy,post,mutate}=mutation;
  async function submit(){
    const result=await post('/api/workspace/memory',{content,provenance,project_id:projectId,retention_days:Number(retention||30)},'Memory saved');
    if(result!==null){setContent('');setProvenance('');setRetention('30');markClean()}
  }
  return <form {...formProps} className="manager-form" aria-label="Add memory" onSubmit={event=>{event.preventDefault();void submit()}}>
    <FormField label="Fact or decision" required>{control=><Textarea {...control} name="content" value={content} onChange={event=>setContent(event.target.value)}/>}</FormField>
    <FormField label="Source or reason" required>{control=><Input {...control} name="provenance" value={provenance} onChange={event=>setProvenance(event.target.value)}/>}</FormField>
    <FormField label="Retention in days">{control=><Input {...control} name="retention_days" type="number" min={1} max={3650} value={retention} onChange={event=>setRetention(event.target.value)}/>}</FormField>
    <div className="manager-item-actions">
      <ActionButton busy={busy} primary type="submit">Save memory</ActionButton>
      <ActionButton busy={busy} type="button" onClick={()=>void mutate(async()=>{download('termx-memory.json',await request(`/api/workspace/memory/export${scoped(projectId)}`))},'Memory exported',{refresh:false})}>Export memory</ActionButton>
    </div>
  </form>;
}

export default function MemorySection({me,projectId}:{me:HostIdentity;projectId:string|null}){
  const {data,loading,error:loadError,reload}=useManagerLoad(()=>request<{memory?:MemoryRow[]}>(`/api/workspace/memory${scoped(projectId)}`));
  const mutation=useManagerMutation(reload);
  const {busy,error,notice,post,remove,setNotice}=mutation;
  const [search,setSearch]=useState(''),[editing,setEditing]=useState<MemoryRow|null>(null);
  const memories=data?.memory||[];
  const matches=rowMatcher(search,me.session_id);
  return <ManagerPage section="memory" projectId={projectId} load={{data,loading,busy,error:error||loadError,notice,reload}} search={{label:"Memory",value:search,onChange:setSearch,count:memories.length}}>
    <>
      <h3>Memory with provenance</h3>
      {!memories.length&&<Empty>No memory saved. Add facts and decisions that should carry into future sessions.</Empty>}
      {memories.filter(matches).map(memory=><article key={memory.id} className="manager-memory">
        <p>{memory.content}</p>
        <small>{memory.provenance} · {memory.project_id?'Project':'Your account'} · expires {date(memory.expires_at)}{memory.excluded?' · Excluded from execution':''}</small>
        <div>
          <ActionButton busy={busy} onClick={()=>setEditing(memory)}>Edit memory</ActionButton>
          <ActionButton busy={busy} onClick={()=>void post('/api/workspace/memory',{identifier:memory.id,revision:memory.revision,content:memory.content,provenance:memory.provenance,project_id:memory.project_id||null,retention_days:memory.retention_days,excluded:!memory.excluded},'Memory scope updated')}>{memory.excluded?'Include in context':'Exclude from context'}</ActionButton>
          <ActionButton busy={busy} onClick={()=>void remove(`/api/workspace/memory/${memory.id}`,'Memory deleted')}>Delete</ActionButton>
        </div>
      </article>)}
      {editing&&<MemoryEditor key={editing.id} memory={editing} onCancel={()=>setEditing(null)} onSaved={()=>{setEditing(null);setNotice('Memory updated');reload()}}/>}
      <details open><summary>Add memory</summary><AddMemoryForm projectId={projectId} mutation={mutation}/></details>
    </>
  </ManagerPage>;
}
