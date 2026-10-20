/** Copyright 2023 Vercel, Inc. Apache-2.0; see third-party/AI_ELEMENTS_LICENSE.
 * Selected Message, MessageContent and MessageResponse from the official source:
 * https://github.com/vercel/ai-elements/blob/main/packages/elements/src/message.tsx
 * Modified import paths and CSS classes to match the approved TermX design.
 * Optional diagram/math/highlighter plugins are not loaded in chat messages.
 */
import type {UIMessage} from 'ai';
import type {ComponentProps,HTMLAttributes} from 'react';
import {cloneElement,isValidElement,memo} from 'react';
import {defaultComponents,Streamdown,type Components,type ExtraProps} from 'streamdown';
import {cn} from '../../lib/utils';
export type MessageProps=HTMLAttributes<HTMLDivElement>&{from:UIMessage['role']};
export const Message=({className,from,...props}:MessageProps)=><div data-role={from} className={cn('message',from==='user'?'message-user is-user':'message-assistant is-assistant',className)} {...props}/>;
export type MessageContentProps=HTMLAttributes<HTMLDivElement>;
export const MessageContent=({children,className,...props}:MessageContentProps)=><div className={cn('message-content',className)} {...props}>{children}</div>;
export type MessageResponseProps=ComponentProps<typeof Streamdown>;
// Streamdown forwards code props to its actual scroll body. Keep that body
// keyboard reachable without replacing its streaming, copy, or parsing logic.
type CodeProps=ComponentProps<'code'>&{'data-block'?:string};
const CodeFence=({children}:ComponentProps<'pre'>&ExtraProps)=>isValidElement<CodeProps>(children)
  ? cloneElement(children,{'data-block':'true',tabIndex:0,role:'region','aria-label':'Code block'}) : children;
const MarkdownTable=defaultComponents.table;
const Table=(props:ComponentProps<typeof MarkdownTable>)=><MarkdownTable {...props} tabIndex={0}/>;
const accessibleComponents={pre:CodeFence,table:Table} satisfies Components;
export const MessageResponse=memo(({className,components,...props}:MessageResponseProps)=><Streamdown
  className={cn('message-response',className)}
  components={components?{...accessibleComponents,...components}:accessibleComponents}
  skipHtml
  codeBlockMaxHeight={480}
  tableMaxHeight={400}
  controls={{code:{copy:true,download:false},table:{copy:true,download:false,fullscreen:false},image:false}}
  {...props}
/>);
MessageResponse.displayName='MessageResponse';
