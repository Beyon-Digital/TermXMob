import {useEffect, useId, useRef, useState} from 'react';
import {ChevronDown, ImagePlus, LoaderCircle, Trash2, UserRound, X} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../components/ui/collapsible';
import {uploadStudioImage, STUDIO_IMAGE_BYTES} from '../lib/media-studio';
import {createStudioSubject, deleteStudioSubject, loadStudioSubjects, type StudioSubject} from '../lib/studio-subjects';
import {transfer} from '../lib/transfers';
import {useStudioDraftState,useStudioProjectReadOnly} from '../lib/studio-draft-state';
import './studio-subjects.css';

type Props = {
  projectId: string | null; ownerId?: string; visible: boolean; observing?: boolean;
  onSelect: (imageId: string, name: string, description: string) => void;
  onSelectMany?: (imageIds: string[], name: string, description: string) => void;
  maxReferences?: number;
  embedded?: boolean;
};
type LocalImage = {file: File; url: string; key: string};
const errorMessage = (error: unknown) => error instanceof Error ? error.message : 'The workspace could not complete this action.';

// Keep this independent of the main gallery to avoid a component import cycle.
// Native transfers may outlive abort, so a running slot is released only on settlement.
const subjectPreviewQueue: Array<() => void> = [];
let subjectPreviewTransfers = 0;
function fetchSubjectPreview(path: string, signal: AbortSignal): Promise<Blob> {
  return new Promise((resolve, reject) => {
    let started = false;
    const cancel = () => {
      if (started) return;
      const index = subjectPreviewQueue.indexOf(start);
      if (index !== -1) subjectPreviewQueue.splice(index, 1);
      signal.removeEventListener('abort', cancel);
      reject(new DOMException('Preview cancelled', 'AbortError'));
    };
    const start = () => {
      if (signal.aborted) {cancel(); return;}
      started = true; subjectPreviewTransfers++;
      signal.removeEventListener('abort', cancel);
      void transfer(path, {signal}).then(blob => {
        if (signal.aborted) reject(new DOMException('Preview cancelled', 'AbortError'));
        else resolve(blob);
      }, reject).finally(() => {
        subjectPreviewTransfers--;
        while (subjectPreviewTransfers < 3 && subjectPreviewQueue.length) subjectPreviewQueue.shift()!();
      });
    };
    if (signal.aborted) {cancel(); return;}
    signal.addEventListener('abort', cancel, {once: true});
    if (subjectPreviewTransfers < 3) start(); else subjectPreviewQueue.push(start);
  });
}

function SubjectPhoto({imageId, name, scope, active, fallbackPreview, sessionSignal}: {
  imageId: string; name: string; scope: string; active: boolean; fallbackPreview: boolean; sessionSignal: AbortSignal;
}) {
  const root = useRef<HTMLSpanElement>(null);
  const [view, setView] = useState({inView: false, visit: 0});
  const [preview, setPreview] = useState<{scope: string; id: string; visit: number; url: string} | null>(null);
  const eligible = active && view.inView;
  const eligibleNow = useRef(eligible); eligibleNow.current = eligible;
  useEffect(() => {
    const show = (inView: boolean) => setView(previous => previous.inView === inView
      ? previous : {inView, visit: previous.visit + 1});
    show(false);
    const element = root.current;
    if (!active || !element) return;
    if (typeof IntersectionObserver === 'undefined') {show(fallbackPreview); return;}
    let stopped = false;
    // A viewport observer also clips intersections against the scrollable saved-set list.
    const observer = new IntersectionObserver(entries => {
      if (!stopped) show(entries.some(entry => entry.target === element && entry.isIntersecting && entry.intersectionRatio > 0));
    }, {threshold: 0.01});
    observer.observe(element);
    return () => {stopped = true; observer.disconnect();};
  }, [imageId, scope, active, fallbackPreview]);
  useEffect(() => {
    if (!eligible || sessionSignal.aborted) return;
    let valid = true, url = '';
    const controller = new AbortController();
    const stop = () => {
      valid = false; controller.abort();
      if (url) {URL.revokeObjectURL(url); url = '';}
    };
    sessionSignal.addEventListener('abort', stop, {once: true});
    void fetchSubjectPreview('/api/media/artifacts/' + encodeURIComponent(imageId) + '/export?format=original', controller.signal)
      .then(blob => {
        if (!valid || controller.signal.aborted || !eligibleNow.current) return;
        if (!['image/png', 'image/jpeg', 'image/webp'].includes(blob.type) || !blob.size || blob.size > STUDIO_IMAGE_BYTES) return;
        url = URL.createObjectURL(blob);
        setPreview({scope, id: imageId, visit: view.visit, url});
      }).catch(() => {});
    return () => {sessionSignal.removeEventListener('abort', stop); stop();};
  }, [imageId, scope, eligible, view.visit, sessionSignal]);
  return <span className="subject-photo-preview" ref={root}>
    {eligible && preview?.scope === scope && preview.id === imageId && preview.visit === view.visit
      ? <img src={preview.url} alt={name} loading="lazy"/>
      : <span className="subject-photo-placeholder" role="img" aria-label={name}><UserRound size={19}/></span>}
  </span>;
}

export default function StudioSubjects(props: Props) {
  return <StudioSubjectsPanel key={JSON.stringify([props.ownerId || "", props.projectId])} {...props}/>;
}

function StudioSubjectsPanel({projectId, ownerId, visible, observing = true, onSelect, onSelectMany, maxReferences, embedded = false}: Props) {
  const projectReadOnly=useStudioProjectReadOnly();
  const scope = JSON.stringify([ownerId || '', projectId]);
  const scopeNow = useRef(scope); scopeNow.current = scope;
  const visibleNow = useRef(visible); visibleNow.current = visible;
  const observingNow = useRef(observing); observingNow.current = observing;
  const [locked, setLocked] = useState(false);
  const lockedNow = useRef(false);
  const [foreground, setForeground] = useState(document.visibilityState !== 'hidden');
  const foregroundNow = useRef(foreground); foregroundNow.current = foreground;
  const epoch = useRef(0);
  const controllers = useRef(new Set<AbortController>());
  const previewSession = useRef(new AbortController());
  const busyNow = useRef(false);
  const filesNow = useRef<LocalImage[]>([]);
  const [open, setOpen] = useState(false);
  const effectiveOpen = embedded || open;
  const [data, setData] = useState<{scope: string; rows: StudioSubject[]}>({scope, rows: []});
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [query, setQuery] = useState('');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [files, setFiles] = useState<LocalImage[]>([]);
  useStudioDraftState('subjects',!!(name.trim()||description.trim()||files.length),busy,'Finish saving the reference set before changing projects.');
  const [pendingRemove, setPendingRemove] = useState('');
  const [portraits, setPortraits] = useState<Record<string, string>>({});
  const id = useId();
  const allowed = visible && !locked && foreground;
  const readActive = allowed && observing;
  const workActive = readActive && !projectReadOnly;
  const selectionGeneration = epoch.current;
  const referenceLimit = maxReferences === undefined ? 1
    : Number.isInteger(maxReferences) && maxReferences > 0 ? Math.min(5, maxReferences) : 0;
  const rows = allowed && data.scope === scope ? data.rows : [];
  const alive = (generation: number, key: string) => generation === epoch.current && key === scopeNow.current && visibleNow.current && observingNow.current && !lockedNow.current && foregroundNow.current;
  const controller = () => {
    const value = new AbortController(); controllers.current.add(value); return value;
  };
  const clearFiles = () => {
    filesNow.current.forEach(image => {if (image.url) URL.revokeObjectURL(image.url);});
    filesNow.current = []; setFiles([]);
  };
  const releaseFilePreviews = () => {
    const next = filesNow.current.map(image => {
      if (image.url) URL.revokeObjectURL(image.url);
      return {...image, url: ''};
    });
    filesNow.current = next; setFiles(next);
  };
  const cancel = () => {
    epoch.current++;
    previewSession.current.abort(); previewSession.current = new AbortController();
    controllers.current.forEach(value => value.abort()); controllers.current.clear();
    busyNow.current = false;
  };
  const suspend = () => {
    const interrupted = busyNow.current;
    cancel(); releaseFilePreviews(); setPendingRemove('');
    setBusy(false); setLoading(false); setProgress('');
    if (interrupted) setNotice('The action was interrupted. Your draft is kept. Review saved sets before retrying.');
  };
  const reset = () => {
    cancel(); clearFiles(); setData({scope: scopeNow.current, rows: []});
    setName(''); setDescription(''); setQuery(''); setPortraits({}); setPendingRemove('');
    setBusy(false); setLoading(false); setProgress(''); setError(''); setNotice(''); setOpen(false);
  };
  useEffect(() => {
    const lock = () => { lockedNow.current = true; setLocked(true); reset(); };
    const unlock = () => { lockedNow.current = false; setLocked(false); };
    const visibility = () => {
      const active = document.visibilityState !== 'hidden';
      foregroundNow.current = active; setForeground(active);
      if (!active) suspend();
    };
    window.addEventListener('termx-locked', lock);
    window.addEventListener('termx-session-locked', lock);
    window.addEventListener('termx-signed-out', lock);
    window.addEventListener('termx-session-unlocked', unlock);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      cancel(); filesNow.current.forEach(image => {if (image.url) URL.revokeObjectURL(image.url);}); filesNow.current = [];
      window.removeEventListener('termx-locked', lock);
      window.removeEventListener('termx-session-locked', lock);
      window.removeEventListener('termx-signed-out', lock);
      window.removeEventListener('termx-session-unlocked', unlock);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);
  useEffect(() => { if (!visible || !observing) suspend(); }, [visible, observing]);
  useEffect(() => {
    if (!workActive || !filesNow.current.some(image => !image.url)) return;
    const next = filesNow.current.map(image => image.url ? image : {...image, url: URL.createObjectURL(image.file)});
    filesNow.current = next; setFiles(next);
  }, [workActive]);
  useEffect(() => {
    if (!readActive || !effectiveOpen) return;
    const generation = epoch.current, key = scope;
    const abort = controller(); setLoading(true); setError('');
    setData(previous => previous.scope === key ? previous : {scope: key, rows: []});
    void loadStudioSubjects(projectId, abort.signal).then(items => {
      if (!alive(generation, key) || abort.signal.aborted) return;
      setData({scope: key, rows: items.filter(item => item.project_id === projectId)});
    }).catch(reason => {
      if (alive(generation, key) && !abort.signal.aborted) setError(errorMessage(reason));
    }).finally(() => {
      controllers.current.delete(abort);
      if (alive(generation, key) && !abort.signal.aborted) setLoading(false);
    });
    return () => { abort.abort(); controllers.current.delete(abort); };
  }, [scope, readActive, effectiveOpen]);

  const chooseFiles = (picked: File[]) => {
    if (!allowed || busyNow.current) return;
    setError(''); setNotice('');
    if (filesNow.current.length + picked.length > 5) { setError('Choose between 1 and 5 photos per set.'); return; }
    if (picked.some(file => !['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || !file.size || file.size > STUDIO_IMAGE_BYTES)) {
      setError('Choose static PNG, JPEG or WebP images, up to 8 MiB each.'); return;
    }
    const next = [...filesNow.current, ...picked.map(file => {
      const url = workActive ? URL.createObjectURL(file) : '';
      return {file, url, key: url || crypto.randomUUID()};
    })];
    filesNow.current = next; setFiles(next);
  };
  const removeFile = (index: number) => {
    if (!allowed || busyNow.current) return;
    if (filesNow.current[index].url) URL.revokeObjectURL(filesNow.current[index].url);
    filesNow.current = filesNow.current.filter((_, position) => position !== index); setFiles(filesNow.current);
  };
  const save = async () => {
    if (!workActive || busyNow.current || loading || !name.trim() || !filesNow.current.length) return;
    const generation = epoch.current, key = scope, abort = controller();
    busyNow.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const images: string[] = [], selectedFiles = [...filesNow.current];
      for (let index = 0; index < selectedFiles.length; index++) {
        if (!alive(generation, key) || abort.signal.aborted) return;
        setProgress('Uploading photo ' + (index + 1) + ' of ' + selectedFiles.length + ' to this workspace…');
        const uploaded = await uploadStudioImage(selectedFiles[index].file, projectId, abort.signal);
        if (!alive(generation, key) || abort.signal.aborted) return;
        images.push(uploaded.id);
      }
      setProgress('Saving reference set…');
      const item = await createStudioSubject(projectId, name.trim(), description.trim(), images, abort.signal);
      if (!alive(generation, key) || abort.signal.aborted) return;
      if (item.project_id !== projectId) throw Error('The saved reference set did not match this project. Reload the panel.');
      setData(previous => ({scope: key, rows: [item, ...(previous.scope === key ? previous.rows : []).filter(row => row.id !== item.id)]}));
      setName(''); setDescription(''); clearFiles(); setNotice('Reference set saved to this workspace.');
    } catch (reason) {
      if (alive(generation, key) && !abort.signal.aborted) setError(errorMessage(reason));
    } finally {
      controllers.current.delete(abort);
      if (alive(generation, key)) { busyNow.current = false; setBusy(false); setProgress(''); }
    }
  };
  const remove = async (item: StudioSubject) => {
    if (!workActive || busyNow.current) return;
    const generation = epoch.current, key = scope, abort = controller();
    busyNow.current = true; setBusy(true); setError(''); setNotice('');
    try {
      await deleteStudioSubject(item.id, abort.signal);
      if (!alive(generation, key) || abort.signal.aborted) return;
      setData(previous => ({scope: key, rows: previous.rows.filter(row => row.id !== item.id)}));
      setPendingRemove(''); setNotice('Reference set removed. Uploaded image files are kept.');
    } catch (reason) {
      if (alive(generation, key) && !abort.signal.aborted) setError(errorMessage(reason));
    } finally {
      controllers.current.delete(abort);
      if (alive(generation, key)) { busyNow.current = false; setBusy(false); }
    }
  };
  const selectPhoto = (item: StudioSubject, imageId: string) => {
    if (busyNow.current || !effectiveOpen || !alive(selectionGeneration, scope) || !item.image_ids.includes(imageId)) return;
    onSelect(imageId, item.name, item.description);
    setNotice('Selected one reference photo from ' + item.name + '.');
  };
  const selectSet = (item: StudioSubject) => {
    if (busyNow.current || !effectiveOpen || !alive(selectionGeneration, scope) || !onSelectMany || !item.image_ids.length || item.image_ids.length > referenceLimit) return;
    // Preserve saved order and keep the parent's composer separate from cached metadata.
    onSelectMany([...item.image_ids], item.name, item.description);
    setNotice('Selected all ' + item.image_ids.length + ' reference photos from ' + item.name + ' in saved order.');
  };
  if (!allowed) return null;
  const matches = rows.filter(item => (item.name + ' ' + item.description).toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  let fallbackPhotoIndex = 0;
  return <Collapsible className="studio-subjects" open={effectiveOpen} onOpenChange={next => {if (embedded) return; if (!next) {cancel(); setBusy(false); setLoading(false); setProgress('');} setOpen(next);}}>
    {!embedded && <CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="subjects-trigger"><UserRound size={14}/><span>Saved subjects & products</span><ChevronDown size={14} className={open ? 'subjects-chevron is-open' : 'subjects-chevron'}/></Button></CollapsibleTrigger>}
    <CollapsibleContent className="subjects-panel">
      <p className="subjects-explainer">Save a person, mascot or product with 1–5 photos. {onSelectMany
        ? 'Choose one photo or use a complete set within the current model’s reference limit; consistency is not guaranteed.'
        : 'Choose one photo for the current model; consistency is not guaranteed.'}</p>
      <label className="subjects-search" htmlFor={id + '-search'}>Find a saved set<Input id={id + '-search'} type="search" placeholder="Search names and descriptions" value={query} onChange={event => setQuery(event.target.value)}/></label>
      {loading && <p role="status" className="subjects-status"><LoaderCircle size={14} className="subjects-spinner"/>Loading saved reference sets…</p>}
      {!loading && !rows.length && <p className="subjects-empty">No saved reference sets in this project yet.</p>}
      {!loading && !!rows.length && !matches.length && <p className="subjects-empty">No reference sets match your search.</p>}
      <div className="subjects-list">
        {matches.map(item => {
          const selected = item.image_ids.includes(portraits[item.id]) ? portraits[item.id] : item.image_ids[0];
          const overLimit = item.image_ids.length > referenceLimit;
          const limitId = id + '-set-limit-' + item.id;
          return <article key={item.id} className="subject-row" aria-label={item.name}>
            <div className="subject-row-heading"><div><strong>{item.name}</strong>{item.description && <p>{item.description}</p>}</div>
              <Button variant="ghost" size="icon" aria-label={'Remove ' + item.name} disabled={busy || !workActive} onClick={() => {if (workActive) setPendingRemove(item.id);}}><Trash2 size={14}/></Button></div>
            <div className="subject-photos" role="group" aria-label={'Reference photos for ' + item.name}>
              {item.image_ids.map((imageId, index) => <button type="button" key={imageId} className="subject-photo" aria-label={'Photo ' + (index + 1) + ' for ' + item.name} aria-pressed={selected === imageId} disabled={busy} onClick={() => {if (allowed) setPortraits(previous => ({...previous, [item.id]: imageId}));}}>
                <SubjectPhoto imageId={imageId} name={item.name + ', photo ' + (index + 1)} scope={scope} active={readActive && effectiveOpen} fallbackPreview={fallbackPhotoIndex++ < 3} sessionSignal={previewSession.current.signal}/><span>{index + 1}</span>
              </button>)}
              <Button variant="secondary" size="sm" disabled={busy || !workActive || !selected} onClick={() => selectPhoto(item, selected)}>Use selected photo<span className="sr-only"> from {item.name}</span></Button>
              {onSelectMany && <Button variant="secondary" size="sm" disabled={busy || !workActive || !item.image_ids.length || overLimit} aria-describedby={overLimit ? limitId : undefined} onClick={() => selectSet(item)}>Use reference set<span className="sr-only"> from {item.name}</span></Button>}
            </div>
            {onSelectMany && overLimit && <p id={limitId} className="subject-set-limit">This set has {item.image_ids.length} photos. This model supports up to {referenceLimit} reference {referenceLimit === 1 ? 'photo' : 'photos'} in this workspace. Choose one photo instead.</p>}
            {pendingRemove === item.id && <div className="subject-remove-confirm" role="group" aria-label={'Confirm removal of ' + item.name}><p>Remove this saved set? Uploaded image files will be kept.</p><Button variant="destructive" size="sm" disabled={busy || !workActive} onClick={() => void remove(item)}>Remove set</Button><Button variant="ghost" size="sm" disabled={busy} onClick={() => setPendingRemove('')}>Cancel</Button></div>}
          </article>;
        })}
      </div>
      <div className="subjects-create" role="group" aria-label="New reference set">
        <h3>New reference set</h3>
        <label htmlFor={id + '-name'}>Name<Input id={id + '-name'} maxLength={80} placeholder="e.g. Remy or the travel mug" value={name} disabled={busy} onChange={event => setName(event.target.value)}/></label>
        <label htmlFor={id + '-description'}>Description <span>(optional)</span><textarea id={id + '-description'} rows={2} maxLength={1000} placeholder="Details to remember when choosing a reference" value={description} disabled={busy} onChange={event => setDescription(event.target.value)}/></label>
        <label className="subjects-upload" htmlFor={id + '-files'}><ImagePlus size={16}/><span>Add photos ({files.length}/5)</span><input id={id + '-files'} type="file" aria-label="Reference set photos" accept="image/png,image/jpeg,image/webp" multiple disabled={projectReadOnly || busy || files.length >= 5} onChange={event => {chooseFiles(Array.from(event.target.files || [])); event.target.value = '';}}/></label>
        {!!files.length && <div className="subjects-local-photos">{files.map((image, index) => <div key={image.key}>{image.url ? <img src={image.url} alt={image.file.name}/> : <span>{image.file.name}</span>}<Button variant="secondary" size="icon" aria-label={'Remove selected photo ' + (index + 1)} disabled={busy} onClick={() => removeFile(index)}><X size={12}/></Button></div>)}</div>}
        <p className="subjects-disclosure">PNG, JPEG or WebP · up to 8 MiB and 32 megapixels each. Saving uploads photos only to this workspace. {onSelectMany ? 'References guide the output. Photos are sent to your chosen model only when you submit a generation.' : 'Generation uses only the selected photo when you submit it to your chosen model.'}</p>
        <Button variant="secondary" size="sm" disabled={busy || !workActive || loading || !name.trim() || !files.length || rows.length >= 100} onClick={() => void save()}>{busy && progress ? <LoaderCircle size={14} className="subjects-spinner"/> : <ImagePlus size={14}/>}Save reference set</Button>
      </div>
      {progress && <p role="status" className="subjects-status">{progress}</p>}
      {notice && <p role="status" className="subjects-status">{notice}</p>}
      {error && <p role="alert" className="subjects-error">{error}</p>}
    </CollapsibleContent>
  </Collapsible>;
}
