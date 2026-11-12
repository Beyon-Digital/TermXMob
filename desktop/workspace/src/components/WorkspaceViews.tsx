import {useEffect,useRef} from 'react';
import './workspace-views.css';
export type WorkspaceView='projects'|'tasks'|'workflows'|'overview';
const views=[['projects','Projects'],['tasks','Activity'],['workflows','Workflows'],['overview','Overview']] as const;
/** One persistent work navigation, shared by project and workflow surfaces. */
export default function WorkspaceViews({current,onNavigate}:{current:WorkspaceView;onNavigate:(view:WorkspaceView)=>void}){
 const nav=useRef<HTMLElement>(null);
 useEffect(()=>{nav.current?.querySelector<HTMLElement>('[aria-current=page]')?.scrollIntoView?.({block:'nearest',inline:'nearest'})},[current]);
 return <nav ref={nav} className="workspace-views" aria-label="Workspace views" onKeyDown={event=>{
  const buttons=Array.from(nav.current?.querySelectorAll<HTMLButtonElement>('button')||[]),index=buttons.indexOf(document.activeElement as HTMLButtonElement);
  const next=event.key==='ArrowRight'?(index+1)%buttons.length:event.key==='ArrowLeft'?(index-1+buttons.length)%buttons.length:event.key==='Home'?0:event.key==='End'?buttons.length-1:null;
  if(next===null||!buttons[next])return;event.preventDefault();buttons[next].focus();buttons[next].scrollIntoView?.({block:'nearest',inline:'nearest'});
 }}>{views.map(([id,label])=><button key={id} type="button" aria-current={current===id?'page':undefined} onClick={()=>onNavigate(id)}>{label}</button>)}</nav>;
}
