import {useEffect,useState} from 'react';
import {canonicalSettingsCategory} from '../lib/settings-categories';
import {request} from '../lib/api';
import type {HostIdentity} from '../lib/host-types';
import CloudAccounts from './CloudAccounts';
import ExtensionManager from './ExtensionManager';
import HookSettings from './HookSettings';
import McpManager from './McpManager';
import ScheduleManager from './ScheduleManager';
import ModelsSection from './ModelsSection';
import AgentsSection from './AgentsSection';
import BotsSection from './BotsSection';
import MemorySection from './MemorySection';
import VoiceSection from './VoiceSection';
import AccessSection from './AccessSection';
import {Empty,ManagerPage,ManagerSearch,ManagerStatus} from './manager-ui';
import {useManagerLoad} from './use-manager';
import './managers.css';

export interface ManagersProps { section: string; projectId: string | null; sessionId: string | null; onSection?: (name: string) => void; onCapabilitiesChanged?: () => void; embedded?: boolean; agentId?: string | null }

function ExtensionsSection({projectId}:{projectId:string|null}){
  const {data,loading,error,reload}=useManagerLoad(()=>request<Record<string,unknown>>('/api/workspace/extensions'));
  const [search,setSearch]=useState('');
  const total=Object.values(data||{}).reduce((count:number,value)=>count+(Array.isArray(value)?value.length:0),0);
  return <ManagerPage section="extensions" projectId={projectId} disabled={loading} onRefresh={reload}>
    {(search||total>=8)&&<ManagerSearch label="Plugins & skills" value={search} onChange={setSearch}/>}
    <ManagerStatus error={error} notice="" onRetry={reload}/>
    <ExtensionManager data={data||{}} projectId={projectId} search={search} onChanged={reload}/>
  </ManagerPage>;
}

/** Categories that load their own data; Refresh asks them to reload by bumping `version`. */
function PassivePage({section,projectId,sessionId,admin,me}:{section:'cloud'|'hooks'|'automations'|'connections';projectId:string|null;sessionId:string|null;admin:boolean;me:HostIdentity}){
  const [version,setVersion]=useState(0),refresh=()=>setVersion(value=>value+1);
  return <ManagerPage section={section} projectId={projectId} disabled={false} onRefresh={refresh}>
    {section==='cloud'&&<CloudAccounts/>}
    {section==='hooks'&&<HookSettings version={version}/>}
    {section==='automations'&&<ScheduleManager projectId={projectId} sessionId={sessionId} version={version}/>}
    {section==='connections'&&<McpManager ownerKey={me.principal?.id?(me.host_id||location.origin)+':'+me.principal.id:undefined} projectId={projectId} admin={admin} search="" version={version} onChanged={refresh}/>}
  </ManagerPage>;
}

export function Managers({section: incoming, projectId, sessionId, onCapabilitiesChanged, embedded=false, agentId=null}: ManagersProps) {
  const section=canonicalSettingsCategory(incoming);
  const [me,setMe]=useState<HostIdentity>({}),[ready,setReady]=useState(false);
  useEffect(()=>{
    let cancelled=false;
    request<HostIdentity>('/auth/me').then(value=>{if(!cancelled)setMe(value)}).catch(()=>{}).finally(()=>{if(!cancelled)setReady(true)});
    return()=>{cancelled=true};
  },[]);
  const admin=(me.principal?.scopes||[]).includes('host-admin');
  // A category and scope mount fresh, so nothing typed or loaded in one leaks into the next.
  const scope=`${section}:${projectId??''}`;
  return <section className={"managers"+(embedded?" managers-embedded":"")} aria-label="Workspace managers">
    {!ready?<div className="manager-main"><Empty>Loading current settings…</Empty></div>
    :section==='models'?<ModelsSection key={scope} admin={admin} me={me} projectId={projectId} onCapabilitiesChanged={onCapabilitiesChanged}/>
    :section==='voice'?<VoiceSection key={scope} admin={admin} projectId={projectId}/>
    :section==='agents'?<AgentsSection key={scope} admin={admin} me={me} projectId={projectId}/>
    :section==='bots'?<BotsSection key={scope} admin={admin} me={me} projectId={projectId}/>
    :section==='memory'?<MemorySection key={scope+':'+(agentId??'')} me={me} projectId={projectId} agentId={agentId}/>
    :section==='access'?<AccessSection key={scope} admin={admin} me={me} projectId={projectId}/>
    :section==='extensions'?<ExtensionsSection key={scope} projectId={projectId}/>
    :['cloud','hooks','automations','connections'].includes(section)?<PassivePage key={scope} section={section as 'cloud'} projectId={projectId} sessionId={sessionId} admin={admin} me={me}/>
    :<ManagerPage section={section} projectId={projectId} disabled={false} onRefresh={()=>{}}><Empty>Choose a category from the settings menu.</Empty></ManagerPage>}
  </section>;
}
export default Managers;
