import {expect,it} from 'vitest';
import {hasUnsentDraft} from './window-journal';
it('includes context-only and image-only drafts in window recovery',()=>{expect(hasUnsentDraft({text:'',saved:'',dirty:true,context:[{type:'approved-browser-upload',tab_id:'owned-tab',file:{id:'owned-file',filename:'notes.txt'}}]})).toBe(true);expect(hasUnsentDraft({text:'',saved:'',dirty:false,attachments:[{name:'image.png',mime:'image/png',data:'YWJj'}]})).toBe(true);expect(hasUnsentDraft({text:'  ',saved:'',dirty:false,context:[],attachments:[]})).toBe(false)});
