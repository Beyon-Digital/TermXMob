import {act,cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import {useState} from 'react';
import WorkspaceNavigation from './WorkspaceNavigation';
import {Dialog,DialogContent,DialogDescription,DialogTitle,DialogTrigger} from './ui/dialog';
function Fixture({hover=false,initial=false}:{hover?:boolean;initial?:boolean}){const [open,setOpen]=useState(initial);return <><WorkspaceNavigation open={open} hover={hover} width={244} onOpenChange={setOpen} rail={<button>Navigation action</button>} footer={<button>Settings</button>} resize={<div role="separator"/>}><button>Conversation</button></WorkspaceNavigation><button>Editor</button></>}
beforeEach(()=>{vi.useFakeTimers();vi.stubGlobal('matchMedia',vi.fn(()=>({matches:false,addEventListener:vi.fn(),removeEventListener:vi.fn()})))});
afterEach(()=>{cleanup();vi.useRealTimers();vi.unstubAllGlobals()});
it('collapses pinned navigation while leaving a keyboard accessible rail',()=>{render(<Fixture initial/>);expect(screen.getByRole('complementary')).toBeVisible();expect(screen.getByRole('separator')).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'Hide sidebar'}));expect(screen.queryByRole('complementary')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Show sidebar'})).toHaveAttribute('aria-expanded','false');fireEvent.click(screen.getByRole('button',{name:'Show sidebar'}));expect(screen.getByRole('complementary')).toBeVisible()});
it('reveals a floating sidebar on hover without a layout resize handle and dismisses on pointer leave',()=>{const {container}=render(<Fixture hover/>);const root=container.querySelector('.workspace-navigation')!;fireEvent.pointerEnter(root,{pointerType:'mouse'});expect(screen.getByRole('complementary')).toBeVisible();expect(screen.queryByRole('separator')).not.toBeInTheDocument();fireEvent.pointerLeave(root);act(()=>vi.advanceTimersByTime(230));expect(screen.queryByRole('complementary')).not.toBeInTheDocument()});
it('holds navigation during keyboard interaction, then dismisses with Escape without immediately reopening',()=>{const {container}=render(<Fixture hover/>);fireEvent.pointerEnter(container.querySelector('.workspace-navigation')!,{pointerType:'mouse'});act(()=>screen.getByRole('button',{name:'Conversation'}).focus());fireEvent.pointerLeave(container.querySelector('.workspace-navigation')!);act(()=>vi.advanceTimersByTime(230));expect(screen.getByRole('complementary')).toBeVisible();fireEvent.keyDown(document,{key:'Escape'});expect(screen.queryByRole('complementary')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Show sidebar'})).toHaveFocus()});
it('closes a floating sidebar when the user clicks the editor',()=>{render(<Fixture hover/>);fireEvent.click(screen.getByRole('button',{name:'Show sidebar'}));fireEvent.pointerDown(screen.getByRole('button',{name:'Editor'}));expect(screen.queryByRole('complementary')).not.toBeInTheDocument()});
it('uses explicit navigation and a dismiss backdrop on narrow screens',()=>{vi.stubGlobal('matchMedia',vi.fn(()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()})));const {container}=render(<Fixture hover/>);fireEvent.pointerEnter(container.querySelector('.workspace-navigation')!,{pointerType:'mouse'});expect(screen.queryByRole('complementary')).not.toBeInTheDocument();fireEvent.click(screen.getByRole('button',{name:'Show sidebar'}));expect(screen.getByRole('button',{name:'Close navigation'})).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'Close navigation'}));expect(screen.queryByRole('complementary')).not.toBeInTheDocument()});

it('uses the same visible state for the sidebar shortcut and hover reveal',()=>{const {container}=render(<Fixture hover/>);fireEvent.pointerEnter(container.querySelector('.workspace-navigation')!,{pointerType:'mouse'});expect(screen.getByRole('complementary')).toBeVisible();act(()=>window.dispatchEvent(new Event('termx-sidebar-toggle')));expect(screen.queryByRole('complementary')).not.toBeInTheDocument();act(()=>window.dispatchEvent(new Event('termx-sidebar-toggle')));expect(screen.getByRole('complementary')).toBeVisible()});

it('returns keyboard focus to the rail when collapsing from within pinned navigation',()=>{render(<Fixture initial/>);act(()=>screen.getByRole('button',{name:'Conversation'}).focus());act(()=>window.dispatchEvent(new Event('termx-sidebar-toggle')));expect(screen.queryByRole('complementary')).not.toBeInTheDocument();expect(screen.getByRole('button',{name:'Show sidebar'})).toHaveFocus()});

it('makes a closing sidebar inert immediately and removes it after the exit interval',()=>{const {container}=render(<Fixture initial/>);const sidebar=container.querySelector('#workspace-sidebar')!;fireEvent.click(screen.getByRole('button',{name:'Hide sidebar'}));expect(sidebar).toHaveAttribute('inert');expect(sidebar).toHaveAttribute('aria-hidden','true');expect(sidebar).toBeInTheDocument();act(()=>vi.advanceTimersByTime(121));expect(sidebar).not.toBeInTheDocument()});
it('cancels a retained close when the sidebar is reopened quickly',()=>{const {container}=render(<Fixture initial/>);const sidebar=container.querySelector('#workspace-sidebar')!;fireEvent.click(screen.getByRole('button',{name:'Hide sidebar'}));fireEvent.click(screen.getByRole('button',{name:'Show sidebar'}));act(()=>vi.advanceTimersByTime(200));expect(screen.getByRole('complementary')).toBe(sidebar);expect(sidebar).not.toHaveAttribute('inert')});

it('reserves only the rail width immediately after closing and in floating mode',()=>{
 const view=render(<Fixture initial/>),navigation=view.container.querySelector<HTMLElement>('.workspace-navigation')!;
 expect(navigation.style.width).toBe('calc(var(--agent-rail-width, 52px) + var(--workspace-sidebar-width))');
 fireEvent.click(screen.getByRole('button',{name:'Hide sidebar'}));
 expect(navigation.style.width).toBe('var(--agent-rail-width, 52px)');
 expect(view.container.querySelector('#workspace-sidebar')).toBeInTheDocument();
 view.unmount();
 const floating=render(<Fixture hover/>).container.querySelector<HTMLElement>('.workspace-navigation')!;
 fireEvent.pointerEnter(floating,{pointerType:'mouse'});
 expect(floating.style.width).toBe('var(--agent-rail-width, 52px)');
});


function ProjectDialog({save,onSaved}:{save:()=>Promise<void>;onSaved:()=>void}){
 const [open,setOpen]=useState(false),[nested,setNested]=useState(false),[pending,setPending]=useState(false);
 return <Dialog open={open} onOpenChange={next=>{if(!pending)setOpen(next)}}>
  <DialogTrigger asChild><button>Open project dialog</button></DialogTrigger>
  <DialogContent onEscapeKeyDown={event=>{if(pending)event.preventDefault()}} onPointerDownOutside={event=>{if(pending)event.preventDefault()}}>
   <DialogTitle>Project editor</DialogTitle><DialogDescription>Edit a project.</DialogDescription>
   <input aria-label="Project name"/>
   <button disabled={pending} onClick={()=>{setPending(true);void save().then(()=>{onSaved();setPending(false);setOpen(false)})}}>Save project</button>
   <Dialog open={nested} onOpenChange={setNested}><DialogTrigger asChild><button>Confirm project switch</button></DialogTrigger><DialogContent><DialogTitle>Switch project</DialogTitle><DialogDescription>Confirm the project change.</DialogDescription><button onClick={()=>setNested(false)}>Keep editing</button></DialogContent></Dialog>
  </DialogContent>
 </Dialog>;
}
function DialogFixture({hover=false,showDialog=true,save=async()=>{},onSaved=()=>{}}:{hover?:boolean;showDialog?:boolean;save?:()=>Promise<void>;onSaved?:()=>void}){
 const [open,setOpen]=useState(false);
 return <><WorkspaceNavigation open={open} hover={hover} width={244} onOpenChange={setOpen} rail={<button>Navigation action</button>} footer={null} resize={null}>{showDialog?<ProjectDialog save={save} onSaved={onSaved}/>:<button>Other workspace</button>}</WorkspaceNavigation><button>Editor</button></>;
}
function openProjectDialog(){const trigger=screen.getByRole('button',{name:'Open project dialog'});act(()=>trigger.focus());fireEvent.click(trigger);return screen.getByRole('dialog',{name:'Project editor'})}

it('keeps a narrow reduced-motion sidebar mounted through a portaled pending save',async()=>{
 vi.stubGlobal('matchMedia',vi.fn(()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()})));
 let resolve!:()=>void;const save=vi.fn(()=>new Promise<void>(done=>{resolve=done})),onSaved=vi.fn();
 const {container}=render(<DialogFixture save={save} onSaved={onSaved}/>),root=container.querySelector('.workspace-navigation')!;
 fireEvent.click(screen.getByRole('button',{name:'Show sidebar'}));
 const sidebar=container.querySelector('#workspace-sidebar')!,dialog=openProjectDialog();
 expect(root.contains(dialog)).toBe(false);
 const submit=within(dialog).getByRole('button',{name:'Save project'});
 fireEvent.pointerDown(submit,{pointerType:'touch'});fireEvent.click(submit);
 act(()=>{window.dispatchEvent(new Event('termx-sidebar-toggle'));vi.advanceTimersByTime(500)});
 fireEvent.keyDown(document,{key:'Escape'});
 expect(root).toHaveAttribute('data-state','open');expect(sidebar).toBeInTheDocument();expect(dialog).toBeInTheDocument();expect(save).toHaveBeenCalledOnce();
 await act(async()=>{resolve();await Promise.resolve()});
 expect(onSaved).toHaveBeenCalledOnce();expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(root).toHaveAttribute('data-state','open');
 fireEvent.click(screen.getByRole('button',{name:'Close navigation'}));act(()=>vi.advanceTimersByTime(1));
 expect(sidebar).not.toBeInTheDocument();
});

it('holds hover navigation through portal focus and an already scheduled close timer',()=>{
 const {container}=render(<DialogFixture hover/>),root=container.querySelector('.workspace-navigation')!;
 fireEvent.pointerEnter(root,{pointerType:'mouse'});fireEvent.pointerLeave(root);
 const dialog=openProjectDialog();
 fireEvent.blur(root,{relatedTarget:within(dialog).getByRole('textbox',{name:'Project name'})});
 fireEvent.pointerLeave(root);act(()=>vi.advanceTimersByTime(500));
 expect(root).toHaveAttribute('data-state','open');expect(dialog).toBeInTheDocument();
 act(()=>window.dispatchEvent(new Event('termx-sidebar-toggle')));
 expect(root).toHaveAttribute('data-state','open');
 fireEvent.click(within(dialog).getByRole('button',{name:'Close'}));act(()=>vi.advanceTimersByTime(1));
 act(()=>screen.getByRole('button',{name:'Editor'}).focus());fireEvent.blur(root,{relatedTarget:screen.getByRole('button',{name:'Editor'})});act(()=>vi.advanceTimersByTime(230));
 expect(root).toHaveAttribute('data-state','closed');
});

it('lets nested dialog Escape dismiss one layer at a time before navigation',()=>{
 const {container}=render(<DialogFixture hover/>),root=container.querySelector('.workspace-navigation')!;
 fireEvent.pointerEnter(root,{pointerType:'mouse'});const dialog=openProjectDialog();
 fireEvent.click(within(dialog).getByRole('button',{name:'Confirm project switch'}));
 expect(screen.getByRole('dialog',{name:'Switch project'})).toBeInTheDocument();
 fireEvent.keyDown(document,{key:'Escape'});
 expect(screen.queryByRole('dialog',{name:'Switch project'})).not.toBeInTheDocument();expect(screen.getByRole('dialog',{name:'Project editor'})).toBeInTheDocument();expect(root).toHaveAttribute('data-state','open');
 fireEvent.keyDown(document,{key:'Escape'});
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(root).toHaveAttribute('data-state','open');
 fireEvent.keyDown(document,{key:'Escape'});
 expect(root).toHaveAttribute('data-state','closed');expect(screen.getByRole('button',{name:'Show sidebar'})).toHaveFocus();
});

it('keeps navigation when its dialog scrim is pressed and restores outside dismissal afterward',()=>{
 vi.stubGlobal('matchMedia',vi.fn(()=>({matches:true,addEventListener:vi.fn(),removeEventListener:vi.fn()})));
 const {container}=render(<DialogFixture/>),root=container.querySelector('.workspace-navigation')!;
 fireEvent.click(screen.getByRole('button',{name:'Show sidebar'}));openProjectDialog();
 act(()=>vi.advanceTimersByTime(1));
 const overlay=document.querySelector('.dialog-overlay')!;
 fireEvent.pointerDown(overlay,{pointerType:'mouse'});
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(root).toHaveAttribute('data-state','open');
 fireEvent.pointerDown(screen.getByRole('button',{name:'Editor'}));act(()=>vi.advanceTimersByTime(1));
 expect(root).toHaveAttribute('data-state','closed');expect(container.querySelector('#workspace-sidebar')).not.toBeInTheDocument();
});

it('unregisters owned portals when the navigation content changes',()=>{
 const {container,rerender}=render(<DialogFixture hover/>),root=container.querySelector('.workspace-navigation')!;
 fireEvent.pointerEnter(root,{pointerType:'mouse'});openProjectDialog();
 rerender(<DialogFixture hover showDialog={false}/>);
 expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
 fireEvent.pointerDown(screen.getByRole('button',{name:'Editor'}));
 expect(root).toHaveAttribute('data-state','closed');
});
