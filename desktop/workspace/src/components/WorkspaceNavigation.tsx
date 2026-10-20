import {useEffect,useRef,useState,type ReactNode} from 'react';
import {PanelLeft} from 'lucide-react';
import {Button} from './ui/button';

/** Navigation stays mounted while revealed so hovering never shifts the editor. */
export default function WorkspaceNavigation({open,hover,width,onOpenChange,rail,footer,children,resize}:{open:boolean;hover:boolean;width:number;onOpenChange:(open:boolean)=>void;rail:ReactNode;footer:ReactNode;children:ReactNode;resize:ReactNode}){
 const [revealed,setRevealed]=useState(false),[narrow,setNarrow]=useState(()=>matchMedia('(max-width:850px)').matches);
 const root=useRef<HTMLDivElement>(null),toggle=useRef<HTMLButtonElement>(null),closeTimer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),ignoreFocus=useRef(false);
 const floating=narrow||hover,visible=open||hover&&!narrow&&revealed;
 const cancelClose=()=>{clearTimeout(closeTimer.current)};
 const returnFocus=()=>{ignoreFocus.current=true;toggle.current?.focus();ignoreFocus.current=false};
 const close=()=>{cancelClose();if(root.current?.contains(document.activeElement))returnFocus();setRevealed(false);onOpenChange(false)};
 const scheduleClose=()=>{cancelClose();closeTimer.current=setTimeout(()=>{if(!root.current?.contains(document.activeElement))setRevealed(false)},220)};
 useEffect(()=>{const media=matchMedia('(max-width:850px)');const changed=()=>{setNarrow(media.matches);setRevealed(false)};media.addEventListener('change',changed);return()=>{media.removeEventListener('change',changed);clearTimeout(closeTimer.current)}},[]);
 useEffect(()=>{setRevealed(false)},[hover]);
 useEffect(()=>{const toggleNavigation=()=>{if(visible)close();else{cancelClose();setRevealed(false);onOpenChange(true)}};window.addEventListener('termx-sidebar-toggle',toggleNavigation);return()=>window.removeEventListener('termx-sidebar-toggle',toggleNavigation)},[visible,onOpenChange]);
 useEffect(()=>{
  if(!visible||!floating)return;
  const outside=(event:PointerEvent)=>{if(!root.current?.contains(event.target as Node)){setRevealed(false);onOpenChange(false)}};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!event.defaultPrevented){event.preventDefault();close();returnFocus()}};
  document.addEventListener('pointerdown',outside);document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('pointerdown',outside);document.removeEventListener('keydown',escape)};
 },[visible,floating,onOpenChange]);
 return <div ref={root} className={'workspace-navigation'+(floating?' navigation-floating':'')+(visible?' navigation-open':'')} data-sidebar-mode={hover?'hover':open?'pinned':'collapsed'} onPointerEnter={event=>{cancelClose();if(hover&&!narrow&&event.pointerType!=='touch')setRevealed(true)}} onPointerLeave={()=>{if(hover&&!open)scheduleClose()}} onFocus={()=>{cancelClose();if(hover&&!narrow&&!ignoreFocus.current)setRevealed(true)}} onBlur={event=>{if(!event.currentTarget.contains(event.relatedTarget as Node)&&hover&&!open)scheduleClose()}}>
  <nav className="workspace-activity-rail" aria-label="Workspace navigation">
   <Button ref={toggle} variant="ghost" size="icon" aria-label={visible?'Hide sidebar':'Show sidebar'} title="Toggle sidebar · ⌘/Ctrl+B" aria-expanded={visible} aria-controls="workspace-sidebar" onClick={()=>visible?close():onOpenChange(true)}><PanelLeft size={18}/></Button>
   {rail}<span className="flex-spacer"/>{footer}
  </nav>
  {visible&&floating&&narrow&&<button className="navigation-backdrop" aria-label="Close navigation" onClick={close}/>}
  {visible&&<aside id="workspace-sidebar" className="sidebar" style={{width}} aria-label="Projects and conversations">{children}</aside>}
  {visible&&!floating&&resize}
 </div>
}
