import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import SessionSettings,{RUN_PROFILES,DEFAULT_RUN_BUDGET,type SessionSettingsProps} from './SessionSettings';
import {request,type Session} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
vi.mock('./VoiceControls',()=>({default:()=>null}));
vi.mock('./PresetPicker',()=>({default:()=>null}));
const session={id:'a',engine:'internal',cwd:'/workspace',run_limits:{max_parallel_subagents:6,max_subagents_total:24}} as unknown as Session;
const props:SessionSettingsProps={open:true,onOpenChange:vi.fn(),session,running:false,turnsCount:0,runnerOptions:[],runnerAccounts:[],engines:[],providers:[],models:[],favorites:[],favoriteKey:'',onFavorite:vi.fn(),onEngine:vi.fn(),patch:vi.fn().mockResolvedValue(undefined),onError:vi.fn(),onRefresh:vi.fn(),reply:'',onTranscript:vi.fn(),onConversationSettings:vi.fn()};
beforeEach(()=>{vi.mocked(request).mockResolvedValue({presets:[],extensions:[]})});
afterEach(()=>{cleanup();vi.clearAllMocks()});
it('preserves custom limits until an explicit run profile is selected',async()=>{
 render(<SessionSettings {...props}/>);expect(screen.getByText(/existing custom limits are preserved/)).toBeInTheDocument();expect(props.patch).not.toHaveBeenCalled();expect(screen.queryByRole('slider')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('radio',{name:/Extended/}));await waitFor(()=>expect(props.patch).toHaveBeenCalledWith({run_limits:{...RUN_PROFILES[2].limits}}));
});
it('reflects default limits on conversation changes without carrying over another session’s custom limits',()=>{
 const {rerender}=render(<SessionSettings {...props}/>);expect(screen.getByRole('radio',{name:/Balanced/})).not.toBeChecked();
 rerender(<SessionSettings {...props} session={{...session,id:'b',run_limits:undefined}}/>);expect(screen.getByRole('radio',{name:/Balanced/})).toBeChecked();expect(props.patch).not.toHaveBeenCalled();
 expect(DEFAULT_RUN_BUDGET).toEqual({max_steps:256,max_seconds:14400,shell_timeout_s:120,max_parallel_subagents:3,max_subagents_total:8});
});
it('keeps settings accessible with Escape and disables active-task engine and profile edits',async()=>{
 render(<SessionSettings {...props} running/>);expect(screen.getByRole('dialog',{name:'Session settings'})).toBeInTheDocument();for(const input of screen.getAllByRole('radio'))expect(input).toBeDisabled();expect(screen.getByLabelText('Session engine')).toBeDisabled();
 fireEvent.keyDown(document,{key:'Escape',code:'Escape'});await waitFor(()=>expect(props.onOpenChange).toHaveBeenCalledWith(false));
});
it('keeps runtime and voice controls off the initial agent panel and exposes runtime intentionally',()=>{
 render(<SessionSettings {...props}/>);expect(screen.queryByLabelText('Session execution location')).not.toBeInTheDocument();
 fireEvent.click(screen.getByRole('button',{name:'Runtime'}));expect(screen.getByLabelText('Session execution location')).toBeInTheDocument();expect(screen.queryByLabelText('Session engine')).not.toBeInTheDocument();
});

it('keeps main-agent engine fixed while exposing other installed engines in focused conversations',()=>{const engines=[{id:'codex',name:'Codex'},{id:'claude',name:'Claude'}];const view=render(<SessionSettings {...props} coordinating engines={engines}/>);expect(screen.getByRole('combobox',{name:'Session engine'})).toBeDisabled();expect(screen.getByText(/Main agent uses Internal Agent for delegation/)).toBeVisible();expect(props.onEngine).not.toHaveBeenCalled();view.rerender(<SessionSettings {...props} coordinating={false} engines={engines}/>);expect(screen.getByRole('combobox',{name:'Session engine'})).toBeEnabled();fireEvent.change(screen.getByRole('combobox',{name:'Session engine'}),{target:{value:'codex'}});expect(props.onEngine).toHaveBeenCalledWith('codex')});
