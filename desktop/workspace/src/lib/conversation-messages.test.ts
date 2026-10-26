import {expect,it} from 'vitest';
import {conversationMessages} from './conversation-messages';
import type {Turn} from './api';
it('keeps source-labeled child reports in history without an invented user prompt or changed answer id',()=>{const report={id:'notification-ledger',prompt:'',status:'completed',engine:'coordination',result:'Researcher: failed\n\nSource error',coordination_report:{source_session_id:'child-chat',source_task_id:'child-task',kind:'task.failed',label:'Researcher',source_status:'failed'}};const turns:Turn[]=[{id:'user-turn',prompt:'Research this',sequence:1,task:{id:'main-task',prompt:'Research this',status:'completed',result:'Delegated the research'}},{id:'report-turn',prompt:'',sequence:2,task:report}];const rows=conversationMessages(turns);expect(rows.map(row=>[row.id,row.role])).toEqual([['user-turn','user'],['user-turn:answer','assistant'],['report-turn:answer','assistant']]);expect(rows.at(-1)?.task).toBe(report);expect(rows.at(-1)?.text).toBe(report.result)});
it('does not treat an unmarked blank prompt or ordinary completed task as a system report',()=>{const rows=conversationMessages([{id:'ordinary',prompt:'',sequence:1,task:{id:'task',status:'completed',prompt:'',result:'Output'}}]);expect(rows.map(row=>row.role)).toEqual(['user','assistant'])});

it('keeps the latest real failure available when a later child report is delivered',async()=>{
 const {latestExecutionTask}=await import('./conversation-messages');
 const failed={id:'failed-main',prompt:'Original task',status:'failed',error:'Review this failure'};
 const turns=[{id:'original',prompt:'Original task',sequence:1,task:failed},{id:'report',prompt:'',sequence:2,task:{id:'report-task',prompt:'',status:'completed',engine:'coordination'}}];
 expect(latestExecutionTask(turns)).toBe(failed);
 expect(latestExecutionTask([turns[1]])).toBeUndefined();
});
