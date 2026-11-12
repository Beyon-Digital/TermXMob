import * as React from 'react';
import * as Primitive from '@radix-ui/react-popover';
import {cn} from '../../lib/utils';
import {useNavigationLayerRef} from './navigation-layer';
import './overlays.css';

export const Popover = Primitive.Root;
export const PopoverTrigger = Primitive.Trigger;
export const PopoverAnchor = Primitive.Anchor;
export const PopoverClose = Primitive.Close;
export const PopoverContent = React.forwardRef<React.ElementRef<typeof Primitive.Content>, React.ComponentPropsWithoutRef<typeof Primitive.Content>>(({className, sideOffset = 4, align = 'start', ...props}, forwarded) => {
  const layer = useNavigationLayerRef<React.ElementRef<typeof Primitive.Content>>();
  const attach = React.useCallback((node: React.ElementRef<typeof Primitive.Content> | null) => {
    layer(node);
    if (typeof forwarded === 'function') forwarded(node); else if (forwarded) forwarded.current = node;
  }, [forwarded, layer]);
  return <Primitive.Portal><Primitive.Content ref={attach} sideOffset={sideOffset} align={align} collisionPadding={8} className={cn('ui-popover', className)} {...props}/></Primitive.Portal>;
});
PopoverContent.displayName = 'PopoverContent';
