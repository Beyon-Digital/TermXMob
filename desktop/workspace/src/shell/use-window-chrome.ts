import {useEffect, useState} from 'react';
import {isSettingsCategory} from '../lib/settings-categories';
import {workspaceFullscreen} from '../lib/workspace-fullscreen';
import type {WorkspaceMode} from '../lib/workspace-mode';
import {useAuth} from './AuthGate';
import {managerTitle} from './manager-titles';

type Options = {
  workspace: WorkspaceMode;
  manager: string;
  /** The page behind settings, or the manager itself when it is not settings. */
  shellManager: string;
  sessionTitle: string | undefined;
  report: (reason: unknown) => void;
};

/** What the window itself shows and reports: the document title, the connection state and full screen. */
export function useWindowChrome({workspace, manager, shellManager, sessionTitle, report}: Options) {
  const {auth} = useAuth();
  const [online, setOnline] = useState(navigator.onLine);
  const [fullscreen, setFullscreen] = useState(false);

  useEffect(() => {
    const page = shellManager
      ? isSettingsCategory(manager)
        ? 'Settings'
        : managerTitle[shellManager] || 'Control plane'
      : workspace === 'data'
        ? 'Data'
        : workspace === 'studio'
          ? 'Studio'
          : sessionTitle || 'Main agent';
    document.title = page + ' · TermX';
  }, [manager, shellManager, workspace, sessionTitle]);

  useEffect(() => {
    const status = () => setOnline(navigator.onLine);
    window.addEventListener('online', status);
    window.addEventListener('offline', status);
    return () => {
      window.removeEventListener('online', status);
      window.removeEventListener('offline', status);
    };
  }, []);

  useEffect(() => {
    if (!auth) return;
    let stopped = false;
    const read = () =>
      void workspaceFullscreen()
        .then(value => {
          if (!stopped) setFullscreen(value);
        })
        .catch(() => {});
    read();
    document.addEventListener('fullscreenchange', read);
    window.addEventListener('focus', read);
    return () => {
      stopped = true;
      document.removeEventListener('fullscreenchange', read);
      window.removeEventListener('focus', read);
    };
  }, [auth?.principal.id]);

  async function toggleFullscreen() {
    try {
      setFullscreen(await workspaceFullscreen(true));
    } catch (error) {
      report(error);
    }
  }

  return {online, fullscreen, toggleFullscreen};
}
