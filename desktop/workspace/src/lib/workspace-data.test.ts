import {describe,it,expect,vi,beforeEach} from 'vitest';
import {SessionCache,listSessions} from './workspace-data';
import {gql,type Task} from './api';

vi.mock('./api',()=>({gql:vi.fn()}));
beforeEach(()=>vi.resetAllMocks());
const snapshot={id:'conversation',title:'Test',engine:'internal',revision:1};
type FixtureTurn={canonical_id:string;sequence:number;prompt:string;task_id:string|null;task?:Task};
function turn(id:string,sequence:number,status='completed',report=false):FixtureTurn{
 const prompt='Goal '+id;
 return {canonical_id:id,sequence,prompt,task_id:'task-'+id,task:{id:'task-'+id,prompt,status,engine:report?'coordination':'internal',...(report?{result:'Private result '+id,coordination_report:{kind:'task.completed',label:id,source_status:'completed'}}:{})}};
}
function page(rows:FixtureTurn[],hasEarlier=false){
 return {workspace_turns:{edges:rows.map(node=>({cursor:String(node.sequence),node})),pageInfo:{hasNextPage:false,hasPreviousPage:hasEarlier,startCursor:rows.length?String(rows[0].sequence):null,endCursor:rows.length?String(rows.at(-1)!.sequence):null}}};
}
function metadata(variables:any,rows:FixtureTurn[],overrides=new Map<string,{task?:Task}|null>()){
 return {nodes:variables.ids.map((encoded:string)=>{
  const key=atob(encoded);
  if(key.startsWith('WorkspaceSessionNode:'))return {snapshot};
  const id=key.slice('WorkspaceTurnNode:'.length);
  if(overrides.has(id))return overrides.get(id);
  const row=rows.find(row=>row.canonical_id===id);
  return row?{task:row.task}:null;
 })};
}
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done});return {promise,resolve}}

describe('bounded workspace cursor data',()=>{
 it('fetches a 500 session sidebar in bounded pages',async()=>{
  vi.mocked(gql).mockImplementation(async(_query,variables)=>{const index=variables?.after?Number(variables.after):0;return {workspace_sessions:{edges:Array.from({length:100},(_,i)=>({node:{snapshot:{...snapshot,id:String(index*100+i)}}})),pageInfo:{hasNextPage:index<4,endCursor:String(index+1)}}} as any});
  expect(await listSessions()).toHaveLength(500);expect(gql).toHaveBeenCalledTimes(5);
 });

 it('opens the latest 200 of 10,000 messages and retains bounded forward and history cursors',async()=>{
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes('))return {nodes:(variables?.ids as string[]).map(encoded=>atob(encoded).startsWith('WorkspaceSessionNode:')?{snapshot}:{task:atob(encoded).endsWith('turn-9999')?{id:'latest-task',prompt:'Latest',status:'running'}:null})} as any;
   const start=query.includes('last:200')?(variables?.before?Number(variables.before)-201:9800):10000;
   const count=query.includes('last:200')?200:0;
   return page(Array.from({length:count},(_,i)=>({canonical_id:'turn-'+(start+i),sequence:start+i+1,prompt:'Message '+i,task_id:null})),start>0) as any;
  });
  const cache=new SessionCache(),first=await cache.read('conversation');
  expect(first.turns).toHaveLength(200);expect(first.turns?.[0].sequence).toBe(9801);expect(first.hasEarlier).toBe(true);expect(gql).toHaveBeenCalledTimes(2);
  vi.mocked(gql).mockClear();await cache.read('conversation');
  expect(gql).toHaveBeenCalledTimes(2);expect(vi.mocked(gql).mock.calls[1][1]?.after).toBe('10000');
  expect((vi.mocked(gql).mock.calls[0][1]?.ids as string[]).length).toBeLessThanOrEqual(64);
  expect((await cache.history('conversation')).turns).toHaveLength(400);
 });

 it('refreshes a running main root after a later child report appends',async()=>{
  const root=turn('root',1,'running'),report=turn('report',2,'completed',true);let phase=0;
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes('))return metadata(variables,[root,report]) as any;
   return page(phase===0?[root]:phase===1?[report]:[]) as any;
  });
  const cache=new SessionCache();await cache.read('conversation');phase=1;
  expect((await cache.read('conversation')).turns?.map(row=>row.id)).toEqual(['root','report']);
  root.task={...root.task!,status:'completed',result:'Consolidated both project results'};phase=2;
  const refreshed=await cache.read('conversation');
  expect(refreshed.turns?.[0].task).toMatchObject({status:'completed',result:'Consolidated both project results'});
  const requested=(vi.mocked(gql).mock.calls.at(-2)![1]?.ids as string[]).map(id=>atob(id));
  expect(requested).toContain('WorkspaceTurnNode:root');expect(requested).toContain('WorkspaceTurnNode:report');
 });

 it('revalidates older reports from multiple sources and drops nullable inaccessible nodes',async()=>{
  const first=turn('source-one-report',1,'completed',true),second=turn('source-two-report',2,'completed',true),last=turn('last',3);const rows=[first,second,last];let initial=true;
  const unavailable={...first.task!,result:'Source conversation is unavailable',coordination_report:{...first.task!.coordination_report!,label:'Unavailable source',source_status:'unavailable'}};
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes('))return metadata(variables,rows,initial?undefined:new Map<string,{task?:Task}|null>([[first.canonical_id,{task:unavailable}],[second.canonical_id,null]])) as any;
   const result=page(initial?rows:[]);initial=false;return result as any;
  });
  const cache=new SessionCache();expect(JSON.stringify(await cache.read('conversation'))).toContain('Private result source-two-report');
  const refreshed=await cache.read('conversation');
  expect(refreshed.turns?.map(row=>row.id)).toEqual(['source-one-report','last']);
  expect(refreshed.turns?.[0].task?.coordination_report?.source_status).toBe('unavailable');
  expect(JSON.stringify(refreshed)).not.toContain('Private result');
 });

 it('bounds metadata and fairly rotates reports and queued turns while prioritizing the active root',async()=>{
  const queued=Array.from({length:75},(_,index)=>({...turn('queued-'+index,index+1),task:undefined}));
  const root=turn('active-root',76,'running'),reports=Array.from({length:85},(_,index)=>turn('report-'+index,index+77,'completed',true));
  const rows=[...queued,root,...reports],requests:string[][]=[];let initial=true;
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes(')){requests.push((variables?.ids as string[]).map(id=>atob(id)));return metadata(variables,rows) as any}
   const result=page(initial?rows:[]);initial=false;return result as any;
  });
  const cache=new SessionCache();await cache.read('conversation');
  for(let index=0;index<3;index++)await cache.read('conversation');
  for(const ids of requests.slice(1)){expect(ids.length).toBeLessThanOrEqual(64);expect(ids).toContain('WorkspaceTurnNode:active-root')}
  const visited=new Set(requests.flat());
  for(const row of rows)expect(visited.has('WorkspaceTurnNode:'+row.canonical_id)).toBe(true);
 });

 it('late binds an older queued user turn even when a report is last',async()=>{
  const queued:FixtureTurn={...turn('queued',1),task:undefined},report=turn('report',2,'completed',true),rows=[queued,report];let initial=true;
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes('))return metadata(variables,rows) as any;
   const result=page(initial?rows:[]);initial=false;return result as any;
  });
  const cache=new SessionCache();await cache.read('conversation');queued.task=turn('queued',1,'running').task;
  expect((await cache.read('conversation')).turns?.[0]).toMatchObject({id:'queued',prompt:'Goal queued',task:{status:'running'}});
 });

 it('deduplicates replayed forward-page turns by canonical identity',async()=>{
  const root=turn('root',1,'running'),report=turn('report',2,'completed',true);let initial=true;
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes('))return metadata(variables,[root,report]) as any;
   const result=page(initial?[root]:[{...root,task:{...root.task!,status:'completed'}},report,report]);initial=false;return result as any;
  });
  const cache=new SessionCache();await cache.read('conversation');const refreshed=await cache.read('conversation');
  expect(refreshed.turns?.map(row=>row.id)).toEqual(['root','report']);expect(refreshed.turns?.[0].task?.status).toBe('completed');
 });

 it('coalesces history requests and preserves history arriving during a forward poll',async()=>{
  const root=turn('root',201,'running'),older=turn('older',1),report=turn('report',202,'completed',true),rows=[root,older,report];
  const historyResult=deferred<any>(),historyStarted=deferred<void>(),pollResult=deferred<any>(),pollStarted=deferred<void>();let initial=true,forward=0,historyQueries=0;
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes('))return metadata(variables,rows) as any;
   if(variables?.before){historyQueries++;historyStarted.resolve();return historyResult.promise}
   if(initial){initial=false;return page([root],true) as any}
   forward++;if(forward===2){pollStarted.resolve();return pollResult.promise}
   return page([]) as any;
  });
  const cache=new SessionCache();await cache.read('conversation');
  const history=cache.history('conversation');expect(cache.history('conversation')).toBe(history);await historyStarted.promise;
  const poll=cache.read('conversation');await pollStarted.promise;
  historyResult.resolve(page([older]));await history;pollResult.resolve(page([report]));
  expect((await poll).turns?.map(row=>row.id)).toEqual(['older','root','report']);
  expect((await cache.history('conversation')).hasEarlier).toBe(false);expect(historyQueries).toBe(1);
 });

 it('invalidates in-flight reads at signout without resurrecting the previous workspace cache',async()=>{
  const pending=deferred<any>();vi.mocked(gql).mockImplementationOnce(()=>pending.promise);
  const cache=new SessionCache(),old=cache.read('conversation');cache.clear();pending.resolve({nodes:[{snapshot}]});
  await expect(old).rejects.toThrow('authenticated workspace changed');
  vi.mocked(gql).mockImplementation(async(query)=>query.includes('nodes(')?{nodes:[{snapshot:{...snapshot,title:'Fresh workspace'}}]} as any:page([]) as any);
  expect((await cache.read('conversation')).title).toBe('Fresh workspace');
 });

 it('rejects late history results after the authenticated workspace changes',async()=>{
  const root=turn('root',201),pending=deferred<any>(),started=deferred<void>();let initial=true;
  vi.mocked(gql).mockImplementation(async(query,variables)=>{
   if(query.includes('nodes('))return metadata(variables,[root]) as any;
   if(variables?.before){started.resolve();return pending.promise}
   const result=page(initial?[root]:[],true);initial=false;return result as any;
  });
  const cache=new SessionCache();await cache.read('conversation');const history=cache.history('conversation');await started.promise;
  cache.clear();pending.resolve(page([turn('old-workspace',1)]));await expect(history).rejects.toThrow('authenticated workspace changed');
 });
});
