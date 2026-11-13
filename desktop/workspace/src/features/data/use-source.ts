import {useCallback, useEffect, useRef, useState} from 'react';
import {ApiError} from '../../lib/api';
import {dataBody, dataError, dataPath, dataRequest, useDataScope, type DataConnection} from '../../lib/data-studio';
import type {SchemaTable} from '../../lib/query-spec';

export type SourceSchema = {tables: SchemaTable[]};
export type SchemaStatus = 'idle' | 'loading' | 'ready' | 'error';
export type SourceController = {
  schema: SourceSchema | null; schemaStatus: SchemaStatus; schemaError: string;
  /** True when the server asked for a source test before it will read this source. */
  needsTest: boolean;
  testing: boolean; testMessage: string; testError: string;
  loadSchema: () => void; testConnection: () => void;
};

function schemaIdentity(workspaceId: string, source: DataConnection | undefined) {
  const generations = [source?.source_generation, source?.credential_generation];
  const version = generations.every(value => typeof value === 'number' && Number.isFinite(value) && value >= 0)
    ? ['generations', ...generations] : ['revision', source?.revision];
  // Test receipts change revision without changing the source. Older DTOs need
  // the conservative revision fallback; authority/profile changes always reset.
  return JSON.stringify([workspaceId, source?.id, version, source?.engine,
    Object.entries(source?.config || {}).sort(([a], [b]) => a.localeCompare(b)),
    source?.created_by, source?.shared_read, source?.credential_configured, source?.password_configured]);
}

/** Schema and connection test for the selected source. The source is contacted only by an explicit call. */
export function useDataSource(workspaceId: string, source: DataConnection | undefined, onRefresh: () => void): SourceController {
  const scope = useDataScope(), ticket = useRef(0), flight = useRef(false), current = useRef('');
  const [schema, setSchema] = useState<SourceSchema | null>(null), [schemaStatus, setSchemaStatus] = useState<SchemaStatus>('idle'), [schemaError, setSchemaError] = useState('');
  const [needsTest, setNeedsTest] = useState(false), [testing, setTesting] = useState(false), [testMessage, setTestMessage] = useState(''), [testError, setTestError] = useState('');
  const sourceId = source?.id || '', identity = schemaIdentity(workspaceId, source), verified = source?.read_policy?.verified;
  const previous = useRef<{identity: string; verified: boolean | undefined} | null>(null);
  current.current = identity;
  useEffect(() => {
    const prior = previous.current; previous.current = {identity, verified};
    if (prior?.identity === identity && !(prior.verified === true && verified !== true)) return;
    ticket.current++; flight.current = false;
    setSchema(null); setSchemaStatus('idle'); setSchemaError(''); setNeedsTest(false); setTesting(false);
  }, [identity, verified]);
  useEffect(() => {setTestMessage(''); setTestError('');}, [workspaceId, sourceId]);
  const path = useCallback((suffix: string) => dataPath(workspaceId, '/connections/' + encodeURIComponent(sourceId) + suffix), [workspaceId, sourceId]);
  const loadSchema = useCallback(() => {
    if (!sourceId || flight.current) return;
    const mine = ++ticket.current, controller = scope.controller();
    flight.current = true; setSchemaStatus('loading'); setSchemaError(''); setNeedsTest(false);
    const live = () => scope.current() && !controller.signal.aborted && current.current === identity && mine === ticket.current;
    void dataRequest<SourceSchema>(path('/schema'), {signal: controller.signal}).then(result => {
      if (!live()) return;
      setSchema(result); setSchemaStatus('ready');
    }).catch(reason => {
      if (!live()) return;
      setSchema(null); setSchemaStatus('error'); setSchemaError(dataError(reason));
      setNeedsTest(reason instanceof ApiError && reason.status === 409);
    }).finally(() => {
      if (mine === ticket.current) flight.current = false;
      scope.release(controller);
    });
  }, [identity, path, scope, sourceId]);
  const testConnection = useCallback(() => {
    if (!sourceId || flight.current) return;
    const mine = ++ticket.current, controller = scope.controller();
    flight.current = true; setTesting(true); setTestMessage(''); setTestError('');
    const live = () => scope.current() && !controller.signal.aborted && current.current === identity && mine === ticket.current;
    const invalidateSchema = (message: string) => {
      setSchema(null); setSchemaStatus('error'); setSchemaError(message); setNeedsTest(true);
    };
    void dataRequest<{ok: boolean; message: string}>(path('/test'), {...dataBody('POST', {}), signal: controller.signal}).then(result => {
      if (!live()) return;
      setTestMessage(result.message || (result.ok ? 'Connection succeeded.' : 'Connection test failed.'));
      if (result.ok) {setNeedsTest(false); setSchemaError(''); setSchemaStatus(value => value === 'error' ? 'idle' : value); onRefresh();}
      else invalidateSchema(result.message || 'Connection test failed.');
    }).catch(reason => {
      if (live()) {setTestError(dataError(reason)); invalidateSchema(dataError(reason));}
    }).finally(() => {
      if (mine === ticket.current) {flight.current = false; if (scope.current()) setTesting(false);}
      scope.release(controller);
    });
  }, [identity, onRefresh, path, scope, sourceId]);
  return {schema, schemaStatus, schemaError, needsTest, testing, testMessage, testError, loadSchema, testConnection};
}

/** Opening a saved view or a table is the person asking for it, so load its tables without a second click. Stays idle when the source cannot be read yet. */
export function useAutoSchema(controller: Pick<SourceController, 'schemaStatus' | 'loadSchema'>, enabled: boolean) {
  const {schemaStatus, loadSchema} = controller;
  useEffect(() => {if (enabled && schemaStatus === 'idle') loadSchema();}, [enabled, schemaStatus, loadSchema]);
}
