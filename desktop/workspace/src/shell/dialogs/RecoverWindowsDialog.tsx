import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import type {Session} from '../../lib/api';
import type {RecoveryRow} from '../WindowTransfer';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  rows: RecoveryRow[];
  /** Conversation titles are looked up here; a window whose conversation is gone shows as a preserved workspace. */
  sessions: Pick<Session, 'id' | 'title'>[];
  onReopen: (row: RecoveryRow) => void;
};

/** Windows that closed with unsaved files, drafts or artifacts, each with a way to reopen it. */
export default function RecoverWindowsDialog({open, onOpenChange, rows, sessions, onReopen}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Recover unsaved windows</DialogTitle>
        <DialogDescription>
          Reopen your own preserved window state. Active named windows are focused; files are not saved
          automatically.
        </DialogDescription>
        {!rows.length && <p>No other unsaved windows are stored for this account.</p>}
        {rows.map(row => (
          <div className="creation-actions" key={row.slot}>
            <span>
              {sessions.find(session => session.id === row.sessionId)?.title || 'Preserved workspace'} ·{' '}
              {row.buffers} unsaved files · {row.drafts} drafts · {row.artifacts} unsaved artifacts
            </span>
            <Button onClick={() => onReopen(row)} disabled={!row.sessionId}>
              Reopen window
            </Button>
          </div>
        ))}
      </DialogContent>
    </Dialog>
  );
}
