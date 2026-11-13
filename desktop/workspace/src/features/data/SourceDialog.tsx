import {useEffect, useRef, useState} from 'react';
import {Button} from '../../components/ui/button';
import {Dialog, DialogContent, DialogDescription, DialogTitle} from '../../components/ui/dialog';
import {Input} from '../../components/ui/input';
import {Select} from '../../components/ui/select';
import {HostPathField} from '../../components/HostPathPicker';
import {dataBody, dataError, dataPath, dataRequest, useDataScope, type DataConnection, type DataEngine, type DataEngineField} from '../../lib/data-studio';

type SourceDraft = {id?: string; revision?: number; name: string; engine: string; config: Record<string, string | number | boolean | null>; password: string; shared_read: boolean};
const newSource = (engine?: DataEngine): SourceDraft => ({name: '', engine: engine?.id || '', config: Object.fromEntries((engine?.fields || []).filter(field => field.default !== undefined && field.type !== 'password').map(field => [field.name, field.default!])), password: '', shared_read: false});
const draftFor = (record: DataConnection | undefined, engines: DataEngine[]): SourceDraft => record
  ? {id: record.id, revision: record.revision, name: record.name, engine: record.engine, config: {...record.config}, password: '', shared_read: !!record.shared_read}
  : newSource(engines.find(row => row.ready) || engines[0]);

type Props = {open: boolean; record?: DataConnection; engines: DataEngine[]; workspaceId: string; ownerId?: string; onClose: () => void; onSaved: (connection: DataConnection) => void};
/** Connect or edit a source profile. The database is not contacted by saving. */
export default function SourceDialog({open, record, engines, workspaceId, ownerId, onClose, onSaved}: Props) {
  const [draft, setDraft] = useState<SourceDraft>(() => draftFor(record, engines)), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const scope = useDataScope(), flight = useRef(false);
  useEffect(() => {if (open) {setDraft(draftFor(record, engines)); setError('');}}, [open, record]);
  const engine = engines.find(row => row.id === draft.engine);
  async function save() {
    if (flight.current) return;
    flight.current = true; setBusy(true); setError('');
    const controller = scope.controller();
    try {
      const body = {name: draft.name.trim(), engine: draft.engine, config: draft.config, password: draft.password, shared_read: draft.shared_read, ...(draft.id ? {revision: draft.revision} : {})};
      const result = await dataRequest<DataConnection>(dataPath(workspaceId, '/connections' + (draft.id ? '/' + encodeURIComponent(draft.id) : '')), {...dataBody(draft.id ? 'PATCH' : 'POST', body), signal: controller.signal});
      if (!scope.current() || controller.signal.aborted) return;
      onSaved(result);
    } catch (reason) {
      if (scope.current() && !controller.signal.aborted) setError(dataError(reason));
    } finally {
      flight.current = false;
      if (scope.current()) setBusy(false);
      scope.release(controller);
    }
  }
  function field(item: DataEngineField) {
    const value = item.type === 'password' ? draft.password : draft.config[item.name];
    const change = (next: string | number | boolean) => setDraft(previous => item.type === 'password' ? {...previous, password: String(next)} : {...previous, config: {...previous.config, [item.name]: next}});
    if (item.type === 'file') return <HostPathField key={item.name} kind="file" ownerId={ownerId} label={item.label + ' (server host)'} name={'data-' + item.name} value={String(value || '')} onChange={change} disabled={busy}/>;
    if (item.type === 'boolean') return <label key={item.name} className="data-check"><input type="checkbox" checked={!!value} onChange={event => change(event.target.checked)} disabled={busy}/>{item.label}</label>;
    return <label key={item.name}>{item.label}<Input type={item.type === 'number' ? 'number' : item.type === 'password' ? 'password' : 'text'} autoComplete={item.type === 'password' ? 'new-password' : 'off'} value={String(value ?? '')} onChange={event => change(item.type === 'number' && event.target.value ? Number(event.target.value) : event.target.value)} required={!!item.required && !(item.type === 'password' && draft.id)} disabled={busy}/></label>;
  }
  const incomplete = !draft.name.trim() || !engine?.ready || (engine.fields || []).some(item => item.required && item.type !== 'boolean' && item.type !== 'password' && !String(draft.config[item.name] ?? '').trim());
  return <Dialog open={open} onOpenChange={next => {if (!next && !busy) onClose();}}>
    <DialogContent className="data-dialog" closeDisabled={busy}>
      <DialogTitle>{draft.id ? 'Edit connection' : 'Connect database'}</DialogTitle>
      <DialogDescription>Save a source profile. The database is contacted only when you test, load tables or run a query.</DialogDescription>
      <form onSubmit={event => {event.preventDefault(); void save();}}>
        <label>Connection name<Input value={draft.name} onChange={event => setDraft(value => ({...value, name: event.target.value}))} autoComplete="off" required disabled={busy}/></label>
        <label>Database engine<Select value={draft.engine} onChange={event => {const next = engines.find(row => row.id === event.target.value); setDraft(value => ({...newSource(next), name: value.name}));}} required disabled={busy || !!draft.id}>
          {!engines.length && <option value="">No engines available</option>}
          {engines.map(row => <option key={row.id} value={row.id} disabled={!row.ready}>{row.label}{row.ready ? '' : ' · ' + row.status.replaceAll('_', ' ')}</option>)}
        </Select></label>
        {engine && !engine.ready && <p className="data-muted">{engine.reason || 'This engine is not ready on the server host.'}</p>}
        {engine?.fields?.map(field)}
        {engine && !engine.fields?.some(item => item.type === 'password') && engine.family !== 'file' && engine.id !== 'sqlite' && <label>Password<Input type="password" autoComplete="new-password" value={draft.password} onChange={event => setDraft(value => ({...value, password: event.target.value}))} disabled={busy}/></label>}
        {draft.id && <p className="data-muted">Leave the password blank to preserve the saved credential.</p>}
        <label className="data-check"><input type="checkbox" checked={draft.shared_read} onChange={event => setDraft(value => ({...value, shared_read: event.target.checked}))} disabled={busy}/>Allow team members to read this source</label>
        {error && <p role="alert">{error}</p>}
        <div className="data-dialog-actions"><Button variant="ghost" disabled={busy} onClick={onClose}>Cancel</Button><Button type="submit" disabled={busy || incomplete}>{busy ? 'Saving…' : 'Save connection'}</Button></div>
      </form>
    </DialogContent>
  </Dialog>;
}
