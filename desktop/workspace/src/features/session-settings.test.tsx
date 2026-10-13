import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import SessionSettings,{type SessionSettingsProps} from './SessionSettings';
import {request,type Session} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
vi.mock('./SessionModelControls',()=>({default:()=>null}));
vi.mock('./VoiceControls',()=>({default:()=>null}));
vi.mock('./PresetPicker',()=>({default:()=>null}));
const session={id:'a',engine:'internal',cwd:'/workspace',run_limits:{max_parallel_subagents:6,max_subagents_total:24}} as unknown as Session;
const defaults={max_steps:256,max_seconds:14400,shell_timeout_s:120,max_parallel_subagents:3,max_subagents_total:8};
const props:SessionSettingsProps={open:true,onOpenChange:vi.fn(),session,running:false,turnsCount:0,runnerOptions:[],runnerAccounts:[],engines:[],providers:[],models:[],favorites:[],favoriteKey:'',onFavorite:vi.fn(),onEngine:vi.fn(),patch:vi.fn().mockResolvedValue(undefined),onError:vi.fn(),onRefresh:vi.fn(),reply:'',onTranscript:vi.fn(),onConversationSettings:vi.fn()};
beforeEach(()=>{vi.mocked(request).mockResolvedValue({presets:[],extensions:[]})});
afterEach(()=>{cleanup();vi.clearAllMocks();vi.unstubAllGlobals()});
it('matches backend worker limits and saves the selected budget',async()=>{
 render(<SessionSettings {...props}/>);const parallel=screen.getByLabelText('Run max_parallel_subagents'),total=screen.getByLabelText('Run max_subagents_total');expect(parallel).toHaveAttribute('max','8');expect(total).toHaveAttribute('max','32');fireEvent.change(parallel,{target:{value:'8'}});fireEvent.change(total,{target:{value:'32'}});fireEvent.click(screen.getByRole('button',{name:'Save run budget'}));await waitFor(()=>expect(props.patch).toHaveBeenCalledWith({run_limits:{...defaults,max_parallel_subagents:8,max_subagents_total:32}}));
});
it('resets unsaved and stored limits when switching to a default conversation',async()=>{
 const {rerender}=render(<SessionSettings {...props}/>);expect(screen.getByLabelText('Run max_parallel_subagents')).toHaveValue('6');fireEvent.change(screen.getByLabelText('Run max_steps'),{target:{value:'512'}});rerender(<SessionSettings {...props} session={{...session,id:'b',run_limits:undefined}}/>);expect(screen.getByLabelText('Run max_steps')).toHaveValue('256');expect(screen.getByLabelText('Run max_parallel_subagents')).toHaveValue('3');expect(screen.getByLabelText('Run max_subagents_total')).toHaveValue('8');fireEvent.click(screen.getByRole('button',{name:'Save run budget'}));await waitFor(()=>expect(props.patch).toHaveBeenCalledWith({run_limits:defaults}));
});
it('keeps compact settings accessible with Escape and disables active-task budget edits',async()=>{
 Object.defineProperty(window,'matchMedia',{configurable:true,value:vi.fn(()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()}))});render(<SessionSettings {...props} running/>);expect(screen.getByRole('dialog',{name:'Session settings'})).toHaveClass('settings-sheet');for(const key of Object.keys(defaults))expect(screen.getByLabelText('Run '+key)).toBeDisabled();expect(screen.getByRole('button',{name:'Save run budget'})).toBeDisabled();fireEvent.keyDown(document,{key:'Escape',code:'Escape'});await waitFor(()=>expect(props.onOpenChange).toHaveBeenCalledWith(false));
});
