import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {expectNoAxeViolations} from '../test-axe';
import type {ProviderAccount} from '../lib/host-types';
import ProviderAccountForm from './ProviderAccountForm';
afterEach(cleanup);

const saved:ProviderAccount={id:'existing',name:'Existing',kind:'openai-compatible',base_url:'https://custom.example/v1',model:'',capabilities:['image'],
 model_catalog:{'big-model':{context_window:200000,price_in:3,price_out:15},'mystery-model':{}},model_overrides:{},catalog_refreshed_at:null,catalog_error:null};

it('guides provider selection, clears a key when its destination changes, and keeps capabilities advanced',()=>{
 render(<ProviderAccountForm busy={false} onSave={vi.fn()}/>);
 const provider=screen.getByRole('combobox',{name:/^Provider$/});
 expect(provider).toHaveValue('openai');
 expect(screen.queryByLabelText('Provider API address')).not.toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('API key'),{target:{value:'synthetic-write-only'}});
 fireEvent.change(provider,{target:{value:'openrouter'}});
 expect(screen.getByLabelText('API key')).toHaveValue('');
 expect(screen.getByText('https://openrouter.ai/api/v1')).toBeInTheDocument();
 expect(screen.getByText('Advanced account options').closest('details')).not.toHaveAttribute('open');
 fireEvent.change(provider,{target:{value:'custom'}});
 expect(screen.getByLabelText('Provider API address')).toHaveValue('');
});

it('lets a Chat Completions only provider be set up and keeps an existing choice',async()=>{
 const save=vi.fn(async(_input:unknown)=>({}));
 const view=render(<ProviderAccountForm busy={false} onSave={save}/>);
 fireEvent.change(screen.getByRole('combobox',{name:'Provider'}),{target:{value:'custom'}});
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'Local'}});
 fireEvent.change(screen.getByLabelText('Provider API address'),{target:{value:'http://localhost:9000/v1'}});
 const format=screen.getByRole('combobox',{name:'Request format'});
 expect(format).toHaveValue('responses');
 fireEvent.change(format,{target:{value:'chat'}});
 fireEvent.click(screen.getByRole('button',{name:'Save account'}));
 await waitFor(()=>expect(save).toHaveBeenCalledWith(expect.objectContaining({wire:'chat'})));
 view.unmount();
 render(<ProviderAccountForm busy={false} account={{...saved,wire:'chat'}} onSave={save}/>);
 expect(screen.getByRole('combobox',{name:'Request format'})).toHaveValue('chat');
});

it('names each field and reports a problem beside the field instead of disabling Save',async()=>{
 const save=vi.fn();render(<ProviderAccountForm busy={false} onSave={save}/>);
 fireEvent.change(screen.getByRole('combobox',{name:'Provider'}),{target:{value:'custom'}});
 fireEvent.click(screen.getByRole('button',{name:'Save account'}));
 const name=screen.getByLabelText('Display name'),address=screen.getByLabelText('Provider API address');
 expect(name).toHaveAttribute('aria-invalid','true');expect(name).toHaveAccessibleDescription('Enter a display name.');
 expect(address).toHaveAccessibleDescription('Enter the API address your provider supplied.');
 fireEvent.change(name,{target:{value:'Local'}});expect(name).not.toHaveAttribute('aria-invalid');
 fireEvent.change(address,{target:{value:'localhost:11434'}});fireEvent.click(screen.getByRole('button',{name:'Save account'}));
 expect(address).toHaveAccessibleDescription('Enter a full address that starts with https:// or http://.');
 expect(save).not.toHaveBeenCalled();
 expect(name).toHaveAttribute('name','name');expect(address).toHaveAttribute('name','base_url');expect(screen.getByLabelText('API key')).toHaveAttribute('name','api_key');
});

it('keeps the API key after a failed save, can show it, and retains a stable generated id for retry',async()=>{
 let reject!:(reason:Error)=>void;const save=vi.fn((input:unknown)=>{void input;return new Promise((_,failure)=>reject=failure)});
 render(<ProviderAccountForm busy={false} onSave={save}/>);
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'My account'}});
 fireEvent.change(screen.getByLabelText('API key'),{target:{value:'synthetic-write-only'}});
 const key=screen.getByLabelText('API key');expect(key).toHaveAttribute('type','password');
 fireEvent.click(screen.getByRole('button',{name:'Show key'}));expect(key).toHaveAttribute('type','text');
 fireEvent.click(screen.getByRole('button',{name:'Hide key'}));expect(key).toHaveAttribute('type','password');
 const form=screen.getByRole('form');fireEvent.submit(form);fireEvent.submit(form);
 expect(save).toHaveBeenCalledOnce();
 expect(save.mock.calls[0][0]).toMatchObject({api_key:'synthetic-write-only',base_url:'https://api.openai.com/v1',capabilities:['shell','functions']});
 const id=(save.mock.calls[0][0] as {id:string}).id;
 reject(new Error('Host temporarily unavailable'));
 expect((await screen.findByText('Host temporarily unavailable')).closest('[role=alert]')).not.toBeNull();
 expect(screen.getByLabelText('API key')).toHaveValue('synthetic-write-only');
 await waitFor(()=>expect(screen.getByRole('button',{name:'Save account'})).toBeEnabled());
 fireEvent.submit(form);expect(save).toHaveBeenCalledTimes(2);expect(save.mock.calls[1][0]).toMatchObject({id,api_key:'synthetic-write-only'});
});

it('keeps the key when the host reports the save failed with null, and clears it once saved',async()=>{
 const save=vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce({});
 render(<ProviderAccountForm busy={false} onSave={save}/>);
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'Account'}});fireEvent.change(screen.getByLabelText('API key'),{target:{value:'synthetic-write-only'}});
 fireEvent.submit(screen.getByRole('form'));
 expect(await screen.findByText(/The host did not accept the account.*new API key.*still here/)).toBeInTheDocument();expect(screen.getByLabelText('API key')).toHaveValue('synthetic-write-only');
 fireEvent.submit(screen.getByRole('form'));
 await waitFor(()=>expect(screen.getByLabelText('API key')).toHaveValue(''));
});

it('saves the included models and overrides, shows Unknown for missing values, and preserves declarations',async()=>{
 const save=vi.fn().mockResolvedValue({});
 const {container}=render(<ProviderAccountForm busy={false} account={saved} onSave={save}/>);
 expect(screen.queryByLabelText('Provider')).not.toBeInTheDocument();
 const rows=within(screen.getByRole('list',{name:'Discovered models'})).getAllByRole('listitem');
 expect(rows).toHaveLength(2);
 expect(rows[0]).toHaveTextContent('Context window 200,000 tokens');expect(rows[0]).toHaveTextContent('Input $3 per 1M tokens');
 expect(rows[1]).toHaveTextContent('Context window Unknown');expect(rows[1]).toHaveTextContent('Input Unknown');expect(rows[1]).toHaveTextContent('Output Unknown');
 expect(within(rows[0]).getByRole('checkbox',{name:'big-model'})).toBeChecked();
 await expectNoAxeViolations(container);
 fireEvent.click(within(rows[1]).getByText('Override values for mystery-model'));
 fireEvent.change(await within(rows[1]).findByLabelText('Context window (tokens)'),{target:{value:'32000'}});
 expect(rows[1]).toHaveTextContent('Context window 32,000 tokens (your value)');
 fireEvent.click(within(rows[1]).getByRole('checkbox',{name:'mystery-model'}));
 expect(within(rows[0]).getByRole('checkbox',{name:'big-model'})).toBeDisabled();
 fireEvent.change(screen.getByLabelText('Replace API key'),{target:{value:'synthetic-replacement'}});
 fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledWith({id:'existing',name:'Existing',kind:'openai-compatible',base_url:'https://custom.example/v1',model:'big-model',capabilities:['image'],api_key:'synthetic-replacement',wire:'responses',model_meta:{'mystery-model':{context_window:32000}}}));
});

it('saves an empty model list while every discovered model stays included, and omits overrides nobody set',async()=>{
 const save=vi.fn().mockResolvedValue({});render(<ProviderAccountForm busy={false} account={saved} onSave={save}/>);
 fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 expect(save.mock.calls[0][0]).toMatchObject({model:'',api_key:null});expect(save.mock.calls[0][0]).not.toHaveProperty('model_meta');
});

it('filters a long catalog, says how many models show, and builds override fields only for rows you open',async()=>{
 const many=Object.fromEntries(Array.from({length:40},(_,i)=>[`vendor/model-${i}`,{context_window:8000}]));
 const {container}=render(<ProviderAccountForm busy={false} account={{...saved,model:'',model_catalog:many}} onSave={vi.fn()}/>);
 const list=()=>within(screen.getByRole('list',{name:'Discovered models'}));
 expect(list().getAllByRole('listitem')).toHaveLength(40);expect(screen.getByText('Showing 40 of 40 models.')).toBeInTheDocument();
 expect(screen.queryByLabelText('Context window (tokens)')).not.toBeInTheDocument();
 fireEvent.change(screen.getByRole('searchbox',{name:'Filter models'}),{target:{value:'MODEL-3'}});
 expect(list().getAllByRole('listitem')).toHaveLength(11);expect(screen.getByText('Showing 11 of 40 models.')).toBeInTheDocument();
 fireEvent.change(screen.getByRole('searchbox',{name:'Filter models'}),{target:{value:'zzz'}});
 expect(screen.getByText(/No models match “zzz”. Clear the filter to see all 40 models./)).toBeInTheDocument();expect(list().queryAllByRole('listitem')).toHaveLength(0);
 fireEvent.change(screen.getByRole('searchbox',{name:'Filter models'}),{target:{value:'model-7'}});
 fireEvent.click(screen.getByText('Override values for vendor/model-7'));expect(await screen.findAllByLabelText('Context window (tokens)')).toHaveLength(1);
});
it('keeps a short catalog free of filter chrome',()=>{
 render(<ProviderAccountForm busy={false} account={saved} onSave={vi.fn()}/>);expect(screen.queryByRole('searchbox',{name:'Filter models'})).not.toBeInTheDocument();
});

it('refreshes models, and shows the catalog age and a failed refresh in alerts',async()=>{
 const refresh=vi.fn().mockResolvedValue({});
 const view=render(<ProviderAccountForm busy={false} account={{...saved,catalog_refreshed_at:Date.now()/1000-7200,catalog_error:'Provider returned 503'}} onRefresh={refresh} onSave={vi.fn()}/>);
 expect(screen.getByText('Catalog refreshed 2 hours ago.')).toBeInTheDocument();
 expect(screen.getByText(/previous catalog is shown. Provider returned 503/)).toHaveAttribute('role','alert');
 fireEvent.click(screen.getByRole('button',{name:'Refresh models'}));
 expect(screen.getByRole('button',{name:'Refresh models'})).toHaveAttribute('data-keeps-draft');
 await waitFor(()=>expect(refresh).toHaveBeenCalledOnce());
 await expectNoAxeViolations(view.container);
});

it('explains how to discover models before the first save and offers no manual model list',async()=>{
 const {container}=render(<ProviderAccountForm busy={false} onSave={vi.fn()}/>);
 expect(screen.getByText(/Save the account, then choose Refresh models/)).toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Refresh models'})).not.toBeInTheDocument();
 expect(screen.queryByLabelText(/Default model/)).not.toBeInTheDocument();
 await expectNoAxeViolations(container);
});

it('starts a distinct account after a successful creation instead of overwriting it',async()=>{
 const save=vi.fn((input:{id:string})=>Promise.resolve({id:input.id}));render(<ProviderAccountForm busy={false} onSave={save}/>);
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'First account'}});fireEvent.submit(screen.getByRole('form'));
 await waitFor(()=>expect(screen.getByLabelText('Display name')).toHaveValue(''));
 fireEvent.change(screen.getByLabelText('Display name'),{target:{value:'Second account'}});fireEvent.submit(screen.getByRole('form'));
 await waitFor(()=>expect(save).toHaveBeenCalledTimes(2));
 expect(save.mock.calls[1][0].id).not.toBe(save.mock.calls[0][0].id);
});

it('submits only fields that AgentProviderInput declares in schema.graphql',async()=>{
 const {readFileSync}=await import('node:fs');const {resolve}=await import('node:path');
 const schema=readFileSync(resolve(process.cwd(),'..','..','schema.graphql'),'utf8');
 const block=/input AgentProviderInput \{([^}]*)\}/.exec(schema)?.[1]||'';
 const declared=block.split('\n').map(line=>line.trim().split(':')[0]).filter(Boolean);
 expect(declared).toContain('model_meta');
 const save=vi.fn().mockResolvedValue({});render(<ProviderAccountForm busy={false} account={saved} onSave={save}/>);
 const row=within(screen.getByRole('list',{name:'Discovered models'})).getAllByRole('listitem')[1];
 fireEvent.click(within(row).getByText('Override values for mystery-model'));
 fireEvent.change(await within(row).findByLabelText('Context window (tokens)'),{target:{value:'64000'}});
 fireEvent.change(screen.getByLabelText('Replace API key'),{target:{value:'synthetic-replacement'}});
 fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
 await waitFor(()=>expect(save).toHaveBeenCalledOnce());
 for(const key of Object.keys(save.mock.calls[0][0]))expect(declared).toContain(key);
});

it('says what happened when the host refuses the account, and mentions the key only when one was typed',async()=>{
 const save=vi.fn().mockResolvedValue(null);render(<ProviderAccountForm busy={false} account={saved} onSave={save}/>);
 fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
 expect(await screen.findByText(/The host did not accept the account/)).not.toHaveTextContent(/API key/);
 fireEvent.change(screen.getByLabelText('Replace API key'),{target:{value:'synthetic-replacement'}});
 fireEvent.click(screen.getByRole('button',{name:'Save account changes'}));
 expect(await screen.findByText(/new API key/)).toBeInTheDocument();
});
