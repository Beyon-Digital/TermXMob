import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import SessionModelControls from './SessionModelControls';
import {type Session} from '../lib/api';
afterEach(()=>{cleanup();vi.clearAllMocks()});
const session={id:'session',engine:'internal',provider_id:'account',model:'model-a',mode:'ask',turns:[]} as unknown as Session;
const base={session,engines:[{id:'codex',name:'Codex'}],providers:[{id:'account',name:'Account A',model:'model-a,model-b',capabilities:['chat'],secret_configured:true}],runnerAccounts:[],models:['model-a','model-b'],favorites:[],favoriteKey:'',running:false,onFavorite:vi.fn(),onEngine:vi.fn(),onRefresh:vi.fn(),onError:vi.fn()};
it('keeps model and effort in a compact chip while engine and mode remain outside its dialog',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} onPatch={patch}/>);expect(screen.queryByRole('combobox',{name:'Session model'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));fireEvent.change(screen.getByRole('combobox',{name:'Session model'}),{target:{value:'model-b'}});
 expect(patch).toHaveBeenCalledWith({model:'model-b',reasoning_config:{}});expect(screen.queryByRole('combobox',{name:'Session engine'})).not.toBeInTheDocument();expect(screen.queryByRole('combobox',{name:'Session mode'})).not.toBeInTheDocument();
});
it('disables active model and account changes while keeping their selections inspectable',()=>{
 render(<SessionModelControls {...base} running onPatch={vi.fn()}/>);fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));
 for(const label of ['Session provider','Session model'])expect(screen.getByRole('combobox',{name:label})).toBeDisabled();expect(screen.getByRole('status')).toHaveTextContent('current task');
});
it('preserves the selected model when the discovery list is empty',()=>{
 render(<SessionModelControls {...base} models={[]} onPatch={vi.fn()}/>);fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));expect(screen.getByRole('combobox',{name:'Session model'})).toHaveValue('model-a');
});
it('binds a runner account to its advertised credential reference rather than its provider ID',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} session={{...session,runner_id:'runner-1'}} runnerAccounts={[{provider_id:'runner-provider',credential_ref:'runner-credential-7',name:'Runner account',models:['runner-model']}]} onPatch={patch}/>);
 fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));fireEvent.change(screen.getByRole('combobox',{name:'Session provider'}),{target:{value:'runner-provider'}});
 expect(patch).toHaveBeenCalledWith({provider_id:'runner-provider',runner_credential_ref:'runner-credential-7',model:'runner-model',reasoning_config:{}});
});

it('keeps an unavailable saved account inspectable instead of displaying a different default',()=>{
 render(<SessionModelControls {...base} session={{...session,provider_id:'removed-account'}} onPatch={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));expect(screen.getByRole('combobox',{name:'Session provider'})).toHaveValue('removed-account');expect(screen.getByRole('option',{name:'Unavailable account (removed-account)'})).toBeInTheDocument();
});
it('routes model connection setup back to settings without sending a message',()=>{
 const configure=vi.fn();render(<SessionModelControls {...base} providers={[]} onConfigureAgent={configure} onPatch={vi.fn()}/>);
 fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));fireEvent.click(screen.getByRole('button',{name:'Manage connections'}));expect(configure).toHaveBeenCalledOnce();expect(screen.queryByRole('dialog')).toBeNull();
});

it('explains the main agent engine boundary while retaining provider and model choices',()=>{render(<SessionModelControls {...base} coordinating onPatch={vi.fn()}/>);fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));expect(screen.getByText(/Main agent uses Internal Agent for delegation/)).toBeVisible();expect(screen.getByRole('combobox',{name:'Session model'})).toBeEnabled();expect(screen.getByRole('combobox',{name:'Session provider'})).toBeEnabled()});
