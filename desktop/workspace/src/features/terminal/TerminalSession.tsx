import {forwardRef, useEffect, useImperativeHandle, useRef} from 'react';
import {Terminal} from '@xterm/xterm';
import {FitAddon} from '@xterm/addon-fit';
import {SearchAddon} from '@xterm/addon-search';
import {WebLinksAddon} from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';
import {request} from '../../lib/api';
import {terminalFrame} from '../../lib/terminal-frames';
import {terminalKeyHandler} from '../../lib/terminal-keyboard';
import {observeTerminalTheme, readSearchDecorations, readTerminalFont, readTerminalTheme} from './terminal-theme';
import {isTerminalFind} from './terminal-keys';
import {openTerminalLink} from './terminal-links';

export type SessionStatus = {state: string; connected: boolean};
export type SessionHandle = {
  focus: () => void;
  interrupt: () => void;
  findNext: (term: string) => boolean;
  findPrevious: (term: string) => boolean;
  clearSearch: () => void;
};
type Props = {
  id: string;
  /** This session's tab is the selected one. Hidden sessions stay mounted and connected. */
  active: boolean;
  /** The host has authorized this PTY for the current target and the workspace is unlocked. */
  authorized: boolean;
  /** Changes when the transport target changes, so a stale socket is replaced. */
  transportKey: string;
  onStatus: (id: string, status: SessionStatus) => void;
  onError: (error: unknown) => void;
  onLeave: () => void;
  onFind: () => void;
  onResults: (id: string, index: number, count: number) => void;
};

/** One xterm instance and its PTY websocket. The protocol is unchanged: binary output, JSON input / resize / signal. */
const TerminalSession = forwardRef<SessionHandle, Props>(function TerminalSession(props, ref) {
  const latest = useRef(props);
  latest.current = props;
  const host = useRef<HTMLDivElement>(null);
  const terminal = useRef<Terminal | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const fit = useRef<FitAddon | null>(null);
  const search = useRef<SearchAddon | null>(null);
  const resize = useRef<() => void>(() => {});
  const sentSize = useRef('');
  const sendSize = (ws: WebSocket, instance: Terminal, force = false) => {
    const next = instance.cols + 'x' + instance.rows;
    if (!force && sentSize.current === next) return;
    sentSize.current = next;
    ws.send(JSON.stringify({type: 'resize', cols: instance.cols || 80, rows: instance.rows || 24}));
  };

  useImperativeHandle(ref, () => ({
    focus: () => terminal.current?.focus(),
    interrupt: () => { if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({type: 'signal', name: 'int'})); },
    findNext: (term) => search.current?.findNext(term, {incremental: true, decorations: readSearchDecorations()}) ?? false,
    findPrevious: (term) => search.current?.findPrevious(term, {decorations: readSearchDecorations()}) ?? false,
    clearSearch: () => search.current?.clearDecorations(),
  }), []);

  useEffect(() => {
    if (!host.current) return;
    const instance = new Terminal({
      ...readTerminalFont(),
      lineHeight: 1,
      theme: readTerminalTheme(),
      scrollback: 10000,
      convertEol: false,
      allowProposedApi: true,
      disableStdin: true,
    });
    const addon = new FitAddon();
    const finder = new SearchAddon();
    instance.loadAddon(addon);
    instance.loadAddon(finder);
    instance.loadAddon(new WebLinksAddon((event, uri) => { openTerminalLink(event, uri); }));
    instance.open(host.current);
    const leave = terminalKeyHandler(() => latest.current.onLeave());
    instance.attachCustomKeyEventHandler((event) => {
      if (isTerminalFind(event)) {
        if (event.type === 'keydown') { event.preventDefault(); latest.current.onFind(); }
        return false;
      }
      return leave(event);
    });
    host.current.querySelector('textarea')?.setAttribute('aria-description', 'Tab completes shell commands. Shift+Tab returns to the terminal tabs. Command-F, or Control-Shift-F, finds text. Command-click or Control-click opens a link.');
    terminal.current = instance;
    fit.current = addon;
    search.current = finder;
    finder.onDidChangeResults?.(({resultIndex, resultCount}) => latest.current.onResults(latest.current.id, resultIndex, resultCount));

    const size = () => {
      try {
        addon.fit();
        if (socket.current?.readyState === WebSocket.OPEN) sendSize(socket.current, instance);
      } catch { /* the pane is hidden or has no size yet */ }
    };
    resize.current = size;
    const stopTheme = observeTerminalTheme(() => { instance.options.theme = readTerminalTheme(); });
    document.fonts?.ready.then(() => { if (terminal.current === instance) size(); });
    const observer = new ResizeObserver(size);
    observer.observe(host.current);
    instance.onData((data) => { if (socket.current?.readyState === WebSocket.OPEN) socket.current.send(JSON.stringify({type: 'input', data})); });

    return () => {
      stopTheme();
      observer.disconnect();
      socket.current?.close();
      instance.dispose();
      terminal.current = null;
      fit.current = null;
      search.current = null;
    };
  }, []);

  // Showing a hidden session needs a fresh measurement: it had no size while hidden.
  useEffect(() => {
    if (!props.active) return;
    const frame = requestAnimationFrame(() => resize.current());
    return () => cancelAnimationFrame(frame);
  }, [props.active]);

  useEffect(() => {
    const instance = terminal.current;
    if (!instance) return;
    const {id, authorized} = latest.current;
    const status = (state: string, connected: boolean) => latest.current.onStatus(id, {state, connected});
    if (!authorized) { instance.options.disableStdin = true; return; }
    let stopped = false;
    let active: WebSocket | null = null;
    let reconnect: ReturnType<typeof setTimeout>;
    instance.reset();
    const connect = () => {
      if (stopped) return;
      const url = new URL('/api/sessions/' + id + '/pty', location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const ws = new WebSocket(url);
      active = ws;
      ws.binaryType = 'arraybuffer';
      socket.current = ws;
      const fresh = () => !stopped && socket.current === ws;
      status('Connecting', false);
      instance.options.disableStdin = true;
      ws.onopen = () => {
        if (!fresh()) return;
        status('Connected', true);
        instance.options.disableStdin = false;
        fit.current?.fit();
        sendSize(ws, instance, true);
      };
      ws.onmessage = (event) => {
        if (!fresh()) return;
        const frame = terminalFrame(event.data);
        if (frame.output !== undefined) instance.write(frame.output);
        if (frame.exit !== undefined) status('Exited · ' + frame.exit, false);
        if (frame.error) latest.current.onError(new Error(frame.error));
      };
      ws.onclose = (event) => {
        if (!fresh()) return;
        status('Disconnected', false);
        instance.options.disableStdin = true;
        if (event.code === 4401) {
          request('/auth/me').then(() => { if (fresh()) connect(); }).catch((error) => { if (fresh()) latest.current.onError(error); });
        } else if (event.code !== 4403 && event.code !== 4404) {
          reconnect = setTimeout(connect, 2000);
        } else {
          latest.current.onError(new Error(event.code === 4404 ? 'This terminal has exited. Open a new terminal.' : 'Terminal authority was revoked.'));
        }
      };
      ws.onerror = () => { if (fresh()) status('Connection interrupted', false); };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(reconnect);
      active?.close();
      if (socket.current === active) socket.current = null;
      status('Disconnected', false);
    };
  }, [props.authorized, props.transportKey, props.id]);

  return <div className="terminal-host" ref={host} />;
});

export default TerminalSession;
