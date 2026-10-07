import { useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { request } from '../lib/api';

export interface MemoryRecord {
  id: string; revision: number; content: string; provenance: string;
  project_id?: string | null; retention_days: number; excluded?: boolean;
}

/** Keep the original revision and scope until an explicit successful save. */
export function MemoryEditor({ memory, onSaved, onCancel }: {
  memory: MemoryRecord; onSaved: () => void; onCancel: () => void;
}) {
  const [original] = useState(memory);
  const [content, setContent] = useState(memory.content);
  const [provenance, setProvenance] = useState(memory.provenance);
  const [retention, setRetention] = useState(String(memory.retention_days));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const valid = content.trim().length > 0 && provenance.trim().length > 0 &&
    Number.isInteger(Number(retention)) && Number(retention) >= 1 && Number(retention) <= 3650;
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (pending.current || !valid) return;
    pending.current = true; setBusy(true); setError('');
    try {
      await request('/api/workspace/memory', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identifier: original.id, revision: original.revision,
          project_id: original.project_id || null, excluded: Boolean(original.excluded),
          content, provenance, retention_days: Number(retention) }) });
      onSaved();
    } catch (exception) {
      setError(`${exception instanceof Error ? exception.message : String(exception)}. Your draft is kept. If this memory changed, cancel and reopen its current version before saving.`);
    } finally { pending.current = false; setBusy(false); }
  }
  return <form className="manager-form" aria-label="Edit memory" onSubmit={save}>
    <p className="manager-help">{original.project_id ? 'Project memory' : 'Account memory'} · revision {original.revision} · {original.excluded ? 'Excluded from execution' : 'Included in context'}. Saving restarts the selected retention period.</p>
    <label className="manager-field"><span>Fact or decision</span><textarea aria-label="Fact or decision" required maxLength={64000} value={content} onChange={event => setContent(event.target.value)} /></label>
    <label className="manager-field"><span>Source or reason</span><Input required maxLength={1000} value={provenance} onChange={event => setProvenance(event.target.value)} /></label>
    <label className="manager-field"><span>Retention in days</span><Input required type="number" min={1} max={3650} step={1} value={retention} onChange={event => setRetention(event.target.value)} /></label>
    {error && <p role="alert" className="manager-error">{error}</p>}
    <div className="manager-item-actions"><Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>Cancel memory edit</Button><Button type="submit" disabled={busy || !valid}>{busy ? 'Saving memory…' : 'Save memory changes'}</Button></div>
  </form>;
}
