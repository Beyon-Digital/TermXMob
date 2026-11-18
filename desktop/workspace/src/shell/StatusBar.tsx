import {Command, Shield} from 'lucide-react';
import {lazy, Suspense} from 'react';
import {useAuth} from './AuthGate';

const CaptureStatus = lazy(() => import('../features/CaptureStatus'));

type Props = {
  online: boolean;
  /** The project, Studio project or Data workspace this window is about, in words. */
  context: string;
  /** The full path behind `context`, shown on hover. */
  contextPath?: string;
  onOpenSurface: (surface: 'browser' | 'computer') => void;
  onError: (reason: unknown) => void;
  onOpenPalette: () => void;
};

/** Capture controls, connection, context, Lock and Commands along the bottom of the window. */
export default function StatusBar({online, context, contextPath, onOpenSurface, onError, onOpenPalette}: Props) {
  const {auth, locked, beginLock} = useAuth();
  return (
    <footer className="status-bar">
      <Suspense fallback={null}>
        <CaptureStatus
          enabled={!locked && auth.principal.scopes.includes('desktop-view')}
          onOpen={onOpenSurface}
          onError={onError}
        />
      </Suspense>
      <span>
        <span className={'status-dot ' + (online ? 'completed' : 'failed')} />
        {online ? 'Connected' : 'Offline'}
      </span>
      <span className="status-context" title={contextPath}>
        {context}
      </span>
      <span className="flex-spacer" />
      <button title="Lock workspace" onClick={() => void beginLock()}>
        <Shield size={12} />
        Lock
      </button>
      <button onClick={onOpenPalette}>
        <Command size={12} />
        Commands
      </button>
    </footer>
  );
}
