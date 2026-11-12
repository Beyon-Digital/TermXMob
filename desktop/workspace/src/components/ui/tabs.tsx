import * as React from 'react';
import * as Primitive from '@radix-ui/react-tabs';
import {cn} from '../../lib/utils';
import './overlays.css';

export const Tabs = Primitive.Root;
export const TabsList = React.forwardRef<React.ElementRef<typeof Primitive.List>, React.ComponentPropsWithoutRef<typeof Primitive.List>>(({className, ...props}, ref) => <Primitive.List ref={ref} className={cn('ui-tabs-list', className)} {...props}/>);
TabsList.displayName = 'TabsList';
export const TabsTrigger = React.forwardRef<React.ElementRef<typeof Primitive.Trigger>, React.ComponentPropsWithoutRef<typeof Primitive.Trigger>>(({className, ...props}, ref) => <Primitive.Trigger ref={ref} className={cn('ui-tabs-trigger', className)} {...props}/>);
TabsTrigger.displayName = 'TabsTrigger';
export const TabsContent = React.forwardRef<React.ElementRef<typeof Primitive.Content>, React.ComponentPropsWithoutRef<typeof Primitive.Content>>(({className, ...props}, ref) => <Primitive.Content ref={ref} className={cn('ui-tabs-content', className)} {...props}/>);
TabsContent.displayName = 'TabsContent';
