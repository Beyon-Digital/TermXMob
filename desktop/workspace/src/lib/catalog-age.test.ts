import {expect,it} from 'vitest';
import {catalogAge} from './catalog-age';

const now=1_700_000_000_000;
it('asks for a first refresh when discovery has never run',()=>{
 expect(catalogAge(null,now)).toContain('Choose Refresh models');
 expect(catalogAge(undefined,now)).toContain('Choose Refresh models');
});
it.each([[10,'just now'],[60,'1 minute ago.'],[600,'10 minutes ago.'],[3600,'1 hour ago.'],[7200,'2 hours ago.'],[86400*3,'3 days ago.']])('describes a catalog %i seconds old',(age,text)=>{
 expect(catalogAge(now/1000-age,now)).toContain(text);
});
it('treats a clock that is slightly ahead as just now',()=>{expect(catalogAge(now/1000+30,now)).toContain('just now')});
