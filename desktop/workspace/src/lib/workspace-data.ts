import {gql,type Session,type Turn} from './api';
type Connection<T>={edges:{cursor:string;node:T}[];pageInfo:{hasNextPage:boolean;hasPreviousPage?:boolean;startCursor?:string|null;endCursor:string|null}};
type TurnNode=Omit<Turn,'id'>&{canonical_id:string};
const fields='edges{cursor node{id canonical_id prompt task_id sequence task}}pageInfo{hasNextPage hasPreviousPage startCursor endCursor}';
export async function listSessions(archived=false):Promise<Session[]>{
 const result:Session[]=[];let after:string|null=null;
 do{const page:{workspace_sessions:Connection<{snapshot:Session}>}=await gql('query($after:String,$archived:Boolean!){workspace_sessions(first:100,after:$after,archived:$archived){edges{node{id snapshot}}pageInfo{hasNextPage endCursor}}}',{after,archived});result.push(...page.workspace_sessions.edges.map(edge=>edge.node.snapshot));after=page.workspace_sessions.pageInfo.hasNextPage?page.workspace_sessions.pageInfo.endCursor:null}while(after);
 return result;
}
type Cached={snapshot:Session;turns:Turn[];cursor:string|null;start:string|null;hasEarlier:boolean};
export class SessionCache{
 private flights=new Map<string,Promise<Session>>();private records=new Map<string,Cached>();
 clear(){this.records.clear()}
 read(id:string):Promise<Session>{const previous=this.flights.get(id);if(previous)return previous;const flight=this.fetch(id).finally(()=>this.flights.delete(id));this.flights.set(id,flight);return flight}
 async history(id:string):Promise<Session>{await this.read(id);const record=this.records.get(id)!;if(!record.hasEarlier)return this.value(record);const result=await gql<{workspace_turns:Connection<TurnNode>}>('query($id:String!,$before:String){workspace_turns(session_id:$id,last:200,before:$before){'+fields+'}}',{id,before:record.start});const page=result.workspace_turns;const known=new Set(record.turns.map(turn=>turn.id));record.turns=[...page.edges.map(edge=>({...edge.node,id:edge.node.canonical_id})).filter(turn=>!known.has(turn.id)),...record.turns];record.start=page.pageInfo.startCursor||record.start;record.hasEarlier=!!page.pageInfo.hasPreviousPage;return this.value(record)}
 private value(record:Cached):Session{return {...record.snapshot,turns:[...record.turns],hasEarlier:record.hasEarlier}}
 private async fetch(id:string):Promise<Session>{
  const key=btoa('WorkspaceSessionNode:'+id),cached=this.records.get(id);
  const ids=[key,...(cached?.turns.at(-1)?[btoa('WorkspaceTurnNode:'+cached.turns.at(-1)!.id)]:[])];
  const meta=await gql<{nodes:({snapshot?:Session;task?:Turn['task']}|null)[]}>('query($ids:[ID!]!){nodes(ids:$ids){id ... on WorkspaceSessionNode{snapshot} ... on WorkspaceTurnNode{task}}}',{ids});
  if(!meta.nodes[0]?.snapshot)throw new Error('Conversation is unavailable');
  const record=cached||{snapshot:meta.nodes[0].snapshot,turns:[],cursor:null,start:null,hasEarlier:false};record.snapshot=meta.nodes[0].snapshot;
  if(cached?.turns.length&&meta.nodes[1])record.turns=record.turns.map((turn,index)=>index===record.turns.length-1?{...turn,task:meta.nodes[1]!.task}:turn);
  const query=cached?'query($id:String!,$after:String){workspace_turns(session_id:$id,first:200,after:$after){'+fields+'}}':'query($id:String!){workspace_turns(session_id:$id,last:200){'+fields+'}}';
  const result=await gql<{workspace_turns:Connection<TurnNode>}>(query,{id,...(cached?{after:record.cursor}:{})});const page=result.workspace_turns;
  record.turns.push(...page.edges.map(edge=>({...edge.node,id:edge.node.canonical_id})));record.cursor=page.pageInfo.endCursor||record.cursor;
  if(!cached){record.start=page.pageInfo.startCursor||null;record.hasEarlier=!!page.pageInfo.hasPreviousPage}
  this.records.set(id,record);return this.value(record);
 }
}
