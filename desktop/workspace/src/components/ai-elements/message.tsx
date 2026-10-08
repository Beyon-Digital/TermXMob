/** Copyright 2023 Vercel, Inc. Apache-2.0; see third-party/AI_ELEMENTS_LICENSE.
 * Selected Message, MessageContent and MessageResponse from the official source:
 * https://github.com/vercel/ai-elements/blob/main/packages/elements/src/message.tsx
 * Modified import paths and CSS classes to match the approved TermX design.
 * Optional diagram/math/highlighter plugins are not loaded in chat messages.
 */
import type {UIMessage} from 'ai';
import type {ComponentProps,HTMLAttributes} from 'react';
import {memo} from 'react';
import {Streamdown} from 'streamdown';
import {cn} from '../../lib/utils';
export type MessageProps=HTMLAttributes<HTMLDivElement>&{from:UIMessage['role']};
export const Message=({className,from,...props}:MessageProps)=><div data-role={from} className={cn('message',from==='user'?'message-user is-user':'message-assistant is-assistant',className)} {...props}/>;
export type MessageContentProps=HTMLAttributes<HTMLDivElement>;
export const MessageContent=({children,className,...props}:MessageContentProps)=><div className={cn('message-content',className)} {...props}>{children}</div>;
export type MessageResponseProps=ComponentProps<typeof Streamdown>;
export const MessageResponse=memo(({className,...props}:MessageResponseProps)=><Streamdown className={cn('message-response',className)} {...props}/>,(previous,next)=>previous.children===next.children&&next.isAnimating===previous.isAnimating);
MessageResponse.displayName='MessageResponse';
