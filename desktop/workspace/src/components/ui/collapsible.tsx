import * as React from 'react';
import * as Primitive from '@radix-ui/react-collapsible';
import {cn} from '../../lib/utils';
import {observeClosedState} from '../../lib/disclosure-motion';

export const Collapsible = React.forwardRef<React.ElementRef<typeof Primitive.Root>, React.ComponentPropsWithoutRef<typeof Primitive.Root>>(({className,...props},ref)=><Primitive.Root {...props} ref={ref} className={cn('ui-disclosure',className)}/>);
Collapsible.displayName='Collapsible';
export const CollapsibleTrigger = React.forwardRef<React.ElementRef<typeof Primitive.Trigger>, React.ComponentPropsWithoutRef<typeof Primitive.Trigger>>(({className,...props},ref)=><Primitive.Trigger {...props} ref={ref} className={cn('ui-disclosure-trigger',className)}/>);
CollapsibleTrigger.displayName='CollapsibleTrigger';

/** Radix keeps content present for its exit animation. Closed controls are
 * inert immediately, including content mounted only after the first open. */
export const CollapsibleContent = React.forwardRef<React.ElementRef<typeof Primitive.Content>, React.ComponentPropsWithoutRef<typeof Primitive.Content>>(({className,...props},forwarded)=>{
 const node=React.useRef<React.ElementRef<typeof Primitive.Content>|null>(null),observer=React.useRef<MutationObserver|null>(null);
 const watch=React.useCallback((content:HTMLElement|null)=>{
  observer.current?.disconnect();
  observer.current=content?observeClosedState(content,()=>content.closest('.ui-disclosure')?.querySelector<HTMLElement>('.ui-disclosure-trigger')?.focus({preventScroll:true})):null;
 },[]);
 const attach=React.useCallback((value:React.ElementRef<typeof Primitive.Content>|null)=>{
  node.current=value;watch(value);if(typeof forwarded==='function')forwarded(value);else if(forwarded)forwarded.current=value;
 },[forwarded,watch]);
 React.useLayoutEffect(()=>{watch(node.current);return()=>observer.current?.disconnect()},[watch]);
 return <Primitive.Content {...props} ref={attach} className={cn('ui-disclosure-content',className)}/>;
});
CollapsibleContent.displayName='CollapsibleContent';
