import * as React from 'react';
import * as Primitive from '@radix-ui/react-dialog';
import {X} from 'lucide-react';
import {cn} from '../../lib/utils';
export const Dialog=Primitive.Root,DialogTrigger=Primitive.Trigger,DialogTitle=Primitive.Title,DialogDescription=Primitive.Description;
export const DialogContent=React.forwardRef<React.ElementRef<typeof Primitive.Content>,React.ComponentPropsWithoutRef<typeof Primitive.Content>>(({className,children,...props},ref)=><Primitive.Portal><Primitive.Overlay className="dialog-overlay"/><Primitive.Content ref={ref} className={cn('dialog-content',className)} {...props}>{children}<Primitive.Close className="dialog-close" aria-label="Close"><X size={16}/></Primitive.Close></Primitive.Content></Primitive.Portal>);
DialogContent.displayName='DialogContent';
