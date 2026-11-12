import {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {EditorState, Compartment} from '@codemirror/state';
import {EditorView, keymap, lineNumbers} from '@codemirror/view';
import {defaultKeymap, history, historyKeymap} from '@codemirror/commands';
import {javascript} from '@codemirror/lang-javascript';
import {Dialog, DialogContent, DialogTitle} from '../components/ui/dialog';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import WorkspaceMenu from '../components/WorkspaceMenu';
import {ApiError} from '../lib/api';
import {workflowError, workflowTemplates, UNLIMITED_WORKFLOW_LIMITS, type WorkflowDefinition, type WorkflowLimits, type JsonSchema} from '../lib/workflows';

export type WorkflowDraft = {name:string;description:string;project_id:string|null;source:string;input_schema:JsonSchema;limits:WorkflowLimits};
export type WorkflowValidation = {valid:boolean|null;unsupported?:boolean;error?:unknown;limits?:WorkflowLimits;input_schema?:JsonSchema};
type Props = {definition?:WorkflowDefinition;projectId:string|null;open:boolean;disabled?:boolean;onClose:()=>void;onValidate:(draft:WorkflowDraft)=>Promise<WorkflowValidation|null>;onSave:(draft:WorkflowDraft,revision:number|undefined,requestId:string)=>Promise<WorkflowDefinition|null>;onReload:()=>Promise<WorkflowDefinition|null>;onSaved:(definition:WorkflowDefinition)=>void};

/** The source is an async function body. The host validates it without running it. */
function SourceEditor({value,onChange,disabled}:{value:string;onChange:(value:string)=>void;disabled:boolean}){
 const host=useRef<HTMLDivElement>(null),view=useRef<EditorView|null>(null),change=useRef(onChange),editing=useRef(new Compartment()),syncing=useRef(false);
 change.current=onChange;
 useEffect(()=>{
  if(!host.current)return;
  const instance=new EditorView({parent:host.current,state:EditorState.create({doc:value,extensions:[lineNumbers(),history(),javascript(),keymap.of([...defaultKeymap,...historyKeymap]),EditorView.lineWrapping,EditorView.contentAttributes.of({'aria-label':'Workflow source','aria-describedby':'workflow-source-label',spellcheck:'false'}),editing.current.of(EditorView.editable.of(!disabled)),EditorView.updateListener.of(update=>{if(update.docChanged&&!syncing.current)change.current(update.state.doc.toString())}),EditorView.theme({'&':{backgroundColor:'var(--background)',color:'var(--foreground)'},'.cm-content':{fontFamily:'var(--font-mono)'},'.cm-gutters':{backgroundColor:'var(--background)',color:'var(--secondary)',border:'none'},'.cm-cursor':{borderLeftColor:'var(--foreground)'},'.cm-selectionBackground, &.cm-focused .cm-selectionBackground':{backgroundColor:'var(--muted)'},'.cm-scroller':{fontFamily:'var(--font-mono)'}})]})});
  instance.scrollDOM.tabIndex=0;instance.scrollDOM.setAttribute('role','region');instance.scrollDOM.setAttribute('aria-label','Workflow source viewport');
  view.current=instance;return()=>{instance.destroy();view.current=null};
 },[]);
 useEffect(()=>{const instance=view.current;if(instance&&instance.state.doc.toString()!==value){syncing.current=true;try{instance.dispatch({changes:{from:0,to:instance.state.doc.length,insert:value}})}finally{syncing.current=false}}},[value]);
 useEffect(()=>{view.current?.dispatch({effects:editing.current.reconfigure(EditorView.editable.of(!disabled))})},[disabled]);
 return <div className="workflow-source-editor" ref={host}/>;
}

export default function WorkflowEditor({definition,projectId,open,disabled=false,onClose,onValidate,onSave,onReload,onSaved}:Props){
 const initial=definition??workflowTemplates[0];
 const [name,setName]=useState(initial.name),[description,setDescription]=useState(initial.description),[source,setSource]=useState(initial.source),[schema,setSchema]=useState(JSON.stringify(initial.input_schema,null,2));
 const [revision,setRevision]=useState(definition?.revision);
 const [busy,setBusy]=useState(false),[error,setError]=useState(''),[conflict,setConflict]=useState(false),[validation,setValidation]=useState<WorkflowValidation|null>(null),[dirty,setDirty]=useState(false),[discard,setDiscard]=useState(false);
 const identity=useRef({payload:'',id:crypto.randomUUID()}),generation=useRef(0),flight=useRef(false),dialog=useRef<HTMLDivElement>(null),focusRestore=useRef<{target:HTMLElement|null;epoch:number}|null>(null),live=useRef(open&&!disabled);live.current=open&&!disabled;
 useEffect(()=>{if(!open||disabled){generation.current++;flight.current=false;setBusy(false)}},[open,disabled]);
 useEffect(()=>()=>{generation.current++},[]);
 useLayoutEffect(()=>{if(busy)return;const pending=focusRestore.current;focusRestore.current=null;const surface=dialog.current;if(!pending||pending.epoch!==generation.current||!live.current||!surface||surface.contains(document.activeElement))return;const target=pending.target?.isConnected&&!pending.target.matches(':disabled,[aria-disabled=true]')?pending.target:surface.querySelector<HTMLElement>('input,button:not(:disabled)');target?.focus({preventScroll:true})},[busy,open,disabled]);
 function changed(){setDirty(true);setValidation(null);setError('');setConflict(false)}
 function draft():WorkflowDraft{
  let input_schema:JsonSchema;try{input_schema=JSON.parse(schema)}catch{throw new Error('Input schema must be valid JSON.')}
  if(!input_schema||typeof input_schema!=='object'||Array.isArray(input_schema))throw new Error('Input schema must be a JSON object.');
  if(!name.trim())throw new Error('Enter a workflow name.');
  if(!source.trim())throw new Error('Enter JavaScript source.');
  return {name:name.trim(),description:description.trim(),project_id:definition?definition.project_id:projectId,source,input_schema,limits:{...UNLIMITED_WORKFLOW_LIMITS}};
 }
 async function act(kind:'validate'|'save'|'reload'){
  if(flight.current||!live.current)return;flight.current=true;const epoch=generation.current,focused=document.activeElement,restore=focused instanceof HTMLElement&&dialog.current?.contains(focused)?focused:null;setBusy(true);setError('');
  try{
   if(kind==='reload'){
    const value=await onReload();if(value&&epoch===generation.current){setName(value.name);setDescription(value.description);setSource(value.source);setSchema(JSON.stringify(value.input_schema,null,2));setRevision(value.revision);setConflict(false);setDirty(false);setValidation(null)}
   }else{
    const value=draft();
    if(kind==='validate'){const result=await onValidate(value);if(epoch===generation.current)setValidation(result)}
    else{const payload=JSON.stringify([value,revision]);if(identity.current.payload!==payload)identity.current={payload,id:crypto.randomUUID()};const saved=await onSave(value,revision,identity.current.id);if(saved&&epoch===generation.current){setDirty(false);onSaved(saved)}}
   }
  }catch(reason){if(epoch===generation.current){setConflict(reason instanceof ApiError&&reason.status===409);setError(reason instanceof ApiError&&reason.status===409?'A newer version was saved. Your edits are kept.':workflowError(reason))}}
  finally{if(epoch===generation.current){flight.current=false;focusRestore.current={target:restore,epoch};setBusy(false)}}
 }
 function close(){if(busy)return;if(dirty)setDiscard(true);else onClose()}
 return <><Dialog open={open} onOpenChange={value=>{if(!value)close()}}><DialogContent ref={dialog} className="workflow-editor-dialog" aria-describedby={undefined} closeDisabled={busy}>
  <header className="workflow-editor-heading"><DialogTitle>{definition?'Edit workflow':'New workflow'}</DialogTitle><WorkspaceMenu label="Workflow templates">{workflowTemplates.map(template=><button key={template.id} data-close-menu disabled={busy||disabled} onClick={()=>{setName(template.name);setDescription(template.description);setSource(template.source);setSchema(JSON.stringify(template.input_schema,null,2));changed()}}>{template.name}</button>)}</WorkspaceMenu></header>
  <form className="workflow-editor-form" onSubmit={event=>{event.preventDefault();void act('save')}}>
   <div className="workflow-editor-scroll"><label>Name<Input required maxLength={120} value={name} disabled={busy||disabled} onChange={event=>{setName(event.target.value);changed()}}/></label>
   <label>Description<textarea className="input" rows={2} maxLength={2000} value={description} disabled={busy||disabled} onChange={event=>{setDescription(event.target.value);changed()}}/></label>
   <div className="workflow-source-heading"><label id="workflow-source-label">JavaScript</label><span>Async function body</span></div><SourceEditor value={source} onChange={value=>{setSource(value);changed()}} disabled={busy||disabled}/>
   <details className="workflow-advanced"><summary>Inputs</summary><label>JSON schema<textarea className="input workflow-json-input" aria-label="Workflow input schema" rows={8} spellCheck={false} value={schema} disabled={busy||disabled} onChange={event=>{setSchema(event.target.value);changed()}}/></label></details>

   {error&&<div className="workflow-error" role="alert"><span>{error}</span>{conflict&&<Button variant="ghost" disabled={busy||disabled} onClick={()=>void act('reload')}>Load saved version</Button>}</div>}
   {validation&&<p className={validation.valid?'workflow-validation':'workflow-error'} role="status">{validation.unsupported?'Inputs checked. JavaScript validation is unavailable.':validation.valid?'Workflow is valid.':workflowError(validation.error||'Validation failed.')}</p>}
   </div><footer className="workflow-dialog-actions"><Button variant="ghost" disabled={busy||disabled} onClick={()=>void act('validate')}>{busy?'Working…':'Validate'}</Button><span className="flex-spacer"/><Button variant="ghost" disabled={busy} onClick={close}>Cancel</Button><Button type="submit" disabled={busy||disabled}>{busy?'Saving…':'Save'}</Button></footer>
  </form>
 </DialogContent></Dialog><Dialog open={discard&&open} onOpenChange={setDiscard}><DialogContent className="workflow-confirm-dialog" aria-describedby={undefined}><DialogTitle>Discard changes?</DialogTitle><div className="workflow-dialog-actions"><Button variant="ghost" onClick={()=>setDiscard(false)}>Keep editing</Button><Button variant="destructive" onClick={()=>{setDiscard(false);onClose()}}>Discard</Button></div></DialogContent></Dialog></>;
}
