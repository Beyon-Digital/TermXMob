import {ChevronDown, ChevronUp, CircleStop, Search, Trash2, X} from 'lucide-react';
import {useCallback, useEffect, useRef, useState} from 'react';
import {Button} from '../../components/ui/button';
import type {Project, Session} from '../../lib/api';
import {readTerminalSelection} from '../../lib/terminal-selection';
import {recalledTerminal} from '../../lib/window-state';
import TerminalSession, {type SessionHandle, type SessionStatus} from './TerminalSession';
import {createPty, deletePty, listPtys} from './terminal-api';
import './terminal.css';

type Props = {
  /** The shell this tab shows (its PTY session id). `undefined` is a tab that has not attached to one yet. */
  ptyId: string | undefined;
  /** The tab's DOM id, so Shift+Tab can return to it. */
  tabDomId: string;
  visible: boolean;
  ownerId: string;
  project: Project | null;
  session: Session | null;
  /** Shells other terminal tabs already show; an unattached tab never takes one of them. */
  taken: ReadonlySet<string>;
  onAttach: (id: string) => void;
  /** The shell ended (or was ended here): close the tab. */
  onEnded: () => void;
  onError: (reason: unknown) => void;
};

type Phase = 'checking' | 'ready' | 'gone';

/**
 * One shell in one tab: the content of a `terminal` tab in the new layout. It replaces the old panel's own tab strip, because
 * the layout's tab strip now carries one tab per shell. The PTY socket, theme and keys are `TerminalSession`'s, unchanged.
 */
export default function TerminalTab(props: Props) {
  const {ptyId, project, session, ownerId} = props;
  const [phase, setPhase] = useState<Phase>('checking');
  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [locked, setLocked] = useState(false);
  const [epoch, setEpoch] = useState(0);
  const [busy, setBusy] = useState(false);
  const [finding, setFinding] = useState(false);
  const [term, setTerm] = useState('');
  const [matches, setMatches] = useState<{index: number; count: number} | null>(null);
  const handle = useRef<SessionHandle | null>(null);
  const findInput = useRef<HTMLInputElement>(null);
  const paused = useRef(false);
  const executionRoot = session?.cwd || project?.path || '';

  useEffect(() => {
    const lock = () => {
      paused.current = true;
      setLocked(true);
      setEpoch(value => value + 1);
    };
    const unlock = () => {
      paused.current = false;
      setLocked(false);
      setEpoch(value => value + 1);
    };
    window.addEventListener('termx-locked', lock);
    window.addEventListener('termx-session-unlocked', unlock);
    return () => {
      window.removeEventListener('termx-locked', lock);
      window.removeEventListener('termx-session-unlocked', unlock);
    };
  }, []);

  // The host's inventory decides whether this tab may attach: nothing connects until it has answered for this checkout.
  useEffect(() => {
    if (paused.current || !project) return;
    let stopped = false;
    setPhase('checking');
    listPtys(project, executionRoot)
      .then(rows => {
        if (stopped || paused.current) return;
        if (ptyId) {
          setPhase(rows.some(row => row.id === ptyId) ? 'ready' : 'gone');
          return;
        }
        const remembered = session ? recalledTerminal(session.id) || readTerminalSelection(ownerId, session.id) : '';
        const free = rows.find(row => row.id === remembered && !props.taken.has(row.id)) ?? rows.find(row => !props.taken.has(row.id));
        if (free) props.onAttach(free.id);
        else setPhase('ready');
      })
      .catch(reason => {
        if (!stopped && !paused.current) props.onError(reason);
      });
    return () => {
      stopped = true;
    };
  }, [ptyId, project?.id, project?.path, executionRoot, session?.id, ownerId, epoch]);

  async function create() {
    if (!project || busy || paused.current) return;
    setBusy(true);
    try {
      props.onAttach((await createPty(project, session?.id)).id);
    } catch (reason) {
      props.onError(reason);
    } finally {
      setBusy(false);
    }
  }

  async function end() {
    if (!ptyId || busy) return;
    setBusy(true);
    try {
      await deletePty(ptyId);
      props.onEnded();
    } catch (reason) {
      props.onError(reason);
      setBusy(false);
    }
  }

  const onStatus = useCallback((_id: string, next: SessionStatus) => setStatus(next), []);
  const onResults = useCallback((_id: string, index: number, count: number) => setMatches({index, count}), []);
  const leave = useCallback(() => document.getElementById(props.tabDomId)?.focus(), [props.tabDomId]);
  const openFind = useCallback(() => {
    setFinding(true);
    requestAnimationFrame(() => {
      findInput.current?.focus();
      findInput.current?.select();
    });
  }, []);

  function closeFind() {
    handle.current?.clearSearch();
    setFinding(false);
    setMatches(null);
    handle.current?.focus();
  }
  function runFind(value: string, backwards = false) {
    if (!handle.current) return;
    if (!value) {
      handle.current.clearSearch();
      setMatches(null);
      return;
    }
    const found = backwards ? handle.current.findPrevious(value) : handle.current.findNext(value);
    if (!found) setMatches({index: -1, count: 0});
  }

  const label = locked ? 'Locked' : (status?.state ?? 'Disconnected');
  const result = !term
    ? ''
    : matches && matches.count === 0
      ? 'No matches'
      : matches && matches.index >= 0
        ? matches.index + 1 + ' of ' + (matches.count >= 1000 ? '1000+' : matches.count)
        : '';

  if (!project) {
    return (
      <section className="terminal-region" aria-label="Terminal">
        <p className="terminal-empty">Terminals run inside a project folder. Add or open a project to start one.</p>
      </section>
    );
  }
  if (phase === 'gone') {
    return (
      <section className="terminal-region" aria-label="Terminal">
        <div className="tab-empty" role="status">
          <p>This shell has ended.</p>
          <Button variant="outline" onClick={props.onEnded}>
            Close tab
          </Button>
        </div>
      </section>
    );
  }
  if (!ptyId) {
    return (
      <section className="terminal-region" aria-label="Terminal">
        <div className="tab-empty" role="status">
          <p>{phase === 'checking' ? 'Looking for running terminals…' : 'No terminal is running here.'}</p>
          <Button variant="outline" disabled={busy || phase === 'checking'} onClick={() => void create()}>
            New terminal
          </Button>
        </div>
      </section>
    );
  }
  return (
    <section className="terminal-region" aria-label="Terminal" data-connected-session={!locked && status?.connected ? ptyId : ''}>
      <div className="terminal-toolbar">
        <small className="terminal-status" aria-live="polite">
          {label}
        </small>
        <span className="flex-spacer" />
        <Button variant="ghost" size="icon" aria-label="Find in terminal" title="Find in terminal" onClick={openFind}>
          <Search size={16} strokeWidth={1.5} aria-hidden="true" />
        </Button>
        <Button className="terminal-interrupt" variant="ghost" size="sm" aria-label="Interrupt" title="Send Control-C to the running command" onClick={() => handle.current?.interrupt()}>
          <CircleStop size={16} strokeWidth={1.5} aria-hidden="true" />
          <span className="terminal-interrupt-label">Interrupt</span>
        </Button>
        <Button variant="ghost" size="icon" aria-label="Close terminal" title="Close this terminal and end its shell" disabled={busy} onClick={() => void end()}>
          <Trash2 size={16} strokeWidth={1.5} aria-hidden="true" />
        </Button>
      </div>
      {finding && (
        <div className="terminal-find" role="search" aria-label="Find in terminal">
          <input
            ref={findInput}
            className="terminal-find-input"
            aria-label="Find in terminal"
            placeholder="Find"
            value={term}
            onChange={event => {
              setTerm(event.target.value);
              runFind(event.target.value);
            }}
            onKeyDown={event => {
              if (event.key === 'Enter') {
                event.preventDefault();
                runFind(term, event.shiftKey);
              } else if (event.key === 'Escape') {
                event.preventDefault();
                event.stopPropagation();
                closeFind();
              }
            }}
          />
          <span className="terminal-find-count" aria-live="polite">
            {result}
          </span>
          <Button variant="ghost" size="icon" aria-label="Previous match" title="Previous match" disabled={!term} onClick={() => runFind(term, true)}>
            <ChevronUp size={16} strokeWidth={1.5} aria-hidden="true" />
          </Button>
          <Button variant="ghost" size="icon" aria-label="Next match" title="Next match" disabled={!term} onClick={() => runFind(term)}>
            <ChevronDown size={16} strokeWidth={1.5} aria-hidden="true" />
          </Button>
          <Button variant="ghost" size="icon" aria-label="Close find" title="Close find" onClick={closeFind}>
            <X size={16} strokeWidth={1.5} aria-hidden="true" />
          </Button>
        </div>
      )}
      <div className="terminal-panes">
        <div className="terminal-pane">
          <TerminalSession
            ref={next => {
              handle.current = next;
            }}
            id={ptyId}
            active={props.visible}
            authorized={!locked && phase === 'ready'}
            transportKey={JSON.stringify([project.id, executionRoot, session?.id, ownerId]) + ':' + epoch}
            onStatus={onStatus}
            onError={props.onError}
            onLeave={leave}
            onFind={openFind}
            onResults={onResults}
          />
        </div>
      </div>
    </section>
  );
}
