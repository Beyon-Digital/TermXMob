import {gql} from './api';

/** GraphQL `HostStatus` (snake_case, scope `machine-view`; a managed principal also needs `host-admin`). */
export interface HostStatus {
  version: string;
  pid: number;
  started_at: number;
  uptime_s: number;
  service: {backend: string; supported: boolean; installed: boolean; managed: boolean; state: string};
  holder: {
    mode: string;
    requested: string;
    reason: string | null;
    running: boolean;
    pid: number | null;
    version: string | null;
    protocol: number | null;
    sessions: number;
    exited_sessions: number;
    draining: boolean;
    direct_terminals: number;
  };
  recovery: {
    recovering_tasks: number;
    running_tasks: number;
    lost_cli_sessions: number;
    resumable_cli_sessions: number;
    live_cli_sessions: number;
    lost_engine_sessions: number;
    resumable_engine_sessions: number;
  };
  keep_awake: {enabled: boolean; active: boolean; supported: boolean; backend: string | null; reasons: string[]; count: number; error: string | null};
  heartbeat: {at: number | null; age_s: number | null; interval_s: number; file: string; error: string | null};
}

const FIELDS =
  'version pid started_at uptime_s ' +
  'service{backend supported installed managed state} ' +
  'holder{mode requested reason running pid version protocol sessions exited_sessions draining direct_terminals} ' +
  'recovery{recovering_tasks running_tasks lost_cli_sessions resumable_cli_sessions live_cli_sessions lost_engine_sessions resumable_engine_sessions} ' +
  'keep_awake{enabled active supported backend reasons count error} ' +
  'heartbeat{at age_s interval_s file error}';

export const HOST_STATUS_QUERY = '{host_status{' + FIELDS + '}}';
export const HOST_STATUS_EVENTS_QUERY = 'subscription{host_status_events{' + FIELDS + '}}';

export async function loadHostStatus(): Promise<HostStatus> {
  return (await gql<{host_status: HostStatus}>(HOST_STATUS_QUERY)).host_status;
}

/** A host that has not answered for this long is shown as disconnected. */
export const SILENCE_LIMIT_S = 30;

/** "3 h 12 min", "4 min", "Under a minute". */
export function uptimeLabel(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return days + ' d ' + hours + ' h';
  if (hours > 0) return hours + ' h ' + minutes + ' min';
  return minutes > 0 ? minutes + ' min' : 'Under a minute';
}

/** The terminals' owner in words. A downgrade from the holder to direct mode is never hidden: the reason follows it. */
export function holderSummary(holder: HostStatus['holder']): {text: string; direct: boolean; reason: string} {
  if (holder.mode === 'direct') {
    const requested = holder.requested && holder.requested !== 'direct' ? ' (' + holder.requested + ' was requested)' : '';
    return {
      text: 'Direct mode' + requested + ': terminals end when the host stops.',
      direct: true,
      reason: holder.reason || 'No reason was reported.',
    };
  }
  const sessions = holder.sessions === 1 ? '1 terminal' : holder.sessions + ' terminals';
  return {text: 'Held by the PTY holder: ' + sessions + ' survive a host restart.', direct: false, reason: ''};
}

/** Seconds since the host's heartbeat file was written, or `null` when there is no heartbeat yet. */
export const heartbeatAge = (status: HostStatus) => (status.heartbeat.age_s === null || status.heartbeat.age_s === undefined ? null : Math.max(0, Math.round(status.heartbeat.age_s)));

/** The service in words: the supervisor and what it reports. */
export function serviceLabel(service: HostStatus['service']): string {
  if (!service.supported) return 'No service manager on this system';
  if (!service.installed) return 'Not installed as a service';
  return service.backend + ': ' + (service.state || 'unknown');
}

/** Lost or resumable work that needs a look. Feeds the popover's link to the Agents view. */
export const recoveryAttention = (status: HostStatus) =>
  status.recovery.lost_cli_sessions + status.recovery.resumable_cli_sessions + status.recovery.recovering_tasks;
