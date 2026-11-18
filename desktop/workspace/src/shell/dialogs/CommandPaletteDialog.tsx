import WorkspaceCommands from '../../components/WorkspaceCommands';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {paletteEntries, type Command, type CommandContext} from '../commands';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  context: CommandContext;
  /** The stored key bindings, shown beside each command that has one. */
  bindings: Partial<Record<string, string>>;
  /** Runs the chosen command. The palette closes itself afterwards. */
  onRun: (command: Command) => void;
};

/** The command center. It lists the command registry and nothing else. */
export default function CommandPaletteDialog({open, onOpenChange, context, bindings, onRun}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Workspace commands</DialogTitle>
        <DialogDescription>Use commands or keyboard shortcuts to arrange your workspace.</DialogDescription>
        <WorkspaceCommands
          commands={paletteEntries(context, bindings, command => {
            onRun(command);
            onOpenChange(false);
          })}
        />
      </DialogContent>
    </Dialog>
  );
}
