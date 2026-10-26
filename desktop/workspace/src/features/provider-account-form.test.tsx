import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import ProviderAccountForm from './ProviderAccountForm';
afterEach(cleanup);
it('guides provider selection, clears a key when its destination changes, and keeps capabilities advanced',()=>{
 render(<ProviderAccountForm busy={false} onSave={vi.fn()}/>);
 expect(screen.getByLabelText('Provider')).toHaveValue('openai');
 expect(screen.queryByLabelText('Provider API address')).not.toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('API key'),{target:{value:'synthetic-write-only'}});
 fireEvent.change(screen.getByLabelText('Provider'),{target:{value:'openrouter'}});
 expect(screen.getByLabelText('API key')).toHaveValue('');
 expect(screen.getByText('https://openrouter.ai/api/v1')).toBeInTheDocument();
 expect(screen.getByText('Advanced account options').closest('details')).not.toHaveAttribute('open');
 fireEvent.change(screen.getByLabelText('Provider'),{target:{value:'custom'}});
 expect(screen.getByLabelText('Provider API address')).toHaveValue('');
 expect(screen.getByRole('button',{name:'Save account'})).toBeDisabled();
});
it('clears secrets immediately, prevents duplicate dispatch, and retains a stable generated account id for retry',async()=>{
 let reject!:(reason:Error)=>void;const save=vi.fn((input:unknown)=>{void input;return new Promise((_,failure)=>reject=failure)});
 render(<ProviderAccountForm busy={false} onSave={save}/>);
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'My account'}});
 fireEvent.change(screen.getByLabelText('API key'),{target:{value:'synthetic-write-only'}});
 const form=screen.getByRole('form');fireEvent.submit(form);fireEvent.submit(form);
 expect(save).toHaveBeenCalledOnce();expect(screen.getByLabelText('API key')).toHaveValue('');
 expect(save.mock.calls[0][0]).toMatchObject({api_key:'synthetic-write-only',base_url:'https://api.openai.com/v1',capabilities:['shell','functions']});
 const id=(save.mock.calls[0][0] as {id:string}).id;
 reject(new Error('Host temporarily unavailable'));
 expect(await screen.findByRole('alert')).toHaveTextContent('Host temporarily unavailable');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save account'})).toBeEnabled());
 fireEvent.submit(form);expect(save).toHaveBeenCalledTimes(2);expect(save.mock.calls[1][0]).toMatchObject({id,api_key:null});
});
it('preserves existing endpoint, capability and model declarations during key replacement',async()=>{
 const save=vi.fn().mockResolvedValue({});render(<ProviderAccountForm busy={false} account={{id:'existing',name:'Existing',kind:'openai-compatible',base_url:'https://custom.example/v1',model:'model-one',capabilities:['image']}} onSave={save}/>);
 expect(screen.queryByLabelText('Provider')).not.toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('Replace API key (leave blank to keep current key)'),{target:{value:'synthetic-replacement'}});
 fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledWith({id:'existing',name:'Existing',kind:'openai-compatible',base_url:'https://custom.example/v1',model:'model-one',capabilities:['image'],api_key:'synthetic-replacement'}));
});
it('starts a distinct account after a successful creation instead of overwriting it',async()=>{
 const save=vi.fn((input:{id:string})=>Promise.resolve({id:input.id}));render(<ProviderAccountForm busy={false} onSave={save}/>);
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'First account'}});fireEvent.submit(screen.getByRole('form'));
 await waitFor(()=>expect(screen.getByLabelText('Display name')).toHaveValue(''));
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'Second account'}});fireEvent.submit(screen.getByRole('form'));
 await waitFor(()=>expect(save).toHaveBeenCalledTimes(2));
 expect(save.mock.calls[1][0].id).not.toBe(save.mock.calls[0][0].id);
});
