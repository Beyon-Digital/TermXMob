import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import ChatConnectionSetup from './ChatConnectionSetup';
import type {Provider,Session} from '../lib/api';

afterEach(cleanup);
const session={id:'main',engine:'internal',title:'Main agent',mode:'agent',turns:[]} as unknown as Session;
const accounts:Provider[]=[
 {id:'one',name:'First account',model:'first-model, alternate-model',secret_configured:true,capabilities:['functions']},
 {id:'two',name:'Second account',model:'other-model',secret_configured:true,capabilities:['functions']},
];
const props={session,providers:accounts,engines:[],enabled:true};
const openOptions=()=>fireEvent.click(screen.getByRole('button',{name:'Choose connection'}));

it('requires an explicit choice and applies the model belonging to the selected account once',async()=>{
 let finish!:(saved:boolean)=>void;
 const connect=vi.fn(()=>new Promise<boolean>(resolve=>{finish=resolve}));
 render(<ChatConnectionSetup {...props} onConnect={connect}/>);
 expect(connect).not.toHaveBeenCalled();
 expect(screen.queryByRole('combobox',{name:'Chat account'})).not.toBeInTheDocument();
 openOptions();
 fireEvent.change(screen.getByRole('combobox',{name:'Chat model'}),{target:{value:'alternate-model'}});
 fireEvent.change(screen.getByRole('combobox',{name:'Chat account'}),{target:{value:'two'}});
 expect(screen.getByRole('combobox',{name:'Chat model'})).toHaveValue('other-model');
 const button=screen.getByRole('button',{name:'Use for this chat'});
 fireEvent.click(button);fireEvent.click(button);
 expect(connect).toHaveBeenCalledOnce();expect(connect).toHaveBeenCalledWith({provider_id:'two',model:'other-model',reasoning_config:{}});
 expect(button).toBeDisabled();
 await act(async()=>finish(true));
 expect(screen.getByRole('status')).toHaveTextContent('Connection saved');
});

it('does not invent a model for a connected account without one',()=>{
 const connect=vi.fn();
 render(<ChatConnectionSetup {...props} providers={[{...accounts[0],model:''}]} onConnect={connect}/>);
 openOptions();
 expect(screen.getByRole('combobox',{name:'Chat model'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Use for this chat'})).toBeDisabled();
 expect(screen.getByText('Add a default model to First account in Models & engines, or choose another account.')).toBeVisible();
 expect(connect).not.toHaveBeenCalled();
});

it('lists every discovered model when the account has all of them checked',()=>{
 const connect=vi.fn();
 render(<ChatConnectionSetup {...props} providers={[{...accounts[0],model:'',models:['alpha','beta']}]} onConnect={connect}/>);
 openOptions();
 const select=screen.getByRole('combobox',{name:'Chat model'});
 expect(select).toBeEnabled();
 expect([...select.querySelectorAll('option')].map(option=>option.textContent)).toEqual(['alpha','beta']);
 expect(screen.queryByText(/Add a default model/)).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Use for this chat'}));
 expect(connect).toHaveBeenCalledWith({provider_id:'one',model:'alpha',reasoning_config:{}});
});

it('keeps a configured chat unchanged and identifies a missing native engine honestly',()=>{
 const connect=vi.fn(),configure=vi.fn();
 const view=render(<ChatConnectionSetup {...props} session={{...session,provider_id:'one',model:'first-model'}} onConnect={connect}/>);
 expect(screen.queryByRole('region',{name:'Connect this chat'})).not.toBeInTheDocument();
 view.rerender(<ChatConnectionSetup {...props} session={{...session,engine:'codex'}} onConnect={connect} onConfigureAgent={configure}/>);
 expect(screen.getByRole('heading',{name:'Reconnect this engine'})).toBeVisible();
 expect(screen.queryByRole('button',{name:'Use for this chat'})).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Manage connections'}));
 expect(configure).toHaveBeenCalledOnce();expect(connect).not.toHaveBeenCalled();
});

it('drops a late failed connection response after the user opens another chat',async()=>{
 let finish!:(saved:boolean)=>void;
 const connect=vi.fn(()=>new Promise<boolean>(resolve=>{finish=resolve}));
 const view=render(<ChatConnectionSetup key="first" {...props} onConnect={connect}/>);
 openOptions();
 fireEvent.click(screen.getByRole('button',{name:'Use for this chat'}));
 view.rerender(<ChatConnectionSetup key="second" {...props} session={{...session,id:'second'}} onConnect={connect}/>);
 await act(async()=>finish(false));
 openOptions();
 await waitFor(()=>expect(screen.getByRole('button',{name:'Use for this chat'})).toBeEnabled());
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
});


it.each(['unauthenticated','expired'])('requires native sign-in when the installed engine explicitly reports %s',authState=>{
 const connect=vi.fn();
 const view=render(<ChatConnectionSetup {...props} session={{...session,engine:'codex'}} engines={[{id:'codex',name:'Codex',available:true,auth_state:authState,auth_detail:'Continue sign-in on this host.'}]} onConnect={connect}/>);
 expect(screen.getByRole('heading',{name:'Reconnect this engine'})).toBeVisible();
 expect(screen.getByText('Continue sign-in on this host.')).toBeVisible();
 expect(screen.queryByRole('button',{name:'Use for this chat'})).not.toBeInTheDocument();
 view.rerender(<ChatConnectionSetup {...props} session={{...session,engine:'codex'}} engines={[{id:'codex',name:'Codex',available:true,auth_state:'unknown'}]} onConnect={connect}/>);
 expect(screen.queryByRole('region',{name:'Connect this chat'})).not.toBeInTheDocument();
 expect(connect).not.toHaveBeenCalled();
});

it('keeps connection choices across disclosure changes without saving or dispatching them',()=>{
 const connect=vi.fn();
 render(<ChatConnectionSetup {...props} onConnect={connect}/>);
 const toggle=screen.getByRole('button',{name:'Choose connection'});
 expect(toggle).toHaveAttribute('aria-expanded','false');
 openOptions();
 fireEvent.change(screen.getByRole('combobox',{name:'Chat account'}),{target:{value:'two'}});
 fireEvent.click(screen.getByRole('button',{name:'Hide options'}));
 expect(screen.queryByRole('combobox',{name:'Chat account'})).not.toBeInTheDocument();
 openOptions();
 expect(screen.getByRole('combobox',{name:'Chat account'})).toHaveValue('two');
 expect(screen.getByRole('combobox',{name:'Chat model'})).toHaveValue('other-model');
 expect(connect).not.toHaveBeenCalled();
});

it('keeps a failed connection visible even when its options are collapsed',async()=>{
 const connect=vi.fn(async()=>{throw Error('Account access expired. Reconnect in Models & engines.')});
 render(<ChatConnectionSetup {...props} onConnect={connect}/>);
 openOptions();
 fireEvent.click(screen.getByRole('button',{name:'Use for this chat'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('Account access expired.');
 fireEvent.click(screen.getByRole('button',{name:'Hide options'}));
 expect(screen.getByRole('alert')).toBeVisible();
 expect(screen.getByRole('button',{name:'Choose connection'})).toBeEnabled();
});
