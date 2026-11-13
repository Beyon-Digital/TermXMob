import {useEffect, useRef} from 'react';
import {TriangleAlert} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {formatWhen} from '../../lib/view-time';
import type {ViewError} from '../../lib/data-views';
import {rescueFocusSoon} from './focus';

type Props = {error: ViewError; onReload: () => void; onSaveCopy?: () => void; onDismiss: () => void; reloading?: boolean};
/** Someone else saved a newer revision. Nothing is retried silently; the person chooses. The region takes focus so it is announced, and hands focus to the panel when it goes. */
export default function ConflictBanner({error, onReload, onSaveCopy, onDismiss, reloading = false}: Props) {
  const region = useRef<HTMLDivElement>(null);
  useEffect(() => {
    region.current?.focus();
    return rescueFocusSoon;
  }, []);
  const who = error.updated_by?.name, when = error.updated_at ? formatWhen(error.updated_at, true) : '';
  return <div ref={region} tabIndex={-1} className="data-conflict" role="alert">
    <p className="data-conflict-message"><TriangleAlert size={16} strokeWidth={1.5} aria-hidden="true"/>
      <span>{who ? who + ' changed this view' + (when ? ' at ' + when : '') + '.' : 'Someone else changed this view.'} Your edits have not been saved.</span></p>
    <div className="data-toolbar">
      <Button variant="secondary" size="sm" onClick={onReload} disabled={reloading}>{reloading ? 'Reloading…' : 'Reload latest'}</Button>
      {onSaveCopy && <Button variant="ghost" size="sm" onClick={onSaveCopy} disabled={reloading}>Save as a new view</Button>}
      <Button variant="ghost" size="sm" onClick={onDismiss}>Dismiss</Button>
    </div>
  </div>;
}
