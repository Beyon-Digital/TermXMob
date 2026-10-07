import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AgentPresetEditor } from './AgentPresetEditor';
afterEach(cleanup);
const preset={id:'agent.review',name:'Review',instructions:'Inspect',engine:'internal',tools_mode:'explicit',tools:['read_file'],toolsets:['files'],deny_tools:['run_shell'],file_revision:'rev-3',limits:{max_steps:12},approval_mode:'standard',sandbox_profile:'agent',enabled:true};
it('saves explicit tool and budget edits against the inspected revision without run identifiers', async()=>{
 const save=vi.fn().mockResolvedValue({});render(<AgentPresetEditor preset={preset} onSave={save} onCancel={vi.fn()}/>);
 fireEvent.change(screen.getByLabelText('Allowed tool IDs (one per line)'),{target:{value:'read_file\nsearch_files\nread_file'}});
 fireEvent.change(screen.getByLabelText('Maximum steps'),{target:{value:'9'}});
 expect(save).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'Save preset changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(save.mock.calls[0][0]).toMatchObject({revision:'rev-3',tools:['read_file','search_files'],tools_mode:'explicit',limits:{max_steps:9},deny_tools:['run_shell']});
 expect(save.mock.calls[0][0]).not.toHaveProperty('task_id');expect(save.mock.calls[0][0]).not.toHaveProperty('session_id');
});
it('changes inherited mode deliberately without attaching an explicit list', async()=>{
 const save=vi.fn().mockResolvedValue({});render(<AgentPresetEditor preset={preset} onSave={save}/>);
 fireEvent.click(screen.getByLabelText('Inherit all host-authorized tools'));fireEvent.click(screen.getByRole('button',{name:'Save preset changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());expect(save.mock.calls[0][0]).toMatchObject({tools_mode:'all',revision:'rev-3'});expect(save.mock.calls[0][0]).not.toHaveProperty('tools');
});
it('retains a conflict draft and prevents invalid budgets or pending duplicate saves', async()=>{
 let reject!:(error:Error)=>void;const save=vi.fn(()=>new Promise((_,r)=>{reject=r}));render(<AgentPresetEditor preset={preset} onSave={save}/>);
 fireEvent.change(screen.getByLabelText('Parallel subagents'),{target:{value:'9'}});expect(screen.getByRole('button',{name:'Save preset changes'})).toBeDisabled();
 fireEvent.change(screen.getByLabelText('Parallel subagents'),{target:{value:'2'}});fireEvent.change(screen.getByLabelText('Instructions'),{target:{value:'Unsaved new instructions'}});
 const form=screen.getByRole('form');fireEvent.submit(form);fireEvent.submit(form);expect(save).toHaveBeenCalledOnce();reject(new Error('Revision conflict'));
 expect(await screen.findByRole('alert')).toHaveTextContent('Your draft is kept');expect(screen.getByLabelText('Instructions')).toHaveValue('Unsaved new instructions');
});
