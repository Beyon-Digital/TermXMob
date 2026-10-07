import {forwardRef,type SelectHTMLAttributes} from 'react';
import {cn} from '../../lib/utils';
export const Select=forwardRef<HTMLSelectElement,SelectHTMLAttributes<HTMLSelectElement>>(({className,...props},ref)=><select ref={ref} className={cn('input select',className)} {...props}/>);
Select.displayName='Select';
