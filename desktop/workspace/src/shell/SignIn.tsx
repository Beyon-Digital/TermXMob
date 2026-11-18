import {Shield} from 'lucide-react';
import {useState} from 'react';
import PairSignIn from '../components/PairSignIn';
import {Button} from '../components/ui/button';
import {Input} from '../components/ui/input';
import {json, login, request} from '../lib/api';
import {nativeWorkspace} from '../lib/native';
import type {Methods} from '../lib/use-auth';

export function SignIn({
  methods,
  onDone,
  onError,
  error,
}: {
  methods: Methods | null;
  onDone: () => Promise<void>;
  onError: (error: unknown) => void;
  error: string;
}) {
  const [username, setUsername] = useState(''),
    [password, setPassword] = useState(''),
    [bootstrap, setBootstrap] = useState(''),
    [busy, setBusy] = useState(false);
  async function submit() {
    setBusy(true);
    try {
      await login(username, password, !methods?.configured, bootstrap);
      await onDone();
    } catch (error) {
      onError(error);
    } finally {
      setBusy(false);
      setPassword('');
    }
  }
  async function sso(id: string) {
    try {
      const result = await request<{authorization_url: string}>(
        '/auth/oidc/' + id + '/begin',
        json('POST', {}),
        false,
      );
      location.assign(result.authorization_url);
    } catch (error) {
      onError(error);
    }
  }
  return (
    <main className="sign-in">
      <div className="sign-in-brand">TermX</div>
      <section className="sign-in-card">
        <h1>{methods?.configured ? 'Connect to your workspace' : 'Set up your workspace'}</h1>
        <p>Connect securely, then tell your main agent what you want to get done.</p>
        {!nativeWorkspace() && <PairSignIn onDone={onDone} />}
        <form
          onSubmit={event => {
            event.preventDefault();
            void submit();
          }}
        >
          <label>
            Username
            <Input
              required
              name="username"
              autoComplete="username"
              value={username}
              onChange={event => setUsername(event.target.value)}
            />
          </label>
          <label>
            Password
            <Input
              required
              name="password"
              type="password"
              minLength={methods?.configured ? 1 : 12}
              autoComplete={methods?.configured ? 'current-password' : 'new-password'}
              aria-describedby={methods?.configured ? undefined : 'password-rule'}
              value={password}
              onChange={event => setPassword(event.target.value)}
            />
            {!methods?.configured && (
              <small id="password-rule" className="field-hint">
                At least 12 characters.
              </small>
            )}
          </label>
          {!nativeWorkspace() &&
            !methods?.configured &&
            (methods?.passcode_required ? (
              <label>
                Host passcode
                <Input
                  type="password"
                  name="host-passcode"
                  required
                  aria-label="Host passcode"
                  autoComplete="off"
                  value={bootstrap}
                  onChange={event => setBootstrap(event.target.value)}
                />
                <small className="field-hint">
                  Use the passcode configured by the host owner in its launch settings.
                </small>
              </label>
            ) : (
              <details>
                <summary>Existing host passcode</summary>
                <Input
                  type="password"
                  name="host-passcode"
                  aria-label="Existing host passcode"
                  autoComplete="off"
                  value={bootstrap}
                  onChange={event => setBootstrap(event.target.value)}
                />
              </details>
            ))}
          <Button type="submit" disabled={busy || !methods}>
            {busy ? 'Connecting…' : methods?.configured ? 'Continue with password' : 'Create owner account'}
          </Button>
        </form>
        {methods?.methods
          .filter(method => method.flow === 'redirect')
          .map(method => (
            <Button key={method.id} variant="outline" onClick={() => sso(method.id)}>
              Continue with {method.label || method.id}
            </Button>
          ))}
        {error && (
          <p role="alert" className="inline-error">
            {error}
          </p>
        )}
        <small>
          <Shield size={13} />
          Managed sessions protect every connection.
        </small>
      </section>
      <p className="sign-in-footer">Your conversations and work stay with your host.</p>
    </main>
  );
}
