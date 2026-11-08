import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,render,screen} from '@testing-library/react';
import Chat from './Chat';
import {draftSubmissionOwners,draftWrites,sessionDrafts,submittingDrafts} from '../lib/drafts';
import {gql,request,type Session,type Provider} from '../lib/api';

vi.mock('../lib/api',()=>({gql:vi.fn(),request:vi.fn(),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(()=>{cleanup();sessionDrafts.clear();draftWrites.clear();submittingDrafts.clear();draftSubmissionOwners.clear();vi.clearAllMocks()});
const provider:Provider={id:'fixture-account',name:'Fixture account',model:'fixture-model',secret_configured:true,capabilities:['functions']};
const props={engines:[],providers:[provider],onSelect:vi.fn(),onRefresh:vi.fn(),onError:vi.fn()};
const session=(running=false):Session=>({id:'s1',title:'s1',engine:'internal',provider_id:provider.id,model:'fixture-model',mode:'agent',pinned:false,archived:false,draft_text:'',revision:1,updated_at:1,scroll:0,worktree_id:'wt',
 turns:running?[{id:'turn',prompt:'Go',sequence:1,task:{id:'task-1',prompt:'Go',status:'running',engine:'internal',events:[]}}]:[]});
function host(row:Session){
 vi.mocked(gql).mockImplementation((async()=>({engine_models:[]})) as never);
 vi.mocked(request).mockImplementation((async(path:string)=>path==='/api/runners'?[]:path.includes('runner-agents')?{accounts:[]}:path.endsWith('/queue')?{items:[]}:{...row}) as never);
}
const css=readFileSync(join(process.cwd(),'src/features/chat-composer.css'),'utf8');
const narrow=css.slice(css.indexOf('@container(max-width:540px)'));

// jsdom does not run the cascade, so geometry is measured in a real browser (320 to 430px wide, 44px targets, Auto chip and live voice panel).
// These checks guard the structure that layout relies on: every toolbar control is a direct child with a stable class, and the narrow rules name them.
it('gives every toolbar control a stable class and keeps Send a direct child of the toolbar',()=>{
 const row=session();host(row);render(<Chat session={row} {...props}/>);
 const bar=document.querySelector('.composer-controls')!;
 const direct=(name:string)=>[...bar.children].find(child=>child.getAttribute('aria-label')===name);
 expect(direct('Send message')?.classList.contains('composer-send')).toBe(true);
 expect(direct('Session options')?.classList.contains('composer-session-options')).toBe(true);
 expect(direct('Attach image')?.classList.contains('composer-attach')).toBe(true);
 expect(direct('Start dictation')?.classList.contains('composer-mic')).toBe(true);
 expect(direct('Live voice')?.classList.contains('live-voice-button')).toBe(true);
 expect(direct('Model and effort')?.classList.contains('composer-model-chip')).toBe(true);
 expect(bar.querySelector(':scope>.mode-picker>.composer-mode-chip')).not.toBeNull();
 expect(bar.querySelector(':scope>.flex-spacer')).not.toBeNull();
});
it('puts the running-task actions on their own class so the narrow layout can keep Send on the first row',()=>{
 const row=session(true);host(row);render(<Chat session={row} {...props}/>);
 expect(screen.getByRole('button',{name:'Running task actions'}).classList.contains('composer-running-actions')).toBe(true);
 expect(screen.getByRole('button',{name:'Stop task'}).classList.contains('composer-send')).toBe(true);
});
it('wraps the toolbar on narrow panes instead of letting it overflow, with Send in the first row and the rest in the second',()=>{
 expect(css).toMatch(/@container\(max-width:640px\)\{[^}]*flex-wrap:wrap/);
 expect(narrow).toContain('.composer-controls::after{content:\'\';order:7;flex:0 0 100%;height:0}');
 const order=(selector:string)=>Number(new RegExp(selector.replace(/[.>]/g,m=>'\\'+m)+'\\{[^}]*?order:(\\d+)').exec(narrow)?.[1]);
 const first=['composer-model-chip.session-model-chip','flex-spacer','composer-queue','composer-running-actions','composer-session-options','composer-send'].map(name=>order('>.'+name));
 const second=['composer-attach','composer-mic','live-voice-button','mode-picker'].map(name=>order('>.'+name));
 expect(first.every(Number.isFinite)).toBe(true);
 expect(second.every(Number.isFinite)).toBe(true);
 expect(Math.max(...first)).toBeLessThan(7);
 expect(Math.min(...second)).toBeGreaterThan(7);
 expect(first).toEqual([...first].sort((a,b)=>a-b));
 expect(order('>.composer-send')).toBe(6);
});
it('moves the model to the second row while a task runs, and lets the mode and model chips shrink to an ellipsis',()=>{
 expect(narrow).toMatch(/:has\(>\.composer-queue,>\.composer-running-actions\)>\.composer-model-chip\.session-model-chip\{order:12/);
 expect(narrow).toMatch(/\.composer-mode-chip>span\{[^}]*text-overflow:ellipsis/);
 expect(narrow).toMatch(/\.mode-picker\{[^}]*min-width:0/);
});
it('outranks the coding-session toolbar rules that forced a single non-wrapping row',()=>{
 // chat-session.css sets `.chat-region.coding-session .composer-controls{flex-wrap:nowrap}` (three classes). The narrow rules use four or more.
 expect(readFileSync(join(process.cwd(),'src/features/chat-session.css'),'utf8')).toContain('.chat-region.coding-session .composer-controls{flex-wrap:nowrap');
 expect(narrow).toContain('.chat-region.chat-region .composer-container .composer-controls{row-gap:2px}');
 expect(css).toContain('.chat-region.chat-region .composer-container .composer-controls{flex-wrap:wrap}');
});
it('leaves the wide layout alone: the narrow rules only apply inside container queries', ()=>{
 expect(css.slice(0,css.indexOf('/* Narrow chat panes'))).not.toContain('.composer-attach');
 expect(css.slice(css.indexOf('/* Narrow chat panes')).split('\n').filter(line=>line.includes('.composer-controls')&&!line.startsWith(' ')&&!line.startsWith('@')&&!line.startsWith('}')&&!line.startsWith('/*'))).toEqual([]);
});
