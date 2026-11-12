import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { expectNoAxeViolations } from '../test-axe';
import { AgentPresetEditor } from './AgentPresetEditor';
vi.mock('../lib/api',()=>({request:vi.fn(async()=>({data:{mcp_connections:[]}})),json:(method:string,value:unknown)=>({method,body:JSON.stringify(value)})}));
afterEach(cleanup);
const preset={id:'agent.review',name:'Review',instructions:'Inspect',engine:'internal',tools_mode:'explicit',tools:['read_file'],toolsets:['files'],deny_tools:['run_shell'],file_revision:'rev-3',limits:{max_steps:12},approval_mode:'standard',sandbox_profile:'agent',enabled:true};
it('saves explicit tool edits and clears legacy task quotas against the inspected revision without run identifiers', async()=>{
 const save=vi.fn().mockResolvedValue({});render(<AgentPresetEditor preset={preset} onSave={save} onCancel={vi.fn()}/>);
 fireEvent.change(screen.getByLabelText('Allowed tool IDs (one per line)'),{target:{value:'read_file\nsearch_files\nread_file'}});
 expect(screen.queryByText('Run budget')).not.toBeInTheDocument();expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
 expect(save).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'Save preset changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(save.mock.calls[0][0]).toMatchObject({revision:'rev-3',tools:['read_file','search_files'],tools_mode:'explicit',limits:{},deny_tools:['run_shell']});
 expect(save.mock.calls[0][0]).not.toHaveProperty('task_id');expect(save.mock.calls[0][0]).not.toHaveProperty('session_id');
});
it('keeps tools as a list that the checkboxes and the exact-name field both edit', async()=>{
 const save=vi.fn().mockResolvedValue({});render(<AgentPresetEditor preset={preset} onSave={save}/>);
 expect(screen.getByRole('checkbox',{name:'Read project files'})).toBeChecked();
 fireEvent.click(screen.getByRole('checkbox',{name:'Run shell commands'}));
 expect(screen.getByLabelText('Allowed tool IDs (one per line)')).toHaveValue('read_file\nrun_shell');
 fireEvent.change(screen.getByLabelText('Allowed tool IDs (one per line)'),{target:{value:'read_file\n'}});
 expect(screen.getByLabelText('Allowed tool IDs (one per line)')).toHaveValue('read_file\n');
 expect(screen.getByRole('checkbox',{name:'Run shell commands'})).not.toBeChecked();
 fireEvent.click(screen.getByRole('button',{name:'Save preset changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());expect(save.mock.calls[0][0].tools).toEqual(['read_file']);
});
it('changes inherited mode deliberately without attaching an explicit list', async()=>{
 const save=vi.fn().mockResolvedValue({});render(<AgentPresetEditor preset={preset} onSave={save}/>);
 fireEvent.click(screen.getByLabelText('Inherit all host-authorized tools'));fireEvent.click(screen.getByRole('button',{name:'Save preset changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());expect(save.mock.calls[0][0]).toMatchObject({tools_mode:'all',revision:'rev-3'});expect(save.mock.calls[0][0]).not.toHaveProperty('tools');
});
it('creates a preset without serializing application task quota defaults', async()=>{
 const save=vi.fn().mockResolvedValue({});render(<AgentPresetEditor onSave={save}/>);
 fireEvent.change(screen.getByLabelText(/^Name/),{target:{value:'Unbounded reviewer'}});
 expect(screen.queryByText('Run budget')).not.toBeInTheDocument();
 expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Save preset'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(save.mock.calls[0][0]).toMatchObject({name:'Unbounded reviewer',limits:{},approval_mode:'standard',sandbox_profile:'agent'});
 expect(save.mock.calls[0][0].limits).toEqual({});
});
it('requires a name and labels each field once', async()=>{
 const save=vi.fn();const {container}=render(<AgentPresetEditor onSave={save}/>);
 fireEvent.click(screen.getByRole('button',{name:'Save preset'}));
 expect(screen.getByLabelText(/^Name/)).toHaveAccessibleDescription('Enter a name for this preset.');expect(save).not.toHaveBeenCalled();
 expect(container.querySelectorAll('[aria-label="Name"]')).toHaveLength(0);
 await expectNoAxeViolations(container);
});
it('retains a conflict draft and ignores a duplicate submit while saving', async()=>{
 let reject!:(error:Error)=>void;const save=vi.fn(()=>new Promise((_,r)=>{reject=r}));render(<AgentPresetEditor preset={preset} onSave={save}/>);
 fireEvent.change(screen.getByLabelText('Instructions'),{target:{value:'Unsaved new instructions'}});
 const form=screen.getByRole('form');fireEvent.submit(form);fireEvent.submit(form);expect(save).toHaveBeenCalledOnce();reject(new Error('Revision conflict'));
 expect((await screen.findByText(/Your draft is kept/)).closest('[role=alert]')).not.toBeNull();expect(screen.getByLabelText('Instructions')).toHaveValue('Unsaved new instructions');
});
