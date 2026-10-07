import {persistBuffers,snapshotBuffers} from './window-state';
import {persistArtifactDrafts,snapshotArtifactDrafts} from './artifact-drafts';
import {persistWindowDraft} from './window-journal';
import {sessionDrafts} from './drafts';
import {flushRedockDraft} from './redock-state';
// Local durability is attempted before host lock; an unavailable or conflicting
// autosave cannot leave the machine unlocked indefinitely. Late CAS is never replayed.
export async function preserveBeforeLock(owner:string,deadline=2500){
 const warnings:string[]=[];
 for(const operation of [()=>persistBuffers(owner,snapshotBuffers(owner)),()=>persistArtifactDrafts(owner,snapshotArtifactDrafts(owner))])try{await operation()}catch(error){warnings.push(String(error))}
 for(const [id,draft] of sessionDrafts){try{await persistWindowDraft(owner,id,draft)}catch(error){warnings.push(String(error))}}
 let timer:ReturnType<typeof setTimeout>|undefined;
 try{await Promise.race([Promise.all([...sessionDrafts.keys()].map(async id=>{try{await flushRedockDraft(id)}catch(error){warnings.push(String(error))}})),new Promise<void>(resolve=>{timer=setTimeout(()=>{warnings.push('Conversation autosave is still pending; local drafts remain preserved.');resolve()},deadline)})])}finally{if(timer)clearTimeout(timer)}
 return warnings;
}
