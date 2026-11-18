import * as Primitive from '@radix-ui/react-tooltip';
import type {ReactNode} from 'react';
import './tooltip.css';

export const TooltipProvider = Primitive.Provider;

type Props = {
  /** The visible label. Icon-only controls still carry their own `aria-label`; the tooltip repeats it for the pointer. */
  label: ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
  children: ReactNode;
};

/** A Radix tooltip around one focusable child. It appears on hover and on keyboard focus, and Escape dismisses it. */
export function Tooltip({label, side = 'bottom', children}: Props) {
  return (
    <Primitive.Root>
      <Primitive.Trigger asChild>{children}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content className="ui-tooltip" side={side} sideOffset={8} collisionPadding={8}>
          {label}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}
