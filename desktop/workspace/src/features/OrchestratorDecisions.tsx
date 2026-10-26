import {useEffect, useRef, useState} from 'react';
import {Button} from '../components/ui/button';
import {request, type Task} from '../lib/api';
import BudgetApproval from './BudgetApproval';
import {GeneralApprovalCard} from './GeneralApprovalCard';
import type {OrchestratorChild} from './OrchestratorStatus';
import './orchestrator-decisions.css';

const MAX_SOURCES = 8;
type Source = {key: string; child: OrchestratorChild; task: Task};
type Receipt = {owner: string; epoch: number; sources: Source[]; unavailable: string[]; complete: string};
type Props = {ownerId: string; children: OrchestratorChild[]; enabled: boolean; onRefresh: () => void};
const sourceKey = (child: OrchestratorChild) => JSON.stringify([
  child.session_id, child.task_id, child.revision, child.updated_at, child.pending_approvals,
]);

/** Human decisions resolve the original request without transferring chat control. */
export default function OrchestratorDecisions({ownerId, children, enabled, onRefresh}: Props) {
  const candidates = children.filter(child => child.task_id && (child.pending_approvals || 0) > 0);
  const sources = candidates.slice(0, MAX_SOURCES);
  const [retry, setRetry] = useState(0), [received, setReceived] = useState<Receipt | null>(null);
  const [decisionError, setDecisionError] = useState<{owner: string; epoch: number; message: string} | null>(null);
  const scope = JSON.stringify([ownerId, enabled]), identity = useRef({scope, epoch: 0});
  if (identity.current.scope !== scope) identity.current = {scope, epoch: identity.current.epoch + 1};
  const epoch = identity.current.epoch;
  const key = JSON.stringify([scope, epoch, retry, sources.map(sourceKey)]);
  const current = useRef({key, generation: 0});
  if (current.current.key !== key) current.current = {key, generation: current.current.generation + 1};

  useEffect(() => {
    if (!enabled || !sources.length) return;
    const controller = new AbortController(), generation = current.current.generation;
    const fresh = () => !controller.signal.aborted && current.current.key === key && current.current.generation === generation;
    const visibleKeys = new Set(sources.map(sourceKey));
    const update = (mutate: (receipt: Receipt) => Receipt) => {
      if (!fresh()) return;
      setReceived(previous => mutate(previous?.owner === ownerId && previous.epoch === epoch
        ? {...previous, sources: previous.sources.filter(source => visibleKeys.has(source.key)),
          unavailable: previous.unavailable.filter(source => visibleKeys.has(source))}
        : {owner: ownerId, epoch, sources: [], unavailable: [], complete: ''}));
    };
    // Coordinator snapshots supply source revisions. There is no timer and at
    // most eight sources are read, one at a time. Other agents changing state
    // must not close an unchanged source's human review dialog.
    void (async () => {
      for (const child of sources) {
        if (!fresh()) return;
        const source = sourceKey(child);
        try {
          const task = await request<Task>('/api/workspace/tasks/' + encodeURIComponent(child.task_id!) + '?activity=false', {signal: controller.signal});
          if (!fresh()) return;
          if (task.id !== child.task_id) throw Error('The host returned a different source task.');
          update(previous => ({...previous,
            sources: [...previous.sources.filter(value => value.key !== source),
              ...(task.approvals?.some(approval => approval.status === 'pending') ? [{key: source, child, task}] : [])],
            unavailable: previous.unavailable.filter(value => value !== source)}));
        } catch {
          if (!fresh()) return;
          // A source read reauthorizes project access. A failed read immediately
          // removes its former payload while preserving other current reviews.
          update(previous => ({...previous, sources: previous.sources.filter(value => value.key !== source),
            unavailable: [...previous.unavailable.filter(value => value !== source), source]}));
        }
      }
      update(previous => ({...previous, complete: key}));
    })();
    return () => controller.abort();
  }, [key]);

  // Hide previous accounts, hidden windows and changed source revisions during
  // render, before effects or delayed responses can publish their content.
  if (!enabled || !sources.length) return null;
  const receipt = received?.owner === ownerId && received.epoch === epoch ? received : null;
  const keys = new Set(sources.map(sourceKey));
  const currentSources = (receipt?.sources || []).filter(source => keys.has(source.key));
  const unavailable = (receipt?.unavailable || []).filter(source => keys.has(source)).length;
  const ready = receipt?.complete === key;
  const error = decisionError?.owner === ownerId && decisionError.epoch === epoch ? decisionError.message : '';
  const refresh = () => {setDecisionError(null); setRetry(value => value + 1); onRefresh();};
  const decided = (source: string, confirmed: boolean) => {
    setReceived(previous => previous && ({...previous, sources: previous.sources.filter(value => value.key !== source)}));
    if (!confirmed) setDecisionError({owner: ownerId, epoch,
      message: 'The host did not confirm that decision. Check the current request before trying again.'});
    setRetry(value => value + 1); onRefresh();
  };
  return <section className="orchestrator-decisions" aria-label="Decisions for your agents">
    <header><h3>Decisions for your agents</h3><p>Review the source request here. Your main agent keeps coordinating.</p></header>
    {!ready && <p role="status">Loading current requests…</p>}
    {unavailable > 0 && <div role="alert"><p>Some requests are unavailable. Refresh to check the host and your current access.</p><Button size="sm" variant="outline" onClick={refresh}>Refresh requests</Button></div>}
    {error && <p role="alert">{error}</p>}
    {ready && !currentSources.length && !unavailable && <p role="status">These requests have been resolved. Refreshing agent status…</p>}
    {currentSources.map(({key: source, child, task}) => <article key={task.id} className="orchestrator-decision-source" aria-label={'Requests from ' + child.title}>
      <div className="orchestrator-decision-heading"><strong>{child.title || 'Delegated agent'}</strong><span>{child.control_owner === 'user' ? 'You are directing this chat' : 'Main agent is coordinating'}</span></div>
      {(task.approvals || []).filter(approval => approval.status === 'pending').map(approval => approval.kind === 'budget'
        ? <BudgetApproval key={approval.id} task={task} approval={approval} onDone={() => decided(source, true)} onError={() => decided(source, false)}/>
        : <GeneralApprovalCard key={approval.id} taskId={task.id} approval={approval} onDone={() => decided(source, true)} onError={() => decided(source, false)}/>)}
    </article>)}
    {candidates.length > MAX_SOURCES && <p>Showing the first {MAX_SOURCES} agents needing a decision. Further requests appear as these are resolved.</p>}
  </section>;
}
