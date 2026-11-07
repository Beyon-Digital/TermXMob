import {useId, type ReactNode} from 'react';
import {cn} from '../../lib/utils';
import './form.css';

/** Props a control needs to be wired to its `FormField`. Spread them onto the input. */
export interface FieldControlProps {
  id: string;
  required?: boolean;
  'aria-invalid'?: true;
  'aria-describedby'?: string;
}

export interface FormFieldProps {
  label: ReactNode;
  hint?: ReactNode;
  /** Message for the current validation failure. The alert region stays mounted so changes are announced. */
  error?: ReactNode;
  required?: boolean;
  className?: string;
  children: (control: FieldControlProps) => ReactNode;
}

export function FormField({label, hint, error, required, className, children}: FormFieldProps) {
  const id = useId(), hintId = `${id}-hint`, errorId = `${id}-error`;
  const describedBy = [error ? errorId : null, hint ? hintId : null].filter(Boolean).join(' ');
  const control: FieldControlProps = {id};
  if (required) control.required = true;
  if (error) control['aria-invalid'] = true;
  if (describedBy) control['aria-describedby'] = describedBy;
  return <div className={cn('form-field', className)}>
    <label className="form-field-label" htmlFor={id}>{label}{required && <span aria-hidden="true"> *</span>}</label>
    {children(control)}
    {hint && <p className="form-field-hint" id={hintId}>{hint}</p>}
    <p className="form-field-error" id={errorId} role="alert">{error}</p>
  </div>;
}
