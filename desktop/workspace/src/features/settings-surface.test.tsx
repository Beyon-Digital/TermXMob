import {useState} from 'react';
import {afterEach,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen,waitFor,within} from '@testing-library/react';
import SettingsSurface,{SettingsNavigation,useSettingsRequest} from './SettingsSurface';
import {leaveSettings,useDirtyForm} from './settings-forms';
import {canonicalSettingsCategory,isSettingsCategory,settingsCategories} from '../lib/settings-categories';
import {request} from '../lib/api';
import {expectNoAxeViolations} from '../test-axe';
import {Dialog,DialogContent,DialogDescription,DialogTitle} from '../components/ui/dialog';
vi.mock('../lib/api',()=>({request:vi.fn()}));
afterEach(()=>{cleanup();vi.restoreAllMocks()});

/** A form that reports itself through `useDirtyForm`. */
function Draft({name,save}:{name:string;save?:()=>Promise<unknown>}){
 const {formProps,markClean}=useDirtyForm(name);
 return <form {...formProps} aria-label={name} onSubmit={event=>{event.preventDefault();void Promise.resolve(save?.()).then(markClean)}}><label>{name}<input defaultValue="baseline"/></label><button type="submit">Save {name}</button></form>;
}
/** A form from a manager that still reports saves through `useSettingsRequest`. */
function LegacyForm({name}:{name:string}){const request=useSettingsRequest();return <form aria-label={name} onSubmit={event=>{event.preventDefault();void request('/fixture',{method:'PUT'}).catch(()=>{})}}><label>{name}<input required defaultValue="baseline"/></label><button type="submit">Save {name}</button><button type="button">Delete {name}</button></form>}
function Harness({children}:{children?:React.ReactNode}){const [category,setCategory]=useState('memory'),[open,setOpen]=useState(true);return open?<SettingsSurface category={category} onSelect={setCategory} onClose={()=>setOpen(false)}>{children??<><Draft name="first"/><Draft name="second"/></>}</SettingsSurface>:<button>Workspace</button>}
const edit=(name:string,value='unsaved')=>fireEvent.change(screen.getByRole('textbox',{name}),{target:{value}});
const nav=()=>screen.getByRole('navigation',{name:'Settings categories'});
/** Asks to leave; when blocked, the discard dialog opens, so dismiss it to keep working. */
const leave=()=>{let allowed=true;act(()=>{allowed=window.dispatchEvent(new CustomEvent('termx-settings-leave',{cancelable:true,detail:{}}))});if(!allowed)fireEvent.click(within(screen.getByRole('dialog',{name:'Discard unsaved changes?'})).getByRole('button',{name:'Keep editing'}));return allowed};

it('recognizes every legacy category without classifying a conversation as settings',()=>{
 expect(isSettingsCategory('')).toBe(false);expect(isSettingsCategory('overview')).toBe(false);
 expect(canonicalSettingsCategory('mcp')).toBe('connections');expect(canonicalSettingsCategory('goals')).toBe('automations');
 for(const old of ['general','appearance','shortcuts'])expect(canonicalSettingsCategory(old)).toBe('preferences');
 expect(isSettingsCategory('skills')).toBe(true);expect(new Set(settingsCategories.map(row=>row.id)).size).toBe(settingsCategories.length);
});

it('lists every category once as links with the current page marked, and no duplicate picker',()=>{
 render(<SettingsNavigation category="mcp" onSelect={vi.fn()}/>);
 for(const group of ['Preferences','Workers','Host'])expect(within(nav()).getAllByText(group).length).toBeGreaterThan(0);
 expect(within(nav()).getAllByRole('link')).toHaveLength(settingsCategories.length);
 expect(within(nav()).queryByRole('combobox')).toBeNull();
 expect(within(nav()).getByRole('link',{name:'Tools & connections'})).toHaveAttribute('aria-current','page');
 expect(within(nav()).getAllByRole('link').filter(link=>link.hasAttribute('aria-current'))).toHaveLength(1);
 expect(within(nav()).getByRole('link',{name:'Preferences'})).toHaveAttribute('href','/manage/preferences');
});

it('selects a category on a plain click and leaves modified clicks to the browser',()=>{
 const select=vi.fn();render(<SettingsNavigation category="memory" onSelect={select}/>);
 fireEvent.click(screen.getByRole('link',{name:'Hooks'}),{ctrlKey:true});expect(select).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole('link',{name:'Hooks'}));expect(select).toHaveBeenCalledWith('hooks');
});

it('puts a count on the Bots entry when bots need the owner, named for assistive technology',async()=>{
 vi.mocked(request).mockResolvedValue({data:{needs_you:[{id:'a'},{id:'b'},{id:'c'}]}} as never);
 const {container}=render(<SettingsNavigation category="memory" onSelect={vi.fn()}/>);
 const link=await within(nav()).findByRole('link',{name:/^Bots ?, 3 need you$/});  // the visible label plus a visually hidden ", 3 need you"; the digit badge is aria-hidden
 expect(link).toHaveAttribute('href','/manage/bots');
 expect(link.querySelector('.settings-nav-count')).toHaveAttribute('aria-hidden','true');
 expect(within(nav()).getAllByRole('link')).toHaveLength(settingsCategories.length);
 expect(vi.mocked(request).mock.calls[0][0]).toBe('/graphql');
 await expectNoAxeViolations(container);
});

it('says "1 needs you" for one item and shows no count for none or when the query fails',async()=>{
 vi.mocked(request).mockResolvedValueOnce({data:{needs_you:[{id:'a'}]}} as never);
 const one=render(<SettingsNavigation category="memory" onSelect={vi.fn()}/>);
 expect(await within(nav()).findByRole('link',{name:/^Bots ?, 1 needs you$/})).toBeInTheDocument();
 one.unmount();
 vi.mocked(request).mockReset().mockResolvedValue({data:{needs_you:[]}} as never);
 const none=render(<SettingsNavigation category="memory" onSelect={vi.fn()}/>);
 await waitFor(()=>expect(request).toHaveBeenCalled());
 expect(within(nav()).getByRole('link',{name:'Bots'})).toBeInTheDocument();
 none.unmount();
 vi.mocked(request).mockReset().mockRejectedValue(new Error('no access'));
 render(<SettingsNavigation category="memory" onSelect={vi.fn()}/>);
 await waitFor(()=>expect(request).toHaveBeenCalled());
 expect(within(nav()).getByRole('link',{name:'Bots'})).toBeInTheDocument();
});

it('keeps the status region mounted and shows Save and Discard only while something is unsaved',()=>{
 render(<Harness/>);
 const status=screen.getByRole('status');expect(status).toBeEmptyDOMElement();
 expect(screen.queryByRole('button',{name:'Discard'})).toBeNull();
 edit('first');
 expect(screen.getByRole('status')).toBe(status);expect(status).toHaveTextContent('Unsaved changes');
 expect(screen.getByRole('button',{name:'Save'})).toBeInTheDocument();expect(screen.getByRole('button',{name:'Discard'})).toBeInTheDocument();
});

it('does not count typing in a dialog opened from the page as an unsaved change of the page',()=>{
 function WithDialog(){const [open,setOpen]=useState(false);return <section data-settings-autosave><button onClick={()=>setOpen(true)}>Build from artwork</button>
  <Dialog open={open} onOpenChange={setOpen}><DialogContent><DialogTitle>Build a character</DialogTitle><DialogDescription>Choose artwork.</DialogDescription><label>Artwork character name<input defaultValue=""/></label></DialogContent></Dialog></section>}
 render(<Harness><WithDialog/></Harness>);
 fireEvent.click(screen.getByRole('button',{name:'Build from artwork'}));
 fireEvent.change(screen.getByRole('textbox',{name:'Artwork character name'}),{target:{value:'Mochi'}});
 expect(screen.getByRole('status',{hidden:true})).toBeEmptyDOMElement();expect(screen.queryByRole('button',{name:'Discard',hidden:true})).toBeNull();
});

it('saves every unsaved form from the footer and then reports a clean page',async()=>{
 const saveFirst=vi.fn(async()=>{}),saveSecond=vi.fn(async()=>{});
 render(<Harness><Draft name="first" save={saveFirst}/><Draft name="second" save={saveSecond}/></Harness>);
 edit('first');fireEvent.click(screen.getByRole('button',{name:'Save'}));
 await waitFor(()=>expect(screen.getByRole('status')).toBeEmptyDOMElement());
 expect(saveFirst).toHaveBeenCalledOnce();expect(saveSecond).not.toHaveBeenCalled();
 expect(leave()).toBe(true);
});

it('asks before discarding on a category change and keeps the draft when the person keeps editing',()=>{
 const select=vi.fn();render(<SettingsSurface category="memory" onSelect={select} onClose={vi.fn()}><Draft name="first"/></SettingsSurface>);
 edit('first','Preserve my draft');
 fireEvent.click(within(nav()).getByRole('link',{name:'Hooks'}));
 const dialog=screen.getByRole('dialog',{name:'Discard unsaved changes?'});
 expect(select).not.toHaveBeenCalled();
 fireEvent.click(within(dialog).getByRole('button',{name:'Keep editing'}));
 expect(screen.queryByRole('dialog',{name:'Discard unsaved changes?'})).toBeNull();
 expect(screen.getByRole('textbox',{name:'first'})).toHaveValue('Preserve my draft');expect(select).not.toHaveBeenCalled();
});

it('discards the edits and carries on to the chosen category once the person confirms',()=>{
 const select=vi.fn();render(<SettingsSurface category="memory" onSelect={select} onClose={vi.fn()}><Draft name="first"/></SettingsSurface>);
 edit('first');fireEvent.click(within(nav()).getByRole('link',{name:'Hooks'}));
 fireEvent.click(within(screen.getByRole('dialog',{name:'Discard unsaved changes?'})).getByRole('button',{name:'Discard changes'}));
 expect(select).toHaveBeenCalledWith('hooks');
 expect(screen.getByRole('textbox',{name:'first'})).toHaveValue('baseline');expect(screen.getByRole('status')).toBeEmptyDOMElement();
});

it('resets the forms from the footer Discard after confirmation',()=>{
 render(<Harness/>);edit('first');edit('second');
 fireEvent.click(screen.getByRole('button',{name:'Discard'}));
 fireEvent.click(within(screen.getByRole('dialog',{name:'Discard unsaved changes?'})).getByRole('button',{name:'Discard changes'}));
 expect(screen.getByRole('textbox',{name:'first'})).toHaveValue('baseline');expect(screen.getByRole('textbox',{name:'second'})).toHaveValue('baseline');
 expect(leave()).toBe(true);
});

it('guards Escape and runs the pending action only after the discard is confirmed',()=>{
 const onClose=vi.fn();render(<SettingsSurface category="memory" onSelect={vi.fn()} onClose={onClose}><Draft name="first"/></SettingsSurface>);
 edit('first');fireEvent.keyDown(screen.getByRole('dialog',{name:'Settings'}),{key:'Escape'});
 expect(onClose).not.toHaveBeenCalled();
 fireEvent.click(within(screen.getByRole('dialog',{name:'Discard unsaved changes?'})).getByRole('button',{name:'Discard changes'}));
 expect(onClose).toHaveBeenCalledOnce();
});

it('lets other parts of the app leave through leaveSettings, blocking them while edits are unsaved',()=>{
 render(<Harness/>);const proceed=vi.fn();
 act(()=>leaveSettings(proceed));expect(proceed).toHaveBeenCalledOnce();
 edit('first');act(()=>leaveSettings(proceed));expect(proceed).toHaveBeenCalledOnce();
 fireEvent.click(within(screen.getByRole('dialog',{name:'Discard unsaved changes?'})).getByRole('button',{name:'Keep editing'}));
 expect(proceed).toHaveBeenCalledOnce();
 act(()=>leaveSettings(proceed));
 fireEvent.click(within(screen.getByRole('dialog',{name:'Discard unsaved changes?'})).getByRole('button',{name:'Discard changes'}));
 expect(proceed).toHaveBeenCalledTimes(2);expect(leave()).toBe(true);
});

it('a form that is removed no longer holds the page dirty',()=>{
 function Toggle(){const [show,setShow]=useState(true);return <>{show&&<Draft name="first"/>}<button onClick={()=>setShow(false)}>Hide draft form</button></>}
 render(<Harness><Toggle/></Harness>);edit('first');expect(leave()).toBe(false);
 fireEvent.click(screen.getByRole('button',{name:'Hide draft form'}));
 expect(leave()).toBe(true);
});

it('successful legacy writes clean only their own form; another draft remains protected',async()=>{
 vi.mocked(request).mockResolvedValue({});
 render(<Harness><LegacyForm name="first"/><LegacyForm name="second"/></Harness>);
 edit('first','first edit');edit('second','second edit');
 fireEvent.click(screen.getByRole('button',{name:'Save first'}));await waitFor(()=>expect(request).toHaveBeenCalled());
 await waitFor(()=>expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes'));
 expect(leave()).toBe(false);expect(screen.getByRole('textbox',{name:'second'})).toHaveValue('second edit');
});

it('a legacy save that is still in flight does not clean edits made afterwards',async()=>{
 let resolve!:(value:unknown)=>void;vi.mocked(request).mockImplementation(()=>new Promise(r=>resolve=r));
 render(<Harness><LegacyForm name="first"/></Harness>);
 edit('first','old');fireEvent.click(screen.getByRole('button',{name:'Save first'}));edit('first','new');
 await act(async()=>resolve({}));
 expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes');expect(screen.getByRole('textbox',{name:'first'})).toHaveValue('new');
});

it('reviews destructive actions before invoking an existing handler and supports cancellation',()=>{
 render(<Harness><LegacyForm name="first"/></Harness>);
 const button=screen.getByRole('button',{name:'Delete first'}),handler=vi.fn();button.addEventListener('click',handler);fireEvent.click(button);
 expect(handler).not.toHaveBeenCalled();const review=screen.getByRole('dialog',{name:'Delete first?'});
 fireEvent.click(within(review).getByRole('button',{name:'Cancel'}));expect(handler).not.toHaveBeenCalled();
 fireEvent.click(button);fireEvent.click(screen.getByRole('button',{name:'Confirm delete first'}));expect(handler).toHaveBeenCalledOnce();
});

it('cancels only an action review while preserving a dirty draft',()=>{
 function ReviewedHookDraft(){
  const [category,setCategory]=useState('hooks'),[review,setReview]=useState(false);
  return <SettingsSurface category={category} onSelect={setCategory} onClose={()=>{}}><form aria-label="Hook draft"><label>Hook command<input defaultValue="./before-turn"/></label><button type="button" onClick={()=>setReview(true)}>Review hook</button></form><Dialog open={review} onOpenChange={setReview}><DialogContent data-reviewed-action><DialogTitle>Review lifecycle hook</DialogTitle><DialogDescription>Review the exact hook command before saving.</DialogDescription><button type="button" onClick={()=>setReview(false)}>Cancel review</button></DialogContent></Dialog></SettingsSurface>;
 }
 render(<ReviewedHookDraft/>);
 fireEvent.change(screen.getByRole('textbox',{name:'Hook command'}),{target:{value:'./keep-this-draft'}});
 fireEvent.click(screen.getByRole('button',{name:'Review hook'}));
 fireEvent.click(within(screen.getByRole('dialog',{name:'Review lifecycle hook'})).getByRole('button',{name:'Cancel review'}));
 expect(screen.queryByRole('dialog',{name:'Review lifecycle hook'})).toBeNull();
 expect(screen.queryByRole('dialog',{name:'Discard unsaved changes?'})).toBeNull();
 expect(screen.getByRole('textbox',{name:'Hook command'})).toHaveValue('./keep-this-draft');expect(screen.getByRole('status')).toHaveTextContent('Unsaved changes');
 fireEvent.click(within(nav()).getByRole('link',{name:'Memory'}));
 fireEvent.click(within(screen.getByRole('dialog',{name:'Discard unsaved changes?'})).getByRole('button',{name:'Keep editing'}));
 expect(within(nav()).getByRole('link',{name:'Hooks'})).toHaveAttribute('aria-current','page');
});

it('validates legacy div forms before dispatching their save handler',()=>{
 const save=vi.fn();render(<SettingsSurface category="access" onSelect={()=>{}} onClose={()=>{}}><div className="manager-form"><input aria-label="Retention" type="number" min={1} max={90} defaultValue={0}/><button onClick={save}>Save retention</button></div></SettingsSurface>);
 fireEvent.click(screen.getByRole('button',{name:'Save retention'}));expect(save).not.toHaveBeenCalled();expect(screen.getByLabelText('Retention')).toHaveFocus();expect(screen.getByRole('alert')).toBeInTheDocument();
});

it('fits the visual viewport as a mobile keyboard opens and removes listeners on close',()=>{
 const viewport=Object.assign(new EventTarget(),{height:844,offsetTop:0});const remove=vi.spyOn(viewport,'removeEventListener');vi.stubGlobal('visualViewport',viewport);
 const view=render(<Harness/>);const dialog=screen.getByRole('dialog',{name:'Settings'});
 expect(dialog.style.getPropertyValue('--settings-viewport-height')).toBe('844px');
 viewport.height=420;viewport.offsetTop=35;act(()=>{viewport.dispatchEvent(new Event('resize'))});
 expect(dialog.style.getPropertyValue('--settings-viewport-height')).toBe('420px');expect(dialog.style.getPropertyValue('--settings-viewport-top')).toContain('35px');expect(dialog.style.getPropertyValue('--settings-viewport-bottom')).toBe('auto');
 view.unmount();expect(remove).toHaveBeenCalledTimes(2);vi.unstubAllGlobals();
});

it.each(['/api/skill-sources/validate','/api/skill-sources/publish-preview','/api/extensions/preview','/auth/configuration/test','/api/reviewer/evaluate','/graphql'])('keeps a draft dirty after a successful non-saving POST to %s',async path=>{
 vi.mocked(request).mockResolvedValue({ok:true});
 function Preview(){const request=useSettingsRequest();return <form><input aria-label="Instructions" defaultValue="saved"/><button type="button" onClick={()=>void request(path,{method:'POST',body:path==='/graphql'?JSON.stringify({query:'query { providers { id } }'}):'{}'})}>Review configuration</button></form>}
 render(<SettingsSurface category="extensions" onSelect={()=>{}} onClose={()=>{}}><Preview/></SettingsSurface>);
 fireEvent.change(screen.getByLabelText('Instructions'),{target:{value:'unsaved instructions'}});
 fireEvent.click(screen.getByRole('button',{name:'Review configuration'}));await waitFor(()=>expect(request).toHaveBeenCalled());
 expect(leave()).toBe(false);expect(screen.getByLabelText('Instructions')).toHaveValue('unsaved instructions');
});

it('does not clear an editable form when another settings row is mutated',async()=>{
 vi.mocked(request).mockResolvedValue({ok:true});
 function Rows(){const request=useSettingsRequest();return <section><LegacyForm name="draft"/><article><button onClick={()=>void request('/api/other-resource',{method:'PUT'})}>Update other resource</button></article></section>}
 render(<SettingsSurface category="models" onSelect={()=>{}} onClose={()=>{}}><Rows/></SettingsSurface>);
 fireEvent.change(screen.getByRole('textbox',{name:'draft'}),{target:{value:'still unsaved'}});
 fireEvent.click(screen.getByRole('button',{name:'Update other resource'}));await waitFor(()=>expect(request).toHaveBeenCalled());
 expect(leave()).toBe(false);expect(screen.getByRole('textbox',{name:'draft'})).toHaveValue('still unsaved');
});

it('preserves the caller context slot and resets scroll only after an accepted category change',()=>{
 const select=vi.fn();const view=render(<SettingsSurface category="models" scopeKey="project-a" context={<span>Project Alpha permissions</span>} onSelect={select} onClose={()=>{}}><p>Current model settings</p></SettingsSurface>);
 expect(screen.getByText('Project Alpha permissions')).toBeInTheDocument();
 const content=screen.getByText('Current model settings').closest('.settings-content')!;
 const scroll=vi.fn();Object.defineProperty(content,'scrollTo',{configurable:true,value:scroll});
 view.rerender(<SettingsSurface category="memory" scopeKey="project-a" context={<span>Project Alpha permissions</span>} onSelect={select} onClose={()=>{}}><p>Current memory settings</p></SettingsSurface>);
 expect(scroll).toHaveBeenCalledWith(0,0);expect(screen.getByText('Project Alpha permissions')).toBeInTheDocument();
});

it('has no accessibility violations with unsaved changes showing',async()=>{
 render(<Harness/>);edit('first');
 await expectNoAxeViolations(screen.getByRole('dialog',{name:'Settings'}));
});
