import {useEffect, useState} from 'react';
import {Button} from '../../components/ui/button';
import {FormField} from '../../components/ui/form-field';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import {Textarea} from '../../components/ui/textarea';
import {json, request, type Provider} from '../../lib/api';
import {clearMediaIntent, mediaIntent} from '../../lib/media-intent';
import {transfer} from '../../lib/transfers';
import {characterClips, errorMessage, type CharacterClipName, type CharacterOperation, type CharacterPack} from '../../lib/characters';
import {ActionError, ErrorAlert, useAction} from './use-action';

export interface CreateDraft {
  name: string;
  providerId: string;
  model: string;
  prompt: string;
  billing: boolean;
  removeBackground: boolean;
  referenceId: string;
  regenerateClip: CharacterClipName;
}

export const emptyCreateDraft: CreateDraft = {
  name: '',
  providerId: '',
  model: '',
  prompt: '',
  billing: false,
  removeBackground: false,
  referenceId: '',
  regenerateClip: 'idle',
};

function ReferenceArt({id}: {id: string}) {
  const [shown, setShown] = useState<{id: string; url: string} | null>(null);
  const [failure, setFailure] = useState<{id: string; message: string} | null>(null);
  useEffect(() => {
    let active = true;
    let owned = '';
    transfer('/api/media/artifacts/' + encodeURIComponent(id) + '/export?format=original')
      .then(blob => {
        if (!active) return;
        owned = URL.createObjectURL(blob);
        setShown({id, url: owned});
      })
      .catch(reason => {
        if (active) setFailure({id, message: errorMessage(reason)});
      });
    return () => {
      active = false;
      if (owned) URL.revokeObjectURL(owned);
    };
  }, [id]);
  if (failure?.id === id)
    return (
      <p role="alert" className="inline-error">
        {failure.message}
      </p>
    );
  if (shown?.id === id) return <img className="character-reference" src={shown.url} alt="Canonical character reference" />;
  return <p>Loading reference…</p>;
}

interface Props {
  draft: CreateDraft;
  onDraft: (change: Partial<CreateDraft>) => void;
  providers: Provider[];
  providerError: string;
  onRetryProviders: () => void;
  operations: CharacterOperation[];
  /** The character selected in the library, offered for regenerating one of its animations. */
  pack: CharacterPack | undefined;
  /** Another creation is already running on the host. */
  busy: boolean;
  ownerId: string;
  onStarted: (operationId: string) => void | Promise<void>;
}

export function CreateForm({draft, onDraft, providers, providerError, onRetryProviders, operations, pack, busy, ownerId, onStarted}: Props) {
  const action = useAction();
  const account = providers.find(row => row.id === draft.providerId);
  const references = operations.filter(operation => operation.phase === 'reference_ready' && operation.artifact_id);
  const ready = !!draft.providerId && !!draft.model.trim() && !!draft.prompt.trim() && draft.billing;
  const canRegenerate = !!pack?.reference_id && !pack.builtin;
  function generate(phase: 'reference' | 'clip', regenerate = false) {
    if (!ready || (!regenerate && !draft.name.trim())) return;
    const args = {
      phase,
      name: regenerate ? pack?.name || 'My character' : draft.name.trim() || pack?.name || 'My character',
      prompt: draft.prompt,
      provider_id: draft.providerId,
      model: draft.model.trim(),
      acknowledge_billing: draft.billing,
      remove_background: draft.removeBackground,
      ...(phase === 'clip'
        ? {
            reference_id: regenerate ? pack?.reference_id : draft.referenceId,
            pack_id: regenerate ? pack?.id : undefined,
            clip: regenerate ? draft.regenerateClip : 'idle',
          }
        : {}),
    };
    // A billed request is never retried automatically, and a second click while one is in flight is ignored.
    void action.run(
      async () => {
        const intent = await mediaIntent(ownerId, null, args, 'character-' + phase);
        if (!action.mounted()) return;
        const operation = await request<CharacterOperation>('/api/characters/generate', json('POST', {...args, request_id: intent.id}));
        clearMediaIntent(intent.key, intent.id);
        await onStarted(operation.id);
      },
      {retry: false, exclusive: true},
    );
  }

  return (
    <form
      className="character-form"
      noValidate
      onSubmit={event => {
        event.preventDefault();
        generate('reference');
      }}
    >
      <p>Start with a reference, then give it movement. Your selected image provider bills each generation.</p>
      <ActionError action={action} />
      {providerError && <ErrorAlert message={'Could not load your image accounts. ' + providerError} onRetry={onRetryProviders} />}
      <div className="character-form-grid">
        <FormField label="Character name" required>
          {control => <Input {...control} maxLength={80} value={draft.name} onChange={event => onDraft({name: event.target.value})} placeholder="A name for your companion" />}
        </FormField>
        <FormField label="Image account" required>
          {control => (
            <Select {...control} value={draft.providerId} onChange={event => onDraft({providerId: event.target.value, model: '', billing: false})}>
              <option value="">Choose an account</option>
              {providers.map(row => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ))}
            </Select>
          )}
        </FormField>
        <FormField label="Image model" required>
          {control => (
            <Input
              {...control}
              value={draft.model}
              onChange={event => onDraft({model: event.target.value, billing: false})}
              placeholder={account?.model || 'Enter an image model'}
            />
          )}
        </FormField>
      </div>
      {!providers.length && !providerError && <p>No configured image account is available. Add one in Models & engines to generate artwork, or import a character pack.</p>}
      <FormField label="Character description" required>
        {control => (
          <Textarea {...control} rows={3} value={draft.prompt} onChange={event => onDraft({prompt: event.target.value})} placeholder="Describe its shape, colors, personality and materials…" />
        )}
      </FormField>
      <FormField label={'I agree to charges from ' + (account?.name || 'the selected image account') + ' for this generation.'} hint="Required before anything is generated." className="character-check-field">
        {control => <input {...control} type="checkbox" checked={draft.billing} onChange={event => onDraft({billing: event.target.checked})} />}
      </FormField>
      <FormField label="Remove solid background" className="character-check-field">
        {control => <input {...control} type="checkbox" checked={draft.removeBackground} onChange={event => onDraft({removeBackground: event.target.checked})} />}
      </FormField>
      <div className="character-actions">
        <Button type="submit" disabled={busy || !ready || !draft.name.trim()}>
          Generate reference
        </Button>
        {draft.referenceId && (
          <Button type="button" variant="outline" disabled={busy || !ready} onClick={() => generate('clip')}>
            Create idle animation
          </Button>
        )}
      </div>
      {references.length > 0 && (
        <FormField label="Character reference">
          {control => (
            <Select {...control} value={draft.referenceId} onChange={event => onDraft({referenceId: event.target.value})}>
              <option value="">Choose a reference</option>
              {references.map((row, index) => (
                <option key={row.id} value={row.artifact_id}>
                  {'Reference ' + (index + 1) + (row.created_at ? ' · ' + new Date(row.created_at * 1000).toLocaleString() : '')}
                </option>
              ))}
            </Select>
          )}
        </FormField>
      )}
      {draft.referenceId && <ReferenceArt id={draft.referenceId} />}
      {canRegenerate && pack && (
        <fieldset className="character-regenerate">
          <legend>Regenerate an animation for {pack.name}</legend>
          <FormField label="Animation to regenerate">
            {control => (
              <Select {...control} value={draft.regenerateClip} onChange={event => onDraft({regenerateClip: characterClips.find(value => value === event.target.value) ?? 'idle'})}>
                {characterClips.map(value => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </Select>
            )}
          </FormField>
          <Button type="button" variant="outline" disabled={busy || !ready} onClick={() => generate('clip', true)}>
            Regenerate {draft.regenerateClip}
          </Button>
        </fieldset>
      )}
    </form>
  );
}
