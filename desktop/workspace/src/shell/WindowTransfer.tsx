import {useCallback, useEffect, useLayoutEffect, useRef, useState} from 'react';
import {gql, request, type Project, type Session} from '../lib/api';
import {
  dropArtifactDrafts,
  freezeArtifactDrafts,
  persistArtifactDrafts,
  receiveArtifactDrafts,
  restoreArtifactDrafts,
  snapshotArtifactDrafts,
  unfreezeArtifactDrafts,
} from '../lib/artifact-drafts';
import {
  cancelBrowserRedock,
  commitBrowserRedock,
  listenBrowserRedock,
  openBrowserWindow,
  requestBrowserRedock,
} from '../lib/browser-windows';
import {sessionDrafts} from '../lib/drafts';
import {
  acceptRedock,
  cancelRedock,
  commitRedock,
  detachWorkspace,
  isDetachedWindow,
  listenNativeRedock,
  nativeWorkspace,
  rejectRedock,
  requestRedock,
} from '../lib/native';
import {acceptTransferredDraft, flushRedockDraft, snapshotWindow} from '../lib/redock-state';
import type {Auth} from '../lib/use-auth';
import {
  bufferCount,
  persistWindowDraft,
  persistWindowRecord,
  recoverableWindows,
  type WindowPanel,
  type WindowRecord,
} from '../lib/window-journal';
import {
  dropWindowBuffers,
  freezeWindowBuffers,
  persistBuffers,
  receiveBuffers,
  rememberTerminal,
  snapshotBuffers,
  unfreezeWindowBuffers,
  validateWindowState,
} from '../lib/window-state';
import {useAuth} from './AuthGate';
import {useReport} from './errors';

/**
 * Moving a workspace between windows: detaching a surface into its own window, handing it back (redock: request, the
 * main window validates and accepts, this window commits), and recovering the unsaved state of windows that closed.
 *
 * The wire format is fixed by the native shell (`desktop/src-tauri/src/workspace.rs`) and the browser channel:
 * `{version: 1, sessionId, buffers (at most 100), draft}` plus terminal and artifact handoffs. This file changes how the
 * React side is organised, not what is sent.
 */

/** What the main window must show once a redocked window's state has been accepted. */
export interface TransferredWindow {
  sessionId: string;
  panel: WindowPanel;
  hasBuffers: boolean;
}

export type RecoveryRow = WindowRecord & {drafts: number; buffers: number; artifacts: number};

type Options = {
  /** The selected conversation (`''` for none) and the surface this window shows, as the window journal records them. */
  selected: string;
  surface: WindowPanel;
  /**
   * Show the accepted conversation: select it, lay out `panel`, read its snapshot. Resolves once it has rendered.
   * It runs in the main window only, after the transferred drafts, buffers and terminal were validated and stored.
   */
  onArrive: (arrival: TransferredWindow) => Promise<void>;
};

export function useWindowTransfer({selected, surface, onArrive}: Options) {
  const {auth} = useAuth();
  const report = useReport();
  const [redocking, setRedocking] = useState(false);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [recoveryRows, setRecoveryRows] = useState<RecoveryRow[]>([]);
  const arrive = useRef(onArrive);
  useLayoutEffect(() => {
    arrive.current = onArrive;
  });
  const ownerId = auth.principal.id;

  /** The main window accepts a redocked window: check everything, store what moved, then show it. */
  async function applyRedock(event: {
    id: string;
    ownerId: string;
    sessionId: string;
    panel: WindowPanel;
    payload: unknown;
  }) {
    if (event.ownerId !== ownerId) throw new Error('Workspace handoff principal changed');
    const state = validateWindowState(event.payload);
    if (state.sessionId !== event.sessionId) throw new Error('Workspace handoff conversation changed');
    const current = await request<Session>('/api/workspace/sessions/' + state.sessionId + '?turns=false');
    const draft = sessionDrafts.get(state.sessionId);
    if (
      state.draft &&
      draft &&
      ((draft.dirty &&
        (draft.text !== state.draft.text ||
          JSON.stringify(draft.context || []) !== JSON.stringify(state.draft.context || []))) ||
        (!!draft.attachments?.length &&
          JSON.stringify(draft.attachments) !== JSON.stringify(state.draft.attachments || [])))
    ) {
      throw new Error('Main workspace has a different unsent draft. Review both windows before returning.');
    }
    if (
      state.draft &&
      (current.draft_text !== state.draft.text ||
        JSON.stringify(current.draft_context || []) !== JSON.stringify(state.draft.context || []))
    ) {
      throw new Error('Conversation draft changed during handoff; this window remains open');
    }
    const allowed = await gql<{projects: Project[]}>('query{projects{id name path}}');
    if (state.buffers.some(buffer => !allowed.projects.some(project => project.id === buffer.project_id))) {
      throw new Error('An editor buffer belongs to a project you can no longer access');
    }
    for (const artifact of state.artifacts || []) {
      const current = await request<{project: string | null; kind: string}>(
        '/api/media/artifacts/' + artifact.artifact_id,
      );
      if (current.project !== artifact.project_id || current.kind !== artifact.kind) {
        throw Error('Artifact access or kind changed during handoff.');
      }
    }
    await receiveArtifactDrafts(ownerId, state.artifacts || [], event.id);
    await receiveBuffers(ownerId, state.buffers, event.id);
    const live = await request<Auth>('/auth/me');
    if (live.principal.id !== event.ownerId) throw new Error('Workspace handoff principal changed');
    acceptTransferredDraft(state);
    if (state.draft) await persistWindowDraft(ownerId, state.sessionId, state.draft);
    if (state.terminalId) {
      const visible = await gql<{sessions: {id: string; cwd: string; exited: boolean}[]}>(
        'query{sessions{id cwd exited}}',
      );
      if (!visible.sessions.some(row => row.id === state.terminalId && !row.exited && row.cwd === current.cwd)) {
        throw new Error('Transferred terminal is no longer available in this checkout');
      }
      rememberTerminal(state.sessionId, state.terminalId);
      window.dispatchEvent(
        new CustomEvent('termx-terminal-handoff', {detail: {sessionId: state.sessionId, id: state.terminalId}}),
      );
    }
    await arrive.current({
      sessionId: state.sessionId,
      panel: event.panel,
      hasBuffers: state.buffers.length > 0,
    });
  }

  // Listen for a detached window handing its workspace back. Never from inside a detached window itself.
  useEffect(() => {
    let stopped = false;
    let release: (() => void) | undefined;
    if (nativeWorkspace()) {
      if (isDetachedWindow()) return;
      listenNativeRedock(event => {
        void (async () => {
          try {
            if (stopped || !event.sessionId) throw new Error('Workspace handoff unavailable');
            await applyRedock({...event, sessionId: event.sessionId});
            if (stopped) throw new Error('Workspace handoff unavailable');
            await acceptRedock(event.id);
          } catch (error) {
            await rejectRedock(event.id).catch(() => {});
            report(error);
          }
        })();
      })
        .then(value => {
          if (stopped) value();
          else release = value;
        })
        .catch(report);
    } else {
      release = listenBrowserRedock(ownerId, async event => {
        if (stopped) throw new Error('Workspace handoff unavailable');
        await applyRedock(event);
        if (stopped) throw new Error('Workspace handoff unavailable');
      });
    }
    return () => {
      stopped = true;
      release?.();
    };
  }, [ownerId]);

  // Browser windows keep a journal record so their unsaved state can be recovered after a crash.
  useEffect(() => {
    if (!nativeWorkspace()) void persistWindowRecord(ownerId, selected || null, surface).catch(report);
  }, [ownerId, selected, surface]);

  /** A detached window hands its workspace, draft and unsaved buffers back to the main window. */
  async function redock() {
    if (!selected || redocking) return;
    setRedocking(true);
    let token: string | undefined;
    let committed = false;
    const originalArtifacts = snapshotArtifactDrafts(ownerId);
    try {
      await freezeWindowBuffers();
      await freezeArtifactDrafts();
      await flushRedockDraft(selected);
      const state = snapshotWindow(ownerId, selected);
      token = nativeWorkspace()
        ? await requestRedock(selected, surface, state)
        : await requestBrowserRedock(ownerId, selected, surface, state);
      if (JSON.stringify(snapshotWindow(ownerId, selected)) !== JSON.stringify(state)) {
        throw new Error('Workspace state changed during handoff. This window remains open.');
      }
      await dropWindowBuffers(ownerId);
      window.dispatchEvent(new CustomEvent('termx-redock-committing', {detail: true}));
      if (JSON.stringify(snapshotWindow(ownerId, selected)) !== JSON.stringify(state)) {
        throw new Error('Workspace state changed during handoff. This window remains open.');
      }
      if (nativeWorkspace()) await commitRedock(token);
      else await commitBrowserRedock(token);
      await dropArtifactDrafts(ownerId);
      committed = true;
    } catch (error) {
      if (token) {
        if (nativeWorkspace()) await cancelRedock(token).catch(() => {});
        else cancelBrowserRedock(token);
      }
      report(error);
    } finally {
      if (!committed) {
        unfreezeWindowBuffers();
        unfreezeArtifactDrafts();
        await persistArtifactDrafts(ownerId, originalArtifacts).catch(report);
        await persistBuffers(ownerId, snapshotBuffers(ownerId)).catch(report);
        window.dispatchEvent(new CustomEvent('termx-redock-committing', {detail: false}));
        setRedocking(false);
      }
    }
  }

  /** Open a surface of this workspace in its own window. */
  const detachSurface = useCallback(
    (target: WindowPanel) => {
      if (nativeWorkspace()) {
        void detachWorkspace(selected || null, target).catch(report);
        return;
      }
      void openBrowserWindow(ownerId, selected || null, target).catch(report);
    },
    [ownerId, selected, report],
  );

  /** List the windows that left unsaved files, drafts or artifacts behind and open the recovery dialog. */
  async function recoverWindows() {
    try {
      const rows = await recoverableWindows(ownerId);
      const candidates = await Promise.all(
        rows.map(async row => ({
          ...row,
          buffers: await bufferCount(ownerId, row.slot),
          artifacts: (await restoreArtifactDrafts(ownerId, row.slot)).length,
        })),
      );
      setRecoveryRows(candidates.filter(row => row.buffers || row.drafts || row.artifacts));
      setRecoveryOpen(true);
    } catch (error) {
      report(error);
    }
  }

  async function reopenRecovered(row: RecoveryRow) {
    if (!row.sessionId) return;
    try {
      await openBrowserWindow(ownerId, row.sessionId, row.panel, row);
      setRecoveryOpen(false);
    } catch (error) {
      report(error);
    }
  }

  return {
    redocking,
    redock,
    /** Detach the surface this window is showing. */
    detach: () => detachSurface(surface),
    detachSurface,
    recoverWindows,
    reopenRecovered,
    recoveryOpen,
    setRecoveryOpen,
    recoveryRows,
  };
}

/** Shown over the workspace while a detached window's draft and files travel back to the main window. */
export function RedockCover() {
  return (
    <div className="redock-cover" role="status">
      Returning your draft and unsaved files to the main workspace…
    </div>
  );
}
