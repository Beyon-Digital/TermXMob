import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import SessionModelControls from './SessionModelControls';
import {type Session} from '../lib/api';
afterEach(()=>{cleanup();vi.clearAllMocks()});
const session={id:'session',engine:'internal',provider_id:'account',model:'model-a',mode:'ask',turns:[]} as unknown as Session;
const openEffort=()=>fireEvent.click(screen.getByRole('button',{name:'Model and effort'}));
const openModels=()=>{openEffort();const choose=screen.queryByRole('button',{name:'Choose model'});if(choose)fireEvent.click(choose)};
const openAccounts=()=>{openModels();fireEvent.click(screen.getByRole('button',{name:'Choose account'}))};
const modelList=()=>screen.getByRole('listbox',{name:'Session model'});
const accountList=()=>screen.getByRole('listbox',{name:'Session provider'});
const base={session,engines:[{id:'codex',name:'Codex'}],providers:[{id:'account',name:'Account A',model:'model-a,model-b',capabilities:['chat'],secret_configured:true}],runnerAccounts:[],models:['model-a','model-b'],favorites:[],favoriteKey:'',running:false,onFavorite:vi.fn(),onEngine:vi.fn(),onRefresh:vi.fn(),onError:vi.fn()};

it('opens a separate model list and writes the exact selected ID',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} onPatch={patch}/>);expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
 openModels();expect(screen.queryByRole('combobox')).not.toBeInTheDocument();expect(screen.queryByRole('slider')).not.toBeInTheDocument();
 fireEvent.click(within(modelList()).getByRole('option',{name:'model-b'}));
 expect(patch).toHaveBeenCalledWith({model:'model-b',reasoning_config:{}});expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('disables active model and account changes while keeping the selected model inspectable',()=>{
 render(<SessionModelControls {...base} running onPatch={vi.fn()}/>);openModels();
 expect(modelList()).toHaveAttribute('aria-disabled','true');for(const row of within(modelList()).getAllByRole('option'))expect(row).toBeDisabled();
 expect(screen.getByRole('button',{name:'Choose account'})).toBeDisabled();expect(within(modelList()).getByRole('option',{name:'model-a'})).toHaveAttribute('aria-selected','true');expect(screen.queryByText(/current task/)).not.toBeInTheDocument();
 expect(screen.queryByRole('button',{name:'Back to effort'})).not.toBeInTheDocument();expect(screen.queryByRole('slider')).not.toBeInTheDocument();
});
it('preserves the selected model when the discovery list is empty',()=>{
 render(<SessionModelControls {...base} models={[]} onPatch={vi.fn()}/>);openModels();expect(within(modelList()).getByRole('option',{name:'model-a'})).toHaveAttribute('aria-selected','true');
});
it('closes the dialog when the conversation changes, without an effect to reset it',()=>{
 const view=render(<SessionModelControls {...base} onPatch={vi.fn()}/>);openModels();expect(modelList()).toBeVisible();
 view.rerender(<SessionModelControls {...base} session={{...session,id:'other'} as unknown as Session} onPatch={vi.fn()}/>);expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('binds a runner account to its advertised credential reference rather than its provider ID',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} session={{...session,runner_id:'runner-1'}} runnerAccounts={[{provider_id:'runner-provider',credential_ref:'runner-credential-7',name:'Runner account',models:['runner-model']}]} onPatch={patch}/>);
 openAccounts();fireEvent.click(within(accountList()).getByRole('option',{name:'Runner account'}));
 expect(patch).toHaveBeenCalledWith({provider_id:'runner-provider',runner_credential_ref:'runner-credential-7',model:'runner-model',reasoning_config:{}});
});
it('keeps an unavailable saved account inspectable instead of displaying a different default',()=>{
 render(<SessionModelControls {...base} session={{...session,provider_id:'removed-account'}} onPatch={vi.fn()}/>);openAccounts();
 expect(within(accountList()).getByRole('option',{name:'Unavailable account (removed-account)'})).toHaveAttribute('aria-selected','true');
});
it('routes model connection setup back to settings without sending a message',()=>{
 const configure=vi.fn();render(<SessionModelControls {...base} providers={[]} onConfigureAgent={configure} onPatch={vi.fn()}/>);
 openModels();fireEvent.click(screen.getByRole('button',{name:'Manage connections'}));expect(configure).toHaveBeenCalledOnce();expect(screen.queryByRole('dialog')).toBeNull();
});
it('retains the main agent provider and model choices without explanatory prose',()=>{
 render(<SessionModelControls {...base} coordinating onPatch={vi.fn()}/>);openModels();expect(screen.queryByText(/can delegate work to installed engines/)).not.toBeInTheDocument();
 expect(within(modelList()).getByRole('option',{name:'model-b'})).toBeEnabled();expect(screen.getByRole('button',{name:'Choose account'})).toBeEnabled();
});
it('selects the first configured model when explicitly switching to a local account',()=>{
 const patch=vi.fn(),next={id:'next-account',name:'Next account',model:'next-one, next-two',capabilities:['chat'],secret_configured:true};
 render(<SessionModelControls {...base} providers={[...base.providers,next]} onPatch={patch}/>);openAccounts();fireEvent.click(within(accountList()).getByRole('option',{name:'Next account'}));
 expect(patch).toHaveBeenCalledWith({provider_id:'next-account',model:'next-one',reasoning_config:{}});
});
it('keeps host authentication details out of the compact picker',()=>{
 render(<SessionModelControls {...base} session={{...session,engine:'codex',provider_id:undefined}} engines={[{id:'codex',name:'Codex',available:true,auth_state:'unknown',error:'Account check timed out'}]} onPatch={vi.fn()}/>);openModels();
 expect(screen.queryByText(/account status has not been verified/)).not.toBeInTheDocument();expect(screen.queryByText(/Last engine check/)).not.toBeInTheDocument();expect(screen.queryByText(/connected account/i)).not.toBeInTheDocument();expect(screen.getByText('Recommended set of models')).toBeVisible();
});
it('closes the picker while hidden and preserves selections without writing settings',async()=>{
 const patch=vi.fn();const surface=(visible:boolean)=><><button>Studio action</button><SessionModelControls {...base} session={{...session,model:'model-b'}} visible={visible} onPatch={patch}/></>;
 const view=render(surface(true));openModels();expect(within(modelList()).getByRole('option',{name:'model-b'})).toHaveAttribute('aria-selected','true');
 view.rerender(surface(false));await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());expect(screen.getByRole('button',{name:'Model and effort'})).toBeDisabled();
 const studio=screen.getByRole('button',{name:'Studio action'});studio.focus();await waitFor(()=>expect(studio).toHaveFocus());expect(patch).not.toHaveBeenCalled();
 view.rerender(surface(true));expect(screen.queryByRole('dialog')).not.toBeInTheDocument();openModels();expect(within(modelList()).getByRole('option',{name:'model-b'})).toHaveAttribute('aria-selected','true');
 fireEvent.click(screen.getByRole('button',{name:'Choose account'}));expect(within(accountList()).getByRole('option',{name:'Account A'})).toHaveAttribute('aria-selected','true');expect(patch).not.toHaveBeenCalled();
});
it('starts with only the selected effort, model chooser and reset control',()=>{
 render(<SessionModelControls {...base} session={{...session,engine:'codex'}} onPatch={vi.fn()}/>);openEffort();
 expect(screen.getByRole('dialog',{name:'Model and effort'})).toBeVisible();expect(screen.getByRole('button',{name:'Model and effort'})).toHaveTextContent('Select effort');expect(screen.getByText('Default')).toBeVisible();expect(screen.getByRole('button',{name:'Choose model'})).toHaveTextContent('model-a');
 expect(screen.queryByRole('listbox')).not.toBeInTheDocument();expect(screen.queryByRole('combobox')).not.toBeInTheDocument();expect(screen.queryByText('Account')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Reset effort'})).toBeDisabled();
});
it('closes on owner change and lock without restoring focus into a hidden workspace',async()=>{
 const patch=vi.fn();const view=render(<SessionModelControls {...base} ownerId="owner-a" onPatch={patch}/>);openEffort();expect(screen.getByRole('dialog')).toBeVisible();
 view.rerender(<SessionModelControls {...base} ownerId="owner-b" onPatch={patch}/>);expect(screen.queryByRole('dialog')).not.toBeInTheDocument();openEffort();fireEvent(window,new Event('termx-locked'));
 await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());expect(screen.getByRole('button',{name:'Model and effort'})).toBeDisabled();expect(patch).not.toHaveBeenCalled();
});
it('keeps favorites reachable through the model chooser and prioritizes scoped favorites',()=>{
 const favorite=vi.fn();render(<SessionModelControls {...base} favorites={[JSON.stringify(['internal','account','model-b'])]} onFavorite={favorite} onPatch={vi.fn()}/>);openModels();
 expect(within(modelList()).getAllByRole('option').map(row=>row.getAttribute('aria-label'))).toEqual(['Default','model-b','model-a']);fireEvent.click(screen.getByRole('button',{name:'Favorite selected model'}));expect(favorite).toHaveBeenCalledOnce();
});
it('navigates model options without writes and activates the focused value with Enter',async()=>{
 const user=userEvent.setup(),patch=vi.fn();render(<SessionModelControls {...base} onPatch={patch}/>);openModels();expect(within(modelList()).getByRole('option',{name:'model-a'})).toHaveFocus();
 await user.keyboard('{End}');expect(within(modelList()).getByRole('option',{name:'model-b'})).toHaveFocus();await user.keyboard('{Home}');expect(within(modelList()).getByRole('option',{name:'Default'})).toHaveFocus();await user.keyboard('{ArrowDown}');expect(within(modelList()).getByRole('option',{name:'model-a'})).toHaveFocus();expect(patch).not.toHaveBeenCalled();
 await user.keyboard('{ArrowDown}{Enter}');expect(patch).toHaveBeenCalledExactlyOnceWith({model:'model-b',reasoning_config:{}});
});
it('backs out of the internal account list and closes the model menu with Escape',async()=>{
 const user=userEvent.setup(),patch=vi.fn();render(<SessionModelControls {...base} onPatch={patch}/>);openAccounts();await user.keyboard('{Escape}');expect(modelList()).toBeVisible();
 await user.keyboard('{Escape}');await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());expect(screen.getByRole('button',{name:'Model and effort'})).toHaveFocus();expect(patch).not.toHaveBeenCalled();
});
it('returns from native models to its effort card with Escape and restores the model button',async()=>{
 const user=userEvent.setup(),patch=vi.fn();render(<SessionModelControls {...base} session={{...session,engine:'codex'}} onPatch={patch}/>);openModels();await user.keyboard('{Escape}');expect(screen.getByRole('button',{name:'Choose model'})).toHaveFocus();expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
 await user.keyboard('{Escape}');await waitFor(()=>expect(screen.queryByRole('dialog')).not.toBeInTheDocument());expect(screen.getByRole('button',{name:'Model and effort'})).toHaveFocus();expect(patch).not.toHaveBeenCalled();
});
it('writes null for the explicit default model and leaves an unchanged selection alone',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} onPatch={patch}/>);openModels();fireEvent.click(within(modelList()).getByRole('option',{name:'model-a'}));expect(patch).not.toHaveBeenCalled();
 openModels();fireEvent.click(within(modelList()).getByRole('option',{name:'Default'}));expect(patch).toHaveBeenCalledExactlyOnceWith({model:null,reasoning_config:{}});
});


it('anchors internal model and account menus above their button independently of the composer',()=>{
 render(<div className="composer"><SessionModelControls {...base} onPatch={vi.fn()}/></div>);
 const trigger=screen.getByRole('button',{name:'Model and effort'});
 const rect=(left:number,top:number,width:number,height:number)=>({x:left,y:top,left,top,right:left+width,bottom:top+height,width,height,toJSON:()=>({})});
 vi.spyOn(trigger,'getBoundingClientRect').mockReturnValue(rect(400,500,160,32));
 vi.spyOn(trigger.closest('.composer')!,'getBoundingClientRect').mockReturnValue(rect(200,350,700,182));
 openModels();
 const popup=screen.getByRole('dialog');expect(popup.style.left).toBe('304px');expect(popup.style.bottom).toBe((window.innerHeight-500+8)+'px');
 fireEvent.click(screen.getByRole('button',{name:'Choose account'}));expect(popup.style.left).toBe('304px');expect(popup.style.bottom).toBe((window.innerHeight-500+8)+'px');
});


it('opens configured provider models directly for an unbound main agent without writing on open or navigation',async()=>{
 const patch=vi.fn(),user=userEvent.setup();
 render(<SessionModelControls {...base} session={{...session,provider_id:undefined,model:undefined}} coordinating providers={[...base.providers,{id:'second',name:'Account B',model:'',models:['model-a','discovered-b'],capabilities:['chat'],secret_configured:true},{id:'unconfigured',name:'Unconfigured',model:'hidden-model',capabilities:['chat'],secret_configured:false}]} onPatch={patch}/>);
 openModels();expect(screen.queryByRole('slider')).not.toBeInTheDocument();expect(screen.queryByRole('button',{name:'Reset effort'})).not.toBeInTheDocument();
 expect(within(modelList()).getByRole('option',{name:'model-a · Account A'})).toBeVisible();expect(within(modelList()).getByRole('option',{name:'model-a · Account B'})).toBeVisible();expect(screen.queryByRole('option',{name:/hidden-model/})).not.toBeInTheDocument();
 await user.keyboard('{End}{Home}');expect(patch).not.toHaveBeenCalled();
 fireEvent.click(within(modelList()).getByRole('option',{name:'discovered-b · Account B'}));
 expect(patch).toHaveBeenCalledExactlyOnceWith({provider_id:'second',model:'discovered-b',reasoning_config:{}});expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
it('selects a discovered model when explicitly switching to an account with no raw allowlist',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} providers={[...base.providers,{id:'discovered',name:'Discovered account',model:'',models:['discovered-one'],capabilities:['chat'],secret_configured:true}]} onPatch={patch}/>);
 openAccounts();fireEvent.click(within(accountList()).getByRole('option',{name:'Discovered account'}));expect(patch).toHaveBeenCalledExactlyOnceWith({provider_id:'discovered',model:'discovered-one',reasoning_config:{}});
});
it('binds a directly chosen runner model to its advertised credential without changing the runner',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} session={{...session,provider_id:undefined,model:undefined,runner_id:'runner-1'}} runnerAccounts={[{provider_id:'remote-account',credential_ref:'remote-credential',name:'Remote account',models:['remote-model']}]} onPatch={patch}/>);
 openModels();expect(patch).not.toHaveBeenCalled();expect(screen.queryByRole('button',{name:'Reset effort'})).not.toBeInTheDocument();
 fireEvent.click(within(modelList()).getByRole('option',{name:'remote-model · Remote account'}));expect(patch).toHaveBeenCalledExactlyOnceWith({provider_id:'remote-account',runner_credential_ref:'remote-credential',model:'remote-model',reasoning_config:{}});
});
it('keeps native effort and model views anchored to their actual trigger',()=>{
 render(<SessionModelControls {...base} session={{...session,engine:'codex'}} onPatch={vi.fn()}/>);
 const trigger=screen.getByRole('button',{name:'Model and effort'});vi.spyOn(trigger,'getBoundingClientRect').mockReturnValue({x:400,y:500,left:400,top:500,right:560,bottom:532,width:160,height:32,toJSON:()=>({})});
 openEffort();const popup=screen.getByRole('dialog');expect(popup.style.left).toBe('304px');expect(popup.style.bottom).toBe((window.innerHeight-500+8)+'px');
 fireEvent.click(screen.getByRole('button',{name:'Choose model'}));expect(popup.style.left).toBe('304px');expect(popup.style.bottom).toBe((window.innerHeight-500+8)+'px');
});

it('offers actual configured models when a saved provider is unavailable without silently changing it',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} session={{...session,provider_id:'removed-account'}} onPatch={patch}/>);openModels();
 expect(within(modelList()).getByRole('option',{name:'model-b · Account A'})).toBeVisible();expect(screen.getByRole('button',{name:'Model and effort'})).toHaveTextContent('model-a');expect(patch).not.toHaveBeenCalled();
 fireEvent.click(within(modelList()).getByRole('option',{name:'model-b · Account A'}));expect(patch).toHaveBeenCalledExactlyOnceWith({provider_id:'account',model:'model-b',reasoning_config:{}});
});
it('never creates a provider or model choice when no account is configured',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} session={{...session,provider_id:undefined,model:undefined}} providers={[]} onPatch={patch}/>);openModels();
 expect(within(modelList()).queryAllByRole('option')).toHaveLength(0);expect(patch).not.toHaveBeenCalled();fireEvent.click(screen.getByRole('button',{name:'Choose account'}));expect(within(accountList()).getAllByRole('option')).toHaveLength(1);expect(patch).not.toHaveBeenCalled();
});
it('closes an open picker when its engine or runner changes without writing a selection',()=>{
 const patch=vi.fn(),view=render(<SessionModelControls {...base} session={{...session,engine:'codex'}} onPatch={patch}/>);openEffort();
 view.rerender(<SessionModelControls {...base} onPatch={patch}/>);expect(screen.queryByRole('dialog')).not.toBeInTheDocument();openModels();expect(modelList()).toBeVisible();
 view.rerender(<SessionModelControls {...base} session={{...session,runner_id:'runner-next'}} onPatch={patch}/>);expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(patch).not.toHaveBeenCalled();
});

it('shows an empty internal catalog without an unusable default model and keeps connection recovery available',()=>{
 const patch=vi.fn(),configure=vi.fn();render(<SessionModelControls {...base} session={{...session,model:undefined}} providers={[{...base.providers[0],model:''}]} models={[]} onConfigureAgent={configure} onPatch={patch}/>);openModels();
 expect(within(modelList()).queryAllByRole('option')).toHaveLength(0);expect(screen.getByRole('status')).toHaveTextContent('No models configured');expect(patch).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('button',{name:'Manage connections'}));expect(configure).toHaveBeenCalledOnce();expect(patch).not.toHaveBeenCalled();
});
it('preserves an unavailable saved model when discovery is empty and never rewrites it on activation',()=>{
 const patch=vi.fn();render(<SessionModelControls {...base} session={{...session,provider_id:'removed-account'}} providers={[]} models={[]} onPatch={patch}/>);openModels();
 const saved=within(modelList()).getByRole('option',{name:'model-a'});expect(saved).toHaveAttribute('aria-selected','true');expect(screen.queryByText('No models configured')).not.toBeInTheDocument();
 fireEvent.click(saved);expect(patch).not.toHaveBeenCalled();expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});
