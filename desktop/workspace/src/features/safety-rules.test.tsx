import {afterEach,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import {SafetyManager} from './SafetyManager';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
const api=vi.mocked(request);
afterEach(()=>{cleanup();vi.resetAllMocks()});
const original={id:'owned-rule',decision:'BLOCK',expires_at:Date.now()/1000+3600,scope:{principal_id:'owner',session_id:'original-device',project_id:'project',run_id:'task',tool_id:'browser.observe',target:'https://example.test',intended_effect:'observe',grant_id:'grant',policy_version:3,profile_id:'profile'}};
function setup(fail=false){let rules=[original];api.mockImplementation(async(path,init)=>{if(path==='/api/browser/reviewer')return {configuration:null,evaluations:[],active:false};if(path==='/api/browser/reviews')return [];if(path==='/api/browser/rules')return rules;if(path==='/auth/me')return {principal:{scopes:['desktop-view','desktop-control']}};if(path==='/api/browser/rules/owned-rule'){if(init?.method==='DELETE'){rules=[];return {ok:true}}if(fail)throw Error('Remembered rule authority expired or changed');rules=[{...original,...JSON.parse(String(init?.body)),expires_at:Date.now()/1000+600}];return rules[0]}throw Error(path)})}
it('inspects exact immutable scope, edits decision/expiry only, and revokes the owned rule',async()=>{
 setup();render(<SafetyManager/>);const article=await screen.findByRole('article',{name:'Remembered rule owned-rule'});
 fireEvent.click(within(article).getByText('Inspect exact remembered scope'));expect(within(article).getByRole('region',{name:'Immutable remembered rule scope'})).toHaveTextContent('original-device');
 fireEvent.click(within(article).getByRole('button',{name:'Edit remembered rule'}));fireEvent.change(within(article).getByLabelText('Remembered decision'),{target:{value:'ALLOW'}});fireEvent.change(within(article).getByLabelText('Expires after seconds'),{target:{value:'600'}});fireEvent.click(within(article).getByRole('button',{name:'Save remembered rule'}));
 await waitFor(()=>expect(api).toHaveBeenCalledWith('/api/browser/rules/owned-rule',{method:'PATCH',body:JSON.stringify({decision:'ALLOW',expires_in:600,revision:1})}));await screen.findByText('Remembered rule updated. Scope and host restrictions are unchanged.');expect(screen.queryByLabelText('Rule target')).not.toBeInTheDocument();fireEvent.click(within(article).getByRole('button',{name:'Revoke'}));await screen.findByText('No remembered permissions.');
});
it('shows stale/revoked edit denial without implying that the rule changed',async()=>{setup(true);render(<SafetyManager/>);fireEvent.click(await screen.findByRole('button',{name:'Edit remembered rule'}));fireEvent.click(screen.getByRole('button',{name:'Save remembered rule'}));await screen.findByRole('alert');expect(screen.getByText('BLOCK · browser.observe')).toBeInTheDocument();expect(screen.queryByText('Remembered rule updated. Scope and host restrictions are unchanged.')).not.toBeInTheDocument()});
