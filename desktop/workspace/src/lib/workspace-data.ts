import {gql,type Session,type Turn} from './api';
type Connection<T>={edges:{cursor:string;node:T}[];pageInfo:{hasNextPage:boolean;hasPreviousPage?:boolean;startCursor?:string|null;endCursor:string|null}};
type TurnNode=Omit<Turn,'id'>&{canonical_id:string};
const fields='edges{cursor node{id canonical_id prompt task_id sequence task}}pageInfo{hasNextPage hasPreviousPage startCursor endCursor}';
export async function listSessions(archived=false):Promise<Session[]>{
 const result:Session[]=[];let after:string|null=null;
 do{const page:{workspace_sessions:Connection<{snapshot:Session}>}=await gql('query($after:String,$archived:Boolean!){workspace_sessions(first:100,after:$after,archived:$archived){edges{node{id snapshot}}pageInfo{hasNextPage endCursor}}}',{after,archived});result.push(...page.workspace_sessions.edges.map(edge=>edge.node.snapshot));after=page.workspace_sessions.pageInfo.hasNextPage?page.workspace_sessions.pageInfo.endCursor:null}while(after);
 return result;
}
type Cached={snapshot:Session;turns:Turn[];cursor:string|null;start:string|null;hasEarlier:boolean;liveOffset:number;reportOffset:number};
const terminalStatuses=new Set(['completed','failed','cancelled']);
const refreshCohort=30;
const isReport=(turn:Turn)=>!!turn.task?.coordination_report||turn.task?.engine==='coordination';
/** Keep each metadata request at most 64 nodes, including the session. The
 * newest live task has priority; rotating cohorts also refresh older pending
 * turns and retained reports without rescanning their complete histories. */
function refreshTargets(record?:Cached){
 const turns=record?.turns||[],live=turns.filter(turn=>!isReport(turn)&&(!turn.task||!terminalStatuses.has(turn.task.status))),reports=turns.filter(isReport);
 const selected=new Set<string>();
 const add=(turn?:Turn)=>{if(turn)selected.add(turn.id)};
 add(turns.at(-1));add(live.at(-1));add([...live].reverse().find(turn=>turn.task));
 const cohort=(pool:Turn[],offset=0)=>{const start=pool.length?offset%pool.length:0,count=Math.min(pool.length,refreshCohort);for(let index=0;index<count;index++)add(pool[(start+index)%pool.length]);return pool.length?(start+count)%pool.length:0};
 const liveOffset=cohort(live,record?.liveOffset),reportOffset=cohort(reports,record?.reportOffset);
 return {ids:[...selected],liveOffset,reportOffset};
}
export class SessionCache{
 private generation=0;
 private flights=new Map<string,Promise<Session>>();private historyFlights=new Map<string,Promise<Session>>();private records=new Map<string,Cached>();
 clear(){this.generation++;this.records.clear();this.flights.clear();this.historyFlights.clear()}
 read(id:string):Promise<Session>{const previous=this.flights.get(id);if(previous)return previous;const flight=this.fetch(id).finally(()=>{if(this.flights.get(id)===flight)this.flights.delete(id)});this.flights.set(id,flight);return flight}
 history(id:string):Promise<Session>{const previous=this.historyFlights.get(id);if(previous)return previous;const flight=this.fetchHistory(id).finally(()=>{if(this.historyFlights.get(id)===flight)this.historyFlights.delete(id)});this.historyFlights.set(id,flight);return flight}
 private async fetchHistory(id:string):Promise<Session>{const generation=this.generation;await this.read(id);this.assertGeneration(generation);const record=this.records.get(id)!;if(!record.hasEarlier)return this.value(record);const result=await gql<{workspace_turns:Connection<TurnNode>}>('query($id:String!,$before:String){workspace_turns(session_id:$id,last:200,before:$before){'+fields+'}}',{id,before:record.start});this.assertGeneration(generation);const page=result.workspace_turns;const known=new Set(record.turns.map(turn=>turn.id));record.turns=[...page.edges.map(edge=>({...edge.node,id:edge.node.canonical_id})).filter(turn=>!known.has(turn.id)),...record.turns];record.start=page.pageInfo.startCursor||record.start;record.hasEarlier=!!page.pageInfo.hasPreviousPage;return this.value(record)}
 private assertGeneration(value:number){if(value!==this.generation)throw new Error('Conversation read was cancelled when the authenticated workspace changed')}
 private value(record:Cached):Session{return {...record.snapshot,turns:[...record.turns],hasEarlier:record.hasEarlier}}
 private async fetch(id:string):Promise<Session>{
  const generation=this.generation,key=btoa('WorkspaceSessionNode:'+id),cached=this.records.get(id);
  const refresh=refreshTargets(cached),ids=[key,...refresh.ids.map(identifier=>btoa('WorkspaceTurnNode:'+identifier))];
  const meta=await gql<{nodes:({snapshot?:Session;task?:Turn['task']}|null)[]}>('query($ids:[ID!]!){nodes(ids:$ids){id ... on WorkspaceSessionNode{snapshot} ... on WorkspaceTurnNode{task}}}',{ids});
  this.assertGeneration(generation);
  if(!meta.nodes[0]?.snapshot)throw new Error('Conversation is unavailable');
  if(meta.nodes.length!==ids.length)throw new Error('The host returned incomplete conversation metadata');
  const updated=new Map(refresh.ids.map((identifier,index)=>[identifier,meta.nodes[index+1]]));
  const query=cached?'query($id:String!,$after:String){workspace_turns(session_id:$id,first:200,after:$after){'+fields+'}}':'query($id:String!){workspace_turns(session_id:$id,last:200){'+fields+'}}';
  const result=await gql<{workspace_turns:Connection<TurnNode>}>(query,{id,...(cached?{after:cached.cursor}:{})});this.assertGeneration(generation);const page=result.workspace_turns;
  // History can finish while this poll is in flight. Commit onto that same
  // record and its current rows rather than replacing the history it added.
  const record=cached||{snapshot:meta.nodes[0].snapshot,turns:[],cursor:null,start:null,hasEarlier:false,liveOffset:0,reportOffset:0};record.snapshot=meta.nodes[0].snapshot;
  record.turns=record.turns.flatMap(turn=>{
   if(!updated.has(turn.id))return [turn];
   const node=updated.get(turn.id);
   if(!node||!node.task&&isReport(turn))return []; // A revoked/deleted source cannot keep its cached payload.
   return [{...turn,task:node.task||undefined}];
  });
  const positions=new Map(record.turns.map((turn,index)=>[turn.id,index]));
  for(const edge of page.edges){const turn={...edge.node,id:edge.node.canonical_id},position=positions.get(turn.id);if(position===undefined){positions.set(turn.id,record.turns.length);record.turns.push(turn)}else record.turns[position]=turn}
  record.cursor=page.pageInfo.endCursor||record.cursor;record.liveOffset=refresh.liveOffset;record.reportOffset=refresh.reportOffset;
  if(!cached){record.start=page.pageInfo.startCursor||null;record.hasEarlier=!!page.pageInfo.hasPreviousPage}
  this.records.set(id,record);return this.value(record);
 }
}
