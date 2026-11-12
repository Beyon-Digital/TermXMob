import {useRef, useState, type FormEvent} from 'react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {Input} from '../../components/ui/input';
import {viewError, type ViewError} from '../../lib/data-views';
import {rescueFocusSoon} from './focus';

type Props = {
  open: boolean; title: string; description?: string; submitLabel: string; initialName?: string; helper?: string;
  /** Does the save. A thrown error is shown here; a name that is taken appears under the field. */
  onSubmit: (name: string) => Promise<unknown>;
  /** Return true when the caller shows the failure itself (a conflict banner) and the dialog should close. */ onFail?: (error: ViewError) => boolean;
  onClose: () => void;
};
/** One dialog for naming a view: save a pivot, save a segment, rename. Focus returns to the button that opened it. */
export default function SaveViewDialog({open, title, description, submitLabel, initialName = '', helper, onSubmit, onFail, onClose}: Props) {
  return <Dialog open={open} onOpenChange={next => {if (!next) onClose();}}>
    <DialogContent className="data-dialog" onCloseAutoFocus={rescueFocusSoon}>
      <DialogTitle>{title}</DialogTitle>
      <DialogDescription>{description || helper || 'Choose a name people will recognise.'}</DialogDescription>
      <NameForm submitLabel={submitLabel} initialName={initialName} onSubmit={onSubmit} onFail={onFail} onClose={onClose}/>
    </DialogContent>
  </Dialog>;
}
/** Holds the typed name. It unmounts with the dialog, so a reopened dialog starts clean. */
function NameForm({submitLabel, initialName, onSubmit, onFail, onClose}: Pick<Props, 'submitLabel' | 'onSubmit' | 'onFail' | 'onClose'> & {initialName: string}) {
  const [name, setName] = useState(initialName), [busy, setBusy] = useState(false), [taken, setTaken] = useState(''), [failure, setFailure] = useState(''), flight = useRef(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = name.trim();
    if (!value || flight.current) return;
    flight.current = true; setBusy(true); setTaken(''); setFailure('');
    try {
      await onSubmit(value);
      onClose();
    } catch (reason) {
      const error = viewError(reason);
      if (error.code === 'name_taken') setTaken(error.message);
      else if (onFail?.(error)) onClose();
      else setFailure(error.errors?.[0]?.message || error.message);
    } finally {
      flight.current = false; setBusy(false);
    }
  }
  // Focus stays on the control that has it while saving (read-only, not disabled), so it can return to the opener when the dialog closes.
  return <>
    <form onSubmit={event => void submit(event)} noValidate>
      <div className="data-field"><label htmlFor="save-view-name">Name</label>
        <Input id="save-view-name" value={name} maxLength={200} onChange={event => {setName(event.target.value); setTaken('');}} readOnly={busy} aria-invalid={!!taken} aria-describedby={taken ? 'save-view-error' : undefined}/>
        {taken && <p className="data-field-error" id="save-view-error" role="alert">{taken}</p>}
      </div>
      {failure && <p className="data-field-error" role="alert">{failure}</p>}
      <div className="data-dialog-actions"><Button variant="ghost" type="button" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" disabled={!name.trim()} aria-disabled={busy || undefined}>{busy ? 'Saving…' : submitLabel}</Button></div>
    </form>
  </>;
}
