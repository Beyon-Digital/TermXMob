import {expect,it} from 'vitest';
import {decodeDraftSnapshot,encodeDraftSnapshot} from './draft-snapshot';
import {encodeWindowState,validateWindowState} from './window-state';
import type {Draft} from './drafts';

function draft():Draft{
 const attachments=[{name:'one.png',mime:'image/png',data:'cGl4ZWxz'}];
 return {text:'Newer unsent message',saved:'',dirty:true,attachments,context:[],submission:{prompt:'Original message',key:'retry-original-once',context:[{type:'browser-context',context:{tab_id:'tab',document_revision:1,title:'Reference',url:'https://fixture.invalid'}}],attachments:structuredClone(attachments),intent:'queue'}};
}

it('stores exact duplicate pending images once and restores an independent exact retry payload',()=>{
 const original=draft(),wire=encodeDraftSnapshot(original);
 expect(wire.submission?.attachments).toEqual({kind:'draft-attachments-v1',count:1});
 const restored=decodeDraftSnapshot(wire);expect(restored).toEqual(original);
 restored.submission!.attachments[0].data='changed locally';expect(restored.attachments![0].data).toBe('cGl4ZWxz');expect(original.submission!.attachments[0].data).toBe('cGl4ZWxz');
});

it('accepts literal legacy snapshots and retains changed or reordered pending images literally',()=>{
 const original=draft();expect(decodeDraftSnapshot(original)).toEqual(original);
 original.attachments![0].name='new-draft.png';const wire=encodeDraftSnapshot(original);
 expect(Array.isArray(wire.submission?.attachments)).toBe(true);expect(decodeDraftSnapshot(wire)).toEqual(original);
});

it.each([
 {kind:'draft-attachments-v1',count:0},
 {kind:'draft-attachments-v1',count:2},
 {kind:'draft-attachments-v1',count:1.5},
 {kind:'draft-attachments-v1',count:5},
 {kind:'other',count:1},
 {kind:'draft-attachments-v1',count:1,url:'https://foreign.invalid/image'},
 {kind:'draft-attachments-v1'},
])('rejects a malformed, mismatched or external image reference: %j',reference=>{
 const wire=encodeDraftSnapshot(draft());wire.submission!.attachments=reference as any;
 expect(()=>decodeDraftSnapshot(wire)).toThrow('image reference');
});

it('rejects references without literal source images and nested references',()=>{
 const wire=encodeDraftSnapshot(draft());delete wire.attachments;expect(()=>decodeDraftSnapshot(wire)).toThrow('image reference');
 expect(()=>decodeDraftSnapshot({...wire,attachments:[{kind:'draft-attachments-v1',count:1}]})).toThrow('image attachment handoff');
});

it('roundtrips four maximum-size duplicate images within the existing16MB wire cap',()=>{
 const original=draft();original.attachments=Array.from({length:4},(_,index)=>({name:index+'.png',mime:'image/png',data:'A'.repeat(2700000)}));original.submission!.attachments=structuredClone(original.attachments);
 const runtime={version:1 as const,sessionId:'owned-session',buffers:[],draft:original};
 expect(JSON.stringify(runtime).length).toBeGreaterThan(16*1024*1024);
 const wire=encodeWindowState(runtime);expect(JSON.stringify(wire).length).toBeLessThan(16*1024*1024);
 const decoded=validateWindowState(wire);expect(decoded.draft).toEqual(original);
 // Browser admission and App application both validate, with a compact wire
 // object between them. Neither validation raises the original aggregate cap.
 expect(validateWindowState(encodeWindowState(decoded)).draft).toEqual(original);
});

it('keeps the original cap for distinct current and pending images',()=>{
 const original=draft();original.attachments=Array.from({length:4},(_,index)=>({name:index+'.png',mime:'image/png',data:'A'.repeat(2700000)}));original.submission!.attachments=original.attachments.map(image=>({...image,data:'B'.repeat(2700000)}));
 expect(()=>validateWindowState(encodeWindowState({version:1,sessionId:'owned-session',buffers:[],draft:original}))).toThrow('Invalid workspace handoff');
});
