import {beforeEach,expect,it,vi} from 'vitest';
import {request} from './api';
import {mediaIntent,clearMediaIntent} from './media-intent';
import {defaultMusicDraft,musicDraftErrors,musicModelDuration,musicModels,musicOutput,musicDraftFromJob,submitMusic,mergeMusicJobs,type MusicJob,type MusicModel} from './music-studio';
vi.mock('./api',async original=>({...await original<typeof import('./api')>(),request:vi.fn()}));
vi.mock('./media-intent',()=>({mediaIntent:vi.fn(),clearMediaIntent:vi.fn()}));
const model:MusicModel={id:'elevenlabs/music_v2_5',adapter:'elevenlabs',kind:'audio',label:'Eleven Music 2.5',modes:['music'],description:'Official music model',aspect_ratios:[],resolutions:[],durations:[30,60,120],supported_music_modes:['simple','custom'],supports_custom_lyrics:true,supports_instrumental:true,supports_negative_styles:true,lyrics_constraints:'sections',duration_mode:'exact',prompt_max_length:4100,lyrics_max_length:12000,duration_min:3,duration_max:600,custom_duration_max:120};
const pro:MusicModel={...model,id:'google/lyria-3-pro-preview',label:'Lyria 3 Pro Preview',adapter:'openrouter',duration_mode:'prompt',duration_min:3,duration_max:184,custom_duration_max:undefined,lyrics_constraints:'prompt'};
const clip:MusicModel={...pro,id:'google/lyria-3-clip-preview',label:'Lyria 3 Clip Preview',duration_mode:'fixed',duration_min:30,duration_max:30,durations:[30]};
const job:MusicJob={id:'song',kind:'audio',request_id:'request-fixture',project_id:'project',provider_id:'account',model:model.id,prompt:'A calm piano song',status:'completed',created:1,updated:1,outputs:['/api/media/artifacts/owned/export?format=original'],parameters:{music_mode:'simple',title:'Quiet morning',duration:60}};
beforeEach(()=>{vi.clearAllMocks();vi.mocked(mediaIntent).mockResolvedValue({id:'request-fixture',key:'intent-key'});vi.mocked(request).mockResolvedValue(job);});
it('validates actual supported music modes, lyrics and duration without inventing provider controls',()=>{
 expect(musicDraftErrors({...defaultMusicDraft,prompt:'Soft piano'},model)).toEqual([]);
 expect(musicDraftErrors({...defaultMusicDraft,prompt:'Soft piano',duration:601},model).join(' ')).toContain('3 to 600');
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',lyrics:'[Verse]\nExact words',duration:121},model).join(' ')).toContain('3 to 120');
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',lyrics:'[Verse]\nExact words'},model)).toEqual([]);
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',instrumental:true,styles:'Ambient piano'},model)).toEqual([]);
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',instrumental:true,lyrics:'Words',styles:'Piano'},model).join(' ')).toContain('Remove lyrics');
});
it('rejects provider section and line bounds without truncating or rewriting lyrics',()=>{
 const draft={...defaultMusicDraft,mode:'custom' as const,lyrics:'[Verse]\n'+'x'.repeat(201)};
 expect(musicDraftErrors(draft,model).join(' ')).toContain('200 characters');
 expect(musicDraftErrors({...draft,lyrics:Array(31).fill('line').join('\n')},model).join(' ')).toContain('30 lines');
 expect(musicDraftErrors({...draft,lyrics:'[Verse]\nOne\n[Chorus]\nTwo',duration:3},model).join(' ')).toContain('3 seconds for each');
 expect(draft.lyrics).toContain('x'.repeat(201));
});
it('offers only models explicitly supporting music across provider adapters',()=>{
 const tts={...pro,id:'provider/speech',modes:[]};
 expect(musicModels({models:[model,pro,clip,tts,{...pro,id:'image-model',kind:'image'}],accounts:[]}).map(row=>row.id)).toEqual([model.id,pro.id,clip.id]);
});
it('uses model duration bounds without changing the original music draft',()=>{
 const draft={...defaultMusicDraft,mode:'custom' as const,lyrics:'Keep these exact words',styles:'Soul',title:'Keep this title',duration:180};
 expect(musicModelDuration(draft,clip)).toBe(30);
 expect(musicModelDuration(draft,pro)).toBe(180);
 expect(musicModelDuration(draft,model)).toBe(120);
 expect(draft).toMatchObject({lyrics:'Keep these exact words',styles:'Soul',title:'Keep this title',duration:180});
 expect(musicDraftErrors({...draft,duration:60},clip).join(' ')).toContain('30-second tracks');
});
it('validates every advertised mode and capability before submitting',()=>{
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',lyrics:'Keep these words'}, {...pro,supported_music_modes:['simple']}).join(' ')).toContain('Advanced mode');
 expect(musicDraftErrors({...defaultMusicDraft,prompt:'Piano'}, {...pro,supported_music_modes:['custom']}).join(' ')).toContain('Simple mode');
 expect(musicDraftErrors({...defaultMusicDraft,prompt:'Piano',instrumental:true}, {...pro,supports_instrumental:false}).join(' ')).toContain('does not support instrumental');
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',instrumental:true,styles:'Piano'}, {...pro,supports_instrumental:false}).join(' ')).toContain('does not support instrumental');
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',lyrics:'Words',negative_styles:'Rock'}, {...pro,supports_negative_styles:false}).join(' ')).toContain('excluded styles');
});
it('keeps prompt-based lyrics free of ElevenLabs composition section constraints',()=>{
 const lyrics=Array(32).fill('x'.repeat(201)).join('\n');
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',lyrics,duration:30},clip)).toEqual([]);
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',lyrics},model).join(' ')).toContain('200 characters');
 expect(musicDraftErrors({...defaultMusicDraft,mode:'custom',lyrics}, {...pro,lyrics_max_length:100}).join(' ')).toContain('Shorten the text');
 expect(musicDraftErrors({...defaultMusicDraft,prompt:'Soft piano'}, {...pro,prompt_max_length:5}).join(' ')).toContain('Shorten the text');
});
it('accepts only owned artifact output paths and reconstructs truthful music drafts',()=>{
 expect(musicOutput(job)).toBe(job.outputs[0]);
 expect(musicOutput({...job,outputs:['https://provider.example/music.mp3','javascript:alert(1)']})).toBeNull();
 expect(musicOutput({...job,status:'unknown'})).toBeNull();
 expect(musicDraftFromJob(job)).toMatchObject({mode:'simple',title:'Quiet morning',duration:60});
});
it('sends one exact authorized request and clears only its matching accepted intent',async()=>{
 const input={...defaultMusicDraft,provider_id:'account',model:model.id,prompt:'Soft piano',acknowledge_billing:true};
 await submitMusic('owner','project',input);
 expect(mediaIntent).toHaveBeenCalledWith('owner','project',{project_id:'project',...input},'studio-music');
 expect(JSON.parse(String(vi.mocked(request).mock.calls[0][1]?.body))).toEqual({request_id:'request-fixture',project_id:'project',...input});
 expect(clearMediaIntent).toHaveBeenCalledWith('intent-key','request-fixture');
});
it('never dispatches canceled hashing or a failed final active guard',async()=>{
 let finish!:(value:{id:string;key:string})=>void;vi.mocked(mediaIntent).mockReturnValue(new Promise(resolve=>finish=resolve));
 const abort=new AbortController(),pending=submitMusic('owner','project',{...defaultMusicDraft,provider_id:'account',model:model.id,prompt:'Piano',acknowledge_billing:true},abort.signal);
 abort.abort();finish({id:'request-fixture',key:'intent-key'});await expect(pending).rejects.toMatchObject({name:'AbortError'});expect(request).not.toHaveBeenCalled();
 vi.mocked(mediaIntent).mockResolvedValue({id:'request-fixture',key:'intent-key'});
 await expect(submitMusic('owner','project',{...defaultMusicDraft,provider_id:'account',model:model.id,prompt:'Piano',acknowledge_billing:true},undefined,undefined,()=>false)).rejects.toMatchObject({name:'AbortError'});expect(request).not.toHaveBeenCalled();
});
it('preserves the idempotency receipt on transport uncertainty or a mismatched request ID',async()=>{
 vi.mocked(request).mockRejectedValueOnce(Error('Connection lost'));
 const input={...defaultMusicDraft,provider_id:'account',model:model.id,prompt:'Piano',acknowledge_billing:true};
 await expect(submitMusic('owner','project',input)).rejects.toThrow('Connection lost');expect(clearMediaIntent).not.toHaveBeenCalled();
 vi.mocked(request).mockResolvedValueOnce({...job,request_id:'different-request'});
 await expect(submitMusic('owner','project',input)).rejects.toThrow('matching music receipt');expect(clearMediaIntent).not.toHaveBeenCalled();
});
it('requires charge acknowledgment and never downgrades completed receipts',async()=>{
 await expect(submitMusic('owner','project',{...defaultMusicDraft,provider_id:'account',model:model.id,prompt:'Piano',acknowledge_billing:false})).rejects.toThrow('Review and confirm');expect(request).not.toHaveBeenCalled();
 expect(mergeMusicJobs([job],[{...job,status:'queued',updated:1}])[0].status).toBe('completed');
});

it('checks prompt-duration limits against the model maximum rather than an approximate target',()=>{
 const draft={...defaultMusicDraft,prompt:'Soft piano',duration:60};
 expect(musicDraftErrors(draft,pro,120).join(' ')).toContain('can exceed your 120-second Music limit');
 expect(musicDraftErrors({...draft,duration:30},clip,120)).toEqual([]);
 expect(musicDraftErrors(draft,model,120)).toEqual([]);
 expect(musicDraftErrors({...draft,duration:180},model,120).join(' ')).toContain('within your 120-second Music limit');
 expect(musicDraftErrors(draft,pro,null)).toEqual([]);
});
