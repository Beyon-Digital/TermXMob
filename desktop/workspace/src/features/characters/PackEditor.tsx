import {useCallback, useState} from 'react';
import {Check, ChevronDown, Download, Pause, Play} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {Collapsible, CollapsibleContent, CollapsibleTrigger} from '../../components/ui/collapsible';
import {FormField} from '../../components/ui/form-field';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import CharacterSprite from '../../components/CharacterSprite';
import {json, request} from '../../lib/api';
import {validateCharacterFile} from '../../lib/character-files';
import {download, transfer} from '../../lib/transfers';
import {characterClips, characterPath, clipFor, parseArtifactId, parseNumberField, type CharacterManifest, type CharacterPack} from '../../lib/characters';
import {Section} from './Section';
import {ActionError, useAction} from './use-action';

interface TimingEdit {
  duration?: string;
  x?: string;
  y?: string;
}

interface Props {
  pack: CharacterPack;
  reducedMotion: boolean;
  notify: (message: string) => void;
  reload: () => Promise<void>;
}

/** Mount with `key={pack.id}` so switching packs starts from a clean preview and unsaved timing edits never leak across packs. */
export function PackEditor({pack, reducedMotion, notify, reload}: Props) {
  const action = useAction();
  const [chosenRevision, setChosenRevision] = useState<number | null>(null);
  const [loaded, setLoaded] = useState<{key: string; manifest: CharacterManifest} | null>(null);
  const [state, setState] = useState('idle');
  const [playing, setPlaying] = useState(true);
  const [frame, setFrame] = useState(0);
  const [edits, setEdits] = useState<Record<string, TimingEdit>>({});
  const [timingErrors, setTimingErrors] = useState<TimingEdit>({});
  const [frameKey, setFrameKey] = useState(0);
  const [frameError, setFrameError] = useState('');

  const accepted = pack.accepted_revision && pack.accepted_revision !== pack.revision ? pack.accepted_revision : null;
  // Follow the newest draft until the person picks another revision; an older pick that vanished falls back to it.
  const revision = chosenRevision !== null && (chosenRevision === pack.revision || chosenRevision === accepted) ? chosenRevision : pack.revision;
  const loadedKey = pack.id + ':' + revision;
  const manifest = loaded?.key === loadedKey ? loaded.manifest : null;
  const onManifest = useCallback((value: CharacterManifest) => setLoaded({key: loadedKey, manifest: value}), [loadedKey]);
  const clip = manifest ? clipFor(manifest, state) : undefined;
  const frameCount = clip?.frames.length || 1;
  const shownFrame = Math.min(frame, frameCount - 1);
  const isDraft = revision === pack.revision;

  const edit = edits[state] ?? {};
  const duration = edit.duration ?? String(clip?.frames[0]?.duration_ms ?? 140);
  const anchorX = edit.x ?? String(manifest?.foot_anchor.x ?? 0);
  const anchorY = edit.y ?? String(manifest?.foot_anchor.y ?? 0);
  const setEdit = (change: TimingEdit) => setEdits(previous => ({...previous, [state]: {...previous[state], ...change}}));

  function chooseState(value: string) {
    setState(value);
    setFrame(0);
    setTimingErrors({});
  }

  function saveTiming() {
    const parsed = {
      duration: parseNumberField(duration, 20, 5000, true),
      x: parseNumberField(anchorX, 0, manifest?.logical_size.width ?? 512),
      y: parseNumberField(anchorY, 0, manifest?.logical_size.height ?? 512),
    };
    const problems: TimingEdit = {};
    if (parsed.duration === null) problems.duration = 'Enter a whole number from 20 to 5000.';
    if (parsed.x === null) problems.x = 'Enter a number inside the character’s width.';
    if (parsed.y === null) problems.y = 'Enter a number inside the character’s height.';
    setTimingErrors(problems);
    if (parsed.duration === null || parsed.x === null || parsed.y === null) return;
    const body = {clip: state, duration_ms: parsed.duration, foot_anchor: {x: parsed.x, y: parsed.y}};
    void action.run(async () => {
      await request(characterPath(pack.id) + '/clip', json('PATCH', body));
      setEdits(previous => ({...previous, [state]: {}}));
      notify('Timing saved as a draft revision.');
      await reload();
    });
  }

  async function chooseFrame(file: File | undefined) {
    setFrameKey(key => key + 1);
    if (!file) return;
    const problem = await validateCharacterFile(file, 'png');
    setFrameError(problem ?? '');
    if (problem) return;
    const index = shownFrame;
    void action.run(async () => {
      const response = await transfer('/api/media/inputs?name=' + encodeURIComponent(file.name), {method: 'POST', headers: {'Content-Type': 'image/png'}, body: file});
      const artifactId = parseArtifactId(await response.text());
      await request(characterPath(pack.id) + '/frames/' + index, json('POST', {clip: state, artifact_id: artifactId}));
      notify('Frame ' + (index + 1) + ' replaced in a draft. Inspect the animation before activating it.');
      await reload();
    });
  }

  const states = characterClips.filter(value => manifest?.clips[value] || manifest?.states[value]);

  return (
    <Section
      title={pack.name}
      description={(pack.builtin ? 'Original TermX character' : 'Custom character') + ' · Revision ' + revision}
      className="character-editor"
      actions={
        <>
          <Button
            variant="ghost"
            aria-label="Export character pack"
            onClick={() => void action.run(() => download(characterPath(pack.id) + '/export?revision=' + revision, pack.name + '-v' + revision + '.termxpet'))}
          >
            <Download size={15} />
          </Button>
          {pack.accepted_revision !== pack.revision && (
            <Button
              disabled={!isDraft || !manifest}
              onClick={() =>
                void action.run(async () => {
                  await request(characterPath(pack.id) + '/accept', json('POST', {revision}));
                  window.dispatchEvent(new Event('termx-companions-changed'));
                  notify(pack.name + ' is ready for your agents.');
                  await reload();
                })
              }
            >
              <Check size={14} />
              Use this revision
            </Button>
          )}
        </>
      }
    >
      <ActionError action={action} />
      <div className="character-stage" role="group" aria-label="Animation preview">
        <CharacterSprite
          packId={pack.id}
          revision={revision}
          state={state}
          size={176}
          paused={!playing}
          frame={playing ? undefined : shownFrame}
          reducedMotion={reducedMotion}
          onManifest={onManifest}
        />
        <span className="character-stage-floor" />
        <small aria-live="polite">{state}</small>
      </div>
      <div className="character-playback">
        <Button variant="ghost" aria-label={playing ? 'Pause preview' : 'Play preview'} aria-pressed={playing} onClick={() => setPlaying(value => !value)}>
          {playing ? <Pause size={15} /> : <Play size={15} />}
        </Button>
        <input
          type="range"
          aria-label="Animation frame"
          aria-valuetext={'Frame ' + (shownFrame + 1) + ' of ' + frameCount}
          min={0}
          max={frameCount - 1}
          value={shownFrame}
          onChange={event => {
            setPlaying(false);
            setFrame(Number(event.target.value));
          }}
        />
        <small>
          {playing ? 'Playing' : 'Frame ' + (shownFrame + 1)} / {frameCount}
        </small>
        <Select
          aria-label="Preview character revision"
          value={revision}
          onChange={event => {
            setChosenRevision(Number(event.target.value));
            setFrame(0);
          }}
        >
          <option value={pack.revision}>
            {pack.builtin ? 'Included' : pack.accepted ? 'Active' : 'Latest draft'} · {pack.revision}
          </option>
          {accepted !== null && <option value={accepted}>Active · {accepted}</option>}
        </Select>
      </div>
      <div className="character-state-buttons" role="group" aria-label="Preview animation state">
        {states.map(value => (
          <button type="button" key={value} aria-pressed={state === value} className={state === value ? 'selected' : ''} onClick={() => chooseState(value)}>
            {value}
          </button>
        ))}
      </div>
      {!!pack.fallback_clips?.length && (
        <p className="character-fallback">Shared poses: {pack.fallback_clips.join(', ')}. These states reuse idle frames until you replace their animation.</p>
      )}
      {!pack.builtin && (
        <Collapsible className="character-timing-panel">
          <CollapsibleTrigger className="character-collapse-trigger">
            Timing and frames
            <ChevronDown size={14} aria-hidden="true" />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <form
              className="character-timing"
              noValidate
              onSubmit={event => {
                event.preventDefault();
                saveTiming();
              }}
            >
              <FormField label="Frame duration (ms)" error={timingErrors.duration} required>
                {control => <Input {...control} type="number" inputMode="numeric" value={duration} onChange={event => setEdit({duration: event.target.value})} />}
              </FormField>
              <FormField label="Foot anchor X" error={timingErrors.x} required>
                {control => <Input {...control} type="number" value={anchorX} onChange={event => setEdit({x: event.target.value})} />}
              </FormField>
              <FormField label="Foot anchor Y" error={timingErrors.y} required>
                {control => <Input {...control} type="number" value={anchorY} onChange={event => setEdit({y: event.target.value})} />}
              </FormField>
              <Button variant="outline" type="submit" disabled={!manifest || !isDraft}>
                Save timing
              </Button>
              <FormField label={'Replace frame ' + (shownFrame + 1)} hint="Pause or scrub to choose a frame." error={frameError} className="character-frame-replace">
                {control => (
                  <Input
                    {...control}
                    key={frameKey}
                    type="file"
                    accept="image/png"
                    disabled={playing || !manifest || !isDraft}
                    onChange={event => void chooseFrame(event.target.files?.[0])}
                  />
                )}
              </FormField>
            </form>
          </CollapsibleContent>
        </Collapsible>
      )}
    </Section>
  );
}
