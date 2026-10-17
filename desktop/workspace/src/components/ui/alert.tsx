import {forwardRef,type HTMLAttributes} from 'react';
import {cn} from '../../lib/utils';
export const Alert=forwardRef<HTMLDivElement,HTMLAttributes<HTMLDivElement>>(({className,...props},ref)=><div ref={ref} role="status" className={cn('ai-confirmation',className)} {...props}/>);
export const AlertDescription=({className,...props}:HTMLAttributes<HTMLDivElement>)=><div className={cn('ai-confirmation-description',className)} {...props}/>;
