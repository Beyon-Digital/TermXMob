import {lazy, Suspense, useEffect, useId, useRef, useState, type KeyboardEvent} from 'react';
import {ArrowLeft, Clapperboard, Files} from 'lucide-react';
import {StudioDraftBoundary,type StudioProjectActivity} from '../lib/studio-draft-state';
import type {StudioSection} from '../lib/workspace-mode';
import './media-workspace.css';

const MusicStudio = lazy(() => import('./MusicStudio'));
const MediaStudio = lazy(() => import('./MediaStudio'));
const StudioSettings = lazy(() => import('./StudioSettings'));
const Artifacts = lazy(() => import('./Media'));

type Props = {
  projectId: string | null;
  ownerId?: string;
  projectName?: string;
  readOnly?: boolean;
  onProjectActivity?: (value: StudioProjectActivity) => void;
  visible?: boolean;
  observing?: boolean;
  locked?: boolean;
  presentation?: 'panel' | 'standalone';
  section?: StudioSection;
  onSectionChange?: (section: StudioSection) => void;
  onContext?: (text: string, attachments: unknown[]) => void;
  onConfigureProvider?: () => void;
  onProvidersChanged?: () => void;
};

/** Keep each visited editor mounted so switching tools preserves unsaved work. */
export default function MediaWorkspace({projectId, ownerId, projectName, readOnly = false, onProjectActivity, visible = true, observing = true, locked = false, presentation = 'panel', section, onSectionChange, onContext, onConfigureProvider, onProvidersChanged}: Props) {
  const id = useId();
  const [localSection, setLocalSection] = useState<StudioSection>('create');
  const activeSection = section ?? localSection;
  const settingsOpen = activeSection === 'settings';
  const musicOpen = activeSection === 'music';
  const [musicVisited, setMusicVisited] = useState(musicOpen);
  useEffect(() => {if (musicOpen) setMusicVisited(true);}, [musicOpen]);
  const [settingsVisited, setSettingsVisited] = useState(settingsOpen);
  const [catalogRevision, setCatalogRevision] = useState(0);
  const composerSection = useRef<Exclude<StudioSection, 'settings' | 'music'>>('create');
  if (activeSection !== 'settings' && activeSection !== 'music') composerSection.current = activeSection;
  useEffect(() => {if (settingsOpen) setSettingsVisited(true);}, [settingsOpen]);
  function changeSection(next: StudioSection) {setLocalSection(next); onSectionChange?.(next);}
  function openSettings() {changeSection('settings'); onConfigureProvider?.();}
  function refreshCatalog() {setCatalogRevision(value => value + 1);}
  function refreshProviders() {refreshCatalog(); onProvidersChanged?.();}
  const [surface, setSurface] = useState<'studio' | 'artifacts'>('studio');
  const [artifactsVisited, setArtifactsVisited] = useState(false);
  const studioPanel = useRef<HTMLDivElement>(null);
  const backButton = useRef<HTMLButtonElement>(null);
  const previousSurface = useRef(surface);
  useEffect(() => {setSurface('studio');}, [activeSection]);
  function openArtifacts() {setArtifactsVisited(true); setSurface('artifacts'); if (settingsOpen) changeSection('create');}
  function navigate(event: KeyboardEvent<HTMLButtonElement>) {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 'studio' : event.key === 'End' ? 'artifacts' : surface === 'studio' ? 'artifacts' : 'studio';
    if (next === 'artifacts') openArtifacts(); else {setSurface('studio'); if (settingsOpen) changeSection('create');}
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>('[data-surface="' + next + '"]')?.focus();
  }
  const standalone = presentation === 'standalone';
  useEffect(() => {
    const previous = previousSurface.current;
    previousSurface.current = surface;
    if (!standalone || !visible || previous === surface) return;
    if (surface === 'artifacts') backButton.current?.focus();
    else studioPanel.current?.querySelector<HTMLButtonElement>('[data-open-artifacts]')?.focus();
  }, [surface, standalone, visible]);
  return <StudioDraftBoundary ownerId={ownerId} projectId={projectId} readOnly={readOnly} onActivity={onProjectActivity}><section className={'media-workspace' + (standalone ? ' media-workspace-standalone' : '')} aria-label="Media workspace">
    {readOnly&&<p className="studio-project-read-only" role="status">This project is archived. History is available; restore the project in Manage projects to create or save media.</p>}
    {standalone ? surface === 'artifacts' && <div className="media-workspace-return"><button ref={backButton} type="button" onClick={() => setSurface('studio')}><ArrowLeft size={15} aria-hidden="true"/>Back to Studio</button><span>Artifacts & documents</span></div> : <div className="media-workspace-tabs" role="tablist" aria-label="Media workspace views">
      <button type="button" role="tab" id={id + '-studio-tab'} data-surface="studio" aria-controls={id + '-studio-panel'} aria-selected={surface === 'studio'} tabIndex={surface === 'studio' ? 0 : -1} onKeyDown={navigate} onClick={() => {setSurface('studio'); if (settingsOpen) changeSection('create');}}><Clapperboard size={15}/>Media studio</button>
      <button type="button" role="tab" id={id + '-artifacts-tab'} data-surface="artifacts" aria-controls={id + '-artifacts-panel'} aria-selected={surface === 'artifacts'} tabIndex={surface === 'artifacts' ? 0 : -1} onKeyDown={navigate} onClick={openArtifacts}><Files size={15}/>Artifacts & documents</button>
    </div>}
    <div ref={studioPanel} id={id + '-studio-panel'} role={standalone ? 'region' : 'tabpanel'} aria-label={standalone ? 'Studio tools' : undefined} aria-labelledby={standalone ? undefined : id + '-studio-tab'} hidden={surface !== 'studio' || settingsOpen || musicOpen} inert={surface !== 'studio' || settingsOpen || musicOpen || !visible || locked}><Suspense fallback={<p className="panel-loading">Opening media studio…</p>}><MediaStudio projectId={projectId} ownerId={ownerId} visible={visible && surface === 'studio' && !settingsOpen && !musicOpen && !locked} observing={observing} catalogRevision={catalogRevision} section={composerSection.current} onSectionChange={changeSection} onOpenArtifacts={standalone ? undefined : openArtifacts} onConfigureProvider={openSettings}/></Suspense></div>
    <div className="media-workspace-music" role="region" aria-label="Music tools" hidden={surface !== 'studio' || !musicOpen || !visible || locked} inert={surface !== 'studio' || !musicOpen || !visible || locked}>{musicVisited && <Suspense fallback={<p className="panel-loading">Opening Music…</p>}><MusicStudio key={ownerId} projectId={projectId} projectName={projectName} ownerId={ownerId} visible={visible && surface === 'studio' && musicOpen && !locked} observing={observing} locked={locked} catalogRevision={catalogRevision} onConfigureProvider={openSettings}/></Suspense>}</div>
    <div className="media-workspace-settings" hidden={!settingsOpen || !visible || locked} inert={!settingsOpen || !visible || locked}>{settingsVisited && <Suspense fallback={<p className="panel-loading">Opening Studio Settings…</p>}><StudioSettings key={ownerId} ownerId={ownerId} visible={visible && settingsOpen && !locked} observing={observing} locked={locked} onSaved={refreshCatalog} onProvidersChanged={refreshProviders}/></Suspense>}</div>
    <div id={id + '-artifacts-panel'} role={standalone ? 'region' : 'tabpanel'} aria-label={standalone ? 'Artifacts & documents' : undefined} aria-labelledby={standalone ? undefined : id + '-artifacts-tab'} hidden={surface !== 'artifacts' || settingsOpen} inert={surface !== 'artifacts' || settingsOpen || !visible || locked}>{artifactsVisited && <Suspense fallback={<p className="panel-loading">Opening artifacts…</p>}><Artifacts projectId={projectId} ownerId={ownerId} visible={visible && surface === 'artifacts' && !settingsOpen && !locked} onContext={onContext}/></Suspense>}</div>
  </section></StudioDraftBoundary>;
}
