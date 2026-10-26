import type {Draft,DraftAttachment,PendingSubmission} from './drafts';

/** Wire-only reference. A reference can reuse this snapshot's literal images,
 * never a URL, another window, or another submission. Legacy receivers reject
 * the non-array attachments field rather than silently losing pending images. */
export type DraftImageReference={kind:'draft-attachments-v1';count:number};
export type DraftSnapshot=Omit<Draft,'submission'>&{submission?:Omit<PendingSubmission,'attachments'>&{attachments:DraftAttachment[]|DraftImageReference}};
const record=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
function imageList(value:unknown,message:string):asserts value is DraftAttachment[]{
 if(!Array.isArray(value)||value.length>4)throw new Error(message);
 for(const image of value)if(!record(image)||typeof image.name!=='string'||image.name.length>255||typeof image.mime!=='string'||!['image/png','image/jpeg','image/webp'].includes(image.mime)||typeof image.data!=='string'||image.data.length>2800000)throw new Error(message);
}

export function encodeDraftSnapshot(draft:Draft):DraftSnapshot{
 const snapshot:DraftSnapshot=structuredClone(draft),images=snapshot.attachments,pending=snapshot.submission;
 // Compare the exact outgoing payload. Changed, reordered or removed images
 // retain their independent pending snapshot and the existing aggregate cap.
 if(images?.length&&pending&&Array.isArray(pending.attachments)&&JSON.stringify(images)===JSON.stringify(pending.attachments))pending.attachments={kind:'draft-attachments-v1',count:images.length};
 return snapshot;
}

export function decodeDraftSnapshot(value:unknown):Draft{
 if(!record(value)||typeof value.text!=='string'||typeof value.saved!=='string'||value.text.length>64000||(value.context!==undefined&&(!Array.isArray(value.context)||value.context.length>16)))throw new Error('Invalid conversation draft handoff');
 const snapshot=structuredClone(value) as DraftSnapshot;
 if(snapshot.attachments!==undefined)imageList(snapshot.attachments,'Invalid image attachment handoff');
 const pending=snapshot.submission;
 if(pending!==undefined){
  if(!record(pending)||typeof pending.prompt!=='string'||pending.prompt.length>64000||typeof pending.key!=='string'||!pending.key||pending.key.length>200||!Array.isArray(pending.context)||pending.context.length>16||new TextEncoder().encode(JSON.stringify(pending.context)).length>256000||(pending.intent!==undefined&&!['send','queue','interrupt'].includes(pending.intent))||(pending.interruptTask!==undefined&&(typeof pending.interruptTask!=='string'||pending.interruptTask.length>128)))throw new Error('Invalid pending message handoff');
  const attachments=pending.attachments;
  if(record(attachments)){
   const count=attachments.count,images=snapshot.attachments;
   if(Object.keys(attachments).length!==2||!Object.prototype.hasOwnProperty.call(attachments,'kind')||!Object.prototype.hasOwnProperty.call(attachments,'count')||attachments.kind!=='draft-attachments-v1'||typeof count!=='number'||!Number.isInteger(count)||count<1||count>4||!images||count!==images.length)throw new Error('Invalid pending message image reference');
   // Only one literal source and at most four copies are reconstructed. The
   // 16MB handoff check runs on the wire before this bounded expansion.
   pending.attachments=structuredClone(images);
  }else imageList(attachments,'Invalid pending message image handoff');
 }
 return snapshot as Draft;
}
