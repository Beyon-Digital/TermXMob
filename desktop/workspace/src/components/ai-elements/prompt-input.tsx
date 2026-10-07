/** Copyright 2023 Vercel, Inc. Apache-2.0; third-party/AI_ELEMENTS_LICENSE.
 * Selected PromptInputBody/Tools/Submit from:
 * https://github.com/vercel/ai-elements/blob/main/packages/elements/src/prompt-input.tsx
 * Modified InputGroupButton to our shadcn Button, icon size and Figma classes.
 */
import type {ChatStatus} from 'ai';
import {useCallback,type ComponentProps,type HTMLAttributes} from 'react';
import {CornerDownLeftIcon,LoaderCircle,SquareIcon,XIcon} from 'lucide-react';
import {Button} from '../ui/button';
import {cn} from '../../lib/utils';
export type PromptInputBodyProps=HTMLAttributes<HTMLDivElement>;
export const PromptInputBody=({className,...props}:PromptInputBodyProps)=><div className={cn('prompt-input-body',className)} {...props}/>;
export type PromptInputToolsProps=HTMLAttributes<HTMLDivElement>;
export const PromptInputTools=({className,...props}:PromptInputToolsProps)=><div className={cn('composer-controls',className)} {...props}/>;
export type PromptInputSubmitProps=ComponentProps<typeof Button>&{status?:ChatStatus;onStop?:()=>void};
export const PromptInputSubmit=({className,variant='default',size='icon',status,onStop,onClick,children,...props}:PromptInputSubmitProps)=>{
 const isGenerating=status==='submitted'||status==='streaming';
 let Icon=<CornerDownLeftIcon size={16}/>;
 if(status==='submitted')Icon=<LoaderCircle size={16} className="spin"/>;else if(status==='streaming')Icon=<SquareIcon size={16}/>;else if(status==='error')Icon=<XIcon size={16}/>;
 const handleClick=useCallback((event:React.MouseEvent<HTMLButtonElement>)=>{if(isGenerating&&onStop){event.preventDefault();onStop();return}onClick?.(event)},[isGenerating,onStop,onClick]);
 return <Button aria-label={isGenerating?'Stop':'Submit'} className={cn(className)} onClick={handleClick} size={size} type={isGenerating&&onStop?'button':'submit'} variant={variant} {...props}>{children??Icon}</Button>
};
