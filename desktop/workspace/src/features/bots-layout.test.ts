import {readFileSync} from 'node:fs';
import {expect,it} from 'vitest';

const read=(name:string)=>readFileSync(new URL(name,import.meta.url),'utf8');

// jsdom does not run the cascade, so this guards the rule that keeps checkbox rows in a row.
it('keeps bot checkbox rows horizontal against the global label and manager-form rules',()=>{
 expect(read('./managers.css')).toContain('.manager-form label:has(>input[type=checkbox])');
 const rule=read('./bots.css').split('\n').find(line=>line.includes('label.bot-check:has(>input[type=checkbox])'));
 expect(rule).toBeDefined();
 expect(rule).toContain('flex-direction:row');
 expect(rule).toContain('align-items:flex-start');
});
