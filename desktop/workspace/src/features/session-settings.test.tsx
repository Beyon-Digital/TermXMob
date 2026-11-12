import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import SessionSettings,{type SessionSettingsProps} from './SessionSettings';
import {request,type Session} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
vi.mock('./VoiceControls',()=>({default:()=>null}));
vi.mock('./PresetPicker',()=>({default:()=>null}));
const session={id:'a',engine:'internal',cwd:'/workspace',run_limits:{max_parallel_subagents:6,max_subagents_total:24}} as unknown as Session;
const props:SessionSettingsProps={open:true,onOpenChange:vi.fn(),session,running:false,turnsCount:0,runnerOptions:[],runnerAccounts:[],engines:[],providers:[],models:[],favorites:[],favoriteKey:'',onFavorite:vi.fn(),onEngine:vi.fn(),patch:vi.fn().mockResolvedValue(undefined),onError:vi.fn(),onRefresh:vi.fn(),reply:'',onTranscript:vi.fn(),onConversationSettings:vi.fn()};
beforeEach(()=>{vi.mocked(request).mockResolvedValue({presets:[],extensions:[]})});
afterEach(()=>{cleanup();vi.clearAllMocks()});
it('does not offer task quota profiles or write limits when opening a session with legacy quotas',()=>{
 render(<SessionSettings {...props}/>);
 expect(screen.queryByText('Run style')).not.toBeInTheDocument();
 expect(screen.queryByRole('radio')).not.toBeInTheDocument();
 expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
 expect(props.patch).not.toHaveBeenCalled();
 expect(screen.getByLabelText('Session engine')).toBeEnabled();
});
it('keeps session settings free of quota defaults across conversation changes',()=>{
 const {rerender}=render(<SessionSettings {...props}/>);
 rerender(<SessionSettings {...props} session={{...session,id:'b',run_limits:undefined}}/>);
 expect(screen.queryByRole('radio')).not.toBeInTheDocument();
 expect(screen.queryByText('Run style')).not.toBeInTheDocument();
 expect(props.patch).not.toHaveBeenCalled();
});
it('keeps settings accessible with Escape and disables active-task engine edits',async()=>{
 render(<SessionSettings {...props} running/>);
 expect(screen.getByRole('dialog',{name:'Session settings'})).toBeInTheDocument();
 expect(screen.queryByRole('radio')).not.toBeInTheDocument();
 expect(screen.getByLabelText('Session engine')).toBeDisabled();
 fireEvent.keyDown(document,{key:'Escape',code:'Escape'});
 await waitFor(()=>expect(props.onOpenChange).toHaveBeenCalledWith(false));
});
it('keeps runtime and voice controls off the initial agent panel and exposes runtime intentionally',()=>{
 render(<SessionSettings {...props}/>);expect(screen.queryByLabelText('Session execution location')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Runtime'}));expect(screen.getByLabelText('Session execution location')).toBeInTheDocument();expect(screen.queryByLabelText('Session engine')).not.toBeInTheDocument();
});

it('keeps main-agent engine fixed while exposing other installed engines in focused conversations',()=>{const engines=[{id:'codex',name:'Codex'},{id:'claude',name:'Claude'}];const view=render(<SessionSettings {...props} coordinating engines={engines}/>);expect(screen.getByRole('combobox',{name:'Session engine'})).toBeDisabled();expect(screen.getByText(/can delegate work to installed engines/)).toBeVisible();expect(props.onEngine).not.toHaveBeenCalled();view.rerender(<SessionSettings {...props} coordinating={false} engines={engines}/>);expect(screen.getByRole('combobox',{name:'Session engine'})).toBeEnabled();fireEvent.change(screen.getByRole('combobox',{name:'Session engine'}),{target:{value:'codex'}});expect(props.onEngine).toHaveBeenCalledWith('codex')});
