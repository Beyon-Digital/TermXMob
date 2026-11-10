import {useEffect, useRef, useState} from 'react';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../components/ui/dialog';
import type {Session} from '../lib/api';
import {assignmentRows, isRunning} from '../lib/characters';
import {Assignments} from './characters/Assignments';
import {BuildForm, emptyBuildDraft, type BuildDraft} from './characters/BuildForm';
import {CreateForm, emptyCreateDraft, type CreateDraft} from './characters/CreateForm';
import {PackEditor} from './characters/PackEditor';
import {PackLibrary} from './characters/PackLibrary';
import {PreferencesPanel} from './characters/PreferencesPanel';
import {ErrorAlert} from './characters/use-action';
import {useCharacterLibrary} from './characters/use-character-library';
import './characters.css';

type DialogName = 'create' | 'build';

export default function Characters({sessions, ownerId}: {sessions: Session[]; ownerId: string}) {
  const characters = useCharacterLibrary();
  const {library, pets, reload} = characters;
  const [selectedId, setSelectedId] = useState('');
  const [notice, setNotice] = useState('');
  const [dialog, setDialog] = useState<DialogName | null>(null);
  const [createDraft, setCreateDraft] = useState<CreateDraft>(emptyCreateDraft);
  const [buildDraft, setBuildDraft] = useState<BuildDraft>(emptyBuildDraft);
  const [watched, setWatched] = useState('');
  const seen = useRef(false);

  // Follow the operation the person just started until the host reports a result, then announce it once.
  useEffect(() => {
    if (!watched || !library) return;
    const operation = library.operations.find(row => row.id === watched);
    if (!operation) {
      if (seen.current) setWatched('');
      return;
    }
    seen.current = true;
    if (isRunning(operation)) return;
    setWatched('');
    if (operation.phase === 'reference_ready' && operation.artifact_id) {
      setCreateDraft(draft => ({...draft, referenceId: operation.artifact_id ?? ''}));
      setNotice('Reference ready. Inspect it, then create its first animation.');
    }
    if (operation.phase === 'preview_ready' && operation.pack_id) {
      setSelectedId(operation.pack_id);
      setNotice('Animation ready. Preview every state before activating this revision.');
    }
  }, [library, watched]);

  const pack = library?.packs.find(row => row.id === selectedId) ?? library?.packs[0];

  return (
    <section className="characters-page" data-settings-autosave aria-labelledby="characters-title">
      <header className="characters-heading">
        <h2 id="characters-title">Characters</h2>
        <p>A little company for every agent.</p>
      </header>
      <p role="status" className="characters-notice">
        {notice}
      </p>
      {characters.loadError && (
        <ErrorAlert
          message={(library ? 'Could not refresh your characters. ' : 'Could not load your characters. ') + characters.loadError}
          onRetry={() => void reload()}
        />
      )}
      {!library && !characters.loadError && <p>Loading your characters…</p>}
      {library && (
        <>
          <PreferencesPanel preferences={library.preferences} capabilities={characters.capabilities} save={characters.savePreferences} />
          <PackLibrary
            packs={library.packs}
            selectedId={pack?.id ?? ''}
            operations={library.operations}
            onSelect={setSelectedId}
            onCreate={() => setDialog('create')}
            onBuild={() => setDialog('build')}
            onImported={imported => setSelectedId(imported.id)}
            onCancelled={id => setWatched(current => (current === id ? '' : current))}
            notify={setNotice}
            reload={() => reload()}
          />
          {pack && <PackEditor key={pack.id} pack={pack} reducedMotion={library.preferences.reduced_motion} notify={setNotice} reload={() => reload()} />}
          <Assignments rows={assignmentRows(sessions, pets, library.preferences)} packs={library.packs} preferences={library.preferences} save={characters.savePreferences} />
          <Dialog open={dialog === 'create'} onOpenChange={open => setDialog(open ? 'create' : null)}>
            <DialogContent className="character-dialog">
              <DialogTitle>Create a character</DialogTitle>
              <DialogDescription>Describe a character, review its reference, then give it movement.</DialogDescription>
              <CreateForm
                draft={createDraft}
                onDraft={change => setCreateDraft(draft => ({...draft, ...change}))}
                providers={characters.providers}
                providerError={characters.providerError}
                onRetryProviders={() => void characters.loadProviders()}
                operations={library.operations}
                pack={pack}
                busy={characters.running}
                ownerId={ownerId}
                onStarted={async id => {
                  seen.current = false;
                  setWatched(id);
                  setDialog(null);
                  setNotice('Character creation started. Its progress is saved on this host.');
                  await reload();
                }}
              />
            </DialogContent>
          </Dialog>
          <Dialog
            open={dialog === 'build'}
            onOpenChange={open => {
              setDialog(open ? 'build' : null);
              // A file input comes back empty when the dialog reopens, so the chosen sheet must not outlive it.
              if (!open) setBuildDraft(draft => ({...draft, sheet: null, fileKey: draft.fileKey + 1}));
            }}
          >
            <DialogContent className="character-dialog">
              <DialogTitle>Build from your artwork</DialogTitle>
              <DialogDescription>Compile a sprite sheet into a draft animation.</DialogDescription>
              <BuildForm
                draft={buildDraft}
                onDraft={change => setBuildDraft(draft => ({...draft, ...change}))}
                packs={library.packs}
                onBuilt={async built => {
                  setSelectedId(built.id);
                  setDialog(null);
                  setNotice('Sheet compiled into a draft. Preview its timing and alignment before activating it.');
                  await reload();
                }}
              />
            </DialogContent>
          </Dialog>
        </>
      )}
    </section>
  );
}
