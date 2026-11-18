import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';

type Props = {
  /** The files with unsaved edits that closing would drop. Empty closes the dialog. */
  paths: readonly string[];
  onCancel: () => void;
  onDiscard: () => void;
};

/** Asked before a file tab with unsaved edits closes. Cancel keeps the tab; Discard drops the edits. */
export default function DiscardFilesDialog({paths, onCancel, onDiscard}: Props) {
  return (
    <Dialog open={paths.length > 0} onOpenChange={open => !open && onCancel()}>
      <DialogContent className="discard-dialog">
        <DialogTitle>Discard unsaved changes?</DialogTitle>
        <DialogDescription>
          {paths.length === 1 ? paths[0] + ' has' : paths.length + ' files have'} edits that are not saved. Closing drops them.
        </DialogDescription>
        {paths.length > 1 && (
          <ul className="discard-list">
            {paths.slice(0, 8).map(path => (
              <li key={path}>{path}</li>
            ))}
            {paths.length > 8 && <li>and {paths.length - 8} more</li>}
          </ul>
        )}
        <div className="discard-actions">
          <Button variant="outline" onClick={onCancel}>
            Keep editing
          </Button>
          <Button variant="destructive" onClick={onDiscard}>
            Discard changes
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
