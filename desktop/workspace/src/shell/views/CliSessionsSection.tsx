import {Plus} from 'lucide-react';
import {Button} from '../../components/ui/button';
import CliStatusBadge from '../../features/cli/CliStatusBadge';
import {useCliSessions} from '../../features/cli/CliSessionsProvider';
import {needsYouCount} from '../../lib/bot-schedules';
import {sessionName} from '../../lib/cli-sessions';
import '../../features/cli/cli.css';

type Props = {
  onNew: () => void;
  onOpen: (sessionId: string) => void;
};

/**
 * CLI sessions in the Agents view: each running or finished `claude`, `codex` or other CLI on this host, the ones that need
 * the owner first. A row opens the session's tab (the session keeps running without one). The count in the heading is the
 * sessions waiting on a person.
 */
export default function CliSessionsSection({onNew, onOpen}: Props) {
  const cli = useCliSessions();
  if (!cli.available) return null;
  return (
    <section className="view-section" aria-labelledby="agents-cli" id="agents-cli-section">
      <h3 id="agents-cli" className="view-heading">
        CLI sessions
        {cli.attention > 0 && <span className="view-count">{needsYouCount(cli.attention)}</span>}
      </h3>
      {cli.error && (
        <p className="view-error" role="alert">
          {cli.error}
        </p>
      )}
      {cli.loaded && cli.sessions.length === 0 && (
        <p className="view-empty">Claude Code, Codex and other CLI sessions on this host will appear here.</p>
      )}
      {cli.sessions.length > 0 && (
        <ul className="view-list cli-list" aria-label="CLI sessions">
          {cli.sessions.map(session => (
            <li key={session.id}>
              <button type="button" className="view-row cli-row" data-attention={session.attention || undefined} onClick={() => onOpen(session.id)}>
                <span className="cli-row-text">
                  <span className="view-row-title">{sessionName(session)}</span>
                  <small className="cli-row-place">{cli.placeOf(session)}</small>
                </span>
                <CliStatusBadge status={session.status} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <Button variant="ghost" size="sm" className="view-link" onClick={onNew}>
        <Plus size={16} strokeWidth={1.5} aria-hidden="true" />
        New CLI session
      </Button>
    </section>
  );
}
