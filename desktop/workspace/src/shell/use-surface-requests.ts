import {useCallback, useEffect, useRef, useState} from 'react';
import type {BrowserCommand} from './commands';

type Options = {
  /** The editor is the surface on screen: a queued Quick open can run as soon as it has a size. */
  quickReady: boolean;
  /** The browser is the surface on screen and has been shown before. */
  browserReady: boolean;
  /**
   * Changes whenever the arrangement does, which is when a surface may just have appeared. The check for a queued command
   * runs again each time it changes.
   */
  signature: string;
};

/**
 * Commands that need a surface which may not be on screen yet: Quick open waits for the editor, a browser tab command
 * waits for the Browser. The shell queues the command, switches to the surface, and this runs it once the surface reports
 * it is ready or, when it is already showing, on the next frame.
 *
 * Both layout engines use it; they differ only in what `quickReady`, `browserReady` and `signature` are.
 */
export function useSurfaceRequests({quickReady, browserReady, signature}: Options) {
  const quickCommand = useRef(false);
  const browserCommand = useRef<BrowserCommand | null>(null);
  const [quickNonce, setQuickNonce] = useState(0);

  useEffect(() => {
    const ready = () => {
      if (!quickCommand.current) return;
      const root = document.querySelector<HTMLElement>('.workbench');
      if (!root || root.getBoundingClientRect().height === 0) return;
      quickCommand.current = false;
      window.dispatchEvent(new CustomEvent('termx-quick-file'));
    };
    window.addEventListener('termx-workbench-ready', ready);
    const frame = quickReady ? requestAnimationFrame(ready) : 0;
    return () => {
      if (frame) cancelAnimationFrame(frame);
      window.removeEventListener('termx-workbench-ready', ready);
    };
  }, [quickReady, signature, quickNonce]);

  useEffect(() => {
    const ready = () => {
      const command = browserCommand.current;
      if (!command) return;
      browserCommand.current = null;
      window.dispatchEvent(new CustomEvent('termx-browser-command', {detail: {command, targetSurface: 'browser'}}));
    };
    window.addEventListener('termx-browser-ready', ready);
    if (browserReady) {
      const frame = requestAnimationFrame(() => {
        const root = document.querySelector<HTMLElement>('.browser-workspace');
        if (root && root.getBoundingClientRect().height > 0) ready();
      });
      return () => {
        cancelAnimationFrame(frame);
        window.removeEventListener('termx-browser-ready', ready);
      };
    }
    return () => window.removeEventListener('termx-browser-ready', ready);
  }, [browserReady, signature]);

  const requestQuickOpen = useCallback(() => {
    quickCommand.current = true;
    setQuickNonce(value => value + 1);
  }, []);
  const requestBrowserCommand = useCallback((command: BrowserCommand) => {
    browserCommand.current = command;
  }, []);
  return {requestQuickOpen, requestBrowserCommand};
}
