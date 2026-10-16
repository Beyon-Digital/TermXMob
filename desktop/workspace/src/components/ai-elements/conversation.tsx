/** Selected from Vercel AI Elements, Apache-2.0.
 * Upstream ca8be600bfd01b3131f9bf6134b51602a71f4374.
 * See third-party/AI_ELEMENTS_LICENSE and AI_ELEMENTS_PROVENANCE.md.
 * Adapted local imports and theme CSS; existing TermX transports own actions.
 */
import type {ComponentProps} from 'react';
import {cn} from '../../lib/utils';
export type ConversationEmptyStateProps = ComponentProps<"div"> & {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
};

export const ConversationEmptyState = ({
  className,
  title = "No messages yet",
  description = "Start a conversation to see messages here",
  icon,
  children,
  ...props
}: ConversationEmptyStateProps) => (
  <div
    className={cn(
      "chat-empty",
      className
    )}
    {...props}
  >
    {children ?? (
      <>
        {icon && <div className="text-muted-foreground">{icon}</div>}
        <div className="space-y-1">
          <h3 className="font-medium text-sm">{title}</h3>
          {description && (
            <p className="text-muted-foreground text-sm">{description}</p>
          )}
        </div>
      </>
    )}
  </div>
);

