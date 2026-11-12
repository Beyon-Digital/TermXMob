import {useEffect, useId, useRef, useState} from 'react';
import {Clapperboard, CircleAlert, LoaderCircle, Plus, Sparkles} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {createStudioClient, type StudioCatalog, type StudioModel, type StudioJob} from '../lib/media-studio';
import {buildFactoryScenes, factoryPlatforms, factoryRatio, factoryStyles, type FactoryPlatform, type FactoryStyle} from '../lib/studio-factory';
import StudioPanel from './StudioPanel';
import {useStudioDraftState,useStudioProjectReadOnly} from '../lib/studio-draft-state';
import './media-factory.css';

const documentIsVisible = () => document.visibilityState !== 'hidden';

type Scene = {id: string; title: string; prompt: string; status: 'draft' | 'submitting' | 'queued' | 'unknown'; job?: StudioJob; error?: string};
type BatchReview = {key: string; providerId: string; providerName: string; modelId: string; modelName: string; adapter: 'openrouter' | 'kie'; scenes: {id: string; title: string; prompt: string}[]; aspect: string; resolution: string; duration: string};
type Props = {catalog: StudioCatalog; ownerId?: string; projectId: string | null; visible: boolean; observing?: boolean; onJob: (job: StudioJob) => void};

export default function MediaFactory(props: Props) {
  return <FactorySession key={JSON.stringify([props.ownerId, props.projectId])} {...props}/>;
}
function FactorySession({catalog, ownerId, projectId, visible, observing = true, onJob}: Props) {
  const uid = useId();
  const projectReadOnly=useStudioProjectReadOnly();
  const [brief, setBrief] = useState(''), [style, setStyle] = useState<FactoryStyle>('cinematic');
  const [platform, setPlatform] = useState<FactoryPlatform>('vertical'), [count, setCount] = useState(3);
  const [scenes, setScenes] = useState<Scene[]>([]), [accountId, setAccountId] = useState(''), [modelId, setModelId] = useState('');
  const [resolution, setResolution] = useState(''), [duration, setDuration] = useState('');
  const [review, setReview] = useState<BatchReview | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [locked, setLocked] = useState(false), [foreground, setForeground] = useState(documentIsVisible);
  const foregroundNow = useRef(foreground); foregroundNow.current = foreground;
  const currentReview = useRef<BatchReview | null>(review); currentReview.current = review;
  const alive = useRef(true), active = useRef(visible && observing), lockedNow = useRef(false), flight = useRef<AbortController | null>(null);
  active.current = visible && observing && foreground;
  const videoModels = catalog.models.filter((row): row is StudioModel & {kind: 'video'; adapter: 'openrouter' | 'kie'} => row.kind === 'video' && (row.adapter === 'openrouter' || row.adapter === 'kie') && row.modes.includes('video') && !row.requires_image && !row.requires_audio && !row.requires_video);
  const accounts = catalog.accounts.filter(row => row.capabilities.includes('video') && videoModels.some(model => model.adapter === row.adapter));
  const account = accounts.find(row => row.id === accountId) || accounts.find(row => row.configured && row.adapter === 'openrouter') || accounts.find(row => row.configured) || accounts[0];
  const models = videoModels.filter(row => row.adapter === account?.adapter);
  const model = models.find(row => row.id === modelId) || models[0];
  const chosenResolution = model?.resolutions.includes(resolution) ? resolution : model?.resolutions[0] || '';
  const chosenDuration = model?.durations.includes(Number(duration)) ? duration : String(model?.durations[0] || '');
  const aspect = factoryRatio(platform, model?.aspect_ratios || []);
  const remaining = scenes.filter(row => row.status === 'draft');
  useStudioDraftState('factory',!!(brief.trim()||scenes.some(scene=>scene.status==='draft'||scene.status==='submitting'||scene.status==='unknown')),busy,'Pause the current scene queue before changing projects.');
  const canWork = !projectReadOnly && visible && observing && foreground && !locked && !!account?.configured && !!model;
  const batchKey = JSON.stringify({brief, style, platform, count, account: account?.id, configured: account?.configured, model: model?.id, adapter: model?.adapter, aspect: aspect.ratio, resolution: chosenResolution, duration: chosenDuration, scenes: remaining.map(row => [row.id, row.title, row.prompt.trim()])});
  useEffect(() => {setReview(null);}, [batchKey]);
  useEffect(() => {
    alive.current = true;
    const lock = () => {lockedNow.current = true; setLocked(true); setReview(null); setBrief(''); setScenes([]); flight.current?.abort();};
    const unlock = () => {lockedNow.current = false; setLocked(false);};
    window.addEventListener('termx-locked', lock); window.addEventListener('termx-session-locked', lock); window.addEventListener('termx-signed-out', lock); window.addEventListener('termx-session-unlocked', unlock);
    return () => {alive.current = false; flight.current?.abort(); window.removeEventListener('termx-locked', lock); window.removeEventListener('termx-session-locked', lock); window.removeEventListener('termx-signed-out', lock); window.removeEventListener('termx-session-unlocked', unlock);};
  }, []);
  function pauseBatch() {
    currentReview.current = null;
    flight.current?.abort(); flight.current = null; setBusy(false); setReview(null);
    setScenes(rows => rows.map(row => row.status === 'submitting' ? {...row, status: 'unknown',
      error: 'Batch paused. Refresh the library to reconcile this scene before retrying.'} : row));
  }
  useEffect(() => {
    if (!visible || !observing) pauseBatch();
  }, [visible, observing]);
  useEffect(() => {
    const visibility = () => {
      const next = documentIsVisible();
      foregroundNow.current = next;
      if (!next) {active.current = false; pauseBatch();}
      setForeground(next);
    };
    document.addEventListener('visibilitychange', visibility);
    return () => document.removeEventListener('visibilitychange', visibility);
  }, []);
  const update = (id: string, patch: Partial<Scene>) => setScenes(rows => rows.map(row => row.id === id ? {...row, ...patch} : row));
  function drafts() {
    try {setScenes(buildFactoryScenes(brief, style, count).map(row => ({...row, status: 'draft'}))); setReview(null); setError('');}
    catch (cause) {setError(cause instanceof Error ? cause.message : String(cause));}
  }
  function reviewBatch() {
    if (!documentIsVisible() || !foregroundNow.current || flight.current || !active.current || !canWork || !account || !model || !remaining.length || remaining.some(row => !row.prompt.trim())) return;
    setReview({key: batchKey, providerId: account.id, providerName: account.name, modelId: model.id, modelName: model.label, adapter: model.adapter,
      scenes: remaining.map(({id, title, prompt}) => ({id, title, prompt: prompt.trim()})), aspect: aspect.ratio, resolution: chosenResolution, duration: chosenDuration});
  }
  async function queue(approved: BatchReview) {
    if (!documentIsVisible() || !foregroundNow.current || flight.current || !active.current || !canWork || lockedNow.current || currentReview.current !== approved || approved.key !== batchKey || !approved.scenes.length) return;
    currentReview.current = null;
    const controller = new AbortController(); flight.current = controller; setBusy(true); setError(''); setReview(null);
    const client = createStudioClient(ownerId, projectId);
    try {
      for (const scene of approved.scenes) {
        if (!foregroundNow.current || !documentIsVisible() || controller.signal.aborted || flight.current !== controller || !alive.current || !active.current || lockedNow.current) break;
        update(scene.id, {status: 'submitting'});
        try {
          const job = await client.generateVideo({provider_id: approved.providerId, model: approved.modelId, prompt: scene.prompt, acknowledge_billing: true,
            ...(approved.aspect && {aspect_ratio: approved.aspect}), ...(approved.resolution && {resolution: approved.resolution}), ...(approved.duration && {duration: Number(approved.duration)})}, controller.signal);
          if (!foregroundNow.current || !documentIsVisible() || controller.signal.aborted || flight.current !== controller || !alive.current || lockedNow.current || !active.current) break;
          if (job.project_id !== projectId) throw new Error('The job receipt belongs to a different workspace. Refresh your library to reconcile the batch.');
          update(scene.id, {status: job.status === 'unknown' || job.status === 'failed' ? 'unknown' : 'queued', job}); onJob(job);
          if (job.status === 'unknown' || job.status === 'failed') {setError('The batch paused after a provider outcome needs review. Check your library before queuing remaining scenes.'); break;}
        } catch (cause) {
          if (!foregroundNow.current || !documentIsVisible() || controller.signal.aborted || flight.current !== controller || !alive.current || lockedNow.current) break;
          update(scene.id, {status: 'unknown', error: 'Request outcome needs review. Refresh the library before submitting this scene again.'});
          if (active.current) setError(cause instanceof Error ? cause.message : String(cause));
          break;
        }
      }
    } finally {if (flight.current === controller) {flight.current = null; if (alive.current) setBusy(false);}}
  }
  if (!visible || locked) return null;
  return <section className="studio-factory" aria-label="Content factory">
    <header><span className="studio-eyebrow">Content factory</span><h2><Clapperboard size={20}/>One brief. A series of scenes.</h2><p>Build editable scene drafts from shot templates, then choose which batch to send to your video provider.</p></header>
    <fieldset disabled={busy} className="factory-brief"><legend>1. Shape your brief</legend>
      <label htmlFor={uid + '-brief'}>What are we making?</label><textarea id={uid + '-brief'} value={brief} maxLength={10000} onChange={event => {setBrief(event.target.value); setReview(null);}} placeholder="A short campaign introducing a handmade ceramic collection…"/>
      <div className="factory-fields"><label>Platform<Select aria-label="Factory platform" value={platform} onChange={event => {setPlatform(event.target.value as FactoryPlatform); setReview(null);}}>{Object.entries(factoryPlatforms).map(([id, row]) => <option key={id} value={id}>{row.label}</option>)}</Select></label>
      <label>Look<Select aria-label="Factory look" value={style} onChange={event => {setStyle(event.target.value as FactoryStyle); setReview(null);}}>{Object.entries(factoryStyles).map(([id, row]) => <option key={id} value={id}>{row.label}</option>)}</Select></label>
      <label>Scene count<Input aria-label="Scene count" type="number" min={1} max={6} value={count} onChange={event => {setCount(Number(event.target.value)); setReview(null);}}/></label></div>
      <Button variant="outline" disabled={!brief.trim() || count < 1 || count > 6 || !Number.isInteger(count)} onClick={drafts}><Plus size={15}/>{scenes.length ? 'Replace scene drafts' : 'Build scene drafts'}</Button>
    </fieldset>
    {scenes.length > 0 && <fieldset disabled={busy} className="factory-scenes"><legend>2. Review every scene</legend>{scenes.map(scene => <article key={scene.id}><div><strong>{scene.title}</strong><span>{scene.job ? 'Job ' + scene.job.status : scene.status === 'unknown' ? 'Needs review' : scene.status}</span></div><textarea aria-label={scene.title + ' prompt'} value={scene.prompt} disabled={scene.status !== 'draft'} maxLength={20000} onChange={event => {update(scene.id, {prompt: event.target.value}); setReview(null);}}/>{scene.error && <p role="status">{scene.error}</p>}</article>)}</fieldset>}
    <fieldset disabled={busy} className="factory-delivery"><legend>3. Choose your video model</legend><div className="factory-fields"><label>Provider account<Select aria-label="Factory provider account" value={account?.id || ''} disabled={!accounts.length} onChange={event => {setAccountId(event.target.value); setModelId(''); setReview(null);}}>{!accounts.length && <option value="">No video account configured</option>}{accounts.map(row => <option key={row.id} value={row.id}>{row.name}{row.configured ? '' : ' · connect account'}</option>)}</Select></label>
    <label>Model<Select aria-label="Factory model" value={model?.id || ''} disabled={!models.length} onChange={event => {setModelId(event.target.value); setReview(null);}}>{!models.length && <option value="">No supported video models</option>}{models.map(row => <option key={row.id} value={row.id}>{row.label}</option>)}</Select></label>
    {!!model?.resolutions.length && <label>Resolution<Select aria-label="Factory resolution" value={chosenResolution} onChange={event => {setResolution(event.target.value); setReview(null);}}>{model.resolutions.map(value => <option key={value}>{value}</option>)}</Select></label>}
    {!!model?.durations.length && <label>Duration<Select aria-label="Factory duration" value={chosenDuration} onChange={event => {setDuration(event.target.value); setReview(null);}}>{model.durations.map(value => <option key={value} value={value}>{value} seconds</option>)}</Select></label>}</div>
    {model && <p className="factory-hint">Output aspect ratio: {aspect.ratio || 'provider default'}{!aspect.exact ? ' · the selected model does not offer the preferred platform ratio' : ''}.</p>}
    {!account?.configured && <p className="factory-hint">Connect a video provider in Studio Settings.</p>}
    {error && <p className="studio-error" role="alert"><CircleAlert size={16}/>{error}</p>}
    <Button disabled={!canWork || busy || !remaining.length || remaining.some(row => !row.prompt.trim())} onClick={reviewBatch}>{busy ? <LoaderCircle size={16}/> : <Sparkles size={16}/>} {busy ? 'Queuing scenes…' : 'Review ' + remaining.length + ' scene' + (remaining.length === 1 ? '' : 's')}</Button>
    </fieldset>
    <StudioPanel open={!!review && visible && observing && foreground && !locked} title="Review scene batch" onClose={() => setReview(null)} initialFocus="[data-factory-review-cancel]" className="factory-batch-review">
      {review && <><dl className="factory-review-summary"><div><dt>Provider account</dt><dd>{review.providerName}</dd></div><div><dt>Model</dt><dd>{review.modelName}</dd></div><div><dt>Scenes</dt><dd>{review.scenes.length}</dd></div><div><dt>Aspect ratio</dt><dd>{review.aspect || 'Provider default'}</dd></div><div><dt>Resolution</dt><dd>{review.resolution || 'Provider default'}</dd></div><div><dt>Duration per scene</dt><dd>{review.duration ? review.duration + ' seconds' : 'Provider default'}</dd></div></dl>
        <ol className="factory-review-scenes" aria-label="Exact scene prompts">{review.scenes.map(scene => <li key={scene.id}><h3>{scene.title}</h3><p>{scene.prompt}</p></li>)}</ol>
        <div className="factory-review-actions"><Button data-factory-review-cancel type="button" variant="secondary" onClick={() => setReview(null)}>Cancel batch</Button><Button type="button" disabled={!canWork || busy || review.key !== batchKey} onClick={() => void queue(review)}>Queue {review.scenes.length} scene{review.scenes.length === 1 ? '' : 's'} with {review.modelName}</Button></div>
      </>}
    </StudioPanel>
  </section>;
}
