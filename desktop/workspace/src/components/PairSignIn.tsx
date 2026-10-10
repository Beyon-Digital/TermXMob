import {useState} from 'react';
import {Button} from './ui/button';
import {json, request} from '../lib/api';

export function takePairingFragment() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (!params.has('termx_pair')) return null;
  // Consume only from memory. Never retain a one-use credential in browser history.
  history.replaceState(null, '', location.pathname + location.search);
  const ticket = params.get('termx_pair') || '', host = params.get('host_id') || '';
  if (!/^[A-Za-z0-9_-]{32,256}$/.test(ticket) || !host || host.length > 128) return {error:'This pairing link is incomplete. Create a new QR code on the host.'};
  if (location.protocol !== 'https:' && !['localhost','127.0.0.1','[::1]'].includes(location.hostname)) return {error:'Open this pairing link over HTTPS.'};
  return {ticket, host};
}
// Capture before async boot or any route transition, then remove it from the URL.
const incoming = takePairingFragment();

export default function PairSignIn({onDone}: {onDone: () => Promise<void>}) {
  const [error, setError] = useState(incoming?.error || ''), [busy, setBusy] = useState(false), [consumed, setConsumed] = useState(false);
  if (!incoming) return <p className="field-hint">To sign in by QR, open Access & sessions on a signed-in host and scan its one-use pairing code.</p>;
  async function connect() {
    if (!incoming?.ticket || busy || consumed) return;
    setBusy(true); setError('');
    try {
      await request('/auth/pair/exchange', json('POST', {ticket:incoming.ticket, host_id:incoming.host, device_name:'Paired browser', transport:'cookie'}), false);
      setConsumed(true); await onDone();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  }
  return <section aria-label="QR sign-in"><h2>Connect this browser</h2><p>Use the one-use invitation from your host. This browser receives the permissions selected there.</p><Button disabled={busy || consumed || !incoming.ticket} onClick={() => void connect()}>{busy ? 'Connecting…' : 'Connect with pairing code'}</Button>{error && <p role="alert">{error}</p>}<p className="field-hint">Expired or already used codes require a new QR code from the host.</p></section>;
}
