import {useId, useLayoutEffect, useRef, useState} from 'react';
import {Button} from './ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from './ui/dialog';
import {Input} from './ui/input';
import {gql, type Project} from '../lib/api';
import {HostPathField} from './HostPathPicker';

/** Host paths are absolute: POSIX, home-relative, Windows drive or UNC. */
export function isAbsoluteHostPath(value: string) {
  return /^(\/|~(\/|\\|$)|[A-Za-z]:[\\/]|\\\\)/.test(value.trim());
}

/** Compare only path spelling we can resolve without inspecting the host. */
export function sameHostPath(left: string, right: string) {
  if (!left.trim() || !right.trim()) return false;
  const key = (value: string) => {
    const path = value.trim();
    if (/^(?:[A-Za-z]:[\\/]|\\\\)/.test(path)) return path.replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase();
    return path.replace(/\/+$/, '') || '/';
  };
  return key(left) === key(right);
}

type Props = {
  open: boolean;
  ownerId?: string;
  enabled?: boolean;
  projects?: Project[];
  onOpenChange: (open: boolean) => void;
  /** Check isCurrent after any awaited refresh before changing navigation. */
  onRegistered: (project: Project, isCurrent: () => boolean) => void | Promise<void>;
};

type Flight = {ownerId: string | undefined; epoch: number};
type Accepted = {project: Project; path: string; ownerId: string | undefined};

export default function AddProjectDialog({open, ownerId, enabled = true, projects = [], onOpenChange, onRegistered}: Props) {
  const [path, setPath] = useState(''), [name, setName] = useState(''), [busy, setBusy] = useState<'add' | 'open' | null>(null), [error, setError] = useState('');
  const [accepted, setAccepted] = useState<Accepted | null>(null);
  const flight = useRef<Flight | null>(null), generation = useRef(0), mounted = useRef(false);
  const content = useRef<HTMLDivElement | null>(null);
  const scope = useRef({open, ownerId, enabled});
  const errorId = useId();
  const existing = projects.find(project => sameHostPath(project.path, path)) || (accepted && accepted.ownerId === ownerId && sameHostPath(accepted.path, path) ? accepted.project : undefined);
  const reset = () => { setPath(''); setName(''); setError(''); setAccepted(null); };

  useLayoutEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; generation.current++; };
  }, []);
  useLayoutEffect(() => {
    generation.current++;
    const ownerChanged = scope.current.ownerId !== ownerId;
    scope.current = {open, ownerId, enabled};
    if (ownerChanged || !open && !flight.current) reset();
  }, [open, ownerId, enabled]);
  useLayoutEffect(() => {
    const pending = flight.current;
    // Disabled fields cannot serve as Radix's last focused element. Keep a
    // stable focus target while this generation has no enabled controls.
    if (busy && open && enabled && pending?.epoch === generation.current && pending.ownerId === ownerId) content.current?.focus({preventScroll: true});
  }, [busy, open, enabled, ownerId]);

  async function submit() {
    // The ref also blocks multiple submits before React commits disabled controls.
    if (flight.current || !open || !enabled) return;
    const folder = path.trim();
    if (!isAbsoluteHostPath(folder)) { setError('Enter the full folder path on the host.'); return; }
    const epoch = generation.current, ticket: Flight = {ownerId, epoch};
    flight.current = ticket;
    const isCurrent = () => mounted.current && flight.current === ticket && generation.current === epoch && scope.current.open && scope.current.enabled && scope.current.ownerId === ticket.ownerId;
    setBusy(existing ? 'open' : 'add'); setError('');
    try {
      const project = existing || (await gql<{register_project: Project}>('mutation($input:ProjectInput!){register_project(input:$input){id name path}}', {input: {path: folder, name: name.trim()}})).register_project;
      // A completed registration remains available for an explicit retry/open,
      // even when a same-owner hide invalidated its automatic navigation.
      if (mounted.current && scope.current.ownerId === ticket.ownerId) setAccepted({project, path: folder, ownerId: ticket.ownerId});
      if (!isCurrent()) return;
      await onRegistered(project, isCurrent);
      if (!isCurrent()) return;
      reset();
      onOpenChange(false);
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (flight.current === ticket) {
        flight.current = null;
        if (mounted.current) setBusy(null);
      }
    }
  }
  return <Dialog open={open && enabled} onOpenChange={next => {
    if (flight.current) return;
    if (!next) { generation.current++; reset(); }
    onOpenChange(next);
  }}>
    <DialogContent ref={content} tabIndex={-1} closeDisabled={!!busy}
      onEscapeKeyDown={event => { if (flight.current) event.preventDefault(); }}
      onInteractOutside={event => { if (flight.current) { event.preventDefault(); event.detail.originalEvent.preventDefault(); } }}
      onBlurCapture={event => {
        const node = event.currentTarget, pending = flight.current;
        if (!pending || node.contains(event.relatedTarget as Node | null)) return;
        // A click on the nonfocusable scrim can blur to body without focusin.
        // Recover only this live pending dialog while the window has focus.
        queueMicrotask(() => {
          if (mounted.current && flight.current === pending && pending.epoch === generation.current && scope.current.open && scope.current.enabled && scope.current.ownerId === pending.ownerId && content.current === node && node.ownerDocument.hasFocus() && !node.contains(node.ownerDocument.activeElement)) node.focus({preventScroll: true});
        });
      }}>
      <DialogTitle>Add project</DialogTitle>
      <DialogDescription>Use an existing folder on the host.</DialogDescription>
      <form className="add-project-form" aria-busy={!!busy} onSubmit={event => { event.preventDefault(); void submit(); }}>
        <HostPathField value={path} disabled={!!busy} ownerId={ownerId} onChange={value => { setPath(value); setError(''); }} error={!!error} describedBy={error ? errorId : undefined}/>
        {existing ? <p>Already added as {existing.name}.</p> : <label><span>Name <small>(optional)</small></span><Input name="project-name" autoComplete="off" value={name} disabled={!!busy} onChange={event => setName(event.target.value)} placeholder="Defaults to the folder name"/></label>}
        {error && <p id={errorId} className="add-project-error" role="alert">{error}</p>}
        <Button type="submit" disabled={!!busy || !path.trim()}>{busy === 'add' ? 'Adding…' : busy === 'open' ? 'Opening…' : existing ? 'Open project' : 'Add project'}</Button>
      </form>
    </DialogContent>
  </Dialog>;
}
