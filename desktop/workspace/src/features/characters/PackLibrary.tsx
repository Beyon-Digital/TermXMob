import {useState} from 'react';
import {Sparkles, Upload} from 'lucide-react';
import {Button} from '../../components/ui/button';
import {FormField} from '../../components/ui/form-field';
import {Input} from '../../components/ui/input';
import CharacterSprite from '../../components/CharacterSprite';
import {transfer} from '../../lib/transfers';
import {json, request} from '../../lib/api';
import {validateCharacterFile} from '../../lib/character-files';
import {isRunning, parseCharacterPack, type CharacterOperation, type CharacterPack} from '../../lib/characters';
import {Section} from './Section';
import {ActionError, useAction} from './use-action';

function describePack(pack: CharacterPack) {
  const base = pack.builtin ? 'Included' : pack.accepted ? 'Active revision ' + pack.accepted_revision : 'Draft';
  return pack.accepted_revision && pack.accepted_revision !== pack.revision ? base + ' · New draft' : base;
}

interface Props {
  packs: CharacterPack[];
  selectedId: string;
  operations: CharacterOperation[];
  onSelect: (id: string) => void;
  onCreate: () => void;
  onBuild: () => void;
  onImported: (pack: CharacterPack) => void;
  onCancelled: (operationId: string) => void;
  notify: (message: string) => void;
  reload: () => Promise<void>;
}

export function PackLibrary({packs, selectedId, operations, onSelect, onCreate, onBuild, onImported, onCancelled, notify, reload}: Props) {
  const action = useAction();
  const [fileKey, setFileKey] = useState(0);
  const [fileError, setFileError] = useState('');
  const shown = operations.filter(operation => operation.status !== 'completed' && operation.status !== 'cancelled');

  async function chooseFile(file: File | undefined) {
    // A new input element each time, so choosing the same file again still fires a change.
    setFileKey(key => key + 1);
    if (!file) return;
    const problem = await validateCharacterFile(file, 'pack');
    setFileError(problem ?? '');
    if (problem) return;
    void action.run(async () => {
      const response = await transfer('/api/characters/import', {method: 'POST', headers: {'Content-Type': 'application/zip'}, body: file});
      const pack = parseCharacterPack(await response.text());
      onImported(pack);
      notify('Pack imported as a draft. Inspect its animations before activating it.');
      await reload();
    });
  }

  function cancel(operation: CharacterOperation) {
    void action.run(async () => {
      await request('/api/characters/operations/' + encodeURIComponent(operation.id) + '/cancel', json('POST', {}));
      onCancelled(operation.id);
      notify('Cancellation requested. Check the operation status before generating again; provider billing may already have started.');
      await reload();
    });
  }

  return (
    <Section
      title="Library"
      description="Pick a character to preview it, or bring in a new one."
      actions={
        <>
          <Button variant="outline" onClick={onCreate}>
            <Sparkles size={14} />
            Create a character
          </Button>
          <Button variant="outline" onClick={onBuild}>
            <Upload size={14} />
            Build from artwork
          </Button>
        </>
      }
    >
      <ul className="character-grid" aria-label="Characters">
        {packs.map(pack => (
          <li key={pack.id}>
            <button type="button" className={pack.id === selectedId ? 'selected' : ''} aria-pressed={pack.id === selectedId} onClick={() => onSelect(pack.id)}>
              <CharacterSprite packId={pack.id} revision={pack.revision} size={52} reducedMotion lazy />
              <span>
                <strong>{pack.name}</strong>
                <small>{describePack(pack)}</small>
              </span>
            </button>
          </li>
        ))}
      </ul>
      <FormField label="Import character pack" hint="A .termxpet or .zip file up to 32 MiB." error={fileError} className="character-import">
        {control => (
          <Input {...control} key={fileKey} type="file" accept=".termxpet,.zip,application/zip" onChange={event => void chooseFile(event.target.files?.[0])} />
        )}
      </FormField>
      <ActionError action={action} />
      {shown.length > 0 && (
        <div className="character-operations" role="group" aria-label="Character creation progress">
          {shown.map(operation => (
            <p key={operation.id}>
              <strong>{operation.status === 'failed' ? 'Creation needs attention' : operation.status}</strong> · {operation.phase.replaceAll('_', ' ')}
              {operation.error && <span className="inline-error"> {operation.error}</span>}
              {isRunning(operation) && (
                <Button variant="ghost" size="sm" onClick={() => cancel(operation)}>
                  Cancel creation
                </Button>
              )}
            </p>
          ))}
        </div>
      )}
    </Section>
  );
}
