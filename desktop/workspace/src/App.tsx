import AuthGate from './shell/AuthGate';
import {ErrorProvider} from './shell/errors';
import Shell from './shell/Shell';

// The names the app's tests and entry points import from here.
export {SignIn} from './shell/SignIn';
export {sessionListPreview} from './shell/sidebar-rows';
export {useWorkspaceHistory, useWorkspaceThemeScope, workspaceRoute} from './shell/routes';

/**
 * TermX workspace: one error message above the sign-in gate above the workspace shell. Everything that used to
 * live here is in `shell/`: routes, commands and shortcuts, the auth gate, workspace data, window transfer,
 * dialogs, the conversation list and the shell itself.
 */
export default function App() {
  return (
    <ErrorProvider>
      <AuthGate>
        <Shell />
      </AuthGate>
    </ErrorProvider>
  );
}
