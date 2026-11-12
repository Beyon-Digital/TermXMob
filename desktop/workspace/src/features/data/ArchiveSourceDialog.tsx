import {useRef, useState} from 'react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {dataBody, dataError, dataPath, dataRequest, useDataScope, type DataConnection} from '../../lib/data-studio';

type Props = {record?: DataConnection; workspaceId: string; onClose: () => void; onArchived: (record: DataConnection) => void};
/** Final confirmation before a source profile is archived for the whole workspace. */
export default function ArchiveSourceDialog({record, workspaceId, onClose, onArchived}: Props) {
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), scope = useDataScope(), flight = useRef(false);
  async function archive() {
    if (!record || flight.current) return;
    flight.current = true; setBusy(true); setError('');
    const controller = scope.controller();
    try {
      await dataRequest(dataPath(workspaceId, '/connections/' + encodeURIComponent(record.id)), {...dataBody('DELETE', {revision: record.revision}), signal: controller.signal});
      if (scope.current() && !controller.signal.aborted) onArchived(record);
    } catch (reason) {
      if (scope.current() && !controller.signal.aborted) setError(dataError(reason));
    } finally {
      flight.current = false;
      if (scope.current()) setBusy(false);
      scope.release(controller);
    }
  }
  return <Dialog open={!!record} onOpenChange={open => {if (!open && !busy) {setError(''); onClose();}}}>
    <DialogContent className="data-dialog" closeDisabled={busy}>
      <DialogTitle>Archive connection</DialogTitle>
      <DialogDescription>Archive “{record?.name}” from this workspace.</DialogDescription>
      {error && <p role="alert">{error}</p>}
      <div className="data-dialog-actions"><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button variant="destructive" disabled={busy} onClick={() => void archive()}>{busy ? 'Archiving…' : 'Archive'}</Button></div>
    </DialogContent>
  </Dialog>;
}
