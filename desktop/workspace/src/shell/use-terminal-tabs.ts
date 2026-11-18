import {useCallback, useEffect, useRef, useState} from 'react';
import {createPty} from '../features/terminal/terminal-api';
import type {Project, Session} from '../lib/api';
import type {LayoutAction} from '../lib/layout/reduce';
import {NEW_TERMINAL_EVENT, takeNewTerminalRequest} from '../lib/terminal-requests';
import {rememberTerminal} from '../lib/window-state';

type Options = {
  dispatch: (action: LayoutAction) => void;
  project: Project | null;
  session: Session | null;
  onError: (reason: unknown) => void;
};

/**
 * Starting shells for the tab layout: one terminal tab per shell. A new terminal (the `+` of the bottom panel, the New terminal
 * command, Control+Shift+backtick) starts a shell in the conversation's checkout and opens a tab for it; a shell handed over by
 * another window (redock) gets a tab too. The shell itself, and what a tab does with it, is `TerminalTab`.
 */
export function useTerminalTabs({dispatch, project, session, onError}: Options) {
  const [creating, setCreating] = useState(false);
  const busy = useRef(false);
  const now = useRef({project, session, dispatch, onError});
  now.current = {project, session, dispatch, onError};

  const create = useCallback(async (group?: string) => {
    const {project: owner, session: conversation, dispatch: send, onError: report} = now.current;
    if (busy.current) return;
    if (!owner) {
      report(new Error('Open a project to start a terminal.'));
      return;
    }
    busy.current = true;
    setCreating(true);
    try {
      const pty = await createPty(owner, conversation?.id);
      if (conversation) rememberTerminal(conversation.id, pty.id);
      send({type: 'openTab', kind: 'terminal', ref: pty.id, group});
    } catch (reason) {
      report(reason);
    } finally {
      busy.current = false;
      setCreating(false);
    }
  }, []);

  // "New terminal" from a command: the request may have been made before this host mounted.
  useEffect(() => {
    const request = () => {
      if (takeNewTerminalRequest()) void create();
    };
    request();
    window.addEventListener(NEW_TERMINAL_EVENT, request);
    return () => window.removeEventListener(NEW_TERMINAL_EVENT, request);
  }, [create]);

  // A shell that arrived from a detached window shows in a tab of its own.
  useEffect(() => {
    const handoff = (event: Event) => {
      const detail = (event as CustomEvent<{sessionId: string; id: string}>).detail;
      if (detail?.id) now.current.dispatch({type: 'openTab', kind: 'terminal', ref: detail.id});
    };
    window.addEventListener('termx-terminal-handoff', handoff);
    return () => window.removeEventListener('termx-terminal-handoff', handoff);
  }, []);

  return {create, creating};
}
