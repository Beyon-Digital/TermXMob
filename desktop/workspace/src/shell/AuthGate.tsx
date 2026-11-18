import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
  type ReactNode,
} from 'react';
import HostStop from '../components/HostStop';
import LockScreen from '../components/LockScreen';
import {ApiError, lockState, lockWorkspace, request, type LockState} from '../lib/api';
import {checkCompatibility} from '../lib/compatibility';
import {preserveBeforeLock} from '../lib/lock-preservation';
import {listenNativeLocks, nativeWorkspace} from '../lib/native';
import {useAuthSession, type Auth, type Methods} from '../lib/use-auth';
import {restoreWindowDrafts} from '../lib/window-journal';
import {useErrorMessage, useReport} from './errors';
import {useWorkspaceThemeScope} from './routes';
import {SignIn} from './SignIn';

/**
 * Everything that happens before there is a workspace to show, and the lock that can cover it afterwards:
 * the compatibility check and boot, sign-in and pairing, the lock screen, and the host stop dialog.
 * Its children render only while a principal is signed in and read the session through `useAuth`.
 */
export interface AuthContextValue {
  auth: Auth;
  /** Bumped whenever the signed-in identity changes, so late async work can tell it is stale. */
  authEpoch: MutableRefObject<number>;
  /** The latest auth for async callbacks; `auth` is the one to render with. */
  authNow: MutableRefObject<Auth | null>;
  fence: ReturnType<typeof useAuthSession>['fence'];
  locked: boolean;
  /** The latest `locked` for async callbacks. */
  lockedNow: MutableRefObject<boolean>;
  beginLock: () => Promise<void>;
  openHostStop: () => void;
  /** Runs when the workspace is unlocked from the lock screen. Returns the unsubscribe function. */
  onUnlocked: (listener: () => void) => () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth needs an AuthGate');
  return value;
}

/** While nobody is signed in the document keeps the Agent workspace's theme scope. */
function SignedOutScope() {
  useWorkspaceThemeScope('agent');
  return null;
}

export default function AuthGate({children}: {children: ReactNode}) {
  const report = useReport();
  const {error, clearError} = useErrorMessage();
  const {auth, setAuth, methods, setMethods, booting, setBooting, authEpoch, authNow, lockInfoNow, fence} =
    useAuthSession();
  const [locked, setLocked] = useState(false);
  const [lockInfo, setLockInfo] = useState<LockState | null>(null);
  const [lockPreparing, setLockPreparing] = useState(false);
  const [lockNotice, setLockNotice] = useState('');
  const [hostStopOpen, setHostStopOpen] = useState(false);
  const lockedNow = useRef(false);
  const lockFlight = useRef(false);
  const lockIntent = useRef<object | null>(null);
  const unlockListeners = useRef(new Set<() => void>());
  useLayoutEffect(() => {
    lockedNow.current = locked;
  });
  useLayoutEffect(() => {
    lockInfoNow.current = lockInfo;
  });
  const renderEpoch = authEpoch.current;

  async function acceptAuth(value: Auth) {
    const changed =
      authNow.current?.principal.id !== value.principal.id || authNow.current?.session_id !== value.session_id;
    if (changed) authEpoch.current++;
    const generation = authEpoch.current;
    authNow.current = value;
    if (!nativeWorkspace()) await restoreWindowDrafts(value.principal.id).catch(report);
    if (generation === authEpoch.current) setAuth(value);
  }

  async function restoreLockState() {
    const pending = fence();
    try {
      const value = await lockState();
      if (!pending.accepts(value)) return;
      lockInfoNow.current = value;
      setLockInfo(value);
    } catch (reason) {
      if (pending.current()) setLockNotice(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function restore() {
    const pending = fence();
    try {
      await checkCompatibility();
      const methods = await request<Methods>('/auth/methods', {}, false);
      if (!pending.current()) return;
      setMethods(methods);
      if (methods.configured) {
        const value = await request<Auth>('/auth/me');
        if (pending.accepts(value)) await acceptAuth(value);
      }
    } catch (reason) {
      if (!pending.current()) return;
      if (reason instanceof ApiError && reason.status === 423) {
        setLocked(true);
        await restoreLockState();
      } else if (!(reason instanceof Error && reason.message.includes('Session expired'))) {
        report(reason);
      }
    } finally {
      setBooting(false);
    }
  }

  async function acceptUnlockedAuth(value: Auth) {
    const generation = authEpoch.current;
    authNow.current = value;
    if (!nativeWorkspace()) await restoreWindowDrafts(value.principal.id).catch(report);
    if (generation === authEpoch.current && authNow.current?.session_id === value.session_id) setAuth(value);
  }

  async function finishUnlock(value: unknown) {
    const current = value as Auth;
    if (renderEpoch !== authEpoch.current) throw Error('Unlock response belongs to an earlier session.');
    if (
      !current?.principal?.id ||
      !current.session_id ||
      (authNow.current && current.principal.id !== authNow.current.principal.id) ||
      (lockInfoNow.current && current.session_id !== lockInfoNow.current.session_id)
    ) {
      throw Error('Unlock identity changed. Sign out to use another account.');
    }
    setLocked(false);
    lockedNow.current = false;
    lockInfoNow.current = null;
    setLockInfo(null);
    setLockNotice('');
    await acceptUnlockedAuth(current);
    unlockListeners.current.forEach(listener => listener());
  }

  const beginLock = useCallback(async () => {
    const owner = authNow.current;
    if (!owner || lockFlight.current) return;
    const pending = fence();
    const ticket = {};
    lockIntent.current = ticket;
    lockFlight.current = true;
    lockedNow.current = true;
    setLocked(true);
    setLockPreparing(true);
    setLockNotice('');
    sessionStorage.setItem('termx-browser-view-paused', 'true');
    window.dispatchEvent(new Event('termx-locked'));
    try {
      const warnings = await preserveBeforeLock(owner.principal.id);
      if (!pending.current() || lockIntent.current !== ticket) return;
      if (warnings.length) {
        setLockNotice('Your local changes were retained where storage is available. ' + warnings.join(' '));
      }
      await lockWorkspace();
      if (lockIntent.current === ticket && pending.identity()) await restoreLockState();
    } catch (error) {
      if (lockIntent.current === ticket && pending.identity()) {
        setLockNotice(
          'Lock could not be confirmed. Observation remains hidden. ' +
            (error instanceof Error ? error.message : String(error)),
        );
        await restoreLockState();
      }
    } finally {
      if (lockIntent.current === ticket && pending.identity()) {
        lockFlight.current = false;
        setLockPreparing(false);
      }
    }
    // Only stable refs, `fence` and state setters are read, so this never needs to change.
  }, []);

  // Boot, and the sign-out event raised by the API client when a session ends.
  useEffect(() => {
    void restore();
    const signedOut = () => {
      authEpoch.current++;
      authNow.current = null;
      lockInfoNow.current = null;
      lockIntent.current = null;
      lockFlight.current = false;
      setLocked(false);
      setLockInfo(null);
      setLockPreparing(false);
      setAuth(null);
    };
    window.addEventListener('termx-signed-out', signedOut);
    return () => window.removeEventListener('termx-signed-out', signedOut);
  }, []);

  // The session was locked or unlocked from another window or by the host.
  useEffect(() => {
    const lock = () => {
      lockedNow.current = true;
      authEpoch.current++;
      setLocked(true);
      sessionStorage.setItem('termx-browser-view-paused', 'true');
      window.dispatchEvent(new Event('termx-locked'));
      void restoreLockState();
    };
    const unlock = () => {
      const pending = fence();
      void request<Auth>('/auth/me', {}, false)
        .then(value => {
          if (!pending.accepts(value)) return;
          setLocked(false);
          lockedNow.current = false;
          lockInfoNow.current = null;
          setLockInfo(null);
          setLockNotice('');
          void acceptUnlockedAuth(value);
        })
        .catch(reason => {
          if (pending.current()) report(reason);
        });
    };
    window.addEventListener('termx-session-locked', lock);
    window.addEventListener('termx-session-unlocked', unlock);
    let stopped = false;
    let release: (() => void) | undefined;
    void listenNativeLocks()
      .then(value => {
        if (stopped) value();
        else release = value;
      })
      .catch(report);
    return () => {
      stopped = true;
      release?.();
      window.removeEventListener('termx-session-locked', lock);
      window.removeEventListener('termx-session-unlocked', unlock);
    };
  }, [auth?.principal.id, lockInfo?.session_id]);

  useEffect(() => {
    document.body.classList.toggle('workspace-lock-active', locked);
    return () => document.body.classList.remove('workspace-lock-active');
  }, [locked]);

  const openHostStop = useCallback(() => setHostStopOpen(true), []);
  const onUnlocked = useCallback((listener: () => void) => {
    unlockListeners.current.add(listener);
    return () => {
      unlockListeners.current.delete(listener);
    };
  }, []);
  const value = useMemo<AuthContextValue | null>(
    () =>
      auth && {auth, authEpoch, authNow, fence, locked, lockedNow, beginLock, openHostStop, onUnlocked},
    [auth, authEpoch, authNow, fence, locked, beginLock, openHostStop, onUnlocked],
  );

  const lockScreen = (
    <LockScreen
      state={lockInfo}
      preparing={lockPreparing}
      notice={lockNotice}
      onRetry={() => void restoreLockState()}
      onRetryLock={() => void beginLock()}
      onUnlocked={finishUnlock}
    />
  );

  if (booting) {
    return (
      <>
        <SignedOutScope />
        <main className="loading-screen">
          <div className="brand">TermX</div>
          <p>Connecting to your workspace…</p>
        </main>
      </>
    );
  }
  if (!value) {
    return (
      <>
        <SignedOutScope />
        {locked ? (
          lockScreen
        ) : (
          <SignIn
            methods={methods}
            onDone={async () => {
              await acceptAuth(await request<Auth>('/auth/me'));
              setMethods(await request('/auth/methods', {}, false));
              clearError();
            }}
            onError={report}
            error={error}
          />
        )}
      </>
    );
  }
  return (
    <AuthContext.Provider value={value}>
      {children}
      <HostStop open={hostStopOpen && !locked} onOpenChange={setHostStopOpen} />
      {locked && lockScreen}
    </AuthContext.Provider>
  );
}
