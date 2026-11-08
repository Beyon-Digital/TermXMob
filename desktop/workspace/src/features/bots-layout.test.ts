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

// A global label rule adds a 16px vertical margin that once put the 50 permission rows 80px apart.
it('keeps permission rows flush with a 44px hit area and flows them into columns when there is room',()=>{
 const css=read('./bots.css');
 const row=css.split('\n').find(line=>line.startsWith('.bot-tools .bot-check'));
 expect(row).toBeDefined();
 expect(row).toContain('margin:0');
 expect(row).toContain('min-height:44px');
 const group=css.split('\n').find(line=>line.startsWith('.bot-tool-group{'));
 expect(group).toContain('grid-template-columns:repeat(auto-fill');
 expect(css).toContain('.bot-tool-group h4{grid-column:1/-1');
});
