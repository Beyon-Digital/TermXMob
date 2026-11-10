import {useSettingsRequest} from './SettingsSurface';
import { useRef, useState } from 'react';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import type { MemoryRow } from '../lib/host-types';

export type MemoryRecord = Pick<MemoryRow, 'id' | 'revision' | 'content' | 'provenance' | 'project_id' | 'retention_days' | 'excluded' | 'scope' | 'agent_id' | 'author' | 'state'>;

export type MemoryScope = NonNullable<MemoryRow['scope']>;

/** Rows saved before scopes existed map by project, as the host does. */
export const scopeOf = (memory: Pick<MemoryRow, 'scope' | 'project_id'>): MemoryScope => memory.scope || (memory.project_id ? 'project' : 'user');

export const appliesToLabel: Record<MemoryScope, string> = {
  user: 'All your projects', project: 'This project', agent: 'This agent only',
};

/** One label for a note's scope. `viewerAgentId` is the agent whose page lists it; omit it where no list is involved. */
export function appliesToText(memory: Pick<MemoryRow, 'scope' | 'project_id' | 'agent_id'>, viewerAgentId?: string | null): string {
  const scope = scopeOf(memory);
  return scope === 'agent' && viewerAgentId !== undefined && memory.agent_id !== viewerAgentId ? 'One agent' : appliesToLabel[scope];
}

/** The body the memory endpoint takes to save a note again. Scope, agent and project stay as saved. */
export function memoryPayload(memory: MemoryRecord, changes: Record<string, unknown> = {}) {
  return {
    identifier: memory.id, revision: memory.revision, content: memory.content, provenance: memory.provenance,
    project_id: memory.project_id || null, scope: memory.scope, agent_id: memory.agent_id || undefined,
    retention_days: memory.retention_days, excluded: Boolean(memory.excluded), ...changes,
  };
}

/** Keep the original revision, scope and agent until an explicit successful save. */
export function MemoryEditor({ memory, onSaved, onCancel }: {
  memory: MemoryRecord; onSaved: () => void; onCancel: () => void;
}) {
 const request=useSettingsRequest();
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
        body: JSON.stringify(memoryPayload(original, { content, provenance, retention_days: Number(retention) })) });
      onSaved();
    } catch (exception) {
      setError(`${exception instanceof Error ? exception.message : String(exception)}. Your draft is kept. If this memory changed, cancel and reopen its current version before saving.`);
    } finally { pending.current = false; setBusy(false); }
  }
  return <form className="manager-form" aria-label="Edit memory" onSubmit={save}>
    <p className="manager-help">Applies to: {appliesToText(original)}. Cannot be changed after saving. To change it, save a new note and delete this one.</p>
    <p className="manager-help">Revision {original.revision} · {original.excluded ? 'Excluded from execution' : 'Included in context'}. Saving restarts the selected retention period.{original.author === 'agent' && ' An agent wrote this note. Changing its text makes it yours.'}</p>
    <label className="manager-field"><span>Fact or decision</span><textarea aria-label="Fact or decision" required maxLength={64000} value={content} onChange={event => setContent(event.target.value)} /></label>
    <label className="manager-field"><span>Source or reason</span><Input required maxLength={1000} value={provenance} onChange={event => setProvenance(event.target.value)} /></label>
    <label className="manager-field"><span>Retention in days</span><Input required type="number" min={1} max={3650} step={1} value={retention} onChange={event => setRetention(event.target.value)} /></label>
    {error && <p role="alert" className="manager-error">{error}</p>}
    <div className="manager-item-actions"><Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>Cancel memory edit</Button><Button type="submit" disabled={busy || !valid}>{busy ? 'Saving memory…' : 'Save memory changes'}</Button></div>
  </form>;
}
