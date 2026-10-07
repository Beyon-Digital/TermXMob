import type {HTMLAttributes} from 'react';
import {cva,type VariantProps} from 'class-variance-authority';
import {cn} from '../../lib/utils';
const badgeVariants=cva('badge',{variants:{variant:{default:'badge-default',secondary:'badge-secondary',outline:'badge-outline'}},defaultVariants:{variant:'default'}});
export function Badge({className,variant,...props}:HTMLAttributes<HTMLSpanElement>&VariantProps<typeof badgeVariants>){return <span className={cn(badgeVariants({variant}),className)} {...props}/>}
