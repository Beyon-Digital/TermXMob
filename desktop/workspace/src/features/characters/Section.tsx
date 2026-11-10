import {useId, type ReactNode} from 'react';

/**
 * A titled block of the studio page. The page owns the h2, so blocks use h3.
 * SettingsSection does not replace it: that one wraps its children in a list of setting rows and has no slot for header actions.
 */
export function Section({title, description, actions, children, className}: {title: ReactNode; description?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string}) {
  const titleId = useId();
  return (
    <section className={'character-section' + (className ? ' ' + className : '')} aria-labelledby={titleId}>
      <header className="character-section-header">
        <div>
          <h3 id={titleId}>{title}</h3>
          {description && <p>{description}</p>}
        </div>
        {actions && <div className="character-actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}
