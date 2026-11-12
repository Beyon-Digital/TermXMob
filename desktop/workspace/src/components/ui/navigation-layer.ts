import {createContext,useCallback,useContext,useRef} from 'react';

/** Portals retain React ownership even when their DOM sits outside navigation. */
export const NavigationLayerContext=createContext<((node:HTMLElement)=>()=>void)|null>(null);

export function useNavigationLayerRef<T extends HTMLElement>(){
 const register=useContext(NavigationLayerContext),unregister=useRef<(()=>void)|undefined>(undefined);
 return useCallback((node:T|null)=>{
  unregister.current?.();
  unregister.current=node?register?.(node):undefined;
 },[register]);
}
