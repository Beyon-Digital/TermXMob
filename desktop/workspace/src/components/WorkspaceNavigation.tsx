import {useCallback,useEffect,useRef,useState,type ReactNode,type CSSProperties} from 'react';
import {PanelLeft} from 'lucide-react';
import {Button} from './ui/button';
import {NavigationLayerContext} from './ui/navigation-layer';

/** Navigation stays mounted while revealed so hovering never shifts the editor. */
export default function WorkspaceNavigation({open,hover,width,onOpenChange,rail,footer,children,resize,sidebarLabel='Chats and agents',railFirst=false,sidebarAvailable=true}:{open:boolean;hover:boolean;width:number;onOpenChange:(open:boolean)=>void;rail:ReactNode;footer:ReactNode;children:ReactNode;resize:ReactNode;sidebarLabel?:string;railFirst?:boolean;sidebarAvailable?:boolean}){
 const [revealed,setRevealed]=useState(false),[narrow,setNarrow]=useState(()=>matchMedia('(max-width:850px)').matches);
 const root=useRef<HTMLDivElement>(null),toggle=useRef<HTMLButtonElement>(null),closeTimer=useRef<ReturnType<typeof setTimeout>|undefined>(undefined),ignoreFocus=useRef(false);
 const layers=useRef(new Set<HTMLElement>());
 const registerLayer=useCallback((node:HTMLElement)=>{layers.current.add(node);return()=>{layers.current.delete(node)}},[]);
 const hasOpenLayer=()=>Array.from(layers.current).some(node=>node.isConnected&&node.dataset.state==='open');
 const owns=(target:EventTarget|null)=>target instanceof Node&&(!!root.current?.contains(target)||Array.from(layers.current).some(node=>node.isConnected&&node.dataset.state==='open'&&node.contains(target)));
 const floating=narrow||hover,visible=sidebarAvailable&&(open||hover&&!narrow&&revealed);
 const [retained,setRetained]=useState(visible),present=sidebarAvailable&&(visible||retained);
 useEffect(()=>{if(visible){setRetained(true);return}if(!retained)return;const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches,duration=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--motion-exit'))||120;const timer=setTimeout(()=>setRetained(false),reduce?0:duration);return()=>clearTimeout(timer)},[visible,retained]);
 const cancelClose=()=>{clearTimeout(closeTimer.current)};
 const returnFocus=()=>{ignoreFocus.current=true;toggle.current?.focus();ignoreFocus.current=false};
 const close=()=>{cancelClose();if(hasOpenLayer())return;if(owns(document.activeElement))returnFocus();setRevealed(false);onOpenChange(false)};
 const scheduleClose=()=>{cancelClose();closeTimer.current=setTimeout(()=>{if(!hasOpenLayer()&&!owns(document.activeElement))setRevealed(false)},220)};
 useEffect(()=>{const media=matchMedia('(max-width:850px)');const changed=()=>{setNarrow(media.matches);setRevealed(false)};media.addEventListener('change',changed);return()=>{media.removeEventListener('change',changed);clearTimeout(closeTimer.current)}},[]);
 useEffect(()=>{setRevealed(false)},[hover]);
 useEffect(()=>{const toggleNavigation=()=>{if(!sidebarAvailable||hasOpenLayer())return;if(visible)close();else{cancelClose();setRevealed(false);onOpenChange(true)}};window.addEventListener('termx-sidebar-toggle',toggleNavigation);return()=>window.removeEventListener('termx-sidebar-toggle',toggleNavigation)},[visible,onOpenChange,sidebarAvailable]);
 useEffect(()=>{
  if(!visible||!floating)return;
  const outside=(event:PointerEvent)=>{if(!hasOpenLayer()&&!owns(event.target))close()};
  const escape=(event:KeyboardEvent)=>{if(event.key==='Escape'&&!event.defaultPrevented&&!hasOpenLayer()){event.preventDefault();close();returnFocus()}};
  // Observe ownership before Radix can close and unregister a dialog on this press.
  document.addEventListener('pointerdown',outside,true);document.addEventListener('keydown',escape);
  return()=>{document.removeEventListener('pointerdown',outside,true);document.removeEventListener('keydown',escape)};
 },[visible,floating,onOpenChange]);
 return <NavigationLayerContext.Provider value={registerLayer}><div ref={root} className={'workspace-navigation'+(floating?' navigation-floating':'')+(visible?' navigation-open':'')} data-state={visible?'open':'closed'} style={{'--workspace-sidebar-width':width+'px',width:!floating&&visible?'calc(var(--agent-rail-width, 52px) + var(--workspace-sidebar-width))':'var(--agent-rail-width, 52px)'} as CSSProperties} data-sidebar-mode={hover?'hover':open?'pinned':'collapsed'} onPointerEnter={event=>{cancelClose();if(hover&&!narrow&&event.pointerType!=='touch')setRevealed(true)}} onPointerLeave={()=>{if(hover&&!open)scheduleClose()}} onFocus={()=>{cancelClose();if(hover&&!narrow&&!ignoreFocus.current)setRevealed(true)}} onBlur={event=>{if(!owns(event.relatedTarget)&&hover&&!open)scheduleClose()}}>
  <nav className="workspace-activity-rail" aria-label="Workspace navigation">
   {railFirst&&rail}
   {sidebarAvailable&&<Button ref={toggle} variant="ghost" size="icon" aria-label={visible?'Hide sidebar':'Show sidebar'} title="Toggle sidebar · ⌘/Ctrl+B" aria-expanded={visible} aria-controls="workspace-sidebar" onClick={()=>visible?close():onOpenChange(true)}><PanelLeft size={18}/></Button>}
   {!railFirst&&rail}<span className="flex-spacer"/>{footer}
  </nav>
  {visible&&floating&&narrow&&<button className="navigation-backdrop" aria-label="Close navigation" onClick={close}/>}
  {present&&<aside aria-hidden={!visible||undefined} inert={!visible} id="workspace-sidebar" className="sidebar" style={{width,'--workspace-sidebar-width':width+'px'} as CSSProperties} aria-label={sidebarLabel}>{children}</aside>}
  {visible&&!floating&&resize}
 </div></NavigationLayerContext.Provider>
}
