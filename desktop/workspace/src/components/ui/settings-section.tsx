import {useId, type ReactNode} from 'react';
import {cn} from '../../lib/utils';
import './form.css';

export function SettingsSection({title, description, className, children}: {title: ReactNode; description?: ReactNode; className?: string; children: ReactNode}) {
  const titleId = useId();
  return <section className={cn('settings-section', className)} aria-labelledby={titleId}>
    <header className="settings-section-header">
      <h2 id={titleId}>{title}</h2>
      {description && <p>{description}</p>}
    </header>
    <ul className="settings-rows">{children}</ul>
  </section>;
}

export interface RowControlProps { id: string; 'aria-describedby'?: string }

/**
 * One setting. Pass a function to receive the ids that tie a single control to the label and
 * description; pass plain children for rows that hold several self-labelled controls.
 */
export function SettingsRow({label, description, children}: {label: ReactNode; description?: ReactNode; children: ReactNode | ((control: RowControlProps) => ReactNode)}) {
  const id = useId(), descriptionId = `${id}-description`;
  const wired = typeof children === 'function';
  const control: RowControlProps = {id};
  if (description) control['aria-describedby'] = descriptionId;
  return <li className="settings-row">
    <div className="settings-row-text">
      {wired ? <label className="settings-row-label" htmlFor={id}>{label}</label> : <span className="settings-row-label" id={`${id}-label`}>{label}</span>}
      {description && <p className="settings-row-description" id={descriptionId}>{description}</p>}
    </div>
    {wired
      ? <div className="settings-row-control">{children(control)}</div>
      : <div className="settings-row-control" role="group" aria-labelledby={`${id}-label`}>{children}</div>}
  </li>;
}
