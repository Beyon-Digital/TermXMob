import LayoutPresets, {type PresetRow} from '../../components/LayoutPresets';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  presets: PresetRow[];
  onSave: (name: string) => void;
  onRestore: (id: string) => void;
  onRemove: (id: string) => void;
  onReset: () => void;
};

/** Saved layout presets: save the current arrangement, restore, remove, or reset to the default. */
export default function LayoutsDialog({
  open,
  onOpenChange,
  presets,
  onSave,
  onRestore,
  onRemove,
  onReset,
}: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Saved layout presets</DialogTitle>
        <DialogDescription>
          Restore an arrangement without changing the running task or saving files.
        </DialogDescription>
        <LayoutPresets
          presets={presets}
          onSave={onSave}
          onRestore={onRestore}
          onRemove={onRemove}
          onReset={onReset}
        />
      </DialogContent>
    </Dialog>
  );
}
