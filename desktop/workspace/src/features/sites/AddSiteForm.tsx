import {useEffect, useState, type FormEvent} from 'react';
import {Button} from '../../components/ui/button';
import {FormField} from '../../components/ui/form-field';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import {createProfile, defaultProfile, listProfiles, type BrowserProfile, type Site} from '../../lib/sites';

type Props = {
  /** The project the workspace is on; its own browser profile is the default one. */
  projectId: string;
  onAdd: (input: {profile_id: string; url: string; title?: string}) => Promise<Site | undefined>;
  onDone: () => void;
};

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/** Save a site by address. A second profile is offered only when there is more than one lasting profile. */
export default function AddSiteForm({projectId, onAdd, onDone}: Props) {
  const [profiles, setProfiles] = useState<BrowserProfile[] | null>(null);
  const [profileId, setProfileId] = useState('');
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    listProfiles()
      .then(rows => {
        if (!live) return;
        setProfiles(rows);
        setProfileId(defaultProfile(rows, projectId)?.id ?? '');
      })
      .catch(reason => live && setError(message(reason)));
    return () => {
      live = false;
    };
  }, [projectId]);

  const lasting = (profiles ?? []).filter(profile => !profile.ephemeral);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!url.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      let profile = profileId;
      // The first saved site on a host with no browser profile makes one, so Add site never dead-ends.
      if (!profile) profile = (await createProfile(projectId, 'Default')).id;
      const site = await onAdd({profile_id: profile, url: url.trim(), ...(title.trim() ? {title: title.trim()} : {})});
      if (site) onDone();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      className="site-add"
      aria-label="Add site"
      onSubmit={event => void submit(event)}
      onKeyDown={event => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onDone();
        }
      }}
    >
      <FormField label="Address" error={error} hint="Only http and https sites can be saved.">
        {control => <Input {...control} type="url" inputMode="url" autoFocus placeholder="https://example.com" value={url} onChange={event => setUrl(event.target.value)} />}
      </FormField>
      <FormField label="Title">
        {control => <Input {...control} value={title} maxLength={200} onChange={event => setTitle(event.target.value)} />}
      </FormField>
      {lasting.length > 1 && (
        <FormField label="Browser profile">
          {control => (
            <Select {...control} value={profileId} onChange={event => setProfileId(event.target.value)}>
              {lasting.map(profile => (
                <option key={profile.id} value={profile.id}>
                  {profile.name}
                </option>
              ))}
            </Select>
          )}
        </FormField>
      )}
      <div className="site-edit-actions">
        <Button type="submit" variant="secondary" size="sm" disabled={busy || profiles === null || !url.trim()}>
          Save site
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
