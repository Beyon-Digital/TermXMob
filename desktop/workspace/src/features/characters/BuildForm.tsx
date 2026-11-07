import {useState} from 'react';
import {Button} from '../../components/ui/button';
import {FormField} from '../../components/ui/form-field';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import {json, request} from '../../lib/api';
import {validateCharacterFile} from '../../lib/character-files';
import {transfer} from '../../lib/transfers';
import {characterClips, parseArtifactId, asCharacterPack, parseNumberField, type CharacterClipName, type CharacterPack} from '../../lib/characters';
import {ActionError, useAction} from './use-action';

export interface BuildDraft {
  target: string;
  name: string;
  clip: CharacterClipName;
  columns: string;
  rows: string;
  duration: string;
  removeBackground: boolean;
  sheet: File | null;
  /** Changes whenever the file input must be emptied, because a file input cannot be cleared through props. */
  fileKey: number;
}

export const emptyBuildDraft: BuildDraft = {
  target: '',
  name: '',
  clip: 'idle',
  columns: '4',
  rows: '2',
  duration: '140',
  removeBackground: false,
  sheet: null,
  fileKey: 0,
};

interface Props {
  draft: BuildDraft;
  onDraft: (change: Partial<BuildDraft>) => void;
  packs: CharacterPack[];
  onBuilt: (pack: CharacterPack) => void | Promise<void>;
}

export function BuildForm({draft, onDraft, packs, onBuilt}: Props) {
  const action = useAction();
  const [attempted, setAttempted] = useState(false);
  const [fileError, setFileError] = useState('');
  const target = packs.find(pack => pack.id === draft.target);
  const columns = parseNumberField(draft.columns, 1, 16, true);
  const rows = parseNumberField(draft.rows, 1, 16, true);
  const duration = parseNumberField(draft.duration, 20, 5000, true);
  const errors = {
    name: draft.target || draft.name.trim() ? '' : 'Name your new character.',
    sheet: draft.sheet ? '' : 'Choose a PNG sprite sheet.',
    columns: columns === null ? 'Enter a whole number from 1 to 16.' : '',
    rows: rows === null ? 'Enter a whole number from 1 to 16.' : '',
    duration: duration === null ? 'Enter a whole number from 20 to 5000.' : '',
  };
  const shown = (key: keyof typeof errors) => (attempted ? errors[key] : '');

  async function chooseSheet(file: File | undefined) {
    if (!file) {
      setFileError('');
      onDraft({sheet: null});
      return;
    }
    const problem = await validateCharacterFile(file, 'png');
    setFileError(problem ?? '');
    // The input keeps a valid file itself; only a rejected file needs the input replaced to empty it.
    if (problem) onDraft({sheet: null, fileKey: draft.fileKey + 1});
    else onDraft({sheet: file});
  }

  function submit() {
    const sheet = draft.sheet;
    if (Object.values(errors).some(Boolean) || !sheet || columns === null || rows === null || duration === null) {
      setAttempted(true);
      return;
    }
    void action.run(async () => {
      const upload = await transfer('/api/media/inputs?name=' + encodeURIComponent(sheet.name), {method: 'POST', headers: {'Content-Type': 'image/png'}, body: sheet});
      const artifactId = parseArtifactId(await upload.text());
      const built = await request<unknown>(
        '/api/characters/build',
        json('POST', {
          artifact_id: artifactId,
          name: draft.name.trim() || target?.name || sheet.name.slice(0, 80),
          pack_id: draft.target || undefined,
          clip: draft.clip,
          columns,
          rows,
          duration_ms: duration,
          remove_background: draft.removeBackground,
        }),
      );
      const pack = asCharacterPack(built);
      onDraft({sheet: null, fileKey: draft.fileKey + 1});
      setAttempted(false);
      await onBuilt(pack);
    });
  }

  return (
    <form
      className="character-form"
      noValidate
      onSubmit={event => {
        event.preventDefault();
        submit();
      }}
    >
      <p>Upload a transparent PNG sprite sheet. Frames run left to right, then top to bottom. The compiler normalizes their alignment.</p>
      <ActionError action={action} />
      <FormField label="Build character">
        {control => (
          <Select {...control} value={draft.target} onChange={event => onDraft({target: event.target.value})}>
            <option value="">Create a new character</option>
            {packs
              .filter(pack => !pack.builtin)
              .map(pack => (
                <option key={pack.id} value={pack.id}>
                  {pack.name}
                </option>
              ))}
          </Select>
        )}
      </FormField>
      <FormField label="Artwork character name" error={shown('name')} required={!draft.target}>
        {control => <Input {...control} maxLength={80} value={draft.name} onChange={event => onDraft({name: event.target.value})} placeholder="Name your new character" />}
      </FormField>
      <FormField label="Animation sheet" hint="A PNG up to 16 MiB and 4096 pixels on each side." error={fileError || shown('sheet')} required>
        {control => <Input {...control} key={draft.fileKey} type="file" accept="image/png" onChange={event => void chooseSheet(event.target.files?.[0])} />}
      </FormField>
      <div className="character-form-grid">
        <FormField label="Columns" error={shown('columns')} required>
          {control => <Input {...control} type="number" inputMode="numeric" value={draft.columns} onChange={event => onDraft({columns: event.target.value})} />}
        </FormField>
        <FormField label="Rows" error={shown('rows')} required>
          {control => <Input {...control} type="number" inputMode="numeric" value={draft.rows} onChange={event => onDraft({rows: event.target.value})} />}
        </FormField>
        <FormField label="Frame duration (ms)" error={shown('duration')} required>
          {control => <Input {...control} type="number" inputMode="numeric" value={draft.duration} onChange={event => onDraft({duration: event.target.value})} />}
        </FormField>
      </div>
      <FormField label="Animation">
        {control => (
          <Select {...control} value={draft.clip} onChange={event => onDraft({clip: characterClips.find(value => value === event.target.value) ?? 'idle'})}>
            {characterClips.map(value => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </Select>
        )}
      </FormField>
      <FormField label="Remove solid background" className="character-check-field">
        {control => <input {...control} type="checkbox" checked={draft.removeBackground} onChange={event => onDraft({removeBackground: event.target.checked})} />}
      </FormField>
      <p>{target ? 'Adds ' + draft.clip + ' to ' + target.name + ' as a new draft.' : 'Creates a new draft character.'}</p>
      <div className="character-actions">
        <Button type="submit" variant="outline">
          Build animation
        </Button>
      </div>
    </form>
  );
}
