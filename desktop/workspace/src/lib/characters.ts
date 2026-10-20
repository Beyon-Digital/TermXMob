import {request,json} from './api';

export type CharacterState='idle'|'thinking'|'working'|'waiting'|'success'|'error'|'paused'|'sleeping';
export type CharacterFrame={atlas:string|number;x:number;y:number;width:number;height:number;duration_ms:number};
export type CharacterClip={frames:CharacterFrame[];loop:boolean};
export type CharacterManifest={version:number;id:string;name:string;revision:number;logical_size:{width:number;height:number};foot_anchor:{x:number;y:number};interaction_bounds:{x:number;y:number;width:number;height:number};atlases:{file:string;width:number;height:number}[];clips:Record<string,CharacterClip>;states:Record<string,string>};
export type CharacterPack={id:string;name:string;revision:number;builtin:boolean;accepted:boolean;accepted_revision?:number|null;reference_id?:string;fallback_clips?:string[]};
export type CompanionPreferences={enabled:boolean;size:number;activity:'calm'|'playful';reduced_motion:boolean;pinned:string[];hidden:string[];assignments:Record<string,string>};
export type CharacterOperation={id:string;status:string;phase:string;pack_id?:string;artifact_id?:string;error?:string};
export type CharacterLibrary={packs:CharacterPack[];preferences:CompanionPreferences;operations:CharacterOperation[]};
export type CompanionPet={id:string;session_id:string;task_id:string|null;name:string;state:CharacterState;pack_id:string;pack_revision:number;roaming:boolean;action?:{id:string;name:'wave'|'sit'|'hop'|'sleep'|'home'|'approach';target?:string;expires_at:number}|null};
export type CompanionRuntime={enabled:boolean;preferences:CompanionPreferences;pets:CompanionPet[]};
export const characterStates:CharacterState[]=['idle','thinking','working','waiting','success','error','paused','sleeping'];
export const characterClips=[...characterStates,'walk','hop','fall','sit','drag','wave'];
export const characterPath=(id:string)=>'/api/characters/packs/'+encodeURIComponent(id);
export function characterDimensions(logicalSize:{width:number;height:number},size:number){const scale=size/Math.max(logicalSize.width,logicalSize.height);return {width:logicalSize.width*scale,height:logicalSize.height*scale,scale}}
export function clipFor(manifest:CharacterManifest,state:string){const name=manifest.states[state]||state;return manifest.clips[name]||manifest.clips.idle||Object.values(manifest.clips)[0]}
export function frameAt(clip:CharacterClip|undefined,elapsed:number){
 if(!clip?.frames.length)return 0;
 const duration=clip.frames.reduce((sum,frame)=>sum+frame.duration_ms,0);
 let offset=clip.loop?Math.max(0,elapsed)%duration:Math.min(Math.max(0,elapsed),duration-1);
 for(let index=0;index<clip.frames.length;index++){offset-=clip.frames[index].duration_ms;if(offset<0)return index}
 return clip.frames.length-1;
}
export function habitatPosition(start:number,direction:number,elapsed:number,width:number,size:number,speed:number){
 const range=Math.max(0,width-size);if(!range)return {x:0,direction:1};
 const distance=start+direction*Math.max(0,elapsed)*speed/1000,period=2*range,wrapped=((distance%period)+period)%period;
 return {x:wrapped<=range?wrapped:period-wrapped,direction:(wrapped<range?1:-1)*direction};
}
export function companionClip(pet:CompanionPet,walking:boolean,now=Date.now()/1000){
 if(['waiting','error','success','paused'].includes(pet.state))return pet.state;
 if(pet.action&&pet.action.expires_at>now){if(pet.action.name==='sleep')return 'sleeping';if(['wave','sit','hop'].includes(pet.action.name))return pet.action.name}
 return walking?'walk':pet.state;
}
export async function updateCompanions(changes:Partial<CompanionPreferences>){const value=await request<CompanionPreferences>('/api/companions/preferences',json('PATCH',changes));window.dispatchEvent(new Event('termx-companions-changed'));return value}
