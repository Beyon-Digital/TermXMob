import * as React from 'react';
import * as Primitive from '@radix-ui/react-dialog';
import {X} from 'lucide-react';
import {cn} from '../../lib/utils';
import {observeClosedState} from '../../lib/disclosure-motion';
import {useNavigationLayerRef} from './navigation-layer';
export const Dialog=Primitive.Root,DialogTrigger=Primitive.Trigger,DialogTitle=Primitive.Title,DialogDescription=Primitive.Description;
export const DialogContent=React.forwardRef<React.ElementRef<typeof Primitive.Content>,React.ComponentPropsWithoutRef<typeof Primitive.Content>&{closeDisabled?:boolean}>(({className,children,onCloseAutoFocus,closeDisabled=false,...props},ref)=>{
 const opener=React.useRef<HTMLElement|null>(null),mountedNode=React.useRef<HTMLElement|null>(null);
 const observer=React.useRef<MutationObserver|null>(null);
 const contentLayer=useNavigationLayerRef<React.ElementRef<typeof Primitive.Content>>(),overlayLayer=useNavigationLayerRef<React.ElementRef<typeof Primitive.Overlay>>();
 const watch=React.useCallback((node:HTMLElement|null)=>{observer.current?.disconnect();observer.current=node?observeClosedState(node,()=>{if(opener.current?.isConnected)opener.current.focus({preventScroll:true})}):null},[]);
 React.useLayoutEffect(()=>{watch(mountedNode.current);return()=>observer.current?.disconnect()},[watch]);
 const attach=React.useCallback((node:React.ElementRef<typeof Primitive.Content>|null)=>{
  // Controlled dialogs often open without a Radix Trigger. Capture on the
  // content's actual mount, before its FocusScope moves focus inside.
  if(node&&node!==mountedNode.current){mountedNode.current=node;const active=node.ownerDocument.activeElement;opener.current=active instanceof HTMLElement&&active!==node.ownerDocument.body&&!node.contains(active)?active:null}
  mountedNode.current=node;watch(node);contentLayer(node);
  if(typeof ref==='function')ref(node);else if(ref)ref.current=node;
 },[ref,watch,contentLayer]);
 return <Primitive.Portal><Primitive.Overlay ref={overlayLayer} className="dialog-overlay"/><Primitive.Content ref={attach} className={cn('dialog-content',className)} {...props} onCloseAutoFocus={event=>{
  onCloseAutoFocus?.(event);
  if(!event.defaultPrevented&&opener.current?.isConnected){event.preventDefault();opener.current.focus()}
 }}>{children}<Primitive.Close className="dialog-close" aria-label="Close" disabled={closeDisabled}><X size={16}/></Primitive.Close></Primitive.Content></Primitive.Portal>
});
DialogContent.displayName='DialogContent';
