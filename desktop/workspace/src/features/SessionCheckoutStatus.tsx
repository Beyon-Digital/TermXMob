import {ChevronDown,FolderGit2,GitBranch} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Collapsible,CollapsibleContent,CollapsibleTrigger} from '../components/ui/collapsible';
import type {Session} from '../lib/api';
import './session-checkout-status.css';

type CheckoutView={state:string;label:string;explanation:string;disclosure?:string};

/** Describe only the checkout state the host has actually reported. */
export function sessionCheckoutView(session:Session):CheckoutView{
 const state=session.checkout_state;
 if(state==='unavailable')return {state,label:'Checkout unavailable',explanation:session.checkout_notice||'The reported checkout is unavailable. Coding stays blocked until a valid checkout is restored or a new chat is opened.'};
 if(session.runner_id||state==='remote')return {state:'remote',label:'Runner workspace',explanation:session.checkout_notice||'Tools run on the selected runner. This host does not report a local isolated checkout.'};
 if(state==='browser')return {state,label:'Browser workspace',explanation:session.checkout_notice||'This conversation uses browser tools. No local coding checkout is reported.'};
 if(session.mode==='ask'||state==='read-only')return {state:'read-only',label:'Read-only context',explanation:session.checkout_notice||'This mode inspects the reported workspace without editing project files.'};
 if(state==='pending'&&session.checkout_policy==='auto')return {state,label:'Automatic checkout',explanation:session.checkout_notice||'A separate Git worktree will be created from committed HEAD when the first planning or coding turn starts. Uncommitted and untracked source files stay in the original folder.',disclosure:'Starts from committed HEAD. Source edits stay in the original folder and are not copied.'};
 if(state==='not-git')return {state,label:'Project folder',explanation:session.checkout_notice||'This folder is not a Git repository. No isolated Git checkout is reported. Auto requires a Git checkout.'};
 if(state==='scratch')return {state,label:'Scratch workspace',explanation:session.checkout_notice||'This chat uses a scratch folder. No isolated Git checkout is reported.'};
 if(state==='isolated'||session.worktree_id)return {state:'isolated',label:'Isolated checkout',explanation:session.checkout_notice||'This conversation is bound to the reported Git worktree.',...(session.source_dirty?{disclosure:'Uncommitted source edits were not copied and remain in the original folder.'}:{})};
 return {state:state||'unreported',label:'Working folder',explanation:session.checkout_notice||'Tools use the reported working folder. No isolated Git checkout has been reported.'};
}

export default function SessionCheckoutStatus({session,onSessionOptions}:{session:Session|null;onSessionOptions?:()=>void}){
 if(!session||!session.checkout_state&&!session.cwd&&!session.project_id&&!session.runner_id&&!session.worktree_id)return null;
 const view=sessionCheckoutView(session),isolated=view.state==='isolated';
 const rows=[['Working folder',session.cwd],['Branch',session.worktree_branch],['Source folder',session.source_cwd],['Base revision',session.worktree_base_ref],['Runner',session.runner_id]].filter((row):row is [string,string]=>!!row[1]);
 return <Collapsible className="session-checkout-context" data-checkout-state={view.state}>
  <div className="session-checkout-summary">
   <CollapsibleTrigger asChild><Button className="session-checkout-trigger" variant="ghost" size="sm" aria-label={'Workspace checkout details: '+view.label}>
    {isolated?<GitBranch size={16} aria-hidden="true"/>:<FolderGit2 size={16} aria-hidden="true"/>}<span>{view.label}</span>{isolated&&session.worktree_branch&&<code>{session.worktree_branch}</code>}<ChevronDown className="session-checkout-chevron" size={16} aria-hidden="true"/>
   </Button></CollapsibleTrigger>
   {view.disclosure&&<p className="session-checkout-disclosure">{view.disclosure}</p>}
   {view.state==='unavailable'&&<p className="session-checkout-error" role="alert">{view.explanation}</p>}
  </div>
  <CollapsibleContent className="session-checkout-details"><section aria-label="Workspace checkout details">
   {view.state!=='unavailable'&&<p>{view.explanation}</p>}
   {view.state==='pending'&&<p>Auto becomes available after the host reports a ready isolated checkout.</p>}
   {!!rows.length&&<dl>{rows.map(([label,value])=><div key={label}><dt>{label}</dt><dd><code>{value}</code></dd></div>)}</dl>}
   {view.state==='unavailable'&&onSessionOptions&&<Button variant="ghost" size="sm" onClick={onSessionOptions}>Session options</Button>}
  </section></CollapsibleContent>
 </Collapsible>;
}
