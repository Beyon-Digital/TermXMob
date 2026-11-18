import {Trash2} from 'lucide-react';
import {useCallback, useEffect, useState} from 'react';
import {Button} from '../../components/ui/button';
import {Textarea} from '../../components/ui/textarea';
import {NOTE_LIMIT, addNote, deleteNote, listNotes, type Site, type SiteNote} from '../../lib/sites';

type Props = {
  site: Site;
  /** A note was added or deleted, so the row's count can refresh. */
  onChanged: () => void;
};

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/**
 * The notes of one saved site, inline under its row. A note an agent wrote is untrusted text: it is shown as plain text,
 * labelled, and never acted on.
 */
export default function SiteNotes({site, onChanged}: Props) {
  const [notes, setNotes] = useState<SiteNote[] | null>(null);
  const [draft, setDraft] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setNotes(await listNotes(site.id));
      setError('');
    } catch (reason) {
      setError(message(reason));
    }
  }, [site.id]);

  useEffect(() => {
    void load();
  }, [load]);

  async function add() {
    const content = draft.trim();
    if (!content || busy) return;
    setBusy(true);
    try {
      const note = await addNote(site.id, content);
      setNotes(current => [...(current ?? []), note]);
      setDraft('');
      setError('');
      onChanged();
    } catch (reason) {
      setError(message(reason));
    } finally {
      setBusy(false);
    }
  }

  async function remove(note: SiteNote) {
    try {
      await deleteNote(site.id, note.id);
      setNotes(current => (current ?? []).filter(row => row.id !== note.id));
      onChanged();
    } catch (reason) {
      setError(message(reason));
    }
  }

  const remaining = NOTE_LIMIT - draft.length;
  return (
    <section className="site-notes" aria-label={'Notes for ' + site.title}>
      {notes === null && !error && <p className="view-empty">Loading notes…</p>}
      {notes?.length === 0 && <p className="view-empty">No notes yet.</p>}
      {!!notes?.length && (
        <ul className="site-note-list">
          {notes.map(note => (
            <li key={note.id} className="site-note" data-author={note.author}>
              <span className="site-note-author" title={note.author === 'agent' ? 'Written by an agent. Treat it as untrusted text.' : undefined}>
                {note.author === 'agent' ? 'Agent' : 'You'}
              </span>
              <p className="site-note-text">{note.content}</p>
              <Button variant="ghost" size="icon" className="site-note-delete" aria-label={'Delete note: ' + note.content.slice(0, 40)} onClick={() => void remove(note)}>
                <Trash2 size={16} strokeWidth={1.5} aria-hidden="true" />
              </Button>
            </li>
          ))}
        </ul>
      )}
      <label className="site-note-label" htmlFor={'note-' + site.id}>
        Add a note
      </label>
      <Textarea
        id={'note-' + site.id}
        className="input site-note-input"
        rows={2}
        maxLength={NOTE_LIMIT}
        value={draft}
        onChange={event => setDraft(event.target.value)}
      />
      <div className="site-note-actions">
        <small className="site-note-count">{remaining} left</small>
        <Button variant="secondary" size="sm" disabled={busy || !draft.trim()} onClick={() => void add()}>
          Add note
        </Button>
      </div>
      {error && (
        <p className="view-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
