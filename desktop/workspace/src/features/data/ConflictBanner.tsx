import {useEffect, useRef} from 'react';
import {Button} from '../../components/ui/button';
import {formatWhen} from '../../lib/view-time';
import type {ViewError} from '../../lib/data-views';

type Props = {error: ViewError; onReload: () => void; onSaveCopy?: () => void; onDismiss: () => void; reloading?: boolean};
/** Someone else saved a newer revision. Nothing is retried silently; the person chooses. Focus moves to the heading so it is announced. */
export default function ConflictBanner({error, onReload, onSaveCopy, onDismiss, reloading = false}: Props) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {heading.current?.focus();}, []);
  const who = error.updated_by?.name, when = error.updated_at ? formatWhen(error.updated_at, true) : '';
  return <div className="data-conflict" role="alert">
    <h2 ref={heading} tabIndex={-1} className="data-conflict-title">{who ? who + ' changed this view' + (when ? ' at ' + when : '') + '.' : 'Someone else changed this view.'} Your edits have not been saved.</h2>
    <div className="data-toolbar">
      <Button size="sm" onClick={onReload} disabled={reloading}>{reloading ? 'Reloading…' : 'Reload latest'}</Button>
      {onSaveCopy && <Button variant="secondary" size="sm" onClick={onSaveCopy} disabled={reloading}>Save as a new view</Button>}
      <Button variant="ghost" size="sm" onClick={onDismiss}>Dismiss</Button>
    </div>
  </div>;
}
