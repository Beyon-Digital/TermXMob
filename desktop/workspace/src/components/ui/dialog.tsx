import * as React from 'react';
import * as Primitive from '@radix-ui/react-dialog';
import {X} from 'lucide-react';
import {cn} from '../../lib/utils';
export const Dialog=Primitive.Root,DialogTrigger=Primitive.Trigger,DialogTitle=Primitive.Title,DialogDescription=Primitive.Description;
export const DialogContent=React.forwardRef<React.ElementRef<typeof Primitive.Content>,React.ComponentPropsWithoutRef<typeof Primitive.Content>>(({className,children,onCloseAutoFocus,...props},ref)=>{
 const opener=React.useRef<HTMLElement|null>(null),mountedNode=React.useRef<HTMLElement|null>(null);
 const attach=React.useCallback((node:React.ElementRef<typeof Primitive.Content>|null)=>{
  // Controlled dialogs often open without a Radix Trigger. Capture on the
  // content's actual mount, before its FocusScope moves focus inside.
  if(node&&node!==mountedNode.current){mountedNode.current=node;const active=node.ownerDocument.activeElement;opener.current=active instanceof HTMLElement&&active!==node.ownerDocument.body&&!node.contains(active)?active:null}
  if(typeof ref==='function')ref(node);else if(ref)ref.current=node;
 },[ref]);
 return <Primitive.Portal><Primitive.Overlay className="dialog-overlay"/><Primitive.Content ref={attach} className={cn('dialog-content',className)} {...props} onCloseAutoFocus={event=>{
  onCloseAutoFocus?.(event);
  if(!event.defaultPrevented&&opener.current?.isConnected){event.preventDefault();opener.current.focus()}
 }}>{children}<Primitive.Close className="dialog-close" aria-label="Close"><X size={16}/></Primitive.Close></Primitive.Content></Primitive.Portal>
});
DialogContent.displayName='DialogContent';
