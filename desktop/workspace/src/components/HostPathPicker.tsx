import {useEffect, useId, useLayoutEffect, useRef, useState} from 'react';
import {ArrowUp, Check, ChevronRight, File, Folder, Home} from 'lucide-react';
import {request} from '../lib/api';
import {Button} from './ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from './ui/dialog';
import {Input} from './ui/input';
import './host-path-picker.css';

type PathScope = {ownerId?: string; projectId?: string; sessionId?: string; worktreeId?: string; kind?: 'directory' | 'file'};
export type PathEntry = {name: string; path: string; dir: boolean};
export type PathListing = {path: string; parent: string | null; breadcrumbs: {name: string; path: string}[]; entries: PathEntry[]; next_offset: number | null; total: number; home: string | null};
type PickerProps = PathScope & {open: boolean; initialPath?: string; disabled?: boolean; onOpenChange: (open: boolean) => void; onChoose: (path: string) => void};

export function HostPathPicker({open, initialPath = '', disabled = false, ownerId, projectId, sessionId, worktreeId, kind = 'directory', onOpenChange, onChoose}: PickerProps) {
  const scope = JSON.stringify([ownerId, projectId, sessionId, worktreeId, kind]);
  const [navigation, setNavigation] = useState({scope, path: kind === 'directory' ? initialPath : ''});
  const path = navigation.scope === scope ? navigation.path : kind === 'directory' ? initialPath : '';
  const [query, setQuery] = useState(''), [showHidden, setShowHidden] = useState(false), [offset, setOffset] = useState(0);
  const [result, setResult] = useState<{key: string; listing: PathListing} | null>(null);
  const [failure, setFailure] = useState<{key: string; message: string} | null>(null);
  const [loading, setLoading] = useState(false), [retry, setRetry] = useState(0), [selection, setSelection] = useState<{key: string; entry: PathEntry} | null>(null);
  const generation = useRef(0), wasOpen = useRef(false), chooseFlight = useRef(false);
  const current = useRef({open, disabled, scope});
  current.current = {open, disabled, scope};
  const key = JSON.stringify([scope, path, query, showHidden]);
  const listing = result?.key === key ? result.listing : null;
  const error = failure?.key === key ? failure.message : '';
  const selected = selection?.key === key ? selection.entry : null;
  const searchId = useId(), breadcrumbs = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {if (breadcrumbs.current) breadcrumbs.current.scrollLeft = breadcrumbs.current.scrollWidth;}, [result]);

  useEffect(() => {
    if (open && !wasOpen.current) {setNavigation({scope, path: kind === 'directory' ? initialPath : ''}); setQuery(''); setOffset(0); setSelection(null); chooseFlight.current = false;}
    wasOpen.current = open;
  }, [open, scope, initialPath, kind]);
  useEffect(() => {
    const ticket = ++generation.current;
    if (!open || disabled) {setResult(null); setFailure(null); setLoading(false); return;}
    const controller = new AbortController();
    const params = new URLSearchParams({path, kind, query, show_hidden: String(showHidden), offset: String(offset)});
    if (projectId) params.set('project_id', projectId);
    if (sessionId) params.set('session_id', sessionId);
    if (worktreeId) params.set('worktree_id', worktreeId);
    setLoading(true); setFailure(null); setSelection(null);
    void request<PathListing>('/api/workspace/paths?' + params, {signal: controller.signal}).then(listing => {
      if (generation.current !== ticket || !current.current.open || current.current.disabled || current.current.scope !== scope) return;
      setResult(previous => ({key, listing: {...listing, entries: offset && previous?.key === key ? [...previous.listing.entries, ...listing.entries] : listing.entries}}));
    }).catch(reason => {
      if (generation.current !== ticket || controller.signal.aborted || !current.current.open || current.current.disabled || current.current.scope !== scope) return;
      setResult(null); setFailure({key, message: reason instanceof Error ? reason.message : String(reason)});
    }).finally(() => {if (generation.current === ticket) setLoading(false);});
    return () => {generation.current++; controller.abort();};
  }, [open, disabled, scope, key, offset, retry]);
  useEffect(() => {
    const close = () => {generation.current++; setResult(null); setSelection(null); onOpenChange(false);};
    window.addEventListener('termx-locked', close); window.addEventListener('termx-session-locked', close); window.addEventListener('termx-signed-out', close);
    return () => {window.removeEventListener('termx-locked', close); window.removeEventListener('termx-session-locked', close); window.removeEventListener('termx-signed-out', close);};
  }, [onOpenChange]);

  function navigate(next: string) {setNavigation({scope, path: next}); setQuery(''); setOffset(0); setSelection(null);}
  function choose() {
    if (!listing || loading || error || disabled || chooseFlight.current) return;
    const value = kind === 'directory' ? listing.path : selected?.path;
    if (value === undefined) return;
    chooseFlight.current = true; onChoose(value); onOpenChange(false);
  }
  return <Dialog open={open && !disabled} onOpenChange={onOpenChange}>
    <DialogContent className="host-path-picker" onKeyDown={event => {if (event.key === 'Enter' && event.target instanceof HTMLInputElement) {event.preventDefault(); event.stopPropagation();}}}>
      <DialogTitle>{kind === 'directory' ? 'Choose folder' : 'Choose file'}</DialogTitle>
      <DialogDescription>{projectId || sessionId ? 'Browse this project’s files.' : 'Browse folders on the connected host.'}</DialogDescription>
      <div className="host-path-toolbar">
        <Button variant="ghost" size="icon" aria-label="Parent folder" title="Parent folder" disabled={loading || !listing || listing.parent === null} onClick={() => listing?.parent !== null && listing?.parent !== undefined && navigate(listing.parent)}><ArrowUp size={20}/></Button>
        {!projectId && !sessionId && <Button variant="ghost" size="icon" aria-label="Home folder" title="Home folder" disabled={loading} onClick={() => navigate('')}><Home size={20}/></Button>}
        <nav ref={breadcrumbs} aria-label="Folder breadcrumbs" className="host-path-breadcrumbs">{listing?.breadcrumbs.map((crumb, index) => <span key={crumb.path}>{index > 0 && <ChevronRight size={16}/>}<Button variant="ghost" disabled={loading || index === listing.breadcrumbs.length - 1} aria-current={index === listing.breadcrumbs.length - 1 ? 'location' : undefined} onClick={() => navigate(crumb.path)} title={crumb.path || crumb.name}>{crumb.name}</Button></span>)}</nav>
      </div>
      <div className="host-path-filter"><label htmlFor={searchId}>Filter {kind === 'directory' ? 'folders' : 'files'}</label><Input id={searchId} value={query} onChange={event => {setQuery(event.target.value); setOffset(0);}} autoComplete="off" maxLength={200}/><label className="host-path-hidden"><input type="checkbox" checked={showHidden} onChange={event => {setShowHidden(event.target.checked); setOffset(0);}}/>Show hidden</label></div>
      <div className="host-path-entries" aria-label={kind === 'directory' ? 'Folders' : 'Files and folders'} aria-busy={loading}>
        {error ? <div className="host-path-state"><p role="alert">{error}</p><Button variant="secondary" onClick={() => setRetry(value => value + 1)}>Try again</Button>{!projectId && !sessionId && <Button variant="ghost" onClick={() => navigate('')}>Open home folder</Button>}</div> : listing?.entries.map(entry => <Button key={entry.path} className="host-path-entry" variant="ghost" aria-label={(entry.dir ? 'Open folder ' : 'Select file ') + entry.name} aria-pressed={!entry.dir && selected?.path === entry.path} onClick={() => entry.dir ? navigate(entry.path) : setSelection({key, entry})} title={entry.path}>{entry.dir ? <Folder size={20}/> : <File size={20}/>}<span>{entry.name}</span>{entry.dir ? <ChevronRight size={16}/> : selected?.path === entry.path ? <Check size={16}/> : null}</Button>)}
        {loading && <p className="host-path-state" role="status">Opening folder…</p>}
        {!loading && !error && listing && !listing.entries.length && <p className="host-path-state">{query ? 'No matching ' + (kind === 'directory' ? 'folders.' : 'files.') : kind === 'directory' ? 'No folders here.' : 'This folder is empty.'}</p>}
        {!loading && listing?.next_offset !== null && listing?.next_offset !== undefined && <Button variant="ghost" onClick={() => setOffset(listing.next_offset!)}>Show more</Button>}
      </div>
      <p className="host-path-current" title={selected?.path ?? listing?.path}>{kind === 'file' ? selected?.path || 'Select a file' : listing?.path || (projectId || sessionId ? 'Project folder' : 'Home folder')}</p>
      <div className="host-path-actions"><Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button><Button disabled={loading || !listing || !!error || kind === 'file' && !selected} onClick={choose}>{kind === 'directory' ? 'Choose folder' : 'Choose file'}</Button></div>
    </DialogContent>
  </Dialog>;
}

type FieldProps = PathScope & {value: string; onChange: (path: string) => void; disabled?: boolean; label?: string; name?: string; error?: boolean; describedBy?: string};
export function HostPathField({value, onChange, disabled = false, label = 'Project folder', name = 'project-path', error, describedBy, ...scope}: FieldProps) {
  const [open, setOpen] = useState(false);
  const labelId = useId();
  const context = JSON.stringify([scope.ownerId, scope.projectId, scope.sessionId, scope.worktreeId, scope.kind]);
  useLayoutEffect(() => {setOpen(false);}, [disabled, context]);
  return <div className="host-path-field">
    <span className="host-path-label" id={labelId}>{label}</span>
    <div className="host-path-choice" role="group" aria-labelledby={labelId}><p title={value}>{value || 'No ' + (scope.kind === 'file' ? 'file' : 'folder') + ' selected'}</p><Button variant="secondary" disabled={disabled} onClick={() => setOpen(true)}>{value ? 'Change' : 'Browse ' + (scope.kind === 'file' ? 'files' : 'folders')}</Button></div>
    <details className="host-path-manual"><summary tabIndex={disabled ? -1 : undefined} aria-disabled={disabled} onClick={event => {if (disabled) event.preventDefault();}} onKeyDown={event => {if (disabled) event.preventDefault();}}>Enter path</summary><label>{scope.kind === 'file' ? 'File path' : 'Folder path'}<Input name={name} autoComplete="off" spellCheck={false} value={value} disabled={disabled} onChange={event => onChange(event.target.value)} aria-invalid={error} aria-describedby={describedBy}/></label></details>
    <HostPathPicker {...scope} open={open} disabled={disabled} initialPath={value} onOpenChange={setOpen} onChoose={onChange}/>
  </div>;
}
