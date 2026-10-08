import {useEffect,useRef,useState} from 'react';

/** Fence retained Access panels across lock and actual unmount. The backend
 * independently validates current authority; this prevents stale UI callbacks. */
export function useAccessLifecycle(onSuspend:()=>void){
 const latest=useRef(onSuspend);latest.current=onSuspend;
 const state=useRef({mounted:true,locked:false,epoch:0});
 const [locked,setLocked]=useState(false),[refresh,setRefresh]=useState(0);
 useEffect(()=>{
  state.current.mounted=true;
  const lock=()=>{state.current.locked=true;state.current.epoch++;setLocked(true);latest.current()};
  const unlock=()=>{state.current.locked=false;state.current.epoch++;setLocked(false);setRefresh(value=>value+1)};
  window.addEventListener('termx-locked',lock);window.addEventListener('termx-session-locked',lock);window.addEventListener('termx-signed-out',lock);window.addEventListener('termx-session-unlocked',unlock);
  return()=>{state.current.mounted=false;state.current.epoch++;window.removeEventListener('termx-locked',lock);window.removeEventListener('termx-session-locked',lock);window.removeEventListener('termx-signed-out',lock);window.removeEventListener('termx-session-unlocked',unlock)};
 },[]);
 const capture=()=>{const epoch=state.current.epoch;return()=>state.current.mounted&&!state.current.locked&&state.current.epoch===epoch};
 return {locked,refresh,capture,current:()=>state.current.mounted&&!state.current.locked};
}
