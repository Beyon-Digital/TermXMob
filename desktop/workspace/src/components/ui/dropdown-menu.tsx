import * as React from 'react';
import * as Primitive from '@radix-ui/react-dropdown-menu';
import {Check} from 'lucide-react';
import {cn} from '../../lib/utils';
import {useNavigationLayerRef} from './navigation-layer';
import './overlays.css';

export const DropdownMenu = Primitive.Root;
export const DropdownMenuTrigger = Primitive.Trigger;
export const DropdownMenuGroup = Primitive.Group;
export const DropdownMenuContent = React.forwardRef<React.ElementRef<typeof Primitive.Content>, React.ComponentPropsWithoutRef<typeof Primitive.Content>>(({className, sideOffset = 4, align = 'end', ...props}, forwarded) => {
  const layer = useNavigationLayerRef<React.ElementRef<typeof Primitive.Content>>();
  const attach = React.useCallback((node: React.ElementRef<typeof Primitive.Content> | null) => {
    layer(node);
    if (typeof forwarded === 'function') forwarded(node); else if (forwarded) forwarded.current = node;
  }, [forwarded, layer]);
  return <Primitive.Portal><Primitive.Content ref={attach} sideOffset={sideOffset} align={align} collisionPadding={8} className={cn('ui-menu', className)} {...props}/></Primitive.Portal>;
});
DropdownMenuContent.displayName = 'DropdownMenuContent';
export const DropdownMenuItem = React.forwardRef<React.ElementRef<typeof Primitive.Item>, React.ComponentPropsWithoutRef<typeof Primitive.Item>>(({className, ...props}, ref) => <Primitive.Item ref={ref} className={cn('ui-menu-item', className)} {...props}/>);
DropdownMenuItem.displayName = 'DropdownMenuItem';
export const DropdownMenuCheckboxItem = React.forwardRef<React.ElementRef<typeof Primitive.CheckboxItem>, React.ComponentPropsWithoutRef<typeof Primitive.CheckboxItem>>(({className, children, ...props}, ref) => (
  <Primitive.CheckboxItem ref={ref} className={cn('ui-menu-item ui-menu-check', className)} {...props}>
    <span className="ui-menu-indicator"><Primitive.ItemIndicator><Check size={16} aria-hidden="true"/></Primitive.ItemIndicator></span>{children}
  </Primitive.CheckboxItem>
));
DropdownMenuCheckboxItem.displayName = 'DropdownMenuCheckboxItem';
export const DropdownMenuSeparator = React.forwardRef<React.ElementRef<typeof Primitive.Separator>, React.ComponentPropsWithoutRef<typeof Primitive.Separator>>(({className, ...props}, ref) => <Primitive.Separator ref={ref} className={cn('ui-menu-separator', className)} {...props}/>);
DropdownMenuSeparator.displayName = 'DropdownMenuSeparator';
export const DropdownMenuLabel = React.forwardRef<React.ElementRef<typeof Primitive.Label>, React.ComponentPropsWithoutRef<typeof Primitive.Label>>(({className, ...props}, ref) => <Primitive.Label ref={ref} className={cn('ui-menu-label', className)} {...props}/>);
DropdownMenuLabel.displayName = 'DropdownMenuLabel';
