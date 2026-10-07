import {afterEach,expect,it} from 'vitest';
import {acceptTransferredDraft} from './redock-state';
import {sessionDrafts} from './drafts';
afterEach(()=>sessionDrafts.clear());
it('keeps an existing unsent local image when incoming state contains a different image',()=>{const original={text:'',saved:'',dirty:false,attachments:[{name:'main.png',mime:'image/png',data:'bWFpbg=='}]};sessionDrafts.set('session',original);expect(()=>acceptTransferredDraft({version:1,sessionId:'session',buffers:[],draft:{text:'',saved:'',dirty:false,attachments:[{name:'source.png',mime:'image/png',data:'c291cmNl'}]}})).toThrow('different unsent draft');expect(sessionDrafts.get('session')).toEqual(original)});
