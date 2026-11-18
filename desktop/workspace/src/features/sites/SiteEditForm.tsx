import {useState, type FormEvent} from 'react';
import {Button} from '../../components/ui/button';
import {FormField} from '../../components/ui/form-field';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import type {Site} from '../../lib/sites';

type Props = {
  site: Site;
  onSave: (patch: Partial<Pick<Site, 'title' | 'open_mode'>>) => void | Promise<unknown>;
  onCancel: () => void;
};

/** Edit a saved site's title and how it opens, inline under its row. */
export default function SiteEditForm({site, onSave, onCancel}: Props) {
  const [title, setTitle] = useState(site.title);
  const [mode, setMode] = useState<Site['open_mode']>(site.open_mode);
  const [busy, setBusy] = useState(false);
  const changed = title.trim() !== site.title || mode !== site.open_mode;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!title.trim() || !changed) return;
    setBusy(true);
    const patch: Partial<Pick<Site, 'title' | 'open_mode'>> = {};
    if (title.trim() !== site.title) patch.title = title.trim();
    if (mode !== site.open_mode) patch.open_mode = mode;
    await onSave(patch);
    setBusy(false);
    onCancel();
  }

  return (
    <form
      className="site-edit"
      aria-label={'Edit ' + site.title}
      onSubmit={event => void submit(event)}
      onKeyDown={event => {
        if (event.key === 'Escape') {
          event.stopPropagation();
          onCancel();
        }
      }}
    >
      <FormField label="Title" error={title.trim() ? undefined : 'Enter a title.'}>
        {control => <Input {...control} value={title} maxLength={200} onChange={event => setTitle(event.target.value)} />}
      </FormField>
      <FormField label="Open as" hint="An app site reopens at its last page after the host restarts.">
        {control => (
          <Select {...control} value={mode} onChange={event => setMode(event.target.value as Site['open_mode'])}>
            <option value="tab">Tab</option>
            <option value="app">App</option>
          </Select>
        )}
      </FormField>
      <div className="site-edit-actions">
        <Button type="submit" variant="secondary" size="sm" disabled={busy || !changed || !title.trim()}>
          Save
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
