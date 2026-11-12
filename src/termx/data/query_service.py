"""Glue between the pure compiler and the Data runtime: authority, schema cache, run."""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

from fastapi import HTTPException

from termx.data.connectors import validate_read
from termx.data.dialects import dialect_or_none, get_dialect, unsupported_capabilities
from termx.data.dialects.base import Dialect
from termx.data.query_spec import QuerySpec, SchemaIndex, SpecErrors, compile_spec
from termx.data.relations import suggest_relations

log = logging.getLogger(__name__)
SCHEMA_TTL_SECONDS = 60.0
SCHEMA_CACHE_SIZE = 64


class SchemaCache:
    """Introspection only, never rows. Entries expire and are keyed by the connection fingerprint."""

    def __init__(self, ttl: float = SCHEMA_TTL_SECONDS, maximum: int = SCHEMA_CACHE_SIZE, clock=time.monotonic):
        self.ttl, self.maximum, self.clock = ttl, maximum, clock
        self._items: OrderedDict[tuple, tuple[float, Any]] = OrderedDict()

    def get(self, key: tuple) -> Any:
        found = self._items.get(key)
        if found is None:
            return None
        if self.clock() >= found[0]:
            del self._items[key]
            return None
        self._items.move_to_end(key)
        return found[1]

    def put(self, key: tuple, value: Any) -> None:
        self._items[key] = (self.clock() + self.ttl, value)
        self._items.move_to_end(key)
        while len(self._items) > self.maximum:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


class QueryService:
    def __init__(self, service, cache: SchemaCache | None = None):
        self.service = service
        self.cache = cache or SchemaCache()

    @property
    def runtime(self):
        return self.service.runtime

    # Every call re-checks role, workspace, share state, engine and file authority. The cache
    # only skips the source round trip, never an authorisation check.
    def _authorize_connection(self, actor, wid: str, cid: str) -> dict:
        self.service.require(actor, wid, write=True)
        return self.service.require_connection(actor, wid, cid)

    def _authorize(self, actor, wid: str, cid: str) -> tuple[dict, Dialect]:
        connection = self._authorize_connection(actor, wid, cid)
        dialect = get_dialect(connection['engine'])    # 422 for engines without an adapter
        self.runtime.assert_connection_source(connection, wid)
        return connection, dialect

    async def schema_index(self, actor, wid: str, cid: str):
        connection, dialect = self._authorize(actor, wid, cid)
        key = (wid, cid, connection.get('revision'), connection.get('source_generation'),
               connection.get('credential_generation'))
        cached = self.cache.get(key)
        if cached is None:
            raw = await self.runtime.schema(actor, wid, cid)
            cached = SchemaIndex.from_schema(raw, dialect)
            self.cache.put(key, cached)
        return connection, cached, dialect

    @staticmethod
    def _safety_net(connection: dict, compiled) -> None:
        """The compiler is the primary defence; this walk makes a compiler bug fail closed."""
        try:
            validate_read(compiled.sql, connection['engine'], compiled.parameters)
        except HTTPException:
            log.exception('Compiled query was rejected by the read-only validator')
            raise HTTPException(422, 'This query could not be prepared for a read-only run. Use Advanced SQL for it.') from None

    async def compile(self, actor, wid: str, spec: QuerySpec) -> dict:
        connection, index, dialect = await self.schema_index(actor, wid, spec.source)
        try:
            compiled = compile_spec(spec, index, dialect)
        except SpecErrors as error:
            return {'ok': False, 'errors': [item.as_dict() for item in error.errors]}
        self._safety_net(connection, compiled)
        return {'ok': True, 'dialect': dialect.engine, 'sql': compiled.sql, 'parameters': compiled.parameters,
                'columns': compiled.columns, 'limit': compiled.limit, 'offset': compiled.offset,
                'fetch_limit': compiled.fetch_limit, 'joins': compiled.joins, 'warnings': compiled.warnings,
                'schema_hash': compiled.schema_hash}

    async def run(self, actor, wid: str, spec: QuerySpec, request_id: str) -> dict:
        connection, _ = self._authorize(actor, wid, spec.source)
        # Fail before any source contact when the network role has not been verified.
        self.runtime.require_verified_read(connection)
        result = await self.compile(actor, wid, spec)
        if not result['ok']:
            raise HTTPException(422, result['errors'][0]['message'])
        return await self.runtime.run(actor, wid, {'connection_id': spec.source, 'sql': result['sql'],
                                                    'parameters': result['parameters'],
                                                    'column_kinds': [c['value_kind'] for c in result['columns']]}, request_id)

    async def relations(self, actor, wid: str, cid: str, table: str | None) -> dict:
        _, index, dialect = await self.schema_index(actor, wid, cid)
        if table is not None and (table not in index.tables or not index.tables[table].readable):
            raise HTTPException(422, 'That table was not found in this source.')
        return {'connection_id': cid, 'relations': suggest_relations(index, dialect, table)}

    def capabilities(self, actor, wid: str, cid: str) -> dict:
        connection = self._authorize_connection(actor, wid, cid)
        dialect = dialect_or_none(connection['engine'])
        if dialect is not None:
            self.runtime.assert_connection_source(connection, wid)
        body = dialect.capabilities() if dialect else unsupported_capabilities(connection['engine'])
        tested = dialect is not None and self.runtime.read_verified(connection)
        return {'connection_id': cid, 'engine': body['engine'], 'label': body['label'], 'supported': body['supported'],
                'reason': body['reason'], 'qualified': body['qualified'],
                'requires_source_test': body['requires_source_test'], 'source_tested': tested,
                'dialect': body['dialect']}
