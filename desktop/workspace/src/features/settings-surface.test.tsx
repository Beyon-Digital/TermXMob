import {useState} from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import SettingsSurface,{SettingsNavigation,useSettingsRequest} from './SettingsSurface';
import {canonicalSettingsCategory,isSettingsCategory,settingsCategories} from '../lib/settings-categories';
import {request} from '../lib/api';
vi.mock('../lib/api',()=>({request:vi.fn()}));
afterEach(()=>{cleanup();vi.restoreAllMocks()});
function Harness(){const [category,setCategory]=useState('memory'),[open,setOpen]=useState(true);return open?<SettingsSurface category={category} onSelect={setCategory} onClose={()=>setOpen(false)}><Form name="first"/><Form name="second"/></SettingsSurface>:<button>Workspace</button>}
function Form({name}:{name:string}){const request=useSettingsRequest();return <form aria-label={name} onSubmit={event=>{event.preventDefault();void request('/fixture',{method:'PUT'}).catch(()=>{})}}><label>{name}<input required defaultValue="baseline"/></label><button type="submit">Save {name}</button><button type="button">Delete {name}</button></form>}
it('recognizes every legacy category without classifying a conversation as settings',()=>{expect(isSettingsCategory('')).toBe(false);expect(isSettingsCategory('overview')).toBe(false);expect(canonicalSettingsCategory('mcp')).toBe('connections');expect(canonicalSettingsCategory('goals')).toBe('automations');expect(isSettingsCategory('skills')).toBe(true);expect(new Set(settingsCategories.map(row=>row.id)).size).toBe(settingsCategories.length)});
it('keeps one category navigation, supports arrow focus and guards category changes',()=>{const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);render(<Harness/>);const nav=screen.getByRole('navigation',{name:'Settings categories'});expect(within(nav).getAllByRole('button')).toHaveLength(settingsCategories.length);const memory=within(nav).getByRole('button',{name:'Memory'});memory.focus();fireEvent.keyDown(memory,{key:'ArrowRight'});expect(within(nav).getByRole('button',{name:'Hooks'})).toHaveFocus();fireEvent.change(screen.getByRole('textbox',{name:'first'}),{target:{value:'unsaved'}});fireEvent.click(within(nav).getByRole('button',{name:'Hooks'}));expect(confirm).toHaveBeenCalledOnce();expect(memory).toHaveAttribute('aria-current','page');expect(screen.getByRole('textbox',{name:'first'})).toHaveValue('unsaved')});
it('guards Escape and leaves edits in place when discard is refused',()=>{vi.spyOn(window,'confirm').mockReturnValue(false);render(<Harness/>);fireEvent.change(screen.getByRole('textbox',{name:'first'}),{target:{value:'unsaved'}});fireEvent.keyDown(screen.getByRole('dialog'),{key:'Escape'});expect(screen.getByRole('dialog')).toBeInTheDocument();expect(window.dispatchEvent(new Event('termx-settings-leave',{cancelable:true}))).toBe(false)});
it('successful writes clean only their unchanged form; another draft remains protected',async()=>{vi.mocked(request).mockResolvedValue({});const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);render(<Harness/>);fireEvent.change(screen.getByRole('textbox',{name:'first'}),{target:{value:'first edit'}});fireEvent.change(screen.getByRole('textbox',{name:'second'}),{target:{value:'second edit'}});fireEvent.click(screen.getByRole('button',{name:'Save first'}));await waitFor(()=>expect(request).toHaveBeenCalled());fireEvent.click(screen.getByRole('button',{name:'Hooks'}));expect(confirm).toHaveBeenCalled();expect(screen.getByRole('textbox',{name:'second'})).toHaveValue('second edit')});
it('a failed save and edits made while a save is in flight remain dirty',async()=>{let resolve!:(value:unknown)=>void;vi.mocked(request).mockImplementation(()=>new Promise(r=>resolve=r));vi.spyOn(window,'confirm').mockReturnValue(false);render(<Harness/>);fireEvent.change(screen.getByRole('textbox',{name:'first'}),{target:{value:'old'}});fireEvent.click(screen.getByRole('button',{name:'Save first'}));fireEvent.change(screen.getByRole('textbox',{name:'first'}),{target:{value:'new'}});resolve({});await waitFor(()=>expect(screen.getByText('Unsaved settings changes')).toBeInTheDocument());fireEvent.click(screen.getByRole('button',{name:'Hooks'}));expect(screen.getByRole('textbox',{name:'first'})).toHaveValue('new')});
it('reviews destructive actions before invoking an existing handler and supports cancellation',()=>{render(<Harness/>);const button=screen.getByRole('button',{name:'Delete first'}),handler=vi.fn();button.addEventListener('click',handler);fireEvent.click(button);expect(handler).not.toHaveBeenCalled();const review=screen.getByRole('dialog',{name:'Delete first?'});fireEvent.click(within(review).getByRole('button',{name:'Cancel'}));expect(handler).not.toHaveBeenCalled();fireEvent.click(button);fireEvent.click(screen.getByRole('button',{name:'Confirm delete first'}));expect(handler).toHaveBeenCalledOnce()});
it('validates legacy div forms before dispatching their save handler',()=>{const save=vi.fn();render(<SettingsSurface category="access" onSelect={()=>{}} onClose={()=>{}}><div className="manager-form"><input aria-label="Retention" type="number" min={1} max={90} defaultValue={0}/><button onClick={save}>Save retention</button></div></SettingsSurface>);fireEvent.click(screen.getByRole('button',{name:'Save retention'}));expect(save).not.toHaveBeenCalled();expect(screen.getByLabelText('Retention')).toHaveFocus();expect(screen.getByRole('alert')).toBeInTheDocument()});

it('fits the visual viewport as a mobile keyboard opens and removes listeners on close',()=>{const viewport=Object.assign(new EventTarget(),{height:844,offsetTop:0});const remove=vi.spyOn(viewport,'removeEventListener');vi.stubGlobal('visualViewport',viewport);const view=render(<Harness/>);const dialog=screen.getByRole('dialog');expect(dialog.style.getPropertyValue('--settings-viewport-height')).toBe('844px');viewport.height=420;viewport.offsetTop=35;act(()=>{viewport.dispatchEvent(new Event('resize'))});expect(dialog.style.getPropertyValue('--settings-viewport-height')).toBe('420px');expect(dialog.style.getPropertyValue('--settings-viewport-top')).toContain('35px');expect(dialog.style.getPropertyValue('--settings-viewport-bottom')).toBe('auto');view.unmount();expect(remove).toHaveBeenCalledTimes(2);vi.unstubAllGlobals()});

it.each(['/api/skill-sources/validate','/api/skill-sources/publish-preview','/api/extensions/preview','/auth/configuration/test','/api/reviewer/evaluate','/graphql'])('keeps a draft dirty after a successful non-saving POST to %s',async(path)=>{vi.mocked(request).mockResolvedValue({ok:true});const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);function Preview(){const request=useSettingsRequest();return <form><input aria-label="Instructions" defaultValue="saved"/><button type="button" onClick={()=>void request(path,{method:'POST',body:path==='/graphql'?JSON.stringify({query:'query { providers { id } }'}):'{}'})}>Review configuration</button></form>}render(<SettingsSurface category="extensions" onSelect={()=>{}} onClose={()=>{}}><Preview/></SettingsSurface>);fireEvent.change(screen.getByLabelText('Instructions'),{target:{value:'unsaved instructions'}});fireEvent.click(screen.getByRole('button',{name:'Review configuration'}));await waitFor(()=>expect(request).toHaveBeenCalled());expect(window.dispatchEvent(new Event('termx-settings-leave',{cancelable:true}))).toBe(false);expect(confirm).toHaveBeenCalled();expect(screen.getByLabelText('Instructions')).toHaveValue('unsaved instructions')});

it('does not clear an editable form when another settings row is mutated',async()=>{vi.mocked(request).mockResolvedValue({ok:true});vi.spyOn(window,'confirm').mockReturnValue(false);function Rows(){const request=useSettingsRequest();return <section><Form name="draft"/><article><button onClick={()=>void request('/api/other-resource',{method:'PUT'})}>Update other resource</button></article></section>}render(<SettingsSurface category="models" onSelect={()=>{}} onClose={()=>{}}><Rows/></SettingsSurface>);fireEvent.change(screen.getByRole('textbox',{name:'draft'}),{target:{value:'still unsaved'}});fireEvent.click(screen.getByRole('button',{name:'Update other resource'}));await waitFor(()=>expect(request).toHaveBeenCalled());expect(window.dispatchEvent(new Event('termx-settings-leave',{cancelable:true}))).toBe(false);expect(screen.getByRole('textbox',{name:'draft'})).toHaveValue('still unsaved')});


it('uses one guarded category transition for the compact mobile picker',()=>{
 const confirm=vi.spyOn(window,'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
 render(<Harness/>);
 fireEvent.change(screen.getByRole('textbox',{name:'first'}),{target:{value:'Preserve my settings draft'}});
 const picker=screen.getByRole('combobox',{name:'Settings category'});
 fireEvent.change(picker,{target:{value:'hooks'}});
 expect(confirm).toHaveBeenCalledOnce();
 expect(picker).toHaveValue('memory');
 expect(screen.getByRole('textbox',{name:'first'})).toHaveValue('Preserve my settings draft');
 fireEvent.change(picker,{target:{value:'hooks'}});
 expect(confirm).toHaveBeenCalledTimes(2);
 expect(picker).toHaveValue('hooks');
 expect(screen.getByRole('button',{name:'Hooks'})).toHaveAttribute('aria-current','page');
});

it('keeps aliases and every existing category available in the grouped rail and mobile picker',()=>{
 render(<SettingsNavigation category="mcp" onSelect={vi.fn()}/>);
 const nav=screen.getByRole('navigation',{name:'Settings categories'});
 for(const group of ['Preferences','Workers','Host'])expect(within(nav).getByText(group)).toBeInTheDocument();
 expect(within(nav).getAllByRole('button')).toHaveLength(settingsCategories.length);
 expect(within(nav).getAllByRole('option')).toHaveLength(settingsCategories.length);
 expect(within(nav).getByRole('combobox',{name:'Settings category'})).toHaveValue('connections');
 expect(within(nav).getByRole('button',{name:'Tools & connections'})).toHaveAttribute('aria-current','page');
});

it('moves keyboard focus across navigation groups without changing category or discarding a draft',()=>{
 const select=vi.fn();render(<SettingsNavigation category="shortcuts" onSelect={select}/>);
 const shortcut=screen.getByRole('button',{name:'Keyboard shortcuts'});shortcut.focus();
 fireEvent.keyDown(shortcut,{key:'ArrowDown'});
 const models=screen.getByRole('button',{name:'Models & engines'});expect(models).toHaveFocus();
 fireEvent.keyDown(models,{key:'End'});expect(screen.getByRole('button',{name:'Safety & Auto Review'})).toHaveFocus();
 fireEvent.keyDown(screen.getByRole('button',{name:'Safety & Auto Review'}),{key:'Home'});
 expect(screen.getByRole('button',{name:'General'})).toHaveFocus();expect(select).not.toHaveBeenCalled();
});

it('preserves the caller context slot and resets scroll only after an accepted category change',()=>{
 const select=vi.fn();const view=render(<SettingsSurface category="models" scopeKey="project-a" context={<span>Project Alpha permissions</span>} onSelect={select} onClose={()=>{}}><p>Current model settings</p></SettingsSurface>);
 expect(screen.getByText('Project Alpha permissions')).toBeInTheDocument();
 const content=screen.getByText('Current model settings').closest('.settings-content')!;
 const scroll=vi.fn();Object.defineProperty(content,'scrollTo',{configurable:true,value:scroll});
 view.rerender(<SettingsSurface category="memory" scopeKey="project-a" context={<span>Project Alpha permissions</span>} onSelect={select} onClose={()=>{}}><p>Current memory settings</p></SettingsSurface>);
 expect(scroll).toHaveBeenCalledWith(0,0);expect(screen.getByText('Project Alpha permissions')).toBeInTheDocument();
});
