import {forwardRef,type ComponentProps} from 'react';
export const Textarea=forwardRef<HTMLTextAreaElement,ComponentProps<'textarea'>>((props,ref)=><textarea ref={ref} {...props}/>);
