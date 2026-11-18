import {CircleStop, Power, RotateCcw, SquareTerminal} from 'lucide-react';
import {useCallback, useRef, useState} from 'react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {canResume, isEnded, sessionName, type CliSession} from '../../lib/cli-sessions';
import TerminalSession, {type SessionHandle, type SessionStatus} from '../terminal/TerminalSession';
import '../terminal/terminal.css';
import CliStatusBadge from './CliStatusBadge';
import {useCliSessions} from './CliSessionsProvider';
import './cli.css';

type Props = {
  /** The CLI session this tab shows. */
  sessionId: string;
  /** The layout tab's DOM id, so Shift+Tab can return to it. */
  tabDomId: string;
  visible: boolean;
  ownerId: string;
  locked: boolean;
  /** Close the layout tab. The session itself keeps running. */
  onClose: () => void;
  /** Show the tab of another session (a resumed one). */
  onOpenSession: (id: string) => void;
  onError: (reason: unknown) => void;
};

type Confirm = 'stop' | 'resume' | null;

const message = (reason: unknown) => (reason instanceof Error ? reason.message : String(reason));

/** What the host recorded about how an ended session ended, in words. */
function endNote(session: CliSession): string {
  const reason = session.ended_reason ? session.ended_reason.replace(/_/g, ' ') : 'ended';
  const code = session.exit_code === null || session.exit_code === undefined ? '' : ' with exit code ' + session.exit_code;
  return session.status === 'lost' ? 'This session was lost: ' + reason + '.' : 'This session ' + reason + code + '.';
}

/**
 * One CLI session in one tab: a slim header (status, interrupt, stop, resume) over the CLI's own terminal. The terminal is the
 * same xterm and PTY socket every shell uses; typing in it is how input reaches the CLI. Closing the tab never stops the
 * session: it keeps running on the host and the Agents view reopens it. Stop and Resume ask first.
 */
export default function CliTab({sessionId, tabDomId, visible, ownerId, locked, onClose, onOpenSession, onError}: Props) {
  const cli = useCliSessions();
  const session = cli.get(sessionId);
  const handle = useRef<SessionHandle | null>(null);
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState('');
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const leave = useCallback(() => document.getElementById(tabDomId)?.focus(), [tabDomId]);

  if (!session) {
    return (
      <section className="terminal-region cli-tab" aria-label="CLI session">
        <div className="tab-empty" role="status">
          <p>{cli.loaded ? 'This CLI session is not on this host.' : 'Loading the CLI session…'}</p>
          <Button variant="outline" onClick={onClose}>
            Close tab
          </Button>
        </div>
      </section>
    );
  }

  const ended = isEnded(session);
  const live = !ended && !!session.pty_session_id;
  const place = cli.placeOf(session);

  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setFailure('');
    try {
      await work();
    } catch (reason) {
      setFailure(message(reason));
      onError(reason);
    } finally {
      setBusy(false);
      setConfirm(null);
    }
  }

  const stop = () => act(() => cli.stop(session.id));
  const resume = () =>
    act(async () => {
      const next = await cli.resume(session.id);
      onOpenSession(next.id);
    });

  return (
    <section
      className="terminal-region cli-tab"
      aria-label={'CLI session: ' + sessionName(session)}
      data-connected-session={!locked && status?.connected && session.pty_session_id ? session.pty_session_id : ''}
    >
      <header className="cli-header">
        <div className="cli-header-main" role="status" aria-live="polite">
          <CliStatusBadge status={session.status} />
          <span className="cli-header-detail">{session.status_detail || ''}</span>
        </div>
        <span className="cli-header-name" title={session.cwd ?? undefined}>
          {sessionName(session)}
          {place ? <small>{place}</small> : null}
        </span>
        {live && (
          <>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void act(() => cli.interrupt(session.id))}>
              <CircleStop size={16} strokeWidth={1.5} aria-hidden="true" />
              Interrupt
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirm('stop')}>
              <Power size={16} strokeWidth={1.5} aria-hidden="true" />
              Stop
            </Button>
          </>
        )}
        {canResume(session) && (
          <Button variant="secondary" size="sm" disabled={busy} onClick={() => setConfirm('resume')}>
            <RotateCcw size={16} strokeWidth={1.5} aria-hidden="true" />
            Resume
          </Button>
        )}
      </header>
      {live && session.status === 'needs_approval' && (
        <p className="cli-hint" role="status">
          <span>This session is waiting for a decision in its terminal, for example a folder trust prompt. TermX never answers for you.</span>
          <Button variant="ghost" size="sm" onClick={() => handle.current?.focus()}>
            <SquareTerminal size={16} strokeWidth={1.5} aria-hidden="true" />
            Focus terminal
          </Button>
        </p>
      )}
      {failure && (
        <p className="cli-error cli-error-bar" role="alert">
          {failure}
        </p>
      )}
      {live ? (
        <div className="terminal-panes">
          <div className="terminal-pane">
            <TerminalSession
              ref={next => {
                handle.current = next;
              }}
              id={session.pty_session_id!}
              active={visible}
              authorized={!locked}
              transportKey={JSON.stringify([session.id, ownerId])}
              onStatus={(_id, next) => setStatus(next)}
              onError={onError}
              onLeave={leave}
              onFind={() => {}}
              onResults={() => {}}
            />
          </div>
        </div>
      ) : (
        <div className="tab-empty" role="status">
          <p>{ended ? endNote(session) : 'This session has no terminal yet.'}</p>
          <Button variant="outline" onClick={onClose}>
            Close tab
          </Button>
        </div>
      )}
      <Dialog open={confirm !== null} onOpenChange={open => !open && setConfirm(null)}>
        <DialogContent className="cli-dialog" aria-describedby="cli-confirm-description">
          <DialogTitle>{confirm === 'stop' ? 'Stop this session?' : 'Resume this session?'}</DialogTitle>
          <DialogDescription id="cli-confirm-description">
            {confirm === 'stop'
              ? 'The CLI is interrupted and ended. Work it has not saved is lost.'
              : 'A new terminal continues the CLI’s own saved conversation. Running commands and the screen are not restored, and the CLI may ask to trust the folder again.'}
          </DialogDescription>
          <div className="cli-form-actions">
            <Button variant="ghost" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            {confirm === 'stop' ? (
              <Button variant="destructive" disabled={busy} onClick={() => void stop()}>
                Stop session
              </Button>
            ) : (
              <Button disabled={busy} onClick={() => void resume()}>
                Resume session
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </section>
  );
}
