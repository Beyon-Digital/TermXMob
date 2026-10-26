import {useSettingsSave} from './SettingsSurface';
import {useEffect,useRef,useState} from 'react';
import {Button} from '../components/ui/button';
import {json,request as baseRequest} from '../lib/api';
import McpCredentialForm from './McpCredentialForm';
import McpConnectionForm,{type McpDefinition,type McpProject} from './McpConnectionForm';
type Catalog={tools?:{name:string;description?:string;inputSchema?:unknown}[]};
type Connection={id:string;label:string;definition:McpDefinition;definition_digest:string;credential_configured:boolean;catalog?:Catalog;runtime:{connected?:boolean;catalog?:Catalog};auth_url_pending?:string;trust:string;allowed_projects:string[];transport:string};
type ReviewedCall={connection:Connection;tool:string;target:string|null;arguments:Record<string,unknown>;text:string;inspection:number};
async function gql<T>(query:string,variables:Record<string,unknown>={}){const result=await baseRequest<{data:T;errors?:{message:string}[]}>('/graphql',json('POST',{query,variables}));if(result.errors?.length)throw new Error(result.errors.map(error=>error.message).join('\n'));return result.data}
export default function McpManager({projectId,admin,search,version,onChanged}:{projectId:string|null;admin:boolean;search:string;version:number;onChanged:()=>void}){
 const beginSave=useSettingsSave();
 const [connections,setConnections]=useState<Connection[]>([]),[projects,setProjects]=useState<McpProject[]>([]),[editing,setEditing]=useState<Connection|null>(null),[inspecting,setInspecting]=useState<Connection|null>(null),[catalog,setCatalog]=useState<Catalog|null>(null),[tool,setTool]=useState(''),[targets,setTargets]=useState<Record<string,string>>({}),[argumentsText,setArguments]=useState('{}'),[advanced,setAdvanced]=useState(false),[reviewed,setReviewed]=useState<ReviewedCall|null>(null),[result,setResult]=useState<unknown>(null),[busy,setBusy]=useState(false),[loading,setLoading]=useState(true),[error,setError]=useState(''),[notice,setNotice]=useState('');
 const generation=useRef(0),inspection=useRef(0),flight=useRef<symbol|null>(null);
 useEffect(()=>{
  const ticket=++generation.current;inspection.current++;flight.current=null;
  setLoading(true);setError('');setNotice('');setEditing(null);setInspecting(null);setCatalog(null);setTargets({});setAdvanced(false);setReviewed(null);setResult(null);setBusy(false);
  gql<{mcp_connections:Connection[];projects:McpProject[]}>('query($project:String){mcp_connections(project_id:$project){id label transport trust allowed_projects definition definition_digest credential_configured catalog runtime auth_url_pending} projects{id name}}',{project:admin?null:projectId}).then(value=>{if(ticket===generation.current){setConnections(value.mcp_connections);setProjects(value.projects)}}).catch(reason=>{if(ticket===generation.current)setError(reason instanceof Error?reason.message:String(reason))}).finally(()=>{if(ticket===generation.current)setLoading(false)});
  return()=>{generation.current++;inspection.current++};
 },[projectId,admin,version]);
 async function mutate(fn:()=>Promise<unknown>,message:string,refresh=true,persisted=false){
  if(flight.current)return false;
  const operation=Symbol(),ticket=generation.current,saved=persisted?beginSave():undefined;flight.current=operation;setBusy(true);setError('');setNotice('');
  try{await fn();if(ticket!==generation.current)return false;setNotice(message);saved?.();if(refresh)onChanged();return true}
  catch(reason){if(ticket===generation.current)setError(reason instanceof Error?reason.message:String(reason));return false}
  finally{if(ticket===generation.current&&flight.current===operation){flight.current=null;setBusy(false)}}
 }
 const allowedTargets=(connection:Connection)=>projects.filter(project=>connection.allowed_projects.includes(project.id));
 const targetFor=(connection:Connection):string|null=>{
  if(!connection.allowed_projects.length)return null;
  const allowed=allowedTargets(connection);if(allowed.length===1)return allowed[0].id;
  return allowed.some(project=>project.id===targets[connection.id])?targets[connection.id]:'';
 };
 const canUse=(connection:Connection)=>connection.allowed_projects.length?!!targetFor(connection):admin;
 const targetLabel=(target:string|null)=>target===null?'This host (administrator)':projects.find(project=>project.id===target)?.name||target||'Choose an execution project';
 const approved=(connection:Connection,name:string)=>connection.definition.approved_tools.includes('*')||connection.definition.approved_tools.includes(name);
 const usable=(connection:Connection)=>canUse(connection)&&connection.trust==='trusted'&&connection.definition.enabled;
 function invalidateCall(){inspection.current++;setReviewed(null);setResult(null);setError('');setNotice('')}
 function inspect(connection:Connection,nextCatalog=connection.catalog||connection.runtime.catalog||null){invalidateCall();setInspecting(connection);setCatalog(nextCatalog);setTool('');setArguments('{}');setAdvanced(false)}
 function chooseTarget(connection:Connection,target:string){invalidateCall();setTargets(current=>({...current,[connection.id]:target}))}
 async function connect(connection:Connection){
  if(!usable(connection))return;
  const ticket=generation.current,view=inspection.current,target=targetFor(connection);
  await mutate(async()=>{
   const response=await gql<{connect_mcp_connection:{status:string;catalog?:Catalog;note?:string}}>('mutation($id:String!,$project:String){connect_mcp_connection(conn_id:$id,project_id:$project){status catalog note auth_url}}',{id:connection.id,project:target});
   if(ticket===generation.current&&view===inspection.current&&response.connect_mcp_connection.catalog)inspect(connection,response.connect_mcp_connection.catalog);
  },'Connection checked for '+targetLabel(target));
 }
 function reviewCall(){
  if(!inspecting||!usable(inspecting)||!approved(inspecting,tool)||!catalog?.tools?.some(item=>item.name===tool)||flight.current)return;
  setError('');setResult(null);setReviewed(null);
  try{
   const args:unknown=JSON.parse(argumentsText);
   if(!args||typeof args!=='object'||Array.isArray(args))throw Error('Tool arguments must be a JSON object.');
   const text=JSON.stringify(args,null,2);
   if(new TextEncoder().encode(text).length>64000)throw Error('Tool arguments must fit within 64,000 bytes.');
   setReviewed({connection:inspecting,tool,target:targetFor(inspecting),arguments:args as Record<string,unknown>,text,inspection:inspection.current});
  }catch(reason){setError(reason instanceof Error?reason.message:String(reason))}
 }
 function confirmCall(){
  const call=reviewed;
  if(!call||!inspecting||call.inspection!==inspection.current||call.connection.id!==inspecting.id||call.connection.definition_digest!==inspecting.definition_digest||call.tool!==tool||call.target!==targetFor(inspecting)||!usable(inspecting)||!approved(inspecting,call.tool))return;
  setReviewed(null);const ticket=generation.current;
  void mutate(async()=>{
   const response=await gql<{call_mcp_tool:unknown}>('mutation($id:String!,$tool:String!,$input:McpToolCallInput!,$project:String){call_mcp_tool(conn_id:$id,tool:$tool,input:$input,project_id:$project)}',{id:call.connection.id,tool:call.tool,input:{arguments:call.arguments},project:call.target});
   if(ticket===generation.current&&call.inspection===inspection.current)setResult(response.call_mcp_tool);
  },'Selected tool completed for '+targetLabel(call.target),false);
 }
 return <section aria-label="Tool connection configuration"><h2>Tool connections</h2>
  <p className="manager-help">Choose an execution target for each connection. Only accessible projects in its allowlist are offered. The host checks current permissions before resolving credentials, connecting or calling a tool.</p>
  {error&&<p role="alert" className="manager-error">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {loading?<p>Loading current connections…</p>:<>{connections.filter(connection=>(connection.label+' '+connection.id).toLowerCase().includes(search.toLowerCase())).map(connection=><article className="manager-item" key={connection.id}>
   <div><h3>{connection.label||connection.id}</h3><p>{connection.transport} · {connection.runtime.connected?'Connected':'Disconnected'} · {connection.trust}</p><p>{connection.allowed_projects.length?'Only: '+connection.allowed_projects.map(id=>projects.find(project=>project.id===id)?.name||id).join(', '):'Host-wide · administrator only'} · {connection.credential_configured?'Credential configured':'No managed credential'}</p>
    {connection.allowed_projects.length?(allowedTargets(connection).length?<label data-settings-filter>Execution target<select aria-label={'Connection target for '+connection.label} value={targetFor(connection)||''} disabled={busy||allowedTargets(connection).length===1} onChange={event=>chooseTarget(connection,event.target.value)}>{allowedTargets(connection).length!==1&&<option value="">Choose a project</option>}{allowedTargets(connection).map(project=><option key={project.id} value={project.id}>{project.name}</option>)}</select></label>:<p>No accessible project is in this connection’s allowlist.</p>):<p>Execution target: This host (administrator)</p>}
   </div><div className="manager-item-actions"><Button variant="secondary" disabled={busy} onClick={()=>inspect(connection)}>Inspect tools and permissions</Button>
    {connection.runtime.connected?admin?<Button variant="secondary" disabled={busy} onClick={()=>void mutate(()=>gql('mutation($id:String!){disconnect_mcp_connection(conn_id:$id)}',{id:connection.id}),'Connection disconnected')}>Disconnect</Button>:<p>Disconnect requires a host administrator.</p>:<Button variant="secondary" disabled={busy||!usable(connection)} onClick={()=>void connect(connection)}>Connect</Button>}
    {admin&&<><Button variant="secondary" disabled={busy} onClick={()=>setEditing(connection)}>Edit connection</Button>{connection.trust!=='trusted'&&<Button variant="secondary" disabled={busy} onClick={()=>void mutate(()=>gql('mutation($id:String!,$digest:String!){trust_mcp_connection(conn_id:$id,expected_digest:$digest)}',{id:connection.id,digest:connection.definition_digest}),'Reviewed connection trusted',true,true)}>Trust reviewed connection</Button>}<Button variant="secondary" disabled={busy} onClick={()=>void mutate(()=>gql('mutation($id:String!,$digest:String!){delete_mcp_connection(conn_id:$id,expected_digest:$digest)}',{id:connection.id,digest:connection.definition_digest}),'Connection removed',true,true)}>Remove connection</Button></>}
    {connection.auth_url_pending&&<a href={connection.auth_url_pending} target="_blank" rel="noopener noreferrer">Complete connection sign-in</a>}
   </div></article>)}</>}
  {inspecting&&<section className="manager-form" data-settings-filter aria-label="Connection permission inspection"><h3>{inspecting.label} permissions</h3><p>Inspection reads the saved catalog and configuration. It does not connect or run a tool. Workers continue to use each conversation’s current permissions.</p><p>Configured allowed tools: {inspecting.definition.approved_tools.length?inspecting.definition.approved_tools.join(', '):'None'}. Definition changes are applied to future connections, subject to current authority.</p>
   <details><summary>Exact connection configuration</summary><pre role="region" tabIndex={0} aria-label="Connection configuration" className="manager-json">{JSON.stringify(inspecting.definition,null,2)}</pre></details>
   {catalog?.tools?.length?<><label>Tool to inspect<select aria-label="Tool to inspect" value={tool} disabled={busy} onChange={event=>{invalidateCall();setTool(event.target.value);setAdvanced(false)}}><option value="">Select a catalog tool</option>{catalog.tools.map(item=><option key={item.name} value={item.name}>{item.name}</option>)}</select></label>
    {tool&&<><p>{catalog.tools.find(item=>item.name===tool)?.description}</p><pre role="region" tabIndex={0} aria-label="Selected tool input schema" className="manager-json">{JSON.stringify(catalog.tools.find(item=>item.name===tool)?.inputSchema,null,2)}</pre>
     <details key={inspecting.id+':'+tool} open={advanced}><summary onClick={event=>{event.preventDefault();setAdvanced(value=>!value);setReviewed(null)}}>Advanced · run this tool</summary>{advanced&&<><p>This explicitly calls the connected service and can change it. This is a direct settings action, outside a conversation’s approval flow.</p><p>Execution target: <strong>{targetLabel(targetFor(inspecting))}</strong></p><p>The connection must be trusted and enabled, and the selected tool must be approved. The host rechecks authority when it receives the call.</p><label>Tool arguments<textarea aria-label="Tool arguments" disabled={busy} value={argumentsText} onChange={event=>{invalidateCall();setArguments(event.target.value)}}/></label><Button disabled={busy||!usable(inspecting)||!approved(inspecting,tool)} onClick={reviewCall}>Call selected tool</Button>
      {reviewed&&<section aria-label="Review tool call"><h4>Confirm this tool call</h4><p>Connection: {reviewed.connection.label} ({reviewed.connection.id})</p><p>Tool: {reviewed.tool}</p><p>Execution target: {targetLabel(reviewed.target)}{reviewed.target&&' ('+reviewed.target+')'}</p><pre role="region" tabIndex={0} aria-label="Exact tool call arguments" className="manager-json">{reviewed.text}</pre><Button disabled={busy} onClick={confirmCall}>Confirm tool call</Button><Button variant="secondary" disabled={busy} onClick={()=>setReviewed(null)}>Cancel tool call</Button></section>}
     </>}
     </details>
    </>}
   </>:<p>Connect after reviewing trust and scope to load the live catalog.</p>}
   {result!==null&&<pre role="region" tabIndex={0} aria-label="Tool result" className="manager-json">{JSON.stringify(result,null,2)}</pre>}
   <Button variant="secondary" onClick={()=>{invalidateCall();setInspecting(null)}}>Close connection inspection</Button>
  </section>}
  {admin&&editing&&<section>
   <McpConnectionForm key={editing.id} connection={editing.definition} projects={projects} busy={busy} onCancel={()=>setEditing(null)} onSave={value=>mutate(async()=>{
    if(editing.runtime.connected)await gql('mutation($id:String!){disconnect_mcp_connection(conn_id:$id)}',{id:editing.id});
    await gql('mutation($input:McpConnectionInput!){create_mcp_connection(input:$input)}',{input:{data:value,expected_digest:editing.definition_digest}});
   },'Connection settings saved',true,true)}/>
   <McpCredentialForm key={editing.id+':credential'} connection={editing.definition} busy={busy} onSave={(binding,value,kind)=>mutate(
    ()=>gql('mutation($id:String!,$binding:String!,$value:String,$digest:String!,$kind:String!){set_mcp_credential(conn_id:$id,binding:$binding,value:$value,expected_digest:$digest,kind:$kind)}',{id:editing.id,binding,value,digest:editing.definition_digest,kind}),
    'Credential binding updated; reconnect explicitly',true,true
   )}/>
  </section>}
  {admin&&<details><summary>Add a tool connection</summary><McpConnectionForm projects={projects} busy={busy} onSave={value=>mutate(()=>gql('mutation($input:McpConnectionInput!){create_mcp_connection(input:$input)}',{input:{data:value}}),'Untrusted connection saved',true,true)}/></details>}
 </section>
}
