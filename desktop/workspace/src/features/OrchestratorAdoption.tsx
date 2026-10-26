import {useEffect, useRef, useState} from 'react';
import {GitBranch} from 'lucide-react';
import {Button} from '../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../components/ui/dialog';
import {ApiError, json, request, type Session} from '../lib/api';
import './orchestrator-adoption.css';

type Enrollment = {enrolled: boolean; control_owner: 'user' | 'orchestrator' | null};
export type AdoptionPreview = {
  session_id: string; revision: number; review_digest: string; title: string;
  engine: string; cwd?: string | null; model?: string | null; mode: string;
  active_task_ids: string[]; control_owner: 'user' | 'orchestrator'; effect: string;
  enrollment: Enrollment;
};
type AdoptionResult = {ok: boolean; session_id: string; engine: string; control_owner: 'user' | 'orchestrator'; requires_user_handoff?: boolean};
type PendingAdoption = {preview: AdoptionPreview; requestId: string; state: 'ready' | 'sending' | 'uncertain'};
type AdoptionUpdate = {key: string; result?: AdoptionResult};
// Preserve the exact reviewed intent across dialog/panel replacement and retry.
// This registry contains no credentials and never submits work by itself.
const pendingAdoptions = new Map<string, PendingAdoption>();
if (typeof window !== 'undefined') window.addEventListener('termx-signed-out', () => pendingAdoptions.clear());
const notify = (update: AdoptionUpdate) => window.dispatchEvent(new CustomEvent<AdoptionUpdate>('termx-adoption-state', {detail: update}));

export default function OrchestratorAdoption({ownerId, session, onRefresh, enabled = true}: {ownerId: string; session: Session; onRefresh: () => void; enabled?: boolean}) {
  const key = JSON.stringify([ownerId, session.id]);
  const active = useRef(enabled); active.current = enabled;
  const identity = useRef(key), generation = useRef(0), alive = useRef(true), read = useRef<AbortController | null>(null);
  if (identity.current !== key) { identity.current = key; generation.current++; }
  const [viewKey, setViewKey] = useState(key), [open, setOpen] = useState(false), [loading, setLoading] = useState(false);
  const [review, setReview] = useState<AdoptionPreview | null>(null), [record, setRecord] = useState<PendingAdoption | null>(null);
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null), [error, setError] = useState(''), [needsReview, setNeedsReview] = useState(false);
  const fresh = (target: string, ticket: number) => alive.current && active.current && identity.current === target && generation.current === ticket;
  useEffect(() => { alive.current = true; return () => { alive.current = false; read.current?.abort(); }; }, []);
  useEffect(() => {
    read.current?.abort(); read.current = null;
    setViewKey(key); setOpen(false); setLoading(false); setReview(null); setRecord(pendingAdoptions.get(key) || null);
    setEnrollment(null); setError(''); setNeedsReview(false);
    const update = (event: Event) => {
      const detail = (event as CustomEvent<AdoptionUpdate>).detail;
      if (detail?.key !== key || identity.current !== key) return;
      setRecord(pendingAdoptions.get(key) ? {...pendingAdoptions.get(key)!} : null);
      if (detail.result) {
        // A replayed receipt describes its original result, not necessarily current ownership.
        setEnrollment({enrolled: true, control_owner: null});
        setReview(null); setError(''); setNeedsReview(false);
        if (active.current) void preview(false);
      }
    };
    window.addEventListener('termx-adoption-state', update);
    return () => { read.current?.abort(); window.removeEventListener('termx-adoption-state', update); };
  }, [key]);

  useEffect(() => { if (!enabled) { read.current?.abort(); setOpen(false); setLoading(false); } }, [enabled]);

  async function preview(showDialog = true) {
    const ticket = generation.current;
    if (!fresh(key, ticket)) return;
    const current = pendingAdoptions.get(key);
    if (showDialog) setOpen(true); setError('');
    if (current?.state === 'sending') { setRecord(current); setReview(current.preview); return; }
    read.current?.abort();
    const controller = new AbortController();
    read.current = controller; setLoading(true);
    try {
      const value = await request<AdoptionPreview>('/api/workspace/orchestrator/adoption-preview/' + encodeURIComponent(session.id), {signal: controller.signal});
      if (controller.signal.aborted || !fresh(key, ticket)) return;
      if (value.session_id !== session.id) throw Error('The host returned a review for another conversation. Review this chat again.');
      setReview(value); setEnrollment(value.enrollment); setNeedsReview(false);
      if (value.enrollment.enrolled) {
        pendingAdoptions.delete(key); setRecord(null);
      } else {
        const previous = pendingAdoptions.get(key);
        const same = previous && previous.preview.session_id === value.session_id && previous.preview.revision === value.revision && previous.preview.review_digest === value.review_digest;
        const next = same ? {...previous, preview: value} : {preview: value, requestId: crypto.randomUUID(), state: 'ready' as const};
        pendingAdoptions.set(key, next); setRecord(next);
      }
    } catch (reason) {
      if (!controller.signal.aborted && fresh(key, ticket)) { setReview(null); setError(reason instanceof Error ? reason.message : String(reason)); setNeedsReview(true); }
    } finally { if (read.current === controller) { read.current = null; if (fresh(key, ticket)) setLoading(false); } }
  }

  async function include() {
    const ticket = generation.current;
    if (!fresh(key, ticket)) return;
    const intent = pendingAdoptions.get(key);
    if (!intent || intent.state === 'sending' || !review || needsReview || review.enrollment.enrolled || intent.preview.review_digest !== review.review_digest || intent.preview.revision !== review.revision) return;
    intent.state = 'sending'; setRecord({...intent}); setError('');
    notify({key});
    try {
      const value = await request<AdoptionResult>('/api/workspace/orchestrator/adopt', json('POST', {
        session_id: intent.preview.session_id, expected_revision: intent.preview.revision,
        review_digest: intent.preview.review_digest, request_id: intent.requestId,
      }));
      if (value.session_id !== intent.preview.session_id || !value.ok || !['user', 'orchestrator'].includes(value.control_owner)) throw Error('The host did not confirm enrollment for this chat. Refresh its status before trying again.');
      if (pendingAdoptions.get(key) === intent) { pendingAdoptions.delete(key); notify({key, result: value}); }
      if (fresh(key, ticket)) onRefresh();
    } catch (reason) {
      if (pendingAdoptions.get(key) !== intent) return;
      if (reason instanceof ApiError && reason.status === 409) {
        pendingAdoptions.delete(key); notify({key});
        if (fresh(key, ticket)) {
          setRecord(null); setReview(null); setNeedsReview(true);
          setError(reason.message + ' Review the latest chat details before confirming again.');
        }
      } else {
        intent.state = 'uncertain'; notify({key});
        if (fresh(key, ticket)) setError((reason instanceof Error ? reason.message : String(reason)) + ' Enrollment could not be confirmed. Retry the same reviewed request or refresh its status.');
      }
    }
  }

  const sameView = viewKey === key, showing = sameView && open && enabled;
  const managed = sameView && enrollment?.enrolled;
  const busy = sameView && record?.state === 'sending';
  return <div className="orchestrator-adoption">
    {managed ? <p className="control-description" role="status">Included in main agent updates. Check this chat’s control banner for current ownership.</p> : <>
      <Button variant="outline" size="sm" disabled={!enabled} onClick={() => void preview()}><GitBranch size={14}/>{busy ? 'Enrollment pending' : 'Include in main agent updates'}</Button>
      <p className="control-description">Include progress and reports without changing control.</p>
    </>}
    <Dialog open={showing} onOpenChange={setOpen}><DialogContent className="adoption-review-dialog">
      <DialogTitle>Include this chat in main agent updates?</DialogTitle>
      <DialogDescription>Review the exact conversation. Enrollment does not send a message, start a task, or give the main agent control.</DialogDescription>
      {loading && <p role="status">Reading current chat details…</p>}
      {review && <>
        <dl className="adoption-review-target"><dt>Chat</dt><dd>{review.title || 'Untitled conversation'}</dd><dt>Engine</dt><dd>{review.engine}</dd><dt>Folder</dt><dd>{review.cwd || 'No folder selected'}</dd><dt>Model</dt><dd>{review.model || 'Engine default'}</dd><dt>Mode</dt><dd>{review.mode}</dd><dt>Active tasks</dt><dd>{review.active_task_ids.length}</dd></dl>
        <p>{review.effect}</p>
      </>}
      {managed && review && <p role="status">This chat is already included. {review.enrollment.control_owner === 'user' ? 'At review time, you were in control.' : review.enrollment.control_owner === 'orchestrator' ? 'At review time, the main agent had control.' : 'Check the chat’s control banner for current ownership.'}</p>}
      {error && <p className="inline-error" role="alert">{error}</p>}
      {busy && <p role="status">Confirming enrollment… Closing this dialog does not send another request.</p>}
      <div className="adoption-review-actions">
        <Button variant="ghost" onClick={() => setOpen(false)}>{managed ? 'Done' : 'Cancel'}</Button>
        {!managed && (needsReview ? <Button variant="outline" disabled={loading || !!busy} onClick={() => void preview()}>Review latest details</Button> : <>
          {record?.state === 'uncertain' && <Button variant="outline" disabled={loading || !!busy} onClick={() => void preview()}>Refresh enrollment status</Button>}
          <Button disabled={!review || loading || !!busy} onClick={() => void include()}>{record?.state === 'uncertain' ? 'Retry same enrollment' : 'Include this chat'}</Button>
        </>)}
      </div>
    </DialogContent></Dialog>
  </div>;
}
