import {lazy, Suspense, useEffect, useId, useRef, useState, type KeyboardEvent} from 'react';
import {ArrowDown, ArrowDownToLine, ArrowUp, ArrowUpRight, Camera, Check, ChevronRight, CircleAlert, Clapperboard,
  ChevronDown, Film, Image as ImageIcon, Layers3, LoaderCircle, Mic2, Palette, RefreshCw, Search, Settings2, Sparkles, Sun, Upload, X} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {Select} from '../components/ui/select';
import {StudioMark} from '../components/icons/WorkspaceMarks';
import {transfer} from '../lib/transfers';
import {activeStudioJob, buildCinemaPrompt, cinemaApertures, cinemaCameras, cinemaFocals,
  cinemaLenses, createStudioClient, defaultCinema, isPublicMediaUrl, loadStudioCatalog, loadStudioHistory, loadStudioJobs, mergeStudioJobs,
  refreshStudioJob, STUDIO_IMAGES_ENCODED_BYTES, studioArtifactId, studioDefaultSelection, studioImageLimit, studioMotionPresets, studioOutputUrl, studioPresets, studioRestylePresets, uploadStudioImage,
  type CinemaSettings, type StudioModel, type StudioInput, type StudioDefault, type StudioOperation, type StudioCatalog, type StudioJob, type StudioMode, type StudioImageRole, type StudioImageReference} from '../lib/media-studio';
import type {StudioSection} from '../lib/workspace-mode';
import MediaFactory from './MediaFactory';
import StudioPanel from './StudioPanel';
import {useStudioDraftState,useStudioProjectReadOnly} from '../lib/studio-draft-state';
import './media-studio.css';

const StudioSubjects = lazy(() => import('./StudioSubjects'));
const StudioPricing = lazy(() => import('./StudioPricing'));

export type {StudioSection} from '../lib/workspace-mode';
type Tab = StudioMode | 'library';
type FocusPanel = 'preview' | 'references' | 'camera' | 'style' | 'motion' | 'color' | 'lighting' | 'settings' | 'pricing' | 'tools' | null;
type DraftImage = {key: string; reference: StudioImageReference | null; name: string; encodedBytes: number; needsEntry?: boolean};
type Props = {
  projectId: string | null;
  ownerId?: string;
  visible?: boolean;
  observing?: boolean;
  catalogRevision?: number;
  section?: StudioSection;
  onSectionChange?: (section: StudioSection) => void;
  onOpenArtifacts?: () => void;
  onConfigureProvider?: () => void;
};
const tabs = [
  {id: 'image', label: 'Image', icon: ImageIcon},
  {id: 'video', label: 'Video', icon: Film},
] as const;
const copy: Record<StudioMode, {title: string; description: string; placeholder: string}> = {
  image: {title: 'Image studio', description: 'Turn an idea into a finished image.', placeholder: 'Describe your subject, setting, light and style…'},
  video: {title: 'Video studio', description: 'Give your next idea a little motion.', placeholder: 'Describe the scene, action and camera movement…'},
  cinema: {title: 'Cinema studio', description: 'Compose a cinematic still with camera and lens controls.', placeholder: 'Describe the scene you want to photograph…'},
  remix: {title: 'Remix studio', description: 'Edit a source video with a verified video-edit model.', placeholder: 'Describe the change while preserving the original shot…'},
  lipsync: {title: 'Lip sync studio', description: 'Bring a portrait to life with your audio.', placeholder: 'Optional performance direction…'},
};
const POLL_INTERVAL = 8000;
const POLL_WINDOW = 5 * 60 * 1000;

/** A keyed session keeps another owner's or project's media out of the next render. */
export default function MediaStudio(props: Props) {
  return <StudioSession key={JSON.stringify([props.ownerId, props.projectId])} {...props}/>;
}

function StudioSession({projectId, ownerId, visible = true, observing = true, catalogRevision = 0, section, onSectionChange, onOpenArtifacts, onConfigureProvider}: Props) {
  const uid = useId();
  const projectReadOnly = useStudioProjectReadOnly();
  const [tab, setTab] = useState<Tab>('image');
  const [videoTool, setVideoTool] = useState<'generate' | 'remix' | 'factory'>('generate');
  const mode: StudioMode = tab === 'library' ? 'image' : tab === 'video' && videoTool === 'remix' ? 'remix' : tab;
  const operation: StudioOperation = mode === 'cinema' ? 'image' : mode;
  const [localSection, setLocalSection] = useState<StudioSection>('create');
  const activeSection = section ?? localSection;
  const isLibrary = activeSection === 'library', isAssets = activeSection === 'assets';
  const sectionNow = useRef(activeSection); sectionNow.current = activeSection;
  const [panel, setPanel] = useState<FocusPanel>(null);
  const [referenceFocus, setReferenceFocus] = useState('');
  const [referenceSource, setReferenceSource] = useState<'upload' | 'saved' | 'recent'>('upload');
  const [selectedJobId, setSelectedJobId] = useState('');
  const [subjectsVisited, setSubjectsVisited] = useState(section === 'assets');
  useEffect(() => {if (activeSection === 'assets' || panel === 'references' && referenceSource === 'saved') setSubjectsVisited(true);}, [activeSection, panel, referenceSource]);
  const [catalog, setCatalog] = useState<StudioCatalog | null>(null);
  const [jobs, setJobs] = useState<StudioJob[]>([]);
  const jobLedger = useRef<StudioJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [locked, setLocked] = useState(false);
  const selectionTouched=useRef(false);
  const [selections, setSelections] = useState<Partial<Record<StudioOperation, StudioDefault>>>({});
  const selection = selections[operation] ?? studioDefaultSelection(catalog, mode);
  const accountId = selection?.provider_id || '', modelId = selection?.model || '';
  const ratio = selection?.aspect_ratio || '', resolution = selection?.resolution || '', duration = selection?.duration ? String(selection.duration) : '';
  function updateSelection(patch: Partial<StudioDefault>, target = operation) {
    selectionTouched.current=true;
    setSelections(previous => ({...previous, [target]: {...(previous[target] ?? studioDefaultSelection(catalog, target) ?? {provider_id: '', model: ''}), ...patch}}));
  }
  useEffect(() => {
    const initial = studioDefaultSelection(catalog, mode);
    if (initial) setSelections(previous => previous[operation] ? previous : {...previous, [operation]: initial});
  }, [catalog, operation]);
  const [prompt, setPrompt] = useState('');
  const [imageUrl, setImageUrl] = useState('');
  const [audioUrl, setAudioUrl] = useState('');
  const [videoUrl, setVideoUrl] = useState('');
  const [cinema, setCinema] = useState<CinemaSettings>(defaultCinema);
  useEffect(() => {if (activeSection !== 'create') setPanel(null);}, [activeSection]);
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [pollUntil, setPollUntil] = useState(0);
  const [autoPaused, setAutoPaused] = useState(false);
  const [foreground, setForeground] = useState(document.visibilityState !== 'hidden');
  const [filter, setFilter] = useState('all');
  const [history, setHistory] = useState<StudioJob[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [historyStatus, setHistoryStatus] = useState('');
  const [historySearch, setHistorySearch] = useState('');
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState('');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [imageRole, setImageRole] = useState<StudioImageRole>('first_frame');
  const [imageId, setImageId] = useState('');
  const [imageName, setImageName] = useState('');
  const [imageEncodedBytes, setImageEncodedBytes] = useState(0);
  const [primaryKey, setPrimaryKey] = useState<string>(() => crypto.randomUUID());
  const [primaryMissing, setPrimaryMissing] = useState(false);
  const [extraImages, setExtraImages] = useState<DraftImage[]>([]);
  const [extraImageUrl, setExtraImageUrl] = useState('');
  const [imagePreview, setImagePreview] = useState('');
  const [uploading, setUploading] = useState(false);
  const previewObject = useRef('');
  const uploadEpoch = useRef(0);
  const uploadIsPrimary = useRef(false);
  const historyEpoch = useRef(0);
  const historyBusy = useRef(false);
  const historyController = useRef<AbortController | null>(null);
  const uploadController = useRef<AbortController | null>(null);
  const tabNow = useRef(tab); tabNow.current = tab;
  const historyKey = JSON.stringify([filter, historyStatus, historySearch.trim()]);
  const historyKeyNow = useRef(historyKey); historyKeyNow.current = historyKey;
  const epoch = useRef(0);
  const loadSequence = useRef(0);
  const visibleNow = useRef(visible);
  visibleNow.current = visible;
  const observingNow = useRef(observing); observingNow.current = observing;
  const lockedNow = useRef(locked);
  lockedNow.current = locked;
  const controllers = useRef(new Set<AbortController>());
  const submitBusy = useRef(false);
  const refreshBusy = useRef(false);
  const root = useRef<HTMLElement>(null);
  const current = (generation: number) => generation === epoch.current && visibleNow.current && observingNow.current && !lockedNow.current;
  const makeController = () => {
    const controller = new AbortController();
    controllers.current.add(controller);
    return controller;
  };
  const clearLocalImage = () => {
    uploadEpoch.current++; uploadController.current?.abort(); uploadController.current = null;
    if (previewObject.current) URL.revokeObjectURL(previewObject.current);
    previewObject.current = ''; setImagePreview(''); setImageId(''); setImageName(''); setImageEncodedBytes(0); setPrimaryKey(crypto.randomUUID()); setPrimaryMissing(false); setUploading(false);
  };
  const suspendImageWork = () => {
    const incompletePrimary = !!uploadController.current && uploadIsPrimary.current;
    uploadEpoch.current++; uploadController.current?.abort(); uploadController.current = null;
    if (previewObject.current) URL.revokeObjectURL(previewObject.current);
    previewObject.current = ''; setImagePreview(''); setUploading(false);
    if (incompletePrimary) setImageName('');
  };
  const pauseMedia = () => root.current?.querySelectorAll('audio,video')
    .forEach(media => (media as HTMLMediaElement).pause());
  const cancelRequests = () => {
    epoch.current++;
    historyEpoch.current++; historyBusy.current = false;
    uploadEpoch.current++;
    controllers.current.forEach(controller => controller.abort());
    controllers.current.clear();
    refreshBusy.current = false;
    submitBusy.current = false;
  };

  useEffect(() => {
    const lock = () => {
      lockedNow.current = true;
      cancelRequests();
      pauseMedia();
      setLocked(true);
      jobLedger.current = []; setJobs([]); setHistory([]); setHistoryCursor(null); setHistorySearch(''); setHistoryError(''); setHistoryLoading(false);
      clearLocalImage(); setExtraImages([]); setExtraImageUrl(''); setNegativePrompt('');
      setCatalog(null); setSelections({});
      setPrompt('');
      setImageUrl(''); setAudioUrl(''); setVideoUrl('');
      setError(''); setNotice('');
      setSubmitting(false); setRefreshing(false);
    };
    const unlock = () => { lockedNow.current = false; setLocked(false); };
    const visibility = () => {
      const active = document.visibilityState !== 'hidden';
      setForeground(active);
      if (!active) {pauseMedia(); suspendImageWork();}
    };
    window.addEventListener('termx-locked', lock);
    window.addEventListener('termx-session-locked', lock);
    window.addEventListener('termx-signed-out', lock);
    window.addEventListener('termx-session-unlocked', unlock);
    document.addEventListener('visibilitychange', visibility);
    return () => {
      cancelRequests();
      if (previewObject.current) URL.revokeObjectURL(previewObject.current);
      pauseMedia();
      window.removeEventListener('termx-locked', lock);
      window.removeEventListener('termx-session-locked', lock);
      window.removeEventListener('termx-signed-out', lock);
      window.removeEventListener('termx-session-unlocked', unlock);
      document.removeEventListener('visibilitychange', visibility);
    };
  }, []);

  async function load() {
    if (!visibleNow.current || !observingNow.current || lockedNow.current) return;
    const generation = epoch.current, sequence = ++loadSequence.current;
    const controller = makeController();
    setLoading(true); setLoadError('');
    try {
      const [nextCatalog, nextJobs] = await Promise.all([
        loadStudioCatalog(projectId, controller.signal), loadStudioJobs(projectId, controller.signal),
      ]);
      if (!current(generation) || sequence !== loadSequence.current) return;
      setCatalog(nextCatalog);
      const scoped = rememberJobs(nextJobs.filter(job => job.project_id === projectId));
      setJobs(previous => mergeStudioJobs(previous, scoped));
      setPollUntil(Date.now() + POLL_WINDOW); setAutoPaused(false);
    } catch (cause) {
      if (current(generation) && sequence === loadSequence.current) setLoadError(message(cause));
    } finally {
      controllers.current.delete(controller);
      if (current(generation) && sequence === loadSequence.current) setLoading(false);
    }
  }
  useEffect(() => {
    if (visible && observing && !locked) void load();
    if (!visible || !observing) {
      cancelRequests(); pauseMedia(); suspendImageWork(); setHistoryLoading(false);
      setSubmitting(false); setRefreshing(false);
    }
  }, [visible, observing, locked, catalogRevision]);

  const factoryActive = !isLibrary && !isAssets && tab === 'video' && videoTool === 'factory';

  function resetHistory() {
    historyEpoch.current++; historyBusy.current = false; historyController.current?.abort();
    setHistory([]); setHistoryCursor(null); setHistoryError(''); setHistoryLoading(true);
  }
  async function readHistory(append = false) {
    if (!visibleNow.current || !observingNow.current || lockedNow.current || sectionNow.current !== 'library' || historyBusy.current) return;
    const generation = epoch.current, sequence = ++historyEpoch.current, key = historyKey;
    const controller = makeController(); historyController.current = controller;
    historyBusy.current = true; setHistoryLoading(true); setHistoryError('');
    try {
      const page = await loadStudioHistory(projectId, {
        ...(append && historyCursor && {cursor: historyCursor}),
        ...(['image', 'video'].includes(filter) && {kind: filter as 'image' | 'video'}),
        ...(historyStatus && {status: historyStatus as StudioJob['status'] | 'active' | 'attention'}),
        ...(historySearch.trim() && {q: historySearch.trim()}), limit: 40,
      }, controller.signal);
      if (!current(generation) || sequence !== historyEpoch.current || key !== historyKeyNow.current || sectionNow.current !== 'library') return;
      const rows = rememberJobs(page.items.filter(job => job.project_id === projectId)).filter(matchesCurrentHistory);
      setHistory(previous => append ? mergeStudioJobs(previous, rows) : rows);
      setHistoryCursor(page.next_cursor || null);
      setJobs(previous => mergeStudioJobs(previous, rows.filter(activeStudioJob)));
    } catch (cause) {
      if (current(generation) && sequence === historyEpoch.current && key === historyKeyNow.current) setHistoryError(message(cause));
    } finally {
      controllers.current.delete(controller);
      if (sequence === historyEpoch.current) {historyBusy.current = false; if (current(generation)) setHistoryLoading(false);}
    }
  }
  useEffect(() => {
    if (!visible || !observing || locked || !isLibrary) return;
    resetHistory();
    const timer = window.setTimeout(() => void readHistory(), historySearch.trim() ? 300 : 0);
    return () => {window.clearTimeout(timer); historyEpoch.current++; historyBusy.current = false; historyController.current?.abort();};
  }, [visible, observing, locked, activeSection, filter, historyStatus, historySearch]);
  const modeModels = (catalog?.models || []).filter((model): model is StudioModel & {kind: 'image' | 'video'; adapter: 'openrouter' | 'kie'} => (model.kind === 'image' || model.kind === 'video') && (model.adapter === 'openrouter' || model.adapter === 'kie') && model.modes.includes(operation) &&
    (mode !== 'cinema' || model.kind === 'image'));
  const accounts = (catalog?.accounts || []).filter(account => modeModels.some(model =>
    model.adapter === account.adapter && account.capabilities.includes(model.kind)));
  const account = accounts.find(item => item.id === accountId);
  const models = modeModels.filter(model => model.adapter === account?.adapter &&
    account?.capabilities.includes(model.kind));
  const model = models.find(item => item.id === modelId);
  const unavailableSelection = !!selection && (!account || !model);
  const selectedRatio = model?.aspect_ratios.includes(ratio) ? ratio : model?.aspect_ratios[0] || '';
  const selectedResolution = model?.resolutions.includes(resolution) ? resolution : model?.resolutions[0] || '';
  const selectedDuration = model?.durations.includes(Number(duration)) ? duration : String(model?.durations[0] || '');
  useStudioDraftState('visual',!!(selectionTouched.current||prompt.trim()||imageUrl.trim()||audioUrl.trim()||videoUrl.trim()||negativePrompt.trim()||imageId||extraImages.length||extraImageUrl.trim()||JSON.stringify(cinema)!==JSON.stringify(defaultCinema)),submitting||uploading,uploading?'Finish the reference upload before changing projects.':'Wait for the generation receipt before changing projects.');
  const activeJobs = jobs.filter(activeStudioJob);
  const needsVideo = !!model?.requires_video || (mode === 'remix' && !!model?.requires_video_for_remix);
  const showsVideo = needsVideo || !!model?.supports_video;
  const showsAudio = !!model?.requires_audio || !!model?.supports_audio;
  const imageComposition = (mode === 'image' || mode === 'cinema') && model?.kind === 'image' && !!(model.supports_image || model.requires_image);
  const imageLimit = imageComposition ? studioImageLimit(model) : 1;
  const primaryImage: DraftImage | null = imageId || imageUrl.trim() || primaryMissing ? {
    key: primaryKey, reference: imageId ? {image_id: imageId} : imageUrl.trim() ? {image_url: imageUrl.trim()} : null,
    needsEntry: primaryMissing, name: imageName || (primaryMissing && !imageUrl.trim() ? 'Temporary reference needs a new URL' : 'Image reference'), encodedBytes: imageEncodedBytes,
  } : null;
  const compositionImages = [...(primaryImage ? [primaryImage] : []), ...extraImages];
  const showsImage = !!(model?.requires_image || model?.supports_image) && (!imageComposition || imageLimit > 0 || compositionImages.length > 0);
  const compositionPanel = imageComposition && (imageLimit > 1 || extraImages.length > 0 || primaryMissing);
  function setComposition(rows: DraftImage[]) {
    clearLocalImage();
    const [first, ...rest] = rows;
    setImageId(first?.reference?.image_id || ''); setImageUrl(first?.reference?.image_url || '');
    setPrimaryKey(first?.key || crypto.randomUUID());
    setImageName(first?.name || ''); setImageEncodedBytes(first?.encodedBytes || 0); setPrimaryMissing(!!first && (!first.reference || !!first.needsEntry));
    setExtraImages(rest);
  }
  function moveImage(index: number, step: number) {
    const next = [...compositionImages], target = index + step;
    if (target < 0 || target >= next.length) return;
    [next[index], next[target]] = [next[target], next[index]]; setComposition(next);
  }
  function addImageUrl() {
    if (!imageComposition || compositionImages.length >= imageLimit) {setError('The selected model allows at most ' + imageLimit + ' image references.'); return;}
    if (!isPublicMediaUrl(extraImageUrl.trim())) {setError('Enter a public HTTPS URL for the additional image.'); return;}
    setComposition([...compositionImages, {key: crypto.randomUUID(), reference: {image_url: extraImageUrl.trim()}, name: 'URL reference', encodedBytes: 0}]); setExtraImageUrl(''); setError('');
  }

  function rememberJobs(incoming: StudioJob[]) {
    jobLedger.current = mergeStudioJobs(jobLedger.current, incoming);
    const latest = new Map(jobLedger.current.map(job => [job.id, job]));
    return incoming.map(job => latest.get(job.id)!);
  }
  function updateLoadedHistory(previous: StudioJob[], incoming: StudioJob[]) {
    const latest = new Map(mergeStudioJobs(previous, incoming).map(job => [job.id, job]));
    return previous.map(job => latest.get(job.id)!).filter(matchesCurrentHistory);
  }
  function matchesCurrentHistory(job: StudioJob) {
    const [kind, status, search] = JSON.parse(historyKeyNow.current) as [string, string, string];
    if (job.project_id !== projectId || (kind !== 'all' && job.kind !== kind)) return false;
    if (status === 'active' && !activeStudioJob(job)) return false;
    if (status === 'attention' && !['failed', 'unknown'].includes(job.status)) return false;
    if (status && !['active', 'attention'].includes(status) && job.status !== status) return false;
    const term = search.toLocaleLowerCase();
    return !term || job.prompt.toLocaleLowerCase().includes(term) || job.model.toLocaleLowerCase().includes(term);
  }
  async function refresh(ids?: string[], automatic = false) {
    if (refreshBusy.current || !visibleNow.current || !observingNow.current || lockedNow.current) return;
    refreshBusy.current = true;
    const generation = epoch.current;
    const controller = makeController();
    if (!automatic) { setRefreshing(true); setError(''); }
    try {
      let rows = jobs;
      if (!ids) {
        rows = await loadStudioJobs(projectId, controller.signal);
        if (!current(generation)) return;
        rows = rememberJobs(rows.filter(job => job.project_id === projectId));
        setJobs(previous => mergeStudioJobs(previous, rows));
        setHistory(previous => updateLoadedHistory(previous, rows));
      }
      const targets = ids || rows.filter(job => activeStudioJob(job) || job.status === 'unknown').map(job => job.id);
      // Bound background work; manual refresh also reloads the complete history above.
      for (const id of targets.slice(0, automatic ? 4 : 12)) {
        if (!current(generation) || (automatic && document.visibilityState === 'hidden')) return;
        const next = await refreshStudioJob(id, controller.signal);
        if (!current(generation) || next.project_id !== projectId) return;
        const latest = rememberJobs([next]);
        setJobs(previous => mergeStudioJobs(previous, latest));
        setHistory(previous => updateLoadedHistory(previous, latest));
      }
      if (!automatic && current(generation)) {
        setPollUntil(Date.now() + POLL_WINDOW); setAutoPaused(false);
      }
    } catch (cause) {
      if (current(generation)) {
        setError(message(cause));
        if (automatic) { setPollUntil(0); setAutoPaused(true); }
      }
    } finally {
      controllers.current.delete(controller);
      if (generation === epoch.current) {
        refreshBusy.current = false;
        if (current(generation)) setRefreshing(false);
      }
    }
  }

  useEffect(() => {
    if (!visible || !observing || locked || !foreground || !activeJobs.length || !pollUntil) return;
    const timer = window.setTimeout(() => {
      if (Date.now() >= pollUntil) { setAutoPaused(true); return; }
      // Least-recently updated first prevents a long queue from starving later jobs.
      void refresh([...activeJobs].sort((a, b) => a.updated - b.updated).map(job => job.id), true);
    }, POLL_INTERVAL);
    return () => window.clearTimeout(timer);
  }, [visible, observing, locked, foreground, jobs, pollUntil]);

  function referenceError(text: string, focus = 'input:not(:disabled),button:not(:disabled)') {
    setReferenceFocus(focus); setReferenceSource('upload'); setPanel('references');
    setError(text);
  }

  async function generate() {
    if (document.visibilityState === 'hidden' || projectReadOnly || !observingNow.current || sectionNow.current !== 'create' || factoryActive || submitBusy.current || uploading || !model || !account?.configured || !visibleNow.current || lockedNow.current) return;
    if (mode !== 'lipsync' && !prompt.trim()) { setError('Describe what you want to create.'); return; }
    if (imageId && (model.requires_image || model.supports_image) && !model.inline_inputs?.includes('image')) {referenceError('This model does not support saved image references. Remove the reference and enter a public HTTPS URL.'); return;}
    if (imageComposition) {
      const minimum = model.image_reference_limits?.min ?? (model.requires_image ? 1 : 0);
      if (compositionImages.length < minimum || compositionImages.length > imageLimit) {referenceError('This model requires ' + minimum + '–' + imageLimit + ' image references. Remove extras or choose a compatible model.'); return;}
      if (compositionImages.some(row => !row.reference)) {referenceError('Re-enter every missing temporary image reference before generating.', '[aria-label^="Missing image reference"]'); return;}
      if (compositionImages.some(row => row.reference?.image_url && !isPublicMediaUrl(row.reference.image_url))) {referenceError('Every image reference URL must use public HTTPS.'); return;}
      if (compositionImages.some(row => row.reference?.image_id) && !model.inline_inputs?.includes('image')) {referenceError('This model does not support saved image references. Use public HTTPS image URLs.'); return;}
      if (compositionImages.reduce((total, row) => total + row.encodedBytes, 0) > STUDIO_IMAGES_ENCODED_BYTES) {referenceError('The combined image request exceeds 20 MiB. Remove an image or choose smaller files.'); return;}
    }
    const references = [
      {required: model.requires_image && !imageId, value: imageId ? '' : imageUrl, label: 'image'},
      {required: model.requires_audio, value: showsAudio ? audioUrl : '', label: 'audio'},
      {required: needsVideo, value: showsVideo ? videoUrl : '', label: 'video'},
    ];
    const invalid = references.find(item => (item.required || (item.value.trim() && (item.label !== 'image' || (model.supports_image && !imageId)))) && !isPublicMediaUrl(item.value.trim()));
    if (invalid) { referenceError('Enter a public HTTPS URL for your ' + invalid.label + ' reference.', '[aria-label="' + invalid.label[0].toUpperCase() + invalid.label.slice(1) + ' URL"]'); return; }
    if (imageRole === 'first_frame' && model.image_roles?.includes('first_frame') && (imageId || imageUrl.trim()) && ((showsVideo && videoUrl.trim()) || (showsAudio && audioUrl.trim()))) {referenceError('Choose Reference image when combining an image with audio or source video.'); return;}
    setError(''); setNotice('');
    const input: StudioInput = {
        provider_id: account.id, model: model.id, operation: mode === 'cinema' ? 'image' : mode,
        prompt: mode === 'cinema' ? buildCinemaPrompt(prompt, cinema) : prompt.trim(),
        ...(selectedRatio && {aspect_ratio: selectedRatio}),
        ...(selectedResolution && {resolution: selectedResolution}),
        ...(selectedDuration && {duration: Number(selectedDuration)}),
        ...(imageComposition && compositionImages.length > 1 ? {image_references: compositionImages.map(row => row.reference!)} : (model.requires_image || model.supports_image) ? imageId && model.inline_inputs?.includes('image') ? {image_id: imageId} : imageUrl.trim() ? {image_url: imageUrl.trim()} : {} : {}),
        ...((imageId || imageUrl.trim()) && model.image_roles?.length && {image_role: model.image_roles.includes(imageRole) ? imageRole : model.image_roles[0]}),
        ...(model.supports_negative_prompt && negativePrompt.trim() && {negative_prompt: negativePrompt.trim()}),
        ...(showsAudio && audioUrl.trim() && {audio_url: audioUrl.trim()}),
        ...(showsVideo && videoUrl.trim() && {video_url: videoUrl.trim()}),
        acknowledge_billing: true,
      };
    submitBusy.current = true; setSubmitting(true); setError(''); setNotice('');
    const generation = epoch.current;
    const controller = makeController();
    try {
      const api = createStudioClient(ownerId, projectId);
      const operation = input.operation === 'lipsync' ? api.processLipSync : input.operation === 'remix' ? api.remixVideo : input.operation === 'video' ? api.generateVideo : api.generateImage;
      const job = await operation(input, controller.signal);
      if (!current(generation) || job.project_id !== projectId) return;
      acceptJob(job);

      setNotice(job.status === 'completed' ? 'Generation completed.' : 'Job received. Track its status in your results.');
      setPollUntil(Date.now() + POLL_WINDOW); setAutoPaused(false);
    } catch (cause) {
      if (current(generation)) setError(message(cause) + ' Your prompt is preserved. Refresh the library before retrying if the result is uncertain.');
    } finally {
      controllers.current.delete(controller);
      if (generation === epoch.current) {
        submitBusy.current = false;
        if (current(generation)) setSubmitting(false);
      }
    }
  }

  function acceptJob(job: StudioJob) {
    if (!visibleNow.current || lockedNow.current || job.project_id !== projectId) return;
    const latest = rememberJobs([job]);
    setSelectedJobId(job.id);
    setJobs(previous => mergeStudioJobs(previous, latest));
    setHistory(previous => updateLoadedHistory(previous, latest));
    setPollUntil(Date.now() + POLL_WINDOW); setAutoPaused(false);
  }
  async function uploadImage(file: File, append = false) {
    if (projectReadOnly) return;
    if (!visibleNow.current || !observingNow.current || lockedNow.current || !model?.inline_inputs?.includes('image')) return;
    const encoded = Math.ceil(file.size / 3) * 4;
    if (append && (!imageComposition || compositionImages.length >= imageLimit)) {setError('The selected model allows at most ' + imageLimit + ' image references.'); return;}
    const known = (append ? compositionImages : extraImages).reduce((total, row) => total + row.encodedBytes, 0);
    if (imageComposition && known + encoded > STUDIO_IMAGES_ENCODED_BYTES) {setError('These files would exceed the 20 MiB combined image request limit. Choose a smaller image.'); return;}
    if (!append) clearLocalImage();
    const sequence = ++uploadEpoch.current, generation = epoch.current;
    const controller = makeController(); uploadController.current = controller; uploadIsPrimary.current = !append;
    setUploading(true); if (!append) setImageName(file.name); setError('');
    try {
      const reference = await uploadStudioImage(file, projectId, controller.signal);
      if (!current(generation) || sequence !== uploadEpoch.current) return;
      if (append) {
        setComposition([...compositionImages, {key: crypto.randomUUID(), reference: {image_id: reference.id}, name: reference.name, encodedBytes: encoded}]);
        setError(''); return;
      }
      setImageId(reference.id); setImageName(reference.name); setImageUrl(''); setImageEncodedBytes(encoded);
      if (previewObject.current) URL.revokeObjectURL(previewObject.current);
      previewObject.current = URL.createObjectURL(file); setImagePreview(previewObject.current);
    } catch (cause) {
      if (current(generation) && sequence === uploadEpoch.current) {setError(message(cause)); if (!append) setImageName('');}
    } finally {
      controllers.current.delete(controller);
      if (sequence === uploadEpoch.current) {uploadController.current = null; if (current(generation)) setUploading(false);}
    }
  }
  function reuseJob(job: StudioJob) {
    if (job.kind === 'audio' || job.project_id !== projectId || !visibleNow.current || lockedNow.current) return;
    const settings = job.parameters || {};
    const savedAccount = catalog?.accounts.find(row => row.id === job.provider_id);
    const savedModel = catalog?.models.find(row => row.id === job.model && row.adapter === savedAccount?.adapter);
    const operation = settings.operation && settings.operation !== 'music' ? settings.operation : (savedModel?.modes.includes('lipsync') ? 'lipsync' : job.kind);
    const next: Tab = operation === 'lipsync' ? 'lipsync' : operation === 'image' ? 'image' : 'video';
    clearLocalImage(); setExtraImages([]); setExtraImageUrl(''); changeTab(next); setVideoTool(operation === 'remix' ? 'remix' : 'generate');
    updateSelection({provider_id: job.provider_id, model: job.model, aspect_ratio: settings.aspect_ratio, resolution: settings.resolution, duration: settings.duration}, operation); setPrompt(job.prompt);
    setNegativePrompt(settings.negative_prompt || ''); setImageRole(settings.image_role || 'first_frame');
    setImageUrl(settings.image_url || ''); setAudioUrl(settings.audio_url || ''); setVideoUrl(settings.video_url || '');
    if (settings.image_id && savedModel?.inline_inputs?.includes('image')) {setImageId(settings.image_id); setImageName('Saved image reference');}
    if (settings.image_references?.length) {
      setComposition(settings.image_references.map((reference, index) => ({key: crypto.randomUUID(),
        reference: 'has_image_reference' in reference ? null : reference, needsEntry: 'has_image_reference' in reference,
        name: 'Saved reference ' + (index + 1), encodedBytes: 0})));
    }
    const missingComposition = settings.image_references?.some(reference => 'has_image_reference' in reference);
    const missing = [
      settings.has_image_reference && !settings.image_references?.length && !settings.image_url && !(settings.image_id && savedModel?.inline_inputs?.includes('image')) ? 'image' : '',
      settings.has_audio_reference && !settings.audio_url ? 'audio' : '',
      settings.has_video_reference && !settings.video_url ? 'video' : '',
    ].filter(Boolean);
    setNotice('Saved settings loaded.' +
      (!savedModel || !savedAccount ? ' The original model or account is unavailable; select a supported replacement.' : '') +
      (missingComposition ? ' Re-enter each missing temporary image URL in its original position.' : '') +
      (missing.length ? ' Re-enter the ' + missing.join(', ') + ' reference; temporary URLs are not kept in reusable settings.' : ''));
  }
  function canHandoff(job: StudioJob, target: 'image' | 'video') {
    const output = job.status === 'completed' && job.kind === 'image' ? job.outputs.find(value => studioOutputUrl(value)) : undefined;
    if (!output) return false;
    const artifact = studioArtifactId(output);
    return (catalog?.accounts || []).some(account => account.configured && account.capabilities.includes(target) && (catalog?.models || []).some(model => model.adapter === account.adapter && model.modes.includes(target) && (model.supports_image || model.requires_image) && (target !== 'image' || studioImageLimit(model) > 0) && !model.requires_audio && !model.requires_video && (!artifact || model.inline_inputs?.includes('image'))));
  }
  function useOutputReference(job: StudioJob) {
    if (!canHandoff(job, 'image')) return;
    handoffOutput(job, 'image'); setPrompt('');
    setNotice('Image reference added to a new draft.');
  }
  function handoffOutput(job: StudioJob, target: 'image' | 'video') {
    if (job.project_id !== projectId || !visibleNow.current || lockedNow.current) return;
    const output = job.outputs.find(value => studioOutputUrl(value));
    if (!output) {setError('This generation has no supported image output to reuse.'); return;}
    const artifact = studioArtifactId(output);
    const candidates = (catalog?.models || []).filter(row => row.modes.includes(target) && (row.supports_image || row.requires_image) && (target !== 'image' || studioImageLimit(row) > 0) &&
      !row.requires_audio && !row.requires_video && (!artifact || row.inline_inputs?.includes('image')));
    const ordered = [...(catalog?.accounts || [])].sort((a, b) => Number(b.configured) - Number(a.configured) ||
      Number(b.id === job.provider_id) - Number(a.id === job.provider_id) || Number(b.adapter === 'openrouter') - Number(a.adapter === 'openrouter'));
    const selectedAccount = ordered.find(row => row.capabilities.includes(target) && candidates.some(model => model.adapter === row.adapter));
    const selectedModel = candidates.find(row => row.adapter === selectedAccount?.adapter);
    if (!selectedAccount || !selectedModel) {
      setError(target === 'image' ? 'No compatible image-edit model is available for this output. Connect a provider with supported image references.' : 'No compatible animation model is available for this output. Choose a video provider with supported image references.'); return;
    }
    clearLocalImage(); setExtraImages([]); setExtraImageUrl(''); changeTab(target); setVideoTool('generate'); updateSelection({provider_id: selectedAccount.id, model: selectedModel.id}, target);
    setPrompt(target === 'image' ? job.prompt : 'Animate this image with natural, subtle motion.');
    setNegativePrompt(''); setAudioUrl(''); setVideoUrl('');
    setImageRole(selectedModel.image_roles?.includes('first_frame') ? 'first_frame' : selectedModel.image_roles?.[0] || 'reference');
    if (artifact) {setImageId(artifact); setImageName('Output from ' + job.model); setImageUrl('');}
    else setImageUrl(output);
    setNotice((target === 'image' ? 'Image-edit draft ready.' : 'Animation draft ready.') + (!artifact ? ' The provider image link may expire.' : ''));
    window.setTimeout(() => document.getElementById(uid + '-prompt')?.focus(), 0);
  }

  function changeSection(next: StudioSection) {
    setLocalSection(next); onSectionChange?.(next); setPanel(null);
  }
  function changeTab(next: Tab) {
    if (next === 'library') {changeSection('library'); return;}
    setLocalSection('create'); onSectionChange?.('create');
    if (next !== tab && uploadController.current) clearLocalImage();
    setTab(next); setError(''); setNotice('');
    historyEpoch.current++; historyController.current?.abort(); historyBusy.current = false;
  }
  function tabKey(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : step ? (index + step + tabs.length) % tabs.length : -1;
    if (next < 0) return;
    event.preventDefault(); changeTab(tabs[next].id);
    document.getElementById(uid + '-tab-' + tabs[next].id)?.focus();
  }
  const listed = isLibrary ? history.filter(job => job.kind !== 'audio') : jobs.filter(job => mode === 'cinema' || mode === 'image' ? job.kind === 'image' : job.kind === 'video');
  const newest = [...listed].sort((a, b) => b.created - a.created)[0];
  const selectedJob = listed.find(job => job.id === selectedJobId) || newest;
  const referenceCount = (showsImage ? compositionImages.length : 0) + Number(showsAudio && !!audioUrl.trim()) + Number(showsVideo && !!videoUrl.trim());
  const panelActive = visible && foreground && !locked;
  const workActive = panelActive && observing;
  const pickerActive = panelActive && (isAssets || panel === 'references');
  function prepareSavedReference(count: number) {
    if (!isAssets) return true;
    const candidates = (catalog?.models || []).filter(row => row.modes.includes('image') && (row.supports_image || row.requires_image) && row.inline_inputs?.includes('image') && studioImageLimit(row) >= count);
    const chosenAccount = (catalog?.accounts || []).find(row => row.configured && row.capabilities.includes('image') && candidates.some(candidate => candidate.adapter === row.adapter));
    const chosenModel = candidates.find(row => row.adapter === chosenAccount?.adapter);
    if (!chosenModel || !chosenAccount) {setError('Connect a compatible image model before using this saved asset.'); return false;}
    changeTab('image'); updateSelection({provider_id: chosenAccount.id, model: chosenModel.id}, 'image'); setPanel(null); return true;
  }
  function useRecentImage(job: StudioJob) {
    if (!panelActive || job.project_id !== projectId) return;
    const output = job.outputs.find(value => studioOutputUrl(value));
    if (!output || !model) return;
    const artifact = studioArtifactId(output);
    if (artifact && !model.inline_inputs?.includes('image')) {setError('This model does not support saved image references. Choose a compatible model.'); return;}
    if (imageComposition) {
      if (compositionImages.length >= imageLimit) {setError('The selected model allows at most ' + imageLimit + ' image references.'); return;}
      setComposition([...compositionImages, {key: crypto.randomUUID(), reference: artifact ? {image_id: artifact} : {image_url: output}, name: 'Output from ' + job.model, encodedBytes: 0}]);
    } else {clearLocalImage(); setImageId(artifact || ''); setImageUrl(artifact ? '' : output); setImageName('Output from ' + job.model);}
    setNotice('Reference selected.');
  }
  const action = mode === 'cinema' ? 'Generate still image' : mode === 'lipsync' ? 'Generate lip sync' : mode === 'remix' ? 'Generate remix' : 'Generate ' + mode;

  if (locked) return <section className="media-studio studio-locked" aria-label="Media studio"><Layers3 size={28}/><h2>Studio locked</h2><p>Unlock your workspace to see your media.</p></section>;
  return <section className="media-studio studio-canvas-first" aria-label="Media studio" ref={root} hidden={!visible} inert={!visible}>
    <header className="studio-canvas-header"><h1>{isLibrary ? 'Library' : isAssets ? 'Saved assets' : factoryActive ? 'Content factory' : mode === 'cinema' ? 'Cinema' : mode === 'lipsync' ? 'Lip sync' : mode === 'remix' ? 'Remix video' : 'Create'}</h1>
      {!onSectionChange && <nav aria-label="Studio sections">{([['create', 'Create'], ['library', 'Library'], ['assets', 'Saved assets']] as const).map(([id, label]) => <button type="button" key={id} aria-current={activeSection === id ? 'page' : undefined} onClick={() => changeSection(id)}>{label}</button>)}</nav>}
      <Button variant="ghost" size="sm" aria-haspopup="dialog" aria-expanded={panel === 'tools'} onClick={() => setPanel('tools')}><Settings2 size={14}/>Tools<ChevronDown size={13}/></Button>
    </header>
    <div className={'studio-stage' + (isLibrary ? ' studio-library' : '') + (factoryActive ? ' studio-factory-active' : '') + (isAssets ? ' studio-assets-active' : '')}>
      {catalog && <div className="studio-factory-host" hidden={!factoryActive}><MediaFactory catalog={catalog} ownerId={ownerId} projectId={projectId} visible={panelActive && factoryActive} observing={observing} onJob={acceptJob}/></div>}
      {!factoryActive && !isAssets && <div className={"studio-canvas" + (!isLibrary ? ' studio-create-canvas' : '')} role="region" aria-label="Generation results" tabIndex={0}>
        <div className="studio-results-toolbar"><h2>{isLibrary ? 'Generation library' : 'Recent generations'}</h2><Button variant="ghost" size="sm" onClick={() => isLibrary ? void refresh().then(() => readHistory()) : catalog ? void refresh() : void load()} disabled={refreshing || loading || submitting}><RefreshCw size={14} className={refreshing ? 'studio-spin' : ''}/>{refreshing ? 'Refreshing…' : 'Refresh'}</Button></div>
        {isLibrary && <><p className="studio-library-description">Search generation history for this workspace. Download finished media to keep a copy; provider links can expire.</p>
          <div className="studio-history-search"><label htmlFor={uid + '-search'}><Search size={14}/><span className="studio-visually-hidden">Search generations</span></label><Input id={uid + '-search'} placeholder="Search prompts or models…" maxLength={200} value={historySearch} onChange={event => {if (event.target.value !== historySearch) {resetHistory(); setHistorySearch(event.target.value);}}}/>
          <Select aria-label="Generation status" value={historyStatus} onChange={event => {if (event.target.value !== historyStatus) {resetHistory(); setHistoryStatus(event.target.value);}}}><option value="">All statuses</option><option value="active">In progress</option><option value="attention">Needs attention</option>{(['submitting', 'queued', 'running', 'completed', 'failed', 'unknown'] as const).map(value => <option value={value} key={value}>{value === 'unknown' ? 'Status unknown' : value[0].toUpperCase() + value.slice(1)}</option>)}</Select></div>
          <div className="studio-filters" role="group" aria-label="Filter library">{[['all', 'All'], ['image', 'Images'], ['video', 'Videos'], ['active', 'In progress'], ['attention', 'Needs attention']].map(([id, label]) => <button type="button" key={id} aria-pressed={id === 'active' || id === 'attention' ? historyStatus === id : filter === id && !['active', 'attention'].includes(historyStatus)} onClick={() => {const nextKind = id === 'active' || id === 'attention' ? 'all' : id; const nextStatus = id === 'active' || id === 'attention' ? id : ''; if (nextKind !== filter || nextStatus !== historyStatus) {resetHistory(); setFilter(nextKind); setHistoryStatus(nextStatus);}}}>{label}</button>)}</div>
          {historyError && <div className="studio-error" role="alert"><CircleAlert size={15}/><div><p>{historyError}</p><Button variant="ghost" size="sm" onClick={() => void readHistory(!!historyCursor && history.length > 0)}>Retry history</Button></div></div>}
          {historyLoading && <p className="studio-loading" role="status"><LoaderCircle size={15} className="studio-spin"/>Loading history…</p>}
        </>}
        {catalog?.catalog_source === 'snapshot' && <p className="studio-catalog-note"><CircleAlert size={14}/>Saved model catalog. Live availability could not be verified; your provider may not offer every model.</p>}
        {isLibrary && <>
        {loadError && <div className="studio-error" role="alert"><CircleAlert size={17}/><div><strong>Studio could not load</strong><p>{loadError}</p><button type="button" onClick={() => void load()}>Try again</button></div></div>}
        {error && panel !== 'references' && <div className="studio-error" role="alert"><CircleAlert size={17}/><div><p>{error}</p></div></div>}
        {notice && <p className="studio-notice" role="status"><Check size={15}/>{notice}</p>}
        </>}
        {activeJobs.length > 0 && <p className="studio-poll-note"><span className="studio-status-dot"/>{!observing ? 'Automatic checks paused while this window is unfocused.' : autoPaused ? 'Automatic checks paused. Refresh to check your jobs.' : foreground ? 'Checking active jobs every 8 seconds for up to 5 minutes.' : 'Automatic checks pause while this window is hidden.'}</p>}
        {loading && !catalog ? <div className="studio-loading studio-canvas-loading" role="status"><LoaderCircle className="studio-spin" size={22}/><span>Opening your studio…</span></div> : listed.length > 0 ? isLibrary ? <div className="studio-results-grid">{listed.map((job, index) => <JobCard key={job.id} job={job} label={catalog?.models.find(item => item.id === job.model)?.label || job.model}
          available={workActive} onRefresh={() => void refresh([job.id])} refreshing={refreshing} fallbackPreview={index < 3} onReuse={() => reuseJob(job)} onEdit={canHandoff(job, 'image') ? () => handoffOutput(job, 'image') : undefined} onAnimate={canHandoff(job, 'video') ? () => handoffOutput(job, 'video') : undefined} onReference={canHandoff(job, 'image') ? () => useOutputReference(job) : undefined} onSelect={() => {setSelectedJobId(job.id); setPanel('preview');}} selected={panel === 'preview' && selectedJobId === job.id}/>)}</div> : selectedJob && <div className="studio-selected-take"><JobCard key={selectedJob.id} job={selectedJob} label={catalog?.models.find(item => item.id === selectedJob.model)?.label || selectedJob.model} available={workActive} onRefresh={() => void refresh([selectedJob.id])} refreshing={refreshing} fallbackPreview onReuse={() => reuseJob(selectedJob)} onEdit={canHandoff(selectedJob, 'image') ? () => handoffOutput(selectedJob, 'image') : undefined} onAnimate={canHandoff(selectedJob, 'video') ? () => handoffOutput(selectedJob, 'video') : undefined} onReference={canHandoff(selectedJob, 'image') ? () => useOutputReference(selectedJob) : undefined}/>{listed.length > 1 && <nav className="studio-recent-takes" aria-label="Recent takes">{listed.map((job, index) => <button type="button" key={job.id} aria-pressed={selectedJob.id === job.id} onClick={() => setSelectedJobId(job.id)}><span>Take {index + 1}</span><small>{job.status === 'unknown' ? 'Status unknown' : job.status}</small></button>)}</nav>}</div> : !loadError && !(isLibrary && (historyLoading || historyError)) && <div className="studio-empty">
          {isLibrary ? <><span className="studio-empty-icon"><Layers3 size={32}/></span><h3>{historySearch.trim() || filter !== 'all' || historyStatus ? 'No matching generations' : 'Your next idea starts here.'}</h3><p>{historySearch.trim() || filter !== 'all' || historyStatus ? 'Try another search or filter to see more of your work.' : 'Generated images and videos will appear here with their latest provider status.'}</p>{!jobs.length && <Button variant="outline" onClick={() => changeTab('image')}>Create an image<ArrowUpRight size={14}/></Button>}</> : <>
            <span className="studio-empty-symbol" aria-hidden="true">{mode === 'video' || mode === 'remix' ? <Film size={22}/> : mode === 'lipsync' ? <Mic2 size={24}/> : <StudioMark size={40}/>}</span>
            <h3>{mode === 'lipsync' ? 'Bring a portrait to life' : mode === 'remix' ? 'Rework a video' : mode === 'cinema' ? 'Compose a cinematic image' : 'What would you like to create?'}</h3><p>{mode === 'lipsync' ? 'Add a portrait and an audio link.' : mode === 'remix' ? 'Add a source video and describe the change you want to make.' : 'Describe an idea to create your first ' + (mode === 'video' ? 'video.' : 'image.')}</p>
          </>}
        </div>}
        {isLibrary && historyCursor && <Button className="studio-load-more" variant="outline" onClick={() => void readHistory(true)} disabled={historyLoading}>{historyLoading ? <LoaderCircle className="studio-spin" size={14}/> : <Layers3 size={14}/>}Load more generations</Button>}
        {isLibrary && listed.length > 0 && <footer className="studio-canvas-footer"><span>Download outputs promptly · Provider links may expire</span></footer>}
      </div>}

      <form className="studio-controls studio-composer" aria-label="Generation composer" hidden={isLibrary || isAssets || factoryActive} noValidate onSubmit={event => {event.preventDefault(); void generate();}}>
        <div className="studio-dock-modes" role="tablist" aria-label="Studio modes">{tabs.map(({id, label, icon: Icon}, index) => <button key={id} type="button" role="tab" id={uid + '-tab-' + id} aria-controls={uid + '-mode-panel'} aria-selected={id === 'image' ? mode === 'image' || mode === 'cinema' : mode === 'video' || mode === 'lipsync' || mode === 'remix'} tabIndex={(id === 'image' ? mode === 'image' || mode === 'cinema' : mode === 'video' || mode === 'lipsync' || mode === 'remix') ? 0 : -1} onKeyDown={event => tabKey(event, index)} onClick={() => {changeTab(id); setVideoTool('generate');}}><Icon size={18} aria-hidden="true"/><span>{label}</span></button>)}</div>
        <div className="studio-dock-main" id={uid + '-mode-panel'} role="tabpanel" aria-labelledby={uid + '-tab-' + (mode === 'image' || mode === 'cinema' ? 'image' : 'video')}>
          <div className="studio-dock-tools" onFocusCapture={event => event.target.closest<HTMLElement>('.studio-tool-tile')?.scrollIntoView?.({block: 'nearest', inline: 'nearest', behavior: 'auto'})}>
            <Button variant="ghost" size="sm" className="studio-tool-tile" aria-label="Reference" aria-describedby={uid + '-reference-value'} aria-haspopup="dialog" aria-expanded={panel === 'references'} onClick={() => {setReferenceFocus(''); setReferenceSource('upload'); setPanel('references');}} disabled={!model || !(showsImage || showsAudio || showsVideo)}>
              <span className="studio-tool-icon" aria-hidden="true"><Upload size={16}/></span><span className="studio-tool-copy"><span className="studio-tool-label">Reference</span><span className="studio-tool-value" id={uid + '-reference-value'}>{referenceCount > 0 ? referenceCount + ' selected' : model?.requires_image || model?.requires_audio || needsVideo ? 'Required' : 'None added'}</span></span>
            </Button>
            {(mode === 'image' || mode === 'cinema') ? <>
              <Button variant="ghost" size="sm" className="studio-tool-tile" aria-label="Camera" aria-haspopup="dialog" aria-expanded={panel === 'camera'} onClick={() => {if (mode !== 'cinema') changeTab('cinema'); setPanel('camera');}}>
                <span className="studio-tool-icon" aria-hidden="true"><Camera size={16}/></span><span className="studio-tool-copy"><span className="studio-tool-label">Camera</span><span className="studio-tool-value">{mode === 'cinema' ? cinema.focal + ' mm' : 'Auto'}</span></span>
              </Button>
              <Button variant="ghost" size="sm" className="studio-tool-tile" aria-label="Style" aria-haspopup="dialog" aria-expanded={panel === 'style'} onClick={() => setPanel('style')}>
                <span className="studio-tool-icon" aria-hidden="true"><Sparkles size={16}/></span><span className="studio-tool-copy"><span className="studio-tool-label">Style</span><span className="studio-tool-value">Prompt direction</span></span>
              </Button>
            </> : <>
              <Button variant="ghost" size="sm" className="studio-tool-tile" aria-label="Film setup" aria-haspopup="dialog" aria-expanded={panel === 'style'} onClick={() => setPanel('style')}>
                <span className="studio-tool-icon" aria-hidden="true"><Film size={16}/></span><span className="studio-tool-copy"><span className="studio-tool-label">Film setup</span><span className="studio-tool-value">Prompt direction</span></span>
              </Button>
              <Button variant="ghost" size="sm" className="studio-tool-tile" aria-label="Camera" aria-haspopup="dialog" aria-expanded={panel === 'motion'} onClick={() => setPanel('motion')}>
                <span className="studio-tool-icon" aria-hidden="true"><Camera size={16}/></span><span className="studio-tool-copy"><span className="studio-tool-label">Camera</span><span className="studio-tool-value">Movement</span></span>
              </Button>
              <Button variant="ghost" size="sm" className="studio-tool-tile" aria-label="Color" aria-haspopup="dialog" aria-expanded={panel === 'color'} onClick={() => setPanel('color')}>
                <span className="studio-tool-icon" aria-hidden="true"><Palette size={16}/></span><span className="studio-tool-copy"><span className="studio-tool-label">Color</span><span className="studio-tool-value">Prompt direction</span></span>
              </Button>
              <Button variant="ghost" size="sm" className="studio-tool-tile" aria-label="Lighting" aria-haspopup="dialog" aria-expanded={panel === 'lighting'} onClick={() => setPanel('lighting')}>
                <span className="studio-tool-icon" aria-hidden="true"><Sun size={16}/></span><span className="studio-tool-copy"><span className="studio-tool-label">Lighting</span><span className="studio-tool-value">Prompt direction</span></span>
              </Button>
            </>}
            {(mode === 'cinema' || mode === 'lipsync' || mode === 'remix') && <span className="studio-current-tool">{mode === 'cinema' ? 'Still image' : mode === 'lipsync' ? 'Lip sync' : 'Remix'}</span>}
          </div>
          <label className="studio-visually-hidden" htmlFor={uid + '-prompt'}>{mode === 'cinema' ? 'Your scene' : 'Prompt'}</label>
          <textarea className="studio-prompt" id={uid + '-prompt'} value={prompt} maxLength={12000} placeholder={copy[mode].placeholder} onChange={event => setPrompt(event.target.value)} disabled={submitting}/>

          {referenceCount > 0 && <div className="studio-selected-references" role="group" aria-label="Selected reference media"><button type="button" onClick={() => {setReferenceFocus(''); setReferenceSource('upload'); setPanel('references');}}>{referenceCount} {referenceCount === 1 ? 'reference' : 'references'}</button><ol>{(showsImage ? compositionImages : []).map((row, index) => <li key={row.key}>{imagePreview && index === 0 && <img src={imagePreview} alt=""/>}<span>{compositionImages.length > 1 ? (index + 1) + '. ' : ''}{row.name}{!row.reference && <small> · URL needed</small>}</span></li>)}{showsAudio && audioUrl.trim() && <li>Audio reference</li>}{showsVideo && videoUrl.trim() && <li>Video reference</li>}</ol></div>}
          {compositionPanel && compositionImages.length > imageLimit && <p className="studio-reference-warning" role="alert">This model accepts {imageLimit} images. Remove extra references or choose a different model.</p>}
          {showsImage && compositionImages.some(row => !row.reference) && <p className="studio-reference-warning">A temporary image URL is missing. Open Reference to enter it again.</p>}
          <div className="studio-dock-bottom"><div className="studio-dock-settings">
          <div className="studio-model-field"><label htmlFor={uid + '-model'}>Model</label>
          <Select id={uid + '-model'} value={model?.id || ''} disabled={!models.length || submitting || uploading}
            onChange={event => {updateSelection({model: event.target.value});}}>
            {!model && <option value="">{unavailableSelection ? 'Choose a supported model' : 'No supported model'}</option>}
            {models.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
          </Select>
          </div>
        {unavailableSelection && !loading && <p className="studio-reference-warning" role="alert">Your selected model or provider is no longer available. Choose a supported model and provider before generating.</p>}
        {model && <div className="studio-output-settings">
          {model.aspect_ratios.length > 0 && <label><span className="studio-visually-hidden">Aspect ratio</span><Select aria-label="Aspect ratio" value={selectedRatio} disabled={submitting} onChange={event => updateSelection({aspect_ratio: event.target.value})}>{model.aspect_ratios.map(value => <option key={value}>{value}</option>)}</Select></label>}
          {model.resolutions.length > 0 && <label><span className="studio-visually-hidden">Resolution</span><Select aria-label="Resolution" value={selectedResolution} disabled={submitting} onChange={event => updateSelection({resolution: event.target.value})}>{model.resolutions.map(value => <option key={value}>{value}</option>)}</Select></label>}
          {model.durations.length > 0 && <label><span className="studio-visually-hidden">Duration</span><Select aria-label="Duration" value={selectedDuration} disabled={submitting} onChange={event => updateSelection({duration: Number(event.target.value)})}>{model.durations.map(value => <option key={value} value={value}>{value} seconds</option>)}</Select></label>}
        </div>}

            <Button variant="ghost" size="icon" className="studio-settings-trigger" aria-label="Generation settings" aria-haspopup="dialog" title={account?.name || 'Provider account'} onClick={() => setPanel('settings')}><Settings2 size={15}/></Button>
          </div><Button type="submit" className="studio-generate" aria-label={submitting ? 'Submitting generation' : action} disabled={projectReadOnly || !observing || submitting || uploading || loading || !model || !account?.configured || (mode !== 'lipsync' && !prompt.trim())}>{submitting ? <LoaderCircle className="studio-spin" size={16}/> : <Sparkles size={16}/>}<span>{submitting ? 'Submitting…' : 'Generate'}</span></Button></div>
        {(!account?.configured || !model) && !loadError && !loading && <div className="studio-configure"><Settings2 size={17}/><div><strong>{!model ? mode === 'remix' ? 'No verified remix model available' : 'Connect a compatible provider' : 'Finish connecting your account'}</strong><p>{mode === 'remix' ? 'Connect a provider with a supported video-edit model.' : mode === 'lipsync' ? 'Choose a provider with a supported lip sync model.' : 'Set up your providers and models in Studio Settings.'}</p></div>{onConfigureProvider && <Button variant="outline" size="sm" onClick={onConfigureProvider}>Configure provider<ChevronRight size={13}/></Button>}</div>}
        {(loadError || error || notice) && <div className="studio-form-feedback">
        {loadError && <div className="studio-error" role="alert"><CircleAlert size={17}/><div><strong>Studio could not load</strong><p>{loadError}</p><button type="button" onClick={() => void load()}>Try again</button></div></div>}
        {error && panel !== 'references' && <div className="studio-error" role="alert"><CircleAlert size={17}/><div><p>{error}</p></div></div>}
        {notice && <p className="studio-notice" role="status"><Check size={15}/>{notice}</p>}
        </div>}

        </div>
      </form>
    </div>
    <StudioPanel open={panelActive && isLibrary && panel === 'preview'} title="Generation preview" onClose={() => setPanel(null)} className="studio-generation-preview">{panelActive && isLibrary && panel === 'preview' && selectedJob && <JobCard job={selectedJob} label={catalog?.models.find(item => item.id === selectedJob.model)?.label || selectedJob.model} available={workActive && panel === 'preview'} onRefresh={() => void refresh([selectedJob.id])} refreshing={refreshing} fallbackPreview onReuse={() => reuseJob(selectedJob)} onEdit={canHandoff(selectedJob, 'image') ? () => handoffOutput(selectedJob, 'image') : undefined} onAnimate={canHandoff(selectedJob, 'video') ? () => handoffOutput(selectedJob, 'video') : undefined} onReference={canHandoff(selectedJob, 'image') ? () => useOutputReference(selectedJob) : undefined}/>}</StudioPanel>
    <StudioPanel open={panelActive && panel === 'tools'} title="Studio tools" onClose={() => setPanel(null)}><div className="studio-tool-menu">
      {([['cinema', 'Cinema', 'Compose a still image with camera and lens direction.'], ['lipsync', 'Lip sync', 'Animate a portrait with an audio reference.'], ['remix', 'Remix video', 'Edit source video with a verified model.'], ['factory', 'Content factory', 'Build and review a series of scene drafts.']] as const).map(([id, label, description]) => <button type="button" key={id} onClick={() => {changeTab(id === 'factory' || id === 'remix' ? 'video' : id); setVideoTool(id === 'factory' ? 'factory' : id === 'remix' ? 'remix' : 'generate'); setPanel(id === 'cinema' ? 'camera' : id === 'lipsync' || id === 'remix' ? 'references' : null);}}><strong>{label}</strong><span>{description}</span><ChevronRight size={15}/></button>)}</div></StudioPanel>
    <StudioPanel open={pickerActive} inline={isAssets} title={isAssets ? 'Saved reference sets' : 'Reference media'} description={isAssets ? 'Keep people, products and subjects ready for your next generation.' : 'Choose media to guide this generation. Your model determines the supported inputs.'} onClose={() => setPanel(null)} className="studio-reference-panel" initialFocus={isAssets ? undefined : referenceFocus}>
      {!isAssets && <div className="studio-reference-sources" role="tablist" aria-label="Reference sources">{([['upload', 'Upload or URL'], ['saved', 'Saved sets'], ['recent', 'Recent images']] as const).map(([id, label]) => <button type="button" role="tab" key={id} id={uid + '-reference-tab-' + id} aria-controls={uid + '-reference-' + id} aria-selected={referenceSource === id} tabIndex={referenceSource === id ? 0 : -1} onKeyDown={event => {const sources = ['upload', 'saved', 'recent'] as const; const index = sources.indexOf(id); const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0; const next = event.key === 'Home' ? 0 : event.key === 'End' ? sources.length - 1 : step ? (index + step + sources.length) % sources.length : -1; if (next >= 0) {event.preventDefault(); setReferenceSource(sources[next]); document.getElementById(uid + '-reference-tab-' + sources[next])?.focus();}}} onClick={() => setReferenceSource(id)}>{label}</button>)}</div>}
      {model && (showsImage || showsAudio || showsVideo) && <fieldset className="studio-references" id={uid + '-reference-upload'} role="tabpanel" aria-labelledby={uid + '-reference-tab-upload'} hidden={isAssets || referenceSource !== 'upload'}><legend className="studio-visually-hidden">Reference media inputs</legend><p>Use public HTTPS links or supported image files.</p>
          {showsImage && <div className="studio-image-reference"><label>Image URL{!model.requires_image && <span className="studio-optional">optional · image reference</span>}<Input aria-label="Image URL" type="url" required={model.requires_image && !imageId} value={imageUrl} disabled={submitting || uploading || !!imageId} placeholder="https://…/portrait.jpg" onChange={event => setImageUrl(event.target.value)}/></label>
            {model.inline_inputs?.includes('image') ? <><span className="studio-reference-divider">or use an image from your device</span><label className="studio-upload-label"><Upload size={14}/>{uploading ? 'Uploading image…' : imageId ? 'Replace image' : 'Choose image'}<input aria-label="Upload image reference" type="file" accept="image/png,image/jpeg,image/webp" disabled={projectReadOnly || submitting || uploading} onChange={event => {const file = event.target.files?.[0]; event.target.value = ''; if (file) void uploadImage(file);}}/></label><p className="studio-upload-hint">PNG, JPEG or WebP · up to 8 MiB and 32 megapixels.</p></> : <p className="studio-upload-hint">This model requires a public image URL; local image references are not declared supported.</p>}
            {(imageId || uploading) && <div className="studio-reference-selection">{imagePreview && <img src={imagePreview} alt="Selected image reference"/>}<span>{imageName || 'Image reference'}{imageId && <small>Ready in this workspace</small>}</span><Button variant="ghost" size="icon" aria-label="Remove image reference" onClick={() => imageComposition ? setComposition(compositionImages.slice(1)) : clearLocalImage()} disabled={submitting}><X size={14}/></Button></div>}
            {compositionPanel && <div className="studio-image-composition" role="group" aria-label="Ordered image references">
              <div className="studio-composition-heading"><strong>Image composition</strong><span>{compositionImages.length} / {imageLimit}</span></div>
              <p>Images are sent in this order. Each local file can be up to 8 MiB; the combined provider request is limited to 20 MiB. Saved image sizes are checked by the host.</p>
              <ol>{compositionImages.map((row, index) => <li key={row.key}><span className="studio-composition-number">{index + 1}</span><div><strong>{row.name}</strong><small>{row.reference?.image_id ? 'Owned image' : row.reference?.image_url || 'Temporary URL omitted; enter it again'}</small>{(row.needsEntry || !row.reference) && <Input aria-label={'Missing image reference ' + (index + 1)} value={row.reference?.image_url || ''} type="url" placeholder="https://…/reference.jpg" onChange={event => {const rows = [...compositionImages]; rows[index] = {...row, reference: event.target.value.trim() ? {image_url: event.target.value.trim()} : null}; setComposition(rows);}}/>}</div><div className="studio-composition-actions"><Button variant="ghost" size="icon" aria-label={'Move image ' + (index + 1) + ' up'} disabled={submitting || uploading || index === 0} onClick={() => moveImage(index, -1)}><ArrowUp size={13}/></Button><Button variant="ghost" size="icon" aria-label={'Move image ' + (index + 1) + ' down'} disabled={submitting || uploading || index === compositionImages.length - 1} onClick={() => moveImage(index, 1)}><ArrowDown size={13}/></Button><Button variant="ghost" size="icon" aria-label={'Remove image ' + (index + 1)} disabled={projectReadOnly || submitting || uploading} onClick={() => setComposition(compositionImages.filter((_, at) => at !== index))}><X size={13}/></Button></div></li>)}</ol>
              <label>Additional image URL<Input aria-label="Additional image URL" type="url" placeholder="https://…/reference.jpg" value={extraImageUrl} disabled={submitting || uploading || compositionImages.length >= imageLimit} onChange={event => setExtraImageUrl(event.target.value)}/></label><Button variant="outline" size="sm" onClick={addImageUrl} disabled={submitting || uploading || !extraImageUrl.trim() || compositionImages.length >= imageLimit}>Add image URL</Button>
              {model.inline_inputs?.includes('image') && <label className="studio-upload-label"><Upload size={14}/>Add image file<input aria-label="Upload additional image reference" type="file" accept="image/png,image/jpeg,image/webp" disabled={submitting || uploading || compositionImages.length >= imageLimit} onChange={event => {const file = event.target.files?.[0]; event.target.value = ''; if (file) void uploadImage(file, true);}}/></label>}
            </div>}
            {!!model.image_roles?.length && <label>Use image as<Select aria-label="Image reference role" value={model.image_roles.includes(imageRole) ? imageRole : model.image_roles[0]} onChange={event => setImageRole(event.target.value as StudioImageRole)} disabled={submitting}>{model.image_roles.map(role => <option key={role} value={role}>{role === 'first_frame' ? 'First frame' : 'Reference image'}</option>)}</Select></label>}
          </div>}
          {showsAudio && <label>Audio URL{!model.requires_audio && <span className="studio-optional">optional</span>}<Input aria-label="Audio URL" type="url" required={model.requires_audio} value={audioUrl} disabled={submitting} placeholder="https://…/voice.mp3" onChange={event => setAudioUrl(event.target.value)}/></label>}
          {showsVideo && <label>Video URL{!needsVideo && <span className="studio-optional">optional</span>}<Input aria-label="Video URL" type="url" required={needsVideo} value={videoUrl} disabled={submitting} placeholder="https://…/clip.mp4" onChange={event => setVideoUrl(event.target.value)}/></label>}
          {(showsAudio || showsVideo) && <p className="studio-upload-hint">Audio and video references require public HTTPS URLs. Device uploads are not supported by the selected model.</p>}
        </fieldset>}
      <div id={uid + '-reference-saved'} role={isAssets ? undefined : 'tabpanel'} aria-labelledby={isAssets ? undefined : uid + '-reference-tab-saved'} hidden={!isAssets && referenceSource !== 'saved'}>            {subjectsVisited && <Suspense fallback={<p className="studio-upload-hint" role="status">Opening saved subjects…</p>}><StudioSubjects projectId={projectId} ownerId={ownerId} visible={pickerActive && (isAssets || referenceSource === 'saved') && (isAssets || !!model?.inline_inputs?.includes('image'))} observing={observing} embedded onSelect={(id, name, description) => {
              if (!visibleNow.current || lockedNow.current || document.visibilityState === 'hidden') return;
              if (!prepareSavedReference(1)) return;
              clearLocalImage(); setImageId(id); setImageName(name); setImageUrl('');
              setNotice('Selected ' + name + ' as the image reference.' + (description ? ' Subject notes: ' + description : ''));
            }} maxReferences={isAssets ? 5 : imageComposition ? imageLimit : 1} onSelectMany={isAssets || imageComposition && imageLimit > 1 ? (ids, name, description) => {
              if (!visibleNow.current || lockedNow.current || (!isAssets && ids.length > imageLimit) || !ids.length) {setError('This reference set exceeds the selected model image limit.'); return;}
              if (!prepareSavedReference(ids.length)) return;
              setComposition(ids.map((id, index) => ({key: crypto.randomUUID(), reference: {image_id: id}, name: name + ' · photo ' + (index + 1), encodedBytes: 0})));
              setNotice('Reference set selected in photo order.' + (description ? ' Subject notes: ' + description : ''));
            } : undefined}/></Suspense>}
{!isAssets && !model?.inline_inputs?.includes('image') && <p className="studio-panel-note">This model does not accept saved image files. Choose a model with image upload support to use a saved set.</p>}</div>
      <div className="studio-recent-assets" id={uid + '-reference-recent'} role="tabpanel" aria-labelledby={uid + '-reference-tab-recent'} hidden={isAssets || referenceSource !== 'recent'}><h3>Recent generated images</h3>{jobs.filter(job => job.kind === 'image' && job.status === 'completed' && job.outputs.some(output => studioOutputUrl(output))).length ? jobs.filter(job => job.kind === 'image' && job.status === 'completed' && job.outputs.some(output => studioOutputUrl(output))).slice(0, 12).map(job => <button type="button" key={job.id} disabled={!showsImage || imageComposition && compositionImages.length >= imageLimit} onClick={() => useRecentImage(job)}><ImageIcon size={18}/><span>{catalog?.models.find(row => row.id === job.model)?.label || job.model}<small>{job.prompt}</small></span><ArrowUpRight size={14}/></button>) : <p className="studio-panel-note">Completed images from this workspace will appear here.</p>}</div>
      {error && <p className="studio-panel-error" role="alert">{error}</p>}
    </StudioPanel>
    <StudioPanel open={panelActive && panel === 'camera'} title="Camera & lens" description="Creative direction for a cinematic still. These choices are added to the image prompt." onClose={() => setPanel(null)} className="studio-camera-panel"><fieldset className="studio-camera"><legend>Compose your shot</legend><p>Creative direction for a still image.</p><div className="studio-camera-previews">{([cinema.camera, cinema.lens, cinema.aperture]).map(value => <div key={value}><img src={panelActive && panel === 'camera' ? '/assets/studio/cinema/' + value.toLowerCase().replace(/[^a-z0-9]+/g, '_') + '.webp' : undefined} alt="" width="160" height="100" loading="lazy"/><span>{value}</span></div>)}</div><div className="studio-camera-grid">
          {([
            ['camera', 'Camera', cinemaCameras], ['lens', 'Lens', cinemaLenses],
            ['focal', 'Focal length', cinemaFocals], ['aperture', 'Aperture', cinemaApertures],
          ] as const).map(([key, label, options]) => <div key={key}><label htmlFor={uid + '-' + key}>{label}</label><Select id={uid + '-' + key} value={cinema[key]} disabled={submitting}
            onChange={event => setCinema(previous => ({...previous, [key]: event.target.value}))}>{Object.keys(options).map(value => <option key={value} value={value}>{key === 'focal' ? value + ' mm' : value}</option>)}</Select></div>)}
        </div><details className="studio-prompt-preview"><summary>View composed prompt</summary><p>{buildCinemaPrompt(prompt || '[Your scene]', cinema)}</p></details></fieldset></StudioPanel>
    <StudioPanel open={panelActive && (panel === 'style' || panel === 'motion' || panel === 'color' || panel === 'lighting')} title={panel === 'motion' ? 'Camera movement' : panel === 'color' ? 'Color direction' : panel === 'lighting' ? 'Lighting direction' : mode === 'image' || mode === 'cinema' ? 'Prompt style' : 'Film setup'} description="Adds creative direction to your prompt. Model capabilities and generation settings stay unchanged." onClose={() => setPanel(null)}><div className="studio-direction-options">
      {(panel === 'style') && <>{mode !== 'remix' && <div className="studio-preset-chips" role="group" aria-label="Prompt presets">{studioPresets.map(preset => <button type="button" key={preset.id} disabled={submitting} onClick={() => setPrompt(preset.prompt)}>{preset.label}</button>)}</div>}
        {mode === 'video' && <div className="studio-preset-chips" role="group" aria-label="Camera motion presets">{studioMotionPresets.map(preset => <button type="button" key={preset.label} disabled={submitting} onClick={() => setPrompt(value => [value.trim(), preset.prompt].filter(Boolean).join(' '))}>{preset.label}</button>)}</div>}
        {mode === 'remix' && <><p className="studio-remix-note">Only discovered, verified video-edit models appear here. Motion transfer and object-swap variants are available only when the provider declares them.</p><div className="studio-preset-chips" role="group" aria-label="Restyle presets">{studioRestylePresets.map(preset => <button type="button" key={preset.label} disabled={submitting} onClick={() => setPrompt('Restyle the entire video as ' + preset.prompt + '. Keep the original motion, timing, camera movement and composition.')}>{preset.label}</button>)}</div></>}
</>}
      {panel === 'motion' && studioMotionPresets.map(preset => <button type="button" key={preset.label} onClick={() => {setPrompt(value => [value.trim(), preset.prompt].filter(Boolean).join(' ')); setPanel(null);}}>{preset.label}</button>)}
      {(panel === 'color' ? [['Natural', 'Natural, restrained colors.'], ['Warm', 'Warm amber tones with gentle contrast.'], ['Cool', 'Cool blue tones with soft neutral shadows.'], ['Monochrome', 'Black and white with rich tonal detail.']] : panel === 'lighting' ? [['Soft daylight', 'Soft natural daylight with gentle shadows.'], ['Golden hour', 'Warm golden-hour backlight.'], ['Studio light', 'Controlled soft studio lighting.'], ['Low key', 'Low-key cinematic lighting with deep shadows.']] : []).map(([label, direction]) => <button type="button" key={label} onClick={() => {setPrompt(value => [value.trim(), direction].filter(Boolean).join(' ')); setPanel(null);}}>{label}</button>)}
    </div></StudioPanel>
    <StudioPanel open={panelActive && panel === 'settings'} title="Generation settings" description="Choose your provider account and optional model controls." onClose={() => setPanel(null)}>
          <div className="studio-account-field"><label htmlFor={uid + '-account'}>Provider account</label>
          <Select id={uid + '-account'} value={account?.id || ''} disabled={!accounts.length || submitting || uploading}
            onChange={event => {const provider_id = event.target.value, selectedAccount = accounts.find(item => item.id === provider_id), selectedModel = modeModels.find(item => item.adapter === selectedAccount?.adapter && selectedAccount?.capabilities.includes(item.kind)); updateSelection({provider_id, model: selectedModel?.id || ''});}}>
            {!account && <option value="">{accounts.length ? 'Choose a provider account' : 'No compatible account'}</option>}
            {accounts.map(item => <option key={item.id} value={item.id}>{item.name}{!item.configured ? ' · key required' : ''}</option>)}
          </Select>
          {account && <span className="studio-provider-note">{account.adapter === 'openrouter' ? 'OpenRouter' : 'Kie'} · {account.configured ? 'Connected' : 'API key required'}</span>}</div>
{model && <p className="studio-model-description">{model.description}</p>}
        <Button variant="outline" size="sm" aria-haspopup="dialog" onClick={() => setPanel('pricing')} disabled={!model}>Published rates</Button>
        {model?.supports_negative_prompt && <label className="studio-negative-prompt">Negative prompt <span>optional</span><Input aria-label="Negative prompt" value={negativePrompt} maxLength={2000} disabled={submitting} placeholder="Elements or styles to avoid…" onChange={event => setNegativePrompt(event.target.value)}/></label>}
    </StudioPanel>
    <StudioPanel open={panelActive && panel === 'pricing'} title="Published rates" onClose={() => setPanel(null)}>{model && <Suspense fallback={null}><StudioPricing projectId={projectId} ownerId={ownerId} modelId={model.id} adapter={model.adapter} visible={workActive && panel === 'pricing'} expanded/></Suspense>}</StudioPanel>
  </section>;
}

function message(cause: unknown) { return cause instanceof Error ? cause.message : String(cause); }

function JobCard({job, label, available, onRefresh, refreshing, onReuse, onEdit, onAnimate, fallbackPreview, onSelect, selected, onReference}: {
  job: StudioJob; label: string; available: boolean; onRefresh: () => void; refreshing: boolean; fallbackPreview: boolean; onReuse: () => void; onEdit?: () => void; onAnimate?: () => void; onReference?: () => void; onSelect?: () => void; selected?: boolean;
}) {
  const active = activeStudioJob(job);
  const status = {submitting: 'Submitting', queued: 'Queued', running: 'Generating', completed: 'Completed', failed: 'Failed', unknown: 'Status unknown'}[job.status] || 'Status unknown';
  const date = new Date(job.created < 1e12 ? job.created * 1000 : job.created);
  return <article className={"studio-job" + (selected ? " studio-job-selected" : "")} aria-label={label + ' · ' + status}>
    <div className="studio-job-media">
      {job.status === 'completed' && job.outputs.length > 0 ? job.outputs.map((output, index) => <OutputPreview key={output} output={output} kind={job.kind} available={available} fallbackPreview={fallbackPreview && index === 0} title={'Generation ' + job.id + ', output ' + (index + 1)}/>) : <div className={'studio-job-placeholder studio-job-' + job.status}>
        {active ? <LoaderCircle className="studio-spin" size={28}/> : job.status === 'failed' || job.status === 'unknown' ? <CircleAlert size={28}/> : <ImageIcon size={28}/>}<strong>{status}</strong><p>{active ? 'Your provider is working on this generation.' : job.status === 'unknown' ? 'The provider has not confirmed a final result. Check status before starting another job.' : job.status === 'failed' ? 'The provider could not complete this generation.' : 'No output was returned.'}</p>
      </div>}
    </div>
    <div className="studio-job-actions">{onSelect && <Button variant="ghost" size="sm" aria-label={'View generation ' + job.id} aria-pressed={!!selected} onClick={onSelect}>View</Button>}<Button variant="ghost" size="sm" title="Load these settings into a new draft" onClick={onReuse}>Regenerate</Button>{onEdit && <Button variant="ghost" size="sm" onClick={onEdit}>Edit image</Button>}{onAnimate && <Button variant="ghost" size="sm" onClick={onAnimate}>Animate</Button>}{onReference && <Button variant="ghost" size="sm" onClick={onReference}>Use as reference</Button>}</div>
    <div className="studio-job-info"><div className="studio-job-meta"><strong>{label}</strong><span className={'studio-job-status studio-job-status-' + job.status}>{status}</span></div><p className="studio-job-prompt">{job.prompt || 'Lip sync generation'}</p>{job.error && <p className="studio-job-error"><CircleAlert size={13}/>{job.error}</p>}<div className="studio-job-foot"><time dateTime={Number.isNaN(date.getTime()) ? undefined : date.toISOString()}>{Number.isNaN(date.getTime()) ? 'Date unavailable' : date.toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})}</time>{(active || job.status === 'unknown') && <Button variant="ghost" size="sm" onClick={onRefresh} disabled={refreshing}>Check status<RefreshCw size={12}/></Button>}</div></div>
  </article>;
}

// Do not let a newly visible row start dozens of authenticated binary transfers.
// A native transfer may finish after abort, so its slot stays occupied until it settles.
const previewQueue: Array<() => void> = [];
let previewTransfers = 0;
function fetchPreview(path: string, signal: AbortSignal): Promise<Blob> {
  return new Promise((resolve, reject) => {
    let started = false;
    const cancel = () => {
      if (started) return;
      const index = previewQueue.indexOf(start);
      if (index !== -1) previewQueue.splice(index, 1);
      signal.removeEventListener('abort', cancel);
      reject(new DOMException('Preview cancelled', 'AbortError'));
    };
    const start = () => {
      if (signal.aborted) {cancel(); return;}
      started = true; previewTransfers++;
      signal.removeEventListener('abort', cancel);
      void transfer(path, {signal}).then(blob => {
        if (signal.aborted) reject(new DOMException('Preview cancelled', 'AbortError'));
        else resolve(blob);
      }, reject).finally(() => {
        previewTransfers--;
        while (previewTransfers < 3 && previewQueue.length) previewQueue.shift()!();
      });
    };
    if (signal.aborted) {cancel(); return;}
    signal.addEventListener('abort', cancel, {once: true});
    if (previewTransfers < 3) start(); else previewQueue.push(start);
  });
}

function OutputPreview({output, kind, available, title, fallbackPreview}: {
  output: string; kind: string; available: boolean; title: string; fallbackPreview: boolean;
}) {
  const safe = studioOutputUrl(output);
  const root = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);
  const [blobUrl, setBlobUrl] = useState('');
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [openUrl, setOpenUrl] = useState('');
  const [openNotice, setOpenNotice] = useState('');
  const media = useRef<HTMLVideoElement>(null);
  const activeNow = useRef(available); activeNow.current = available;
  const exportEpoch = useRef(0);
  const exportFlight = useRef<AbortController | null>(null);
  const exportUrls = useRef(new Set<string>());
  const exportTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const pendingWindow = useRef<Window | null>(null);
  const eligible = available && inView;

  useEffect(() => {
    setInView(false);
    const element = root.current;
    if (!available || !safe || !element) return;
    // Older webviews get a small initial preview set; all outputs still expose
    // explicit Open/Download actions. Never eagerly fetch an entire fallback gallery.
    if (typeof IntersectionObserver === 'undefined') {setInView(fallbackPreview); return;}
    let stopped = false;
    const observer = new IntersectionObserver(entries => {
      if (!stopped) setInView(entries.some(entry => entry.target === element && entry.isIntersecting));
    }, {threshold: 0.01});
    observer.observe(element);
    return () => {stopped = true; observer.disconnect();};
  }, [safe, available, fallbackPreview]);

  useEffect(() => {
    setBlobUrl(''); setError('');
    if (!eligible || !safe?.startsWith('/')) return;
    let stopped = false, objectUrl = '';
    const controller = new AbortController();
    void fetchPreview(safe, controller.signal).then(blob => {
      if (stopped || controller.signal.aborted || !activeNow.current) return;
      objectUrl = URL.createObjectURL(blob); setBlobUrl(objectUrl);
    }).catch(cause => {if (!stopped && !controller.signal.aborted) setError(message(cause));});
    return () => {stopped = true; controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl);};
  }, [safe, eligible, attempt]);

  useEffect(() => {
    if (!eligible) media.current?.pause();
  }, [eligible]);
  useEffect(() => {
    setOpenUrl(''); setOpenNotice(''); setExportError(''); setExporting(false);
    return () => {
      exportEpoch.current++; exportFlight.current?.abort(); exportFlight.current = null;
      pendingWindow.current?.close(); pendingWindow.current = null;
      exportTimers.current.forEach(timer => clearTimeout(timer)); exportTimers.current.clear();
      exportUrls.current.forEach(url => URL.revokeObjectURL(url)); exportUrls.current.clear();
    };
  }, [safe, available]);

  const url = eligible ? safe?.startsWith('/') ? blobUrl : safe : null;
  const actionUrl = url || openUrl || safe;
  async function exportOutput(open: boolean) {
    if (!safe?.startsWith('/') || !activeNow.current || exportFlight.current) return;
    // Reserve a window during the actual click, before awaiting authenticated bytes.
    const popup = open ? window.open('about:blank', '_blank') : null;
    if (popup) {try {popup.opener = null;} catch {/* Webview may restrict opener access. */}}
    pendingWindow.current = popup;
    const controller = new AbortController(), generation = exportEpoch.current;
    exportFlight.current = controller; setExporting(true); setExportError(''); setOpenNotice('');
    try {
      const blob = await transfer(safe, {signal: controller.signal});
      if (controller.signal.aborted || !activeNow.current || generation !== exportEpoch.current) {popup?.close(); return;}
      const objectUrl = URL.createObjectURL(blob); exportUrls.current.add(objectUrl);
      if (open) {
        setOpenUrl(objectUrl);
        if (popup && !popup.closed) popup.location.replace(objectUrl);
        else setOpenNotice('File ready. Select Open again to view it.');
      } else {
        const anchor = document.createElement('a');
        const extension = ({'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'video/mp4': '.mp4', 'video/webm': '.webm'} as Record<string, string>)[blob.type] || '';
        anchor.href = objectUrl; anchor.download = 'termx-' + kind + extension;
        anchor.click();
        const timer = setTimeout(() => {URL.revokeObjectURL(objectUrl); exportUrls.current.delete(objectUrl); exportTimers.current.delete(timer);}, 1000);
        exportTimers.current.add(timer);
      }
    } catch (cause) {
      popup?.close();
      if (!controller.signal.aborted && activeNow.current && generation === exportEpoch.current) setExportError(message(cause));
    } finally {
      if (generation === exportEpoch.current) {pendingWindow.current = null; exportFlight.current = null; if (activeNow.current) setExporting(false);}
    }
  }
  if (!safe) return <div className="studio-output-error"><CircleAlert size={20}/><p>The provider returned an unsupported output URL.</p></div>;
  return <div className="studio-output" ref={root}>
    {!available ? <div className="studio-output-hidden">Preview paused</div> : !inView ? <div className="studio-output-hidden"><ImageIcon size={22}/><span>{typeof IntersectionObserver === 'undefined' && !fallbackPreview ? 'Use Open to view this output.' : 'Preview loads when visible'}</span></div> : error ? <div className="studio-output-error"><CircleAlert size={20}/><p>Preview unavailable. The link may have expired.</p><Button variant="outline" size="sm" onClick={() => {setError(''); setAttempt(value => value + 1);}}>Retry preview</Button></div> : url ? kind === 'video' ? <video ref={media} key={url + attempt} src={url} controls preload="metadata" playsInline aria-label={title} onError={() => setError('Preview unavailable')}/> : <img key={url + attempt} src={url} alt={title} loading="lazy" referrerPolicy="no-referrer" onError={() => setError('Preview unavailable')}/> : <div className="studio-output-loading" role="status"><LoaderCircle className="studio-spin" size={22}/><span>Loading preview…</span></div>}
    {available && <div className="studio-output-actions"><a href={actionUrl || undefined} target="_blank" rel="noopener noreferrer" aria-label={'Open ' + title} aria-disabled={exporting} onClick={event => {
      if (exporting) {event.preventDefault(); return;}
      if (safe.startsWith('/') && !url && !openUrl) {event.preventDefault(); void exportOutput(true);}
    }}><ArrowUpRight size={14}/>Open</a><a href={actionUrl || undefined} download={'termx-' + kind} target="_blank" rel="noopener noreferrer" aria-label={'Download ' + title} aria-disabled={exporting} onClick={event => {
      if (exporting) {event.preventDefault(); return;}
      if (safe.startsWith('/') && !url && !openUrl) {event.preventDefault(); void exportOutput(false);}
    }}><ArrowDownToLine size={14}/>{exporting ? 'Preparing…' : 'Download'}</a></div>}
    {exportError && <p className="studio-output-export-error" role="alert">{exportError}</p>}
    {openNotice && <p className="studio-output-export-notice" role="status">{openNotice}</p>}
  </div>;
}
