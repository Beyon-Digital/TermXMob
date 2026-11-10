import {describe,expect,it} from 'vitest';
import {agentNotes,autoBlockedReason,autoLabel,meterContextLabel,formatCost,formatTokens,planBlockedReason,usageLabel} from './agentic';

describe('agent meters',()=>{
 it('shows Unknown for values the host could not provide, never zero',()=>{
  expect(formatTokens(null)).toBe('Unknown');expect(formatCost(null)).toBe('Unknown');expect(formatCost(0.0312)).toBe('$0.0312');expect(formatCost(5)).toBe('$5.00');
  expect(usageLabel({tokens:12400,cost_usd:null})).toBe('12.4K tokens · Unknown');
 });
 it('says when the model window is unknown and which window is assumed',()=>{
  expect(meterContextLabel({used:12000,window:128000,limit:96000,window_known:true})).toBe('Context 12K of 128K');
  expect(meterContextLabel({used:null,window:null,limit:120000,window_known:false})).toBe('Context Unknown of an unknown window (120K assumed)');
 });
 it('summarises Auto use',()=>{
  expect(autoLabel({enabled:true,ceilings:{tool_calls:100},used:{tool_calls:7}})).toBe('Auto 7 of 100 calls');
  expect(autoLabel({enabled:false,ceilings:null,used:{}})).toBe('Auto off');
 });
});
describe('mode availability',()=>{
 it('explains what to do when Auto or Plan cannot be used',()=>{
  expect(autoBlockedReason({engine:'internal',worktree_id:'wt'})).toBe('');
  expect(autoBlockedReason({engine:'internal',worktree_id:null})).toMatch(/Delivery under Branches & parallel worktrees.*Project actions, Session checkout/);
  expect(autoBlockedReason({engine:'claude',worktree_id:'wt'})).toMatch(/built-in agent/);
  expect(autoBlockedReason({engine:'internal',worktree_id:'wt',runner_id:'runner-1'})).toMatch(/local host in Session options/);
  expect(planBlockedReason({engine:'claude'})).toMatch(/plans on its own/);expect(planBlockedReason({engine:'internal'})).toBe('');
 });
});
describe('agentNotes',()=>{
 const event=(sequence:number,type:string,payload:Record<string,unknown>={})=>({sequence,type,payload});
 it('collapses a run of auto decisions and reports revisions and ceilings',()=>{
  const notes=agentNotes([event(1,'auto.decision',{tool:'read_file'}),event(2,'auto.decision',{tool:'read_file'}),event(3,'auto.decision',{tool:'edit_file'}),event(4,'plan.revised',{feedback:'Skip the migration'}),event(5,'auto.decision',{tool:'run_check'}),event(6,'auto.ceiling',{ceiling:'tool_calls',limit:100,used:100}),event(7,'tool.started')]);
  expect(notes.map(note=>note.text)).toEqual(['Auto approved 3 calls: read_file, edit_file','You asked for changes: Skip the migration','Auto approved 1 call: run_check','Auto paused at its tool-call limit (100 of 100). Approve more budget in the request below to continue.']);
  expect(new Set(notes.map(note=>note.key)).size).toBe(4);
 });
 it('returns nothing for ordinary events',()=>{expect(agentNotes([event(1,'tool.started')])).toEqual([]);expect(agentNotes()).toEqual([])});
});
describe('agentNotes feedback',()=>{
 it('shortens long feedback in the transcript line',()=>{
  const [note]=agentNotes([{type:'plan.revised',sequence:1,payload:{feedback:'x'.repeat(500)}}]);
  expect(note.text).toBe('You asked for changes: '+'x'.repeat(200)+'…');
 });
});
