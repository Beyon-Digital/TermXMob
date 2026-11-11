import {afterEach, beforeEach, expect, it, vi} from 'vitest';
import {act, cleanup, fireEvent, render, screen, waitFor, within} from '@testing-library/react';
import WorkflowEditor, {type WorkflowDraft} from './WorkflowEditor';
import {ApiError} from '../lib/api';
import {workflowTemplates, type WorkflowDefinition} from '../lib/workflows';

const definition:WorkflowDefinition={...workflowTemplates[2],id:'flow',revision:3,language:'javascript',project_id:'p1',archived:false,limits:{max_agents:4,concurrency:2,timeout_s:90},created_at:1,updated_at:2};
const props=()=>({definition,projectId:'p1',open:true,onClose:vi.fn(),onSaved:vi.fn(),onReload:vi.fn(async()=>({...definition,revision:4,name:'Saved version'})),onValidate:vi.fn(async()=>({valid:true})),onSave:vi.fn(async(_draft:WorkflowDraft,_revision:number|undefined,_requestId:string)=>definition)});
beforeEach(()=>{vi.clearAllMocks()});afterEach(cleanup);

it('edits JavaScript with folded schema and limits and preserves CAS conflicts',async()=>{
 const callbacks=props();callbacks.onSave.mockRejectedValue(new ApiError(409,'changed'));
 render(<WorkflowEditor {...callbacks}/>);
 expect(screen.getByLabelText('Workflow source')).toHaveTextContent('Summarize values');
 expect(screen.getByRole('region',{name:'Workflow source viewport'})).toHaveAttribute('tabindex','0');
 expect(screen.getByText('Inputs').closest('details')).not.toHaveAttribute('open');
 expect(screen.queryByText('Limits')).not.toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('Name'),{target:{value:'Unsaved script'}});
 fireEvent.submit(screen.getByRole('button',{name:'Save'}).closest('form')!);
 expect(await screen.findByRole('alert')).toHaveTextContent('Your edits are kept');
 expect(screen.getByLabelText('Name')).toHaveValue('Unsaved script');
 expect(callbacks.onSave.mock.calls[0][1]).toBe(3);
 expect(callbacks.onSave.mock.calls[0][0].source).toBe(definition.source);
 expect(callbacks.onSave.mock.calls[0][0].limits).toEqual({max_agents:null,concurrency:null,timeout_s:null});
 fireEvent.click(screen.getByRole('button',{name:'Save'}));
 await waitFor(()=>expect(callbacks.onSave).toHaveBeenCalledTimes(2));
 expect(callbacks.onSave.mock.calls[1][1]).toBe(3);
 expect(callbacks.onSaved).not.toHaveBeenCalled();
});

it('reports unsupported syntax validation honestly without claiming success',async()=>{
 const callbacks=props();callbacks.onValidate.mockResolvedValue({valid:false,unsupported:true,error:{code:'runtime_unavailable',message:'Runtime unavailable'}} as never);
 render(<WorkflowEditor {...callbacks}/>);
 fireEvent.click(screen.getByRole('button',{name:'Validate'}));
 expect(await screen.findByRole('status')).toHaveTextContent('Inputs checked. JavaScript validation is unavailable.');
 expect(screen.queryByText('Workflow is valid.')).not.toBeInTheDocument();
 expect(callbacks.onSave).not.toHaveBeenCalled();
});

it('sends one pending save and reuses its request ID after a lost response',async()=>{
 const callbacks=props();let reject!:(error:unknown)=>void;
 callbacks.onSave.mockImplementationOnce(()=>new Promise((_resolve,no)=>{reject=no}));
 render(<WorkflowEditor {...callbacks}/>);
 const form=screen.getByRole('button',{name:'Save'}).closest('form')!;
 fireEvent.submit(form);fireEvent.submit(form);
 expect(callbacks.onSave).toHaveBeenCalledOnce();
 const id=callbacks.onSave.mock.calls[0][2];
 await act(async()=>reject(new Error('Connection lost')));
 expect(await screen.findByRole('alert')).toHaveTextContent('Connection lost');
 fireEvent.submit(form);
 await waitFor(()=>expect(callbacks.onSave).toHaveBeenCalledTimes(2));
 expect(callbacks.onSave.mock.calls[1][2]).toBe(id);
 expect(callbacks.onSaved).toHaveBeenCalledOnce();
});

it('keeps unsaved edits through a hidden child-chat navigation and offers explicit discard',async()=>{
 const callbacks=props(),view=render(<WorkflowEditor {...callbacks}/>);
 fireEvent.change(screen.getByLabelText('Name'),{target:{value:'Draft kept'}});
 view.rerender(<WorkflowEditor {...callbacks} open={false} disabled/>);
 view.rerender(<WorkflowEditor {...callbacks}/>);
 expect(screen.getByLabelText('Name')).toHaveValue('Draft kept');
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 const discard=await screen.findByRole('dialog',{name:'Discard changes?'});
 fireEvent.click(within(discard).getByRole('button',{name:'Keep editing'}));
 expect(callbacks.onClose).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Cancel'}));
 fireEvent.click(within(await screen.findByRole('dialog',{name:'Discard changes?'})).getByRole('button',{name:'Discard'}));
 expect(callbacks.onClose).toHaveBeenCalledOnce();
});

it('ignores a save response after the editor becomes unavailable',async()=>{
 const callbacks=props();let release!:(value:WorkflowDefinition)=>void;
 callbacks.onSave.mockImplementation(()=>new Promise(resolve=>{release=resolve}));
 const view=render(<WorkflowEditor {...callbacks}/>);
 fireEvent.click(screen.getByRole('button',{name:'Save'}));
 view.rerender(<WorkflowEditor {...callbacks} open={false} disabled/>);
 await act(async()=>release(definition));
 expect(callbacks.onSaved).not.toHaveBeenCalled();
});


it.each([null,'p1'])('preserves the saved project boundary %s when editing through another project filter',async project_id=>{
 const callbacks=props();render(<WorkflowEditor {...callbacks} definition={{...definition,project_id}} projectId="p2"/>);
 fireEvent.change(screen.getByLabelText('Name'),{target:{value:'Renamed without moving'}});
 fireEvent.click(screen.getByRole('button',{name:'Save'}));
 await waitFor(()=>expect(callbacks.onSave).toHaveBeenCalledOnce());
 expect(callbacks.onSave.mock.calls[0][0]).toMatchObject({name:'Renamed without moving',project_id});
 expect(callbacks.onSave.mock.calls[0][1]).toBe(definition.revision);
});


it.each(['Validate','Save'] as const)('restores focus lost by the disabled %s action after an asynchronous error',async action=>{
 const callbacks=props();let reject!:(reason:unknown)=>void;
 if(action==='Validate')callbacks.onValidate.mockImplementation(()=>new Promise((_yes,no)=>{reject=no}));
 else callbacks.onSave.mockImplementation(()=>new Promise((_yes,no)=>{reject=no}));
 render(<WorkflowEditor {...callbacks}/>);
 const trigger=screen.getByRole('button',{name:action});trigger.focus();fireEvent.click(trigger);
 expect(trigger).toBeDisabled();
 // jsdom keeps disabled buttons active. Model the browser's lost-focus state until restoration.
 let lost=true;const nativeFocus=trigger.focus.bind(trigger),activeGetter=Object.getOwnPropertyDescriptor(Document.prototype,'activeElement')!.get!;
 const active=vi.spyOn(document,'activeElement','get').mockImplementation(()=>lost?document.body:activeGetter.call(document));
 const focused=vi.spyOn(trigger,'focus').mockImplementation(options=>{lost=false;nativeFocus(options)});
 try{
  await act(async()=>reject(new Error(action==='Validate'?'Unexpected token in source':'Save failed')));
  await waitFor(()=>expect(focused).toHaveBeenCalledWith({preventScroll:true}));
  expect(trigger).toHaveFocus();expect(trigger).not.toBeDisabled();
  expect(screen.getByRole('dialog')).toContainElement(document.activeElement as HTMLElement);
 }finally{focused.mockRestore();active.mockRestore()}
});

it('leaves intentional focus inside the editor in place when validation finishes',async()=>{
 const callbacks=props();let release!:(value:{valid:boolean})=>void;
 callbacks.onValidate.mockImplementation(()=>new Promise(resolve=>{release=resolve}));
 render(<WorkflowEditor {...callbacks}/>);
 const validate=screen.getByRole('button',{name:'Validate'});validate.focus();fireEvent.click(validate);
 const menu=screen.getByRole('button',{name:'Workflow templates'});menu.focus();
 await act(async()=>release({valid:true}));
 await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('Workflow is valid.'));
 await new Promise(resolve=>requestAnimationFrame(resolve));
 expect(menu).toHaveFocus();
});
