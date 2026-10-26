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

it('requires an explicit choice and applies the model belonging to the selected account once',async()=>{
 let finish!:(saved:boolean)=>void;
 const connect=vi.fn(()=>new Promise<boolean>(resolve=>{finish=resolve}));
 render(<ChatConnectionSetup {...props} onConnect={connect}/>);
 expect(connect).not.toHaveBeenCalled();
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
 expect(screen.getByRole('combobox',{name:'Chat model'})).toBeDisabled();
 expect(screen.getByRole('button',{name:'Use for this chat'})).toBeDisabled();
 expect(screen.getByText('Add a default model to First account in Models & engines, or choose another account.')).toBeVisible();
 expect(connect).not.toHaveBeenCalled();
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
 fireEvent.click(screen.getByRole('button',{name:'Use for this chat'}));
 view.rerender(<ChatConnectionSetup key="second" {...props} session={{...session,id:'second'}} onConnect={connect}/>);
 await act(async()=>finish(false));
 await waitFor(()=>expect(screen.getByRole('button',{name:'Use for this chat'})).toBeEnabled());
 expect(screen.queryByRole('alert')).not.toBeInTheDocument();
 expect(screen.queryByRole('status')).not.toBeInTheDocument();
});
