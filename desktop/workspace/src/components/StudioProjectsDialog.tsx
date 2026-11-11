import {useEffect, useId, useRef, useState} from 'react';
import {Archive, ArrowLeft, Pencil, Plus, RotateCcw} from 'lucide-react';
import {Button} from './ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from './ui/dialog';
import {FormField} from './ui/form-field';
import {Input} from './ui/input';
import {Textarea} from './ui/textarea';
import type {StudioProject, StudioProjectsController} from '../lib/studio-projects';
import './studio-projects.css';

type View = {kind: 'list'} | {kind: 'create'} | {kind: 'edit' | 'archive'; project: StudioProject};
export default function StudioProjectsDialog({open, onOpenChange, controller, initialCreate = false, projectId, onProject, projectChangeBlocked, onBeforeArchive}: {
  open: boolean; onOpenChange: (open: boolean) => void; controller: StudioProjectsController;
  initialCreate?: boolean; projectId: string | null; onProject: (id: string) => void; projectChangeBlocked?: string;
  onBeforeArchive?: (project: StudioProject) => Promise<boolean>;
}) {
  const uid = useId(), [view, setView] = useState<View>({kind: 'list'}), [name, setName] = useState(''), [description, setDescription] = useState('');
  const [confirmation, setConfirmation] = useState(''), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [pending, setPending] = useState(false);
  const busy = controller.busy || pending;
  const [showArchived, setShowArchived] = useState(false), flight = useRef(false), generation = useRef(0);
  const heading = useRef<HTMLHeadingElement>(null), firstInput = useRef<HTMLInputElement>(null);
  useEffect(() => {generation.current++; if (open) {setView(initialCreate ? {kind: 'create'} : {kind: 'list'}); setName(''); setDescription(''); setConfirmation(''); setError(''); setNotice(''); setShowArchived(false);} return () => {generation.current++;};}, [open, initialCreate]);
  useEffect(() => {if (!open) return; if (view.kind === 'create' || view.kind === 'edit') firstInput.current?.focus(); else heading.current?.focus();}, [view, open]);
  function changeView(next: View) {if (busy) return; setView(next); setName('project' in next ? next.project.name : ''); setDescription('project' in next ? next.project.description : ''); setConfirmation(''); setError(''); setNotice('');}
  const editing = view.kind === 'edit' || view.kind === 'archive' ? view.project : null;
  const current = editing ? controller.projects.find(row => row.id === editing.id) : undefined;
  const changed = !!editing && (!current || current.revision !== editing.revision);
  async function action(run: () => Promise<StudioProject | null>, after: (project: StudioProject) => void) {
    if (flight.current || busy) return;
    const token = generation.current; flight.current = true; setPending(true); setError(''); setNotice('');
    try {const result = await run(); if (result && token === generation.current) after(result);}
    catch (reason) {if (token === generation.current) setError(reason instanceof Error ? reason.message : String(reason));}
    finally {flight.current = false; setPending(false);}
  }
  function save() {
    if (!name.trim() || name.trim().length > 120 || description.length > 1000) {setError('Enter a project name of 1–120 characters and a description of up to 1,000 characters.'); return;}
    if (changed) {setError('This project changed. Return to projects and open it again.'); return;}
    const values = {name: name.trim(), description: description.trim()};
    void action(() => view.kind === 'edit' ? controller.update(view.project, values) : controller.create(values), project => {
      setNotice(view.kind === 'edit' ? 'Project saved.' : 'Project created.'); setView({kind: 'list'});
      if (view.kind === 'create') {onProject(project.id); onOpenChange(false);}
    });
  }
  const title = view.kind === 'list' ? 'Studio projects' : view.kind === 'create' ? 'Create project' : view.kind === 'edit' ? 'Edit project' : 'Archive project';
  const rows = controller.projects.filter(row => row.archived === showArchived);
  return <Dialog open={open} onOpenChange={next => {if (!busy) onOpenChange(next);}}>
    <DialogContent className="studio-project-dialog" onEscapeKeyDown={event => {if (busy) event.preventDefault();}} onPointerDownOutside={event => {if (busy) event.preventDefault();}}>
      <DialogTitle ref={heading} tabIndex={-1}>{title}</DialogTitle>
      <DialogDescription>{view.kind === 'list' ? 'Organize your Studio generations and saved assets into separate projects.' : view.kind === 'archive' ? 'Archive this Studio collection while keeping its media and history.' : 'A Studio project keeps its own media, saved assets and generation history.'}</DialogDescription>
      {view.kind !== 'list' && <Button variant="ghost" className="studio-project-back" disabled={busy} onClick={() => changeView({kind: 'list'})}><ArrowLeft size={16} aria-hidden="true"/>All projects</Button>}
      {view.kind === 'list' ? <>
        <div className="studio-project-toolbar"><Button disabled={busy} onClick={() => changeView({kind: 'create'})}><Plus size={16} aria-hidden="true"/>Create project</Button><Button variant="ghost" aria-pressed={showArchived} disabled={busy} onClick={() => setShowArchived(value => !value)}>{showArchived ? 'Show active' : 'Show archived'}</Button></div>
        {controller.loading && <p role="status" className="studio-project-hint">Loading projects…</p>}
        {!controller.loading && !controller.error && !rows.length && <p className="studio-project-hint">{showArchived ? 'No archived projects.' : 'No Studio projects yet. Create your first project.'}</p>}
        <ul className="studio-project-list">{rows.map(project => <li key={project.id}>
          <div className="studio-project-copy"><strong>{project.name}</strong>{project.description && <p>{project.description}</p>}{project.id === projectId && <span>Current project</span>}</div>
          <div className="studio-project-row-actions">{showArchived ? <Button variant="outline" disabled={busy} aria-label={'Restore ' + project.name} onClick={() => void action(() => controller.restore(project), () => {setNotice('Project restored.'); setShowArchived(false);})}><RotateCcw size={16} aria-hidden="true"/>Restore</Button> : <Button variant="ghost" disabled={busy} aria-label={'Edit ' + project.name} onClick={() => changeView({kind: 'edit', project})}><Pencil size={16} aria-hidden="true"/><span>Edit</span></Button>}</div>
        </li>)}</ul>
      </> : view.kind === 'archive' && editing ? <div className="studio-project-form">
        <p>Archive <strong>{editing.name}</strong>? It will leave the active project list. Generations, uploaded references and saved assets stay in this project and return when you restore it. Agent projects, chats, worktrees and source folders are kept.</p>
        <p className="studio-project-hint">Active or unresolved generation requests must be settled before archiving.</p>
        <FormField label="Project name" hint={'Type “' + editing.name + '” to confirm.'} required>{control => <Input {...control} value={confirmation} autoComplete="off" disabled={busy} onChange={event => setConfirmation(event.target.value)}/>}</FormField>
        <div className="studio-project-actions"><Button variant="outline" disabled={busy} onClick={() => changeView({kind: 'edit', project: editing})}>Cancel</Button><Button variant="destructive" className="studio-project-confirm" disabled={busy || changed || confirmation !== editing.name || (editing.id === projectId && !!projectChangeBlocked)} onClick={() => void action(async () => {if (onBeforeArchive && !await onBeforeArchive(editing)) return null; return controller.archive(editing);}, () => {if (projectId === editing.id) onProject(''); setView({kind: 'list'}); setNotice('Project archived. Its media and history are preserved.');})}>{busy ? 'Archiving…' : 'Archive project'}</Button></div>
      </div> : <form className="studio-project-form" onSubmit={event => {event.preventDefault(); save();}}>
        <FormField label="Project name" required>{control => <Input {...control} ref={firstInput} value={name} maxLength={120} autoComplete="off" disabled={busy} onChange={event => setName(event.target.value)}/>}</FormField>
        <FormField label="Description" hint="Optional. Up to 1,000 characters.">{control => <Textarea {...control} value={description} maxLength={1000} rows={3} disabled={busy} onChange={event => setDescription(event.target.value)}/>}</FormField>
        <div className="studio-project-actions">{view.kind === 'edit' && <Button variant="outline" className="studio-project-archive" disabled={busy || changed || (editing?.id === projectId && !!projectChangeBlocked)} onClick={() => changeView({kind: 'archive', project: view.project})}><Archive size={16} aria-hidden="true"/>Archive project</Button>}<Button type="submit" disabled={busy || changed || !name.trim()}>{busy ? 'Saving…' : view.kind === 'edit' ? 'Save changes' : 'Create project'}</Button></div>
      </form>}
      {changed && <p role="status" className="studio-project-hint">This project changed. Return to projects and open it again.</p>}
      {projectChangeBlocked && <p id={uid + '-blocked'} className="studio-project-hint">{projectChangeBlocked}</p>}
      {(error || controller.error) && <div className="studio-project-error"><p role="alert">{error || controller.error}</p><Button variant="outline" disabled={busy} onClick={() => {controller.reload(); setError('');}}>Refresh projects</Button></div>}
      {notice && <p role="status" className="studio-project-hint">{notice}</p>}
    </DialogContent>
  </Dialog>;
}
