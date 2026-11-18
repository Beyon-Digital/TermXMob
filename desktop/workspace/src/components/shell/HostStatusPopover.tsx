import {TriangleAlert} from 'lucide-react';
import type {HostHealth} from '../../features/host/use-host-status';
import {heartbeatAge, holderSummary, recoveryAttention, serviceLabel, uptimeLabel} from '../../lib/host-status';
import {Button} from '../ui/button';
import './host-status.css';

type Props = {
  health: HostHealth;
  hostName: string;
  /** Show the Agents view, where lost and resumable CLI sessions are listed. */
  onOpenAgents: () => void;
};

const count = (value: number, one: string, many: string) => value + ' ' + (value === 1 ? one : many);

/**
 * The host's health, read only: uptime, the service, who owns the terminals (and why, when the host fell back to direct
 * mode), work being recovered, keep-awake and the heartbeat. Settings for durability live in Settings, not here.
 */
export default function HostStatusPopover({health, hostName, onOpenAgents}: Props) {
  const {status} = health;
  if (!status) return <p className="host-note">Waiting for the host to report its status.</p>;
  const holder = holderSummary(status.holder);
  const keep = status.keep_awake;
  const age = heartbeatAge(status);
  const recovery = status.recovery;
  const work = recoveryAttention(status);
  return (
    <div className="host-status" role="group" aria-label="Host status">
      <p className="host-title">
        <strong>{hostName || 'This host'}</strong>
        <small>TermX {status.version}</small>
      </p>
      {health.disconnected && (
        <p className="host-alert" role="alert">
          <TriangleAlert size={16} strokeWidth={1.5} aria-hidden="true" />
          No answer from the host for {health.silentFor} seconds. Still retrying.
        </p>
      )}
      <dl className="host-facts">
        <dt>Uptime</dt>
        <dd>{uptimeLabel(status.uptime_s)}</dd>
        <dt>Service</dt>
        <dd>{serviceLabel(status.service)}</dd>
        <dt>Terminals</dt>
        <dd>
          {holder.text}
          {holder.direct && (
            <span className="host-alert host-reason">
              <TriangleAlert size={16} strokeWidth={1.5} aria-hidden="true" />
              Reason: {holder.reason}
            </span>
          )}
          {status.holder.direct_terminals > 0 && <span className="host-sub">{count(status.holder.direct_terminals, 'direct terminal', 'direct terminals')}</span>}
        </dd>
        <dt>Recovery</dt>
        <dd>
          {count(recovery.recovering_tasks, 'task recovering', 'tasks recovering')}
          <span className="host-sub">
            {count(recovery.lost_cli_sessions, 'CLI session lost', 'CLI sessions lost')}, {recovery.resumable_cli_sessions} resumable
          </span>
        </dd>
        <dt>Keep awake</dt>
        <dd>
          {keep.supported ? (keep.active ? 'On' : keep.enabled ? 'Requested, not held' : 'Off') : 'Not supported on this system'}
          {keep.reasons.length > 0 && <span className="host-sub">{keep.reasons.join(', ')}</span>}
          {keep.error && <span className="host-sub">{keep.error}</span>}
        </dd>
        <dt>Heartbeat</dt>
        <dd>{status.heartbeat.error ? status.heartbeat.error : age === null ? 'Not written yet' : age + ' s ago'}</dd>
      </dl>
      {work > 0 && (
        <Button variant="ghost" size="sm" className="host-link" onClick={onOpenAgents}>
          Open CLI sessions
        </Button>
      )}
    </div>
  );
}
