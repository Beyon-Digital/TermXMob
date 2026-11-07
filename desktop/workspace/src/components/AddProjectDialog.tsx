import {useState} from 'react';
import {Button} from './ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from './ui/dialog';
import {Input} from './ui/input';
import {gql, type Project} from '../lib/api';

/** Host paths are absolute: POSIX, home-relative, Windows drive or UNC. */
export function isAbsoluteHostPath(value: string) {
  return /^(\/|~(\/|\\|$)|[A-Za-z]:[\\/]|\\\\)/.test(value.trim());
}

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called with the registered project after the host accepted it. */
  onRegistered: (project: Project) => void | Promise<void>;
};

export default function AddProjectDialog({open, onOpenChange, onRegistered}: Props) {
  const [path, setPath] = useState(''), [name, setName] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const reset = () => { setPath(''); setName(''); setError(''); setBusy(false); };
  async function submit() {
    if (busy) return;
    const folder = path.trim();
    if (!isAbsoluteHostPath(folder)) { setError('Enter the full folder path on the host, for example /Users/you/code/app.'); return; }
    setBusy(true); setError('');
    try {
      const result = await gql<{register_project: Project}>('mutation($input:ProjectInput!){register_project(input:$input){id name path}}', {input: {path: folder, name: name.trim()}});
      await onRegistered(result.register_project);
      reset(); onOpenChange(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason)); setBusy(false);
    }
  }
  return <Dialog open={open} onOpenChange={next => { if (!next) reset(); onOpenChange(next); }}>
    <DialogContent>
      <DialogTitle>Add a project</DialogTitle>
      <DialogDescription>Register a folder on the machine that runs TermX. Agents, the file explorer and terminals use it as their workspace.</DialogDescription>
      <form className="add-project-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
        <label>Folder path<Input name="project-path" autoComplete="off" spellCheck={false} value={path} onChange={event => setPath(event.target.value)} placeholder="/Users/you/code/app" aria-invalid={!!error} aria-describedby={error ? 'add-project-error' : undefined} autoFocus/></label>
        <label><span>Name <small>(optional)</small></span><Input name="project-name" autoComplete="off" value={name} onChange={event => setName(event.target.value)} placeholder="Defaults to the folder name"/></label>
        {error && <p id="add-project-error" className="add-project-error" role="alert">{error}</p>}
        <Button type="submit" disabled={busy || !path.trim()}>{busy ? 'Adding…' : 'Add project'}</Button>
      </form>
    </DialogContent>
  </Dialog>;
}
