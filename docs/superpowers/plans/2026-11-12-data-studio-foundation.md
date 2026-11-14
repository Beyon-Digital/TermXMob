# Data Studio foundation (PR 1) implementation plan

**Historical plan — delivered in [#54](https://github.com/Beyon-Digital/TermXMob/pull/54).** Retained as written for implementation history; findings below describe the pre-implementation checkout. Later phases landed in #55 and #56. Use the [current user guide](../../data-studio.md), [updated design record](../specs/2026-11-12-data-studio-query-ux-design.md) and [dated verification record](../../verification/data-studio-2026-11-14.md) for the shipped behavior, current limits and exact tested revisions. Commands and contract sketches below are historical instructions, not a claim that they were rerun for this documentation update.

Date: 2026-11-12. Spec: `docs/superpowers/specs/2026-11-12-data-studio-query-ux-design.md` (approved). Scope: PR 1 "Foundation" only. Pivots, segments, views storage and audit (PR 2), Copilot proposals (PR 3) and BI-on-views (PR 4) are out of scope.

Two lanes can run in parallel from the first commit. The backend lane touches only `src/termx/data/**` and `tests/**` (Python). The frontend lane touches only `desktop/workspace/**`. The two meet at one contract (section 4), which is frozen by task T0 and checked on both sides against the same JSON fixtures.

## 0. Findings from the code that change the spec

Each item below was checked against the repo at `ebc9ddd` (sqlglot 30.31.0, SQLite 3.53) and is covered by a test in this plan.

1. **`validate_read` rejects `AND`, `OR` and every date function.** It checks `node.sql_name()` of each sqlglot `Func` against `SAFE_FUNCTIONS`. sqlglot reports `a AND b` as function `and`, `a OR b` as `or`, `strftime`, `to_char` and `date_format` all as `time_to_str` (plus `ts_or_ds_to_timestamp` for SQLite and MySQL), and `year()` as `ts_or_ds_to_date`. None of these names is in the set, so today `SELECT 1 FROM t WHERE a=1 AND b=2` and `SELECT strftime('%Y', d) FROM t` fail with "function is not in the read-only function allowlist". No current test uses `AND`/`OR`, which is why it went unnoticed. Compiled specs cannot work until this is fixed. Allowlist additions are listed in section 1.3. This also repairs Advanced SQL for people who type those functions.
2. **2026-11-12 is ISO week 46, not 45.** The format `YYYY-Www` is what is specified. Tests compute the expected label with Python's `date.isocalendar()` and never hard-code a week for this date.
3. **SQLite's authorizer allows only `PRAGMA table_xinfo` in schema mode.** Foreign-key suggestions need `PRAGMA foreign_key_list`, so the authorizer gets one more allowed pragma, in schema mode only (task B6).
4. **Only editors and owners can read a source.** `DataRuntime._source` calls `service.require(..., write=True)` unless the read comes from a report, and `require_connection` rejects viewers for non-report reads. The new routes follow the same rules, so viewers get 403 on all four.
5. **Data has no shell sidebar today.** `App.tsx` passes `sidebarAvailable={workspace!=='data'}` and `open={false}` to `WorkspaceNavigation`, and `DataStudio` draws its own `.data-navigation` rail. The shell sidebar for Data needs a portal host (section 5.1).
6. **The `ORDER BY` alias trap.** PostgreSQL rejects an output alias inside an `ORDER BY` expression (`ORDER BY (alias IS NULL)`). The compiler therefore never emits output aliases in `ORDER BY` or `GROUP BY`; it repeats the underlying expression.
7. **`:` inside a quoted identifier is a bind marker for SQLAlchemy `text()`.** A label or column named `a:b` would become a parameter on network engines. Names containing `:`, NUL or control characters are rejected for labels and aliases and reported as `unsupported_name` for schema columns (section 3.4).
8. **The repo's `html[data-app-workspace=data]` tokens in `agent-theme.css` are the authority.** They define `--accent: #f4f4f4` in dark mode, which differs from `DESIGN.md`'s terracotta. The spec's "near-white primary buttons" refers to Data's own `.button-primary { background: var(--text) }` override. Fix: delete that override and use the shell's `.button-primary` (fill `--accent`, text `--accent-fg`). No token values change. Surfaced here, not silently mixed.

## 1. Backend

### 1.1 Module layout

```
src/termx/data/dialects/__init__.py     registry: get_dialect(engine), dialect_or_none(engine), DIALECTS, unsupported_capabilities(engine)
src/termx/data/dialects/base.py         Dialect ABC, TypeKind, Capabilities, shared helpers (escape_like, ISO date parsing)
src/termx/data/dialects/sqlite.py       SQLiteDialect
src/termx/data/dialects/postgresql.py   PostgreSQLDialect
src/termx/data/dialects/_mysql_family.py  abstract MySQLFamilyDialect (backtick quoting, date_format templates)
src/termx/data/dialects/mysql.py        MySQLDialect(MySQLFamilyDialect)
src/termx/data/dialects/mariadb.py      MariaDBDialect(MySQLFamilyDialect)
src/termx/data/query_spec.py            Pydantic models + pure compiler: compile_spec(spec, schema, dialect) -> CompiledQuery
src/termx/data/relations.py             suggest_relations(schema, dialect, table=None) -> list[Relation]
src/termx/data/query_service.py         QueryService: schema cache, compile, relations, capabilities, run (glue to DataRuntime)
src/termx/data/query_http.py            build_query_router(service, actor)
```

Edits to existing files (all small, each with its own test):

- `connectors.py`: `SAFE_FUNCTIONS` additions (1.3); SQLite authorizer allows `PRAGMA foreign_key_list` in schema mode; both connectors' `schema()` add `foreign_keys` and `row_estimate`.
- `runtime.py`: `DataRuntime.schema()` annotates each column with `value_kind` through the dialect (no change when the engine has no dialect).
- `http.py`: `mount_data` includes `build_query_router`; `/bootstrap` capabilities gain `"visual_query": true`.

`mariadb.py` and `mysql.py` are separate concrete adapters on purpose. They share only the abstract family base. Each declares its own engine id, label, version floor, collation notes and conformance parametrization, so a MariaDB-only difference is a one-class change.

### 1.2 Dialect interface

```python
# src/termx/data/dialects/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Literal

TypeKind = Literal['text', 'integer', 'number', 'boolean', 'date', 'datetime', 'time', 'json', 'binary', 'other']
Bucket = Literal['day', 'week', 'month', 'quarter', 'year']
TextMode = Literal['contains', 'starts_with']
Direction = Literal['asc', 'desc']
LIKE_ESCAPE = '!'   # same on every engine; backslash escaping differs between engines, '!' does not
BUCKET_LABEL_FORMATS = {'day': 'YYYY-MM-DD', 'week': 'YYYY-Www', 'month': 'YYYY-MM', 'quarter': 'YYYY-Qn', 'year': 'YYYY'}


class Dialect(ABC):
    engine: ClassVar[str]               # 'sqlite' | 'postgresql' | 'mysql' | 'mariadb' (the connectors.py engine id)
    label: ClassVar[str]
    sqlglot_dialect: ClassVar[str]      # same value connectors.dialect() returns: sqlite | postgres | mysql | mysql
    version_floor: ClassVar[str]        # shown in the capability matrix; documented minimum server version
    bucketable_kinds: ClassVar[frozenset[TypeKind]]

    # Identifiers and names ------------------------------------------------------------
    @abstractmethod
    def quote(self, identifier: str) -> str: ...                 # doubles the quote character; caller has already validated the name
    @abstractmethod
    def table_ref(self, schema: str | None, table: str, alias: str) -> str: ...   # 'FROM' item incl. AS alias
    def param(self, name: str) -> str: return ':' + name         # all four run through :name binding (sqlite3, SQLAlchemy text())

    # Types and values -----------------------------------------------------------------
    @abstractmethod
    def classify_type(self, declared: str) -> TypeKind: ...      # declared column type text -> TypeKind
    @abstractmethod
    def coerce_param(self, value: object, kind: TypeKind) -> object: ...   # bool -> int on SQLite; numeric/date normalisation; raises ValueError
    @abstractmethod
    def bool_literal(self, value: bool) -> str: ...              # 1/0 on SQLite, TRUE/FALSE elsewhere
    @abstractmethod
    def cast_text(self, expr: str) -> str: ...                   # CAST(x AS TEXT) | CAST(x AS CHAR)

    # Predicates -----------------------------------------------------------------------
    def escape_like(self, value: str) -> str:                    # '!' -> '!!', '%' -> '!%', '_' -> '!_' (shared)
        ...
    @abstractmethod
    def text_match(self, column: str, kind: TypeKind, mode: TextMode, param: str) -> str: ...
        # contains/starts_with, case-insensitive: LOWER(<text>) LIKE LOWER(:p) ESCAPE '!'
        # <text> = column if kind == 'text' else cast_text(column). The bound value is already escaped and wrapped: '%v%' or 'v%'.
    @abstractmethod
    def in_list(self, column: str, params: list[str], negate: bool) -> str: ...      # col IN (:p1, :p2)  |  (col NOT IN (...) OR col IS NULL)
    @abstractmethod
    def between(self, column: str, low: str, high: str, *, high_exclusive: bool) -> str: ...
        # inclusive: col BETWEEN :a AND :b ; exclusive upper (date-only end on a datetime column): (col >= :a AND col < :b)

    # Ordering, paging ------------------------------------------------------------------
    @abstractmethod
    def order_item(self, expr: str, direction: Direction) -> str: ...   # NULLs always last (native NULLS LAST, or "(expr IS NULL), expr DIR")
    @abstractmethod
    def limit_offset(self, limit: int, offset: int) -> str: ...         # ints already validated; rendered with int(), not bound

    # Aggregation ------------------------------------------------------------------------
    def count_distinct(self, expr: str) -> str: return f'COUNT(DISTINCT {expr})'
    def conditional_aggregate(self, fn: Literal['count', 'sum', 'avg', 'min', 'max', 'count_distinct'],
                              value: str | None, predicate: str) -> str: ...
        # SUM(CASE WHEN <predicate> THEN <value> END); count -> COUNT(CASE WHEN p THEN 1 END). No FILTER clause: MySQL/MariaDB lack it.
    @abstractmethod
    def date_bucket(self, column: str, kind: TypeKind, bucket: Bucket) -> str: ...  # canonical text labels, see 1.4

    # Capability matrix ------------------------------------------------------------------
    @abstractmethod
    def capabilities(self) -> dict: ...                          # JSON served by GET .../capabilities (section 4.4)
```

Registry rules (`dialects/__init__.py`): `get_dialect(engine)` returns an instance or raises `HTTPException(422, 'The visual query builder supports SQLite, PostgreSQL, MySQL and MariaDB sources. {label} sources can still use Advanced SQL.')`. `dialect_or_none(engine)` returns `None` and is used by the capabilities route and by schema annotation. The registry is a fixed dict; nothing is loaded from configuration.

### 1.3 Allowlist changes in `connectors.py`

`SAFE_FUNCTIONS` additions, all side-effect free:

| Name | Reason |
|---|---|
| `date_format`, `quarter` | Requested. `quarter` is a real MySQL/MariaDB function node. `date_format` is listed for user-typed SQL that sqlglot leaves as `Anonymous`. |
| `time_to_str` | sqlglot's canonical name for `strftime`, `to_char` and `date_format`. Without it no date bucket passes `validate_read`. |
| `ts_or_ds_to_timestamp` | Wrapper sqlglot adds around the column argument of `strftime` and `date_format` for SQLite and MySQL. |
| `and`, `or` | sqlglot reports connectors as functions. Without them no `WHERE a AND b` passes. `xor` is deliberately not added. |

Everything else the adapters emit is already allowed: `lower`, `cast`, `count`, `sum`, `avg`, `min`, `max`, `coalesce` (not used, listed for completeness), `case`, `if` (sqlglot's rendering of `CASE`), `concat`, `printf`, `date`, `strftime`, `to_char`, `like`. A probe script confirmed that after these additions all emitted shapes for all three sqlglot dialects pass `validate_read`, and that the SQLite statements (week, quarter, `LIKE ... ESCAPE`, `NULLS LAST`, joins, `COUNT(DISTINCT)`) execute through `SQLiteConnector` with its authorizer. The set is also the SQLite authorizer's function list; the extra names are not SQLite functions, so they have no effect there. Test `test_validate_read_accepts_and_or_and_date_functions` pins all of this (task B1).

### 1.4 Per-engine behaviour (normative)

`X` is the quoted column expression.

| Concern | SQLite | PostgreSQL | MySQL | MariaDB |
|---|---|---|---|---|
| sqlglot dialect | `sqlite` | `postgres` | `mysql` | `mysql` |
| Identifier quote | `"` | `"` | `` ` `` | `` ` `` |
| Bound parameter in SQL text | `:p1` | `:p1` | `:p1` | `:p1` |
| Table reference | `"orders" AS "o"` | `"public"."orders" AS "o"` (schema from introspection) | `` `shop`.`orders` AS `o` `` (database from config) | same as MySQL |
| contains / starts_with | `LOWER(CAST(X AS TEXT)) LIKE LOWER(:p) ESCAPE '!'` (cast only when the column kind is not `text`) | same, `CAST(X AS TEXT)` | same, `CAST(X AS CHAR)` | same as MySQL |
| Case folding of the above | ASCII only (`lower()` limitation) | Unicode | Unicode | Unicode |
| `eq`/`ne` on text | exact | exact | column collation (case-insensitive by default) | column collation |
| Bound boolean | `int` | `bool` | `bool` | `bool` |
| Boolean literal | `1` / `0` | `TRUE` / `FALSE` | `TRUE` / `FALSE` | `TRUE` / `FALSE` |
| NULL ordering (always last) | `X DIR NULLS LAST` when SQLite >= 3.30, else `(X IS NULL), X DIR` | `X DIR NULLS LAST` | `(X IS NULL), X DIR` | `(X IS NULL), X DIR` |
| Limit | `LIMIT n OFFSET m` | same | same | same |
| `count_distinct` | `COUNT(DISTINCT X)` | same | same | same |
| Conditional aggregate | `SUM(CASE WHEN p THEN v END)` | same | same | same |
| Bucketable kinds | `date`, `datetime`, `text`, `other` (SQLite stores dates as text; non-ISO text yields NULL and a warning) | `date`, `datetime` | `date`, `datetime` | `date`, `datetime` |
| Version floor | 3.30 | 12 | 8.0 | 10.6 |

Date buckets. The labels are identical on every engine: day `2026-11-12`, week `2026-W46` (ISO 8601 week and ISO week-year, so 2027-01-01 is `2026-W53`), month `2026-11`, quarter `2026-Q4`, year `2026`.

| Bucket | SQLite (`strftime`) | PostgreSQL (`to_char`) | MySQL and MariaDB (`date_format`, `quarter`) |
|---|---|---|---|
| day | `strftime('%Y-%m-%d', X)` | `to_char(X, 'YYYY-MM-DD')` | `date_format(X, '%Y-%m-%d')` |
| week | `printf('%s-W%02d', strftime('%Y', date(X, '-3 days', 'weekday 4')), (CAST(strftime('%j', date(X, '-3 days', 'weekday 4')) AS INTEGER) - 1) / 7 + 1)` | `to_char(X, 'IYYY-"W"IW')` | `date_format(X, '%x-W%v')` |
| month | `strftime('%Y-%m', X)` | `to_char(X, 'YYYY-MM')` | `date_format(X, '%Y-%m')` |
| quarter | `strftime('%Y', X) \|\| '-Q' \|\| ((CAST(strftime('%m', X) AS INTEGER) + 2) / 3)` | `to_char(X, 'YYYY-"Q"Q')` | `CONCAT(date_format(X, '%Y'), '-Q', CAST(quarter(X) AS CHAR))` |
| year | `strftime('%Y', X)` | `to_char(X, 'YYYY')` | `date_format(X, '%Y')` |

SQLite's `%W` is Monday-based but not ISO, so the week expression uses the Thursday-of-the-week rule instead. It was checked against `date.isocalendar()` for 3,285 consecutive days (2019-12-20 onward) with zero mismatches, including the 2020/2021, 2026/2027 and 2024 leap-day boundaries.

### 1.5 Capability matrix

`Dialect.capabilities()` returns the object in section 4.4. Values per engine:

| Field | sqlite | postgresql | mysql | mariadb |
|---|---|---|---|---|
| `supported` | true | true | true | true |
| `qualified` | true | false | false | false |
| `requires_source_test` | false | true | true | true |
| `dialect.quote` | `"` | `"` | `` ` `` | `` ` `` |
| `dialect.table_qualification` | `none` | `schema` | `database` | `database` |
| `dialect.text_match.case_folding` | `ascii` | `unicode` | `unicode` | `unicode` |
| `dialect.text_equality` | `exact` | `exact` | `collation` | `collation` |
| `dialect.null_ordering_impl` | `native` or `emulated` (by SQLite version) | `native` | `emulated` | `emulated` |
| `dialect.boolean.bind` | `integer` | `boolean` | `boolean` | `boolean` |
| `dialect.date_bucket.kinds` | date, datetime, text, other | date, datetime | date, datetime | date, datetime |
| `dialect.version_floor` | `3.30` | `12` | `8.0` | `10.6` |
| `dialect.pivot` | false in PR 1 | false | false | false |

`qualified` mirrors `engines()[i].capabilities.qualified` and flips to true for a network engine only in a later change that lands passing live-container evidence. This PR does not claim it.

Unsupported engines. A catalog engine with no dialect (or a future engine added to `engines()` without one) gets: `GET .../capabilities` returns 200 with `supported: false`, `dialect: null` and a `reason`; `POST .../compile`, `POST .../run` and `GET .../relations` return 422 with the same reason text. The UI reads `supported` and shows the reason plus an "Open Advanced SQL" action.

## 2. QuerySpec

`src/termx/data/query_spec.py`. Every model is `extra='forbid'` and frozen. Structural limits live in the models (FastAPI returns the standard 422 list for them); name and type checks need the schema and live in the compiler (reported as `errors` in section 4).

```python
class Spec(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, populate_by_name=True)

Alias = Annotated[str, Field(pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,31}$')]
Name = Annotated[str, Field(min_length=1, max_length=128)]           # table or column: must match the schema exactly
Label = Annotated[str, Field(min_length=1, max_length=60)]           # output name; compile rejects control chars, ':' and duplicates
Scalar = Union[StrictBool, StrictInt, Annotated[float, Field(allow_inf_nan=False)], Annotated[StrictStr, Field(max_length=500)]]

class TableRef(Spec):    table: Name; alias: Alias
class ColumnRef(Spec):   table_alias: Alias; column: Name
class JoinOn(Spec):      left: ColumnRef; right: ColumnRef            # equality only; right.table_alias must be this join's alias
class Join(Spec):        kind: Literal['inner', 'left']; table: Name; alias: Alias; on: list[JoinOn] = Field(min_length=1, max_length=4)
class SelectColumn(Spec): table_alias: Alias; column: Name; label: Label | None = None
LeafOp = Literal['eq','ne','gt','gte','lt','lte','between','in','not_in','contains','starts_with','is_null','not_null']
class FilterLeaf(Spec):  field: ColumnRef; op: LeafOp; value: Scalar | None = None; values: list[Scalar] | None = Field(default=None, max_length=200)
    # validator: eq/ne/gt/gte/lt/lte/contains/starts_with need value (not None); in/not_in need 1..200 values; between needs exactly 2 values;
    # is_null/not_null take neither; contains/starts_with need a string value; every other combination is a 422.
class FilterGroup(Spec): op: Literal['and', 'or']; items: list[FilterNode] = Field(max_length=50)
FilterNode = Annotated[Union[Annotated[FilterLeaf, Tag('leaf')], Annotated[FilterGroup, Tag('group')]],
                       Discriminator(lambda v: 'group' if (v.get('op') if isinstance(v, dict) else getattr(v, 'op', None)) in ('and', 'or') else 'leaf')]
class GroupBy(Spec):     table_alias: Alias; column: Name; bucket: Bucket | None = None; label: Label | None = None
class Aggregate(Spec):   fn: Literal['count','count_distinct','sum','avg','min','max']; table_alias: Alias | None = None; column: Name | None = None; label: Label | None = None
    # count may omit both (COUNT(*)); every other fn needs both.
class ColumnSort(Spec):  table_alias: Alias; column: Name; direction: Literal['asc', 'desc'] = 'asc'
class OutputSort(Spec):  output: Label; direction: Literal['asc', 'desc'] = 'asc'      # names a select/group/aggregate output label
class QuerySpec(Spec):
    version: Literal[1] = 1
    source: str = Field(min_length=1, max_length=200)           # connection id
    from_: TableRef = Field(alias='from')
    joins: list[Join] = Field(default_factory=list, max_length=4)
    select: list[SelectColumn] = Field(default_factory=list, max_length=200)
    filters: FilterGroup = FilterGroup(op='and', items=[])
    group_by: list[GroupBy] = Field(default_factory=list, max_length=6)
    aggregates: list[Aggregate] = Field(default_factory=list, max_length=12)
    sort: list[Union[ColumnSort, OutputSort]] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=200, ge=1, le=1000, strict=True)
    offset: int = Field(default=0, ge=0, le=1_000_000, strict=True)
    # model validator: nesting depth <= 4, filter leaves <= 50, nested groups need >= 1 item.
```

`pivot` is not part of the model in PR 1. Because `extra='forbid'`, a client that sends it gets a 422 until PR 2 adds it.

### 2.1 Compiler contract

`compile_spec(spec, schema, dialect) -> CompiledQuery` is pure: no I/O, no clock. `schema` is the `runtime.schema` result wrapped in a `SchemaIndex` (case-sensitive table and column lookup, `value_kind`, primary key, foreign keys, introspected schema name).

```python
@dataclass(frozen=True)
class CompiledQuery:
    sql: str                       # clauses separated by '\n'; placeholders :p1..:pN
    parameters: dict[str, object]  # p1..pN, scalar values only
    columns: list[OutputColumn]    # label, value_kind, role (column|group|aggregate), source ref, fn, bucket
    limit: int; offset: int; fetch_limit: int      # fetch_limit = limit + 1 (probe row)
    joins: list[JoinInfo]          # alias, fan_out: none|many|unknown, message
    warnings: list[SpecWarning]
    schema_hash: str               # sha256 of the table/column names and kinds the compile used
class SpecError(Exception): code: str; path: str; message: str      # compile collects every error it can, then raises SpecErrors(list)
```

Steps, in order:

1. **Resolve names.** Aliases are unique, case-insensitively, across `from` and `joins`. Every table and column is looked up in the schema index by exact name. A miss is `unknown_table` or `unknown_column` (no fuzzy matching, no case folding). A schema table or column whose name contains `:`, NUL or a control character is `unsupported_name` and can only be reported if the spec references it. Every `table_alias` must exist; a join's `on.left` may reference only the base table or an earlier join, and `on.right.table_alias` must be that join's own alias (`bad_join`).
2. **Join checks.** Left and right ON columns must have compatible kinds: integer and number match each other; text matches text; date and datetime match each other; every other pairing is `join_type_mismatch`. Fan-out: if the joined table's ON columns include every primary-key column of that table, `fan_out='none'`; if they do not, `fan_out='many'` with the message "Each {left table} row can match many {table} rows, so counts and totals may repeat."; if the table has no primary key, `fan_out='unknown'`. When any `many` join exists and aggregates are present, a `fan_out` warning is added.
3. **Mode and outputs.** If `group_by` or `aggregates` is non-empty, this is a summary: `select` must be empty (`summary_conflict`), and outputs are the group columns followed by the aggregates. Otherwise it is a detail query: an empty `select` expands to every column of the base table and then of each join, in order, never `*`. Default labels: the column name; on a collision `alias.column`. Default aggregate labels: "Number of rows", "Number of {col}", "Unique {col}", "Total {col}", "Average {col}", "Smallest {col}", "Largest {col}". Default bucket label: "{col} by {bucket}". Labels are unique case-insensitively (`duplicate_label`) and contain no control characters or `:` (`unsupported_name`).
4. **Type rules.** `sum` and `avg` need `integer` or `number`. `bucket` needs a kind in the dialect's `bucketable_kinds` (`bucket_requires_date`); a SQLite text column adds a `text_date` warning. Filter value coercion goes through `dialect.coerce_param(value, kind)`; failure is `bad_value` with the column and the offending text. `contains` and `starts_with` accept any kind (cast to text when needed). `gt/lt/between` on `boolean`, `json` and `binary` are `bad_operator`.
5. **Filters to `WHERE`.** Recursively. A root `and` group is not parenthesised; every other group with more than one item is. Each value is appended to `parameters` as the next `pN`. Semantics, identical on all engines:
   - `ne` compiles to `(X <> :p OR X IS NULL)` and `not_in` to `(X NOT IN (...) OR X IS NULL)`, so "is not Closed" keeps rows with no status.
   - `contains` and `starts_with` escape `!`, `%` and `_` in the value, then wrap it (`%v%`, `v%`) and bind the result.
   - A date-only value (`YYYY-MM-DD`) against a `datetime` column is a day range: `eq` is `X >= :d AND X < :d_next`, `gt` is `X >= :d_next`, `gte` is `X >= :d`, `lt` is `X < :d`, `lte` is `X < :d_next`, `between` has an exclusive upper bound of the next day. `d_next` is computed in Python. Date columns and datetime values keep plain comparison and an inclusive `BETWEEN`.
   - Empty `in` lists are rejected by the model, so no empty `IN ()` is ever emitted.
6. **Select, group, aggregate.** Detail: `X AS "label"` per column. Summary: group expressions (bucketed through the dialect) then aggregate expressions, each `AS "label"`. `GROUP BY` repeats the group expressions.
7. **Sort and page.** Each sort key resolves, in order, to an output label, then (detail mode, or a group column in summary mode) to a column reference; anything else is `bad_sort`. The `ORDER BY` uses the underlying expression, never the alias, wrapped by `dialect.order_item`. A deterministic tie-breaker is appended so offset paging is stable: the base table's primary-key columns in detail mode, the group expressions in summary mode, skipping keys already present. The statement ends with `dialect.limit_offset(fetch_limit, offset)`.
8. **Limits.** At most 1,000 bound parameters per statement (`too_many_values`).
9. **Validate.** The caller (`QueryService`) runs `validate_read(sql, engine, parameters)` on the output. A failure there is a bug in the compiler, returned as 422 with a generic message and logged; the sqlglot walk is the safety net, not the primary defence.

Why `fetch_limit = limit + 1`: the runtime truncates to `PREVIEW_ROWS` (1000) and sets `truncated`, but a spec with `limit: 200` would otherwise never reveal that more rows exist. The client shows at most `limit` rows and treats `result.truncated || result.rows.length > limit` as "more rows exist". The next page starts at `offset + min(rows.length, limit)`. The 4 MB and 64 KB display caps in `collect_result` can also set `truncated`; the same rule handles them.

### 2.2 Relations

`suggest_relations(schema, dialect, table=None)` returns at most 200 suggestions, deterministic order: declared foreign keys first, then name matches, each alphabetical by `child_table`, `child_columns`. Each suggestion names the child (has the key) and the parent (referenced).

- `source: 'foreign_key'`, `confidence: 'declared'`: from `foreign_keys` in the schema. Composite keys stay together.
- `source: 'name_match'`, `confidence: 'likely'`: a child column `X_id` (or `Xid`, case-insensitive) whose singular or plural form `X` names a table with a single-column primary key of a compatible kind; or a column with the same name in two tables where it is the primary key of exactly one. Plural handling: `s`, `es`, `ies`/`y`. Pairs already covered by a declared foreign key are skipped.
- When `table` is given, only suggestions that touch it are returned.
- `cardinality`: `many_to_one` from the child side. The sentence is built server-side: "Orders to Customers: customer_id matches id".

## 3. Endpoints

All under `/api/data/workspaces/{wid}`, mounted by `mount_data` through `build_query_router(service, actor)`. Auth, CSRF, TLS and lock handling come from the shared `managed_actor`. Error text follows the existing style (`HTTPException` with a string `detail`).

| Route | Role | Source contact | 409 "test the source first" | Notes |
|---|---|---|---|---|
| `POST /query-specs/compile` | owner, editor | schema only, through the cache | No (same as `/schema`) | Returns SQL and `errors`; never executes |
| `POST /query-specs/run` | owner, editor | executes | Yes, exact text from `DataRuntime._read_definition` | Idempotent on `request_id`; returns the query-run job |
| `GET /connections/{cid}/relations` | owner, editor | schema only, through the cache | No (same as `/schema`) | Optional `?table=` filter |
| `GET /connections/{cid}/capabilities` | owner, editor | none | No; reports `source_tested` | Static per engine plus connection state |

Status codes: 401/403/423 from the actor; 403 "Workspace editor required" for viewers; 404 for an unknown or archived workspace or connection (and "Data connection not found" for a connection in another workspace); 403 "Connection owner must explicitly share read access" when the connection belongs to another user and is not shared; 422 for unsupported engines and run-time spec errors; 409 on run when a network source has no current verified read policy; 503 when the runtime is closed.

### 3.1 `QueryService` glue

```python
class QueryService:
    def __init__(self, service): ...                                      # DataService; owns SchemaCache(ttl=60s, max=64)
    async def schema_index(self, actor, wid, cid) -> tuple[dict, SchemaIndex, dict]:  # connection, index, raw schema
        # Every call re-checks service.require(write=True), require_connection, engine_spec, assert_connection_source.
        # Cache key: (wid, cid, id, revision, source_generation, credential_generation). Entry holds introspection only, never rows.
        # Miss: await service.runtime.schema(actor, wid, cid) (already authorises and fingerprint-checks).
    async def compile(self, actor, wid, spec) -> dict                     # section 4.1 body
    async def run(self, actor, wid, spec, request_id) -> dict:
        # compile -> raise HTTPException(422, first error message) on errors
        # validate_read via runtime.run's own path:
        return await service.runtime.run(actor, wid, {'connection_id': spec.source, 'sql': c.sql, 'parameters': c.parameters}, request_id)
    async def relations(self, actor, wid, cid, table) -> dict
    def capabilities(self, actor, wid, cid) -> dict
```

Idempotency is inherited: `runtime.run` hashes `{sql, parameters}`, so the same `request_id` with the same spec returns the same job and a different spec returns 409. If the schema changes between a retry and the original call and the compiled SQL differs, the retry gets that 409; this is rare and acceptable.

## 4. API contract (frozen by T0)

JSON examples use the fixture schema from section 6.1: `customers(id PK, name, region, created_on DATE)`, `orders(id PK, customer_id -> customers.id, status TEXT, amount REAL, ordered_at DATETIME, note TEXT, paid BOOLEAN)`.

### 4.1 `POST /api/data/workspaces/{wid}/query-specs/compile`

Request:

```json
{
  "spec": {
    "version": 1,
    "source": "conn_01",
    "from": { "table": "orders", "alias": "o" },
    "joins": [
      { "kind": "left", "table": "customers", "alias": "c",
        "on": [ { "left": { "table_alias": "o", "column": "customer_id" }, "right": { "table_alias": "c", "column": "id" } } ] }
    ],
    "select": [
      { "table_alias": "o", "column": "id" },
      { "table_alias": "o", "column": "status" },
      { "table_alias": "o", "column": "amount" },
      { "table_alias": "c", "column": "name", "label": "Customer" }
    ],
    "filters": { "op": "and", "items": [
      { "field": { "table_alias": "o", "column": "status" }, "op": "in", "values": ["open", "paid"] },
      { "field": { "table_alias": "o", "column": "amount" }, "op": "gt", "value": 100 }
    ] },
    "group_by": [],
    "aggregates": [],
    "sort": [ { "table_alias": "o", "column": "amount", "direction": "desc" } ],
    "limit": 200,
    "offset": 0
  }
}
```

Response 200, success:

```json
{
  "ok": true,
  "dialect": "sqlite",
  "sql": "SELECT \"o\".\"id\" AS \"id\", \"o\".\"status\" AS \"status\", \"o\".\"amount\" AS \"amount\", \"c\".\"name\" AS \"Customer\"\nFROM \"orders\" AS \"o\"\nLEFT JOIN \"customers\" AS \"c\" ON \"o\".\"customer_id\" = \"c\".\"id\"\nWHERE \"o\".\"status\" IN (:p1, :p2) AND \"o\".\"amount\" > :p3\nORDER BY \"o\".\"amount\" DESC NULLS LAST, \"o\".\"id\" ASC NULLS LAST\nLIMIT 201 OFFSET 0",
  "parameters": { "p1": "open", "p2": "paid", "p3": 100 },
  "columns": [
    { "label": "id", "value_kind": "integer", "role": "column", "table_alias": "o", "column": "id" },
    { "label": "status", "value_kind": "text", "role": "column", "table_alias": "o", "column": "status" },
    { "label": "amount", "value_kind": "number", "role": "column", "table_alias": "o", "column": "amount" },
    { "label": "Customer", "value_kind": "text", "role": "column", "table_alias": "c", "column": "name" }
  ],
  "limit": 200, "offset": 0, "fetch_limit": 201,
  "joins": [ { "alias": "c", "fan_out": "none", "message": null } ],
  "warnings": [],
  "schema_hash": "9f2c0b7e41d1a6b3"
}
```

Summary example (response `columns` only): `{ "label": "Month", "value_kind": "text", "role": "group", "table_alias": "o", "column": "ordered_at", "bucket": "month" }` and `{ "label": "Total amount", "value_kind": "number", "role": "aggregate", "fn": "sum", "table_alias": "o", "column": "amount" }`. A `warnings` entry looks like `{ "code": "fan_out", "message": "Each order can match many order_items rows, so counts and totals may repeat.", "join_alias": "i" }`; codes in PR 1 are `fan_out` and `text_date`.

Response 200, semantic errors (every error found, in spec order):

```json
{
  "ok": false,
  "errors": [
    { "code": "unknown_column", "path": "filters.items[1].field.column", "message": "Column 'amout' does not exist in table 'orders'." },
    { "code": "bucket_requires_date", "path": "group_by[0].bucket", "message": "'status' is not a date column, so it cannot be grouped by month." }
  ]
}
```

Error codes: `unknown_table`, `unknown_column`, `unknown_alias`, `duplicate_alias`, `duplicate_label`, `unsupported_name`, `bad_join`, `join_type_mismatch`, `summary_conflict`, `bad_type`, `bad_operator`, `bad_value`, `bucket_requires_date`, `bad_sort`, `too_many_values`. Structural problems (unknown field, wrong type, `between` without two values, more than 4 joins, limit above 1000, a `pivot` key) return the standard FastAPI 422 body (`detail` is a list); the client never builds such a body.

### 4.2 `POST /api/data/workspaces/{wid}/query-specs/run`

Request: `{ "request_id": "9b7c...uuid", "spec": { ...same QuerySpec... } }`. The response is exactly what `POST /query-runs` returns today (`DataJob`), polled with the existing `GET /jobs/{jid}`, cancelled with `POST /jobs/{jid}/cancel`, exported with `GET /jobs/{jid}/export.csv`:

```json
{
  "id": "job_77", "status": "queued", "connection_id": "conn_01",
  "created_at": 1789200000.1, "updated_at": 1789200000.1,
  "cancel_requested": false, "preview_limit": 1000
}
```

A completed job carries `result`:

```json
{ "columns": [ { "name": "id", "type": "unknown" } ],
  "rows": [ [ 17 ] ], "row_count": 201, "truncated": false }
```

`result.rows` contains up to `fetch_limit` rows (the probe row included). Errors: 422 with a string `detail` (the first compile error message, or the existing runtime messages), 409 "Test this source first to verify its current read-only role and session." (unchanged text), 409 "This request_id was already used with a different query.".

### 4.3 `GET /api/data/workspaces/{wid}/connections/{cid}/relations?table=orders`

```json
{
  "connection_id": "conn_01",
  "relations": [
    {
      "child_table": "orders", "child_columns": ["customer_id"],
      "parent_table": "customers", "parent_columns": ["id"],
      "source": "foreign_key", "confidence": "declared", "cardinality": "many_to_one",
      "sentence": "Orders to Customers: customer_id matches id"
    },
    {
      "child_table": "orders", "child_columns": ["region_id"],
      "parent_table": "regions", "parent_columns": ["id"],
      "source": "name_match", "confidence": "likely", "cardinality": "many_to_one",
      "sentence": "Orders to Regions: region_id matches id"
    }
  ]
}
```

### 4.4 `GET /api/data/workspaces/{wid}/connections/{cid}/capabilities`

```json
{
  "connection_id": "conn_01", "engine": "sqlite", "label": "SQLite",
  "supported": true, "reason": null,
  "qualified": true, "requires_source_test": false, "source_tested": true,
  "dialect": {
    "quote": "\"", "param_style": "named_colon", "table_qualification": "none",
    "text_match": { "modes": ["contains", "starts_with"], "case": "insensitive", "case_folding": "ascii", "like_escape": "!" },
    "text_equality": "exact",
    "filter_ops": ["eq","ne","gt","gte","lt","lte","between","in","not_in","contains","starts_with","is_null","not_null"],
    "max_in_values": 200,
    "null_ordering": "last", "null_ordering_impl": "native",
    "boolean": { "literal_true": "1", "literal_false": "0", "bind": "integer" },
    "limit_syntax": "limit_offset",
    "aggregates": ["count","count_distinct","sum","avg","min","max"],
    "conditional_aggregation": "case",
    "date_bucket": {
      "buckets": ["day","week","month","quarter","year"], "week_standard": "iso8601",
      "labels": { "day": "YYYY-MM-DD", "week": "YYYY-Www", "month": "YYYY-MM", "quarter": "YYYY-Qn", "year": "YYYY" },
      "kinds": ["date","datetime","text","other"]
    },
    "join_kinds": ["inner","left"], "max_joins": 4, "max_filters": 50,
    "pivot": false, "version_floor": "3.30"
  }
}
```

For an engine without a dialect: `{ "connection_id": "...", "engine": "...", "label": "...", "supported": false, "reason": "The visual query builder supports SQLite, PostgreSQL, MySQL and MariaDB sources. ... sources can still use Advanced SQL.", "qualified": false, "requires_source_test": false, "source_tested": false, "dialect": null }`. `source_tested` is true for SQLite, and for network engines when `read_policy.verified` matches the connection's current source and credential generations (the same test `_read_definition` applies).

### 4.5 Changed existing responses (additive only)

- `GET /connections/{cid}/schema`: every column gains `value_kind`; every table gains `foreign_keys` and `row_estimate`.

```json
{ "name": "orders", "schema": "main", "kind": "table", "row_estimate": null,
  "columns": [ { "name": "customer_id", "type": "INTEGER", "nullable": true, "primary_key": false, "value_kind": "integer" } ],
  "foreign_keys": [ { "columns": ["customer_id"], "referred_table": "customers", "referred_columns": ["id"] } ] }
```

`row_estimate` is `pg_class.reltuples` (null when below zero) on PostgreSQL, `information_schema.TABLES.TABLE_ROWS` on MySQL and MariaDB, and `null` on SQLite (the overview offers "Count rows" through a `count` spec instead of scanning). Existing consumers ignore the new keys.
- `GET /api/data/bootstrap`: `capabilities.visual_query: true`.

### 4.6 TypeScript types (`desktop/workspace/src/lib/query-spec.ts`)

```ts
export type ValueKind = 'text'|'integer'|'number'|'boolean'|'date'|'datetime'|'time'|'json'|'binary'|'other';
export type DateBucket = 'day'|'week'|'month'|'quarter'|'year';
export type Scalar = string|number|boolean;
export type ColumnRef = {table_alias:string; column:string};
export type TableRef = {table:string; alias:string};
export type JoinOn = {left:ColumnRef; right:ColumnRef};
export type Join = {kind:'inner'|'left'; table:string; alias:string; on:JoinOn[]};
export type SelectColumn = ColumnRef & {label?:string};
export type LeafOp = 'eq'|'ne'|'gt'|'gte'|'lt'|'lte'|'between'|'in'|'not_in'|'contains'|'starts_with'|'is_null'|'not_null';
export type FilterLeaf = {field:ColumnRef; op:LeafOp; value?:Scalar; values?:Scalar[]};
export type FilterGroup = {op:'and'|'or'; items:FilterNode[]};
export type FilterNode = FilterLeaf|FilterGroup;
export type GroupBy = ColumnRef & {bucket?:DateBucket; label?:string};
export type AggregateFn = 'count'|'count_distinct'|'sum'|'avg'|'min'|'max';
export type Aggregate = {fn:AggregateFn; table_alias?:string; column?:string; label?:string};
export type SortKey = (ColumnRef|{output:string}) & {direction:'asc'|'desc'};
export type QuerySpec = {version:1; source:string; from:TableRef; joins:Join[]; select:SelectColumn[]; filters:FilterGroup;
  group_by:GroupBy[]; aggregates:Aggregate[]; sort:SortKey[]; limit:number; offset:number};

export type OutputColumn = {label:string; value_kind:ValueKind; role:'column'|'group'|'aggregate'; table_alias?:string; column?:string; fn?:AggregateFn; bucket?:DateBucket};
export type SpecError = {code:string; path:string; message:string};
export type SpecWarning = {code:'fan_out'|'text_date'; message:string; join_alias?:string};
export type JoinInfo = {alias:string; fan_out:'none'|'many'|'unknown'; message:string|null};
export type CompileResult =
  | {ok:true; dialect:string; sql:string; parameters:Record<string,Scalar>; columns:OutputColumn[]; limit:number; offset:number; fetch_limit:number; joins:JoinInfo[]; warnings:SpecWarning[]; schema_hash:string}
  | {ok:false; errors:SpecError[]};
export type Relation = {child_table:string; child_columns:string[]; parent_table:string; parent_columns:string[]; source:'foreign_key'|'name_match'; confidence:'declared'|'likely'; cardinality:'many_to_one'; sentence:string};
export type RelationsResult = {connection_id:string; relations:Relation[]};
export type DialectCapabilities = { quote:string; param_style:'named_colon'; table_qualification:'none'|'schema'|'database';
  text_match:{modes:('contains'|'starts_with')[]; case:'insensitive'; case_folding:'ascii'|'unicode'; like_escape:string};
  text_equality:'exact'|'collation'; filter_ops:LeafOp[]; max_in_values:number; null_ordering:'last'; null_ordering_impl:'native'|'emulated';
  boolean:{literal_true:string; literal_false:string; bind:'integer'|'boolean'}; limit_syntax:'limit_offset'; aggregates:AggregateFn[];
  conditional_aggregation:'case'; date_bucket:{buckets:DateBucket[]; week_standard:'iso8601'; labels:Record<DateBucket,string>; kinds:ValueKind[]};
  join_kinds:('inner'|'left')[]; max_joins:number; max_filters:number; pivot:boolean; version_floor:string };
export type CapabilitiesResult = {connection_id:string; engine:string; label:string; supported:boolean; reason:string|null; qualified:boolean;
  requires_source_test:boolean; source_tested:boolean; dialect:DialectCapabilities|null};
export type SchemaColumn = {name:string; type?:string; nullable?:boolean; primary_key?:boolean; value_kind?:ValueKind};
export type SchemaForeignKey = {columns:string[]; referred_table:string; referred_columns:string[]};
export type SchemaTable = {name:string; schema?:string; kind?:string; row_estimate?:number|null; columns:SchemaColumn[]; foreign_keys?:SchemaForeignKey[]; readable?:boolean; reason?:string};
// Results reuse DataJob / DataResult from lib/data-studio.ts unchanged.
```

Client rule for results: `visibleRows = result.rows.slice(0, spec.limit)`, `hasMore = result.truncated || result.rows.length > spec.limit`, `nextOffset = spec.offset + Math.min(result.rows.length, spec.limit)`.

## 5. Frontend

All UI work follows `~/.agents/skills/frontend-design-spec/SKILL.md` and `~/.agents/DESIGN.md`: tokens only (no hex, no arbitrary values; the checklist `rg -n '#[0-9a-fA-F]{3,8}\b|-\[(#|\d)[^\]]*\]' src --glob '!*.css'` must return nothing for new files), Lucide icons (16px inline, 20px standalone, stroke 1.5), sentence case, Radix for every overlay, one primary action per view, 36px sidebar rows, 40px controls (32px compact on desktop, 44px only under `pointer: coarse`), 40px table rows, no zebra or vertical lines, numbers right-aligned with `tabular-nums`, radius 8 or 12 only, no gradients or emoji.

### 5.1 Files

New dependencies (run in `desktop/workspace`, commit the lockfile): `pnpm add @radix-ui/react-tabs @radix-ui/react-popover @radix-ui/react-dropdown-menu @codemirror/lang-sql`. Registry versions on 2026-11-12: tabs 1.2.1, popover 1.3.1, dropdown-menu 2.2.1, lang-sql 6.10.0. `@codemirror/lang-sql` is a fourth package beyond the three the spec names; the SQL editor needs it for syntax and completion, and it loads lazily with the Advanced panel.

```
src/components/ui/tabs.tsx            Tabs, TabsList, TabsTrigger, TabsContent   (wraps @radix-ui/react-tabs; same forwardRef + cn() style as dialog.tsx)
src/components/ui/popover.tsx         Popover, PopoverTrigger, PopoverContent, PopoverAnchor (surface bg, 1px --border, radius 8, --shadow-overlay)
src/components/ui/dropdown-menu.tsx   DropdownMenu + Trigger/Content/Item/CheckboxItem/Separator/Label
src/components/ui/*.test.tsx          one test file each: keyboard (arrows, Esc, focus return), axe, registered with navigation-layer.ts so the shell sidebar does not close under an open layer

src/lib/query-spec.ts                 types from 4.6 + emptySpec(), uniqueAlias(), describeFilter() (plain-language sentence), describeSummary(), humanOp(kind, op)
src/lib/data-query.ts                 compileSpec, runSpec, fetchRelations, fetchCapabilities, runSpecToCompletion(), useCompiledSpec() (250ms debounce, abort on change)
src/lib/data-tabs.ts                  DataTab model, reducer (open, close, select, reorder), persistence of {id, kind, table|queryId} only
src/lib/data-grid.ts                  column sizing, cell formatting (null as empty with a muted "empty" label), alignment by value_kind
src/lib/query-contract-fixtures/*.json shared fixtures (T0); read-only for the frontend after T0

src/features/data/DataNavigation.tsx      sidebar content (5.2)
src/features/data/DataTabs.tsx            tab strip + panels (5.3)
src/features/data/TablesOverview.tsx      all-tables overview (5.4)
src/features/data/TableTab.tsx            table tab controller: spec reducer, compile, run, paging, states
src/features/data/QueryGrid.tsx           virtualized grid, sortable headers (5.5)
src/features/data/ColumnFilter.tsx        per-column filter popover (5.5)
src/features/data/FilterChips.tsx         chips + Match all/any groups (5.6)
src/features/data/JoinPanel.tsx           "Combine with another table" (5.7)
src/features/data/SummarizePanel.tsx      "Summarize" (5.8)
src/features/data/ShowSql.tsx             read-only SQL + values + copy (5.9)
src/features/data/AdvancedSql.tsx         floating non-modal SQL panel, CodeMirror 6 (5.10)
src/features/data/SqlEditor.tsx           lazy CodeMirror wrapper (value/onChange, aria-label "SQL query", id "data-sql")
src/features/data/SourceDialog.tsx        connect/edit/archive source dialog extracted from DatabaseStudio.tsx
src/features/data/use-sql-run.ts          SQL run/save/cancel/export logic extracted from DatabaseStudio.tsx
src/features/data/data-studio.css         rewritten (5.11)
src/features/data/bi-studio.css           token cleanup only
src/test-utils/virtual.ts                 jsdom helper that gives scroll containers a size so @tanstack/react-virtual renders rows
src/test-utils/codemirror.ts              jsdom polyfills (Range.getClientRects, getBoundingClientRect) + typeSql(label, text)
```

`DatabaseStudio.tsx` stays as the container for a source (connect dialog, test connection, schema loading, saved-query save, run, export), reduced to composition of the extracted pieces. Behaviour must not change in the extraction commit (task F2), proved by the unchanged `database-studio.test.tsx`.

### 5.2 Shell sidebar for Data

`App.tsx`: for `workspace==='data'` pass `sidebarAvailable`, `open={dataSidebarOpen}` (new state, default open when `innerWidth > 850`), `width={260}`, `sidebarLabel="Data navigation"`, `onOpenChange={setDataSidebarOpen}`; the sidebar body is `<div className="data-sidebar-host" ref={setDataSidebarHost}/>`. `DataStudio` receives `navigationHost={dataSidebarHost}` and `DataSession` renders `<DataNavigation/>` into it with `createPortal`. When `navigationHost` is undefined (the existing unit tests, standalone use) `DataNavigation` renders inline as `<aside aria-label="Data navigation">` inside `.data-studio`, so tests that mount `<DataStudio/>` alone keep working. Below 850px the shell already floats the sidebar with a backdrop.

`DataNavigation` (260px, rows 36px, selected row `--surface-2`, no icon on every row, group headings in xs `--text-3`):

1. Workspace select (existing `Select`, label "Data workspace"), a "Create workspace" ghost icon button.
2. Source switcher: `Select` with label "Data source" listing the workspace's connections, plus a "Connect database" ghost button (existing label).
3. Search `Input` with a visible label "Search tables" (placeholder is never the label); filters Tables and Saved queries; results announced through `role="status"`.
4. Groups, each a labelled `<nav>`: **Tables** (virtualised with `@tanstack/react-virtual` above 200 rows), **Saved queries**, **Reports**, **Dashboards**, **Team** (one row, "Members"). Table and saved-query rows open or focus a tab. Reports and Dashboards rows list names from the snapshot and open the existing BI view (BIStudio is not deep-linked in PR 1). Pivots and Segments groups arrive in PR 2.
5. Viewers see only Reports, Dashboards and Team, and the Tables group is replaced by the sentence "Ask an editor to share a table with you." (no action; the sidebar has no empty-state action for viewers).

States: loading (skeleton rows in `--surface-2`, same 36px height), no source (one sentence, "Connect database" is the one action), schema not loaded ("Load tables" button, label kept as "Load schema" for test compatibility), error (inline `role="alert"` with "Try again"), source needs a test (409) shows "Test connection".

### 5.3 Canvas tab strip

`DataTabs` uses Radix Tabs. The list holds the persistent first tab "Overview" and one tab per opened table or saved query; each closable tab has a `Close {title}` icon button and also closes with Delete when focused. Arrow keys, Home and End are Radix behaviour. The strip scrolls horizontally inside itself at narrow widths; the page does not scroll. Tab identity is `table:{connectionId}:{tableName}` or `query:{queryId}`; opening an already open tab focuses it. Persistence (`localStorage`, key `termx.data.tabs.{ownerId}.{workspaceId}`, try/catch on every access) stores ids only: kind, connection id and table name or query id. Filters, sorts, SQL and results are kept in memory per tab, retained across observation pauses through the existing `retainedDrafts` mechanism and discarded on lock or sign-out, matching the existing policy that no SQL, results or secrets enter browser storage.

Right side of the strip: `Advanced` (Radix DropdownMenu, 5.10), `Copilot` (toggle, existing label and behaviour), and `Workspace settings` for owners (existing).

### 5.4 All-tables overview

The Overview tab shows a title row ("Tables" xl), a search-free toolbar with a list/grid toggle (two buttons in `role="group"` with `aria-pressed`, labels "List view" and "Grid view"), and the tables. The preference is remembered per user in `localStorage` (`termx.data.overview.{ownerId}`).

- List: 40px rows with name, column count, estimated rows (right-aligned, `tabular-nums`) and an "Open" ghost button.
- Grid: tiles for discrete objects (1px `--border`, `--surface`, radius 8, padding 16, no shadow, no hover lift): name, "{n} columns", "About {rows} rows" or "Count rows" when `row_estimate` is null, "Open" (secondary) and a "More" dropdown with "Combine with another table". Pivot arrives in PR 2.
- "Count rows" runs a `count` spec and replaces itself with the number (`aria-live="polite"`).
- Empty (no tables): one sentence and "Load schema". Loading: tile or row skeletons matching the layout. Error: inline with "Try again". Source with an unverified network role: the 409 message plus "Test connection". Views the connector excluded show their `reason` text and a disabled "Open".

### 5.5 Table tab, grid, sorting and column filters

`TableTab` layout, top to bottom: toolbar row (Combine with another table, Summarize, Show SQL, Columns picker, Export CSV; ghost buttons, 32px compact on desktop), chips row (5.6), results region. The results region has `role="region"` and `aria-label="Results"`, `aria-busy` while loading, and holds `QueryGrid` plus a footer ("Rows 1 to 200", "Previous", "Next", and "Showing the first {n} rows of the preview. CSV contains the available preview." when truncated).

`QueryGrid` uses `@tanstack/react-virtual` for rows (40px, overscan 8). It renders `role="table"` with `aria-rowcount`, `role="row"` with `aria-rowindex`, `role="columnheader"` with `aria-sort` and `role="cell"`. The scroll container has `tabIndex={0}` and the label "Results". The header is sticky. Each header contains a sort button (cycles ascending, descending, none, updating `sort[0]`; shift-click adds up to 4 keys) and a filter button that opens `ColumnFilter`. Numbers and number-like kinds are right-aligned; NULL renders as muted "empty" text, not the string "NULL". Column widths come from `data-grid.ts` and are not user-resizable in PR 1.

`ColumnFilter` is a Radix Popover: an operator `Select` limited to operators valid for the column's `value_kind` (and the engine's `filter_ops`), value input(s) by kind (text, number, date, a Yes/No/Empty choice for boolean, a comma-separated list for "is any of"), and one primary "Apply" button. Esc closes and returns focus to the header button. Applying adds a chip. Validation errors show inline under the field.

### 5.6 Filter chips

Each chip is a plain-language sentence from `describeFilter()`: "Amount is greater than 100", "Ordered at is between 2026-01-01 and 2026-03-31", "Status is any of open, paid", "Note contains 'late'", "Status is empty". For date columns `gt/lt/gte/lte` read "is after", "is before", "is on or after", "is on or before". When joins exist the column is prefixed with the table ("customers · name"). A chip is a button that reopens the same popover for editing, with a sibling remove button labelled "Remove filter: {sentence}". Delete or Backspace on a focused chip removes it. The first group is "Match all of these" with a `Select` to switch to "Match any of these"; "Add group" creates a nested group (one level of nesting in the UI; the API allows four). A chip row with no filters shows only the "Add filter" ghost button. Chips wrap, never scroll.

### 5.7 Combine with another table

`JoinPanel` (Radix Popover from the toolbar button). It lists suggestions from `GET /relations?table={base}` as sentences with a ghost "Add" button each ("Orders to Customers: customer_id matches id", with a "Likely" text label for `name_match`). Below, a manual builder: a table `Select`, then pairs of column `Select`s ("Orders · customer_id" matches "Customers · id"), kind inner ("Only rows that match") or left ("Keep all {base} rows"). Added joins show under the toolbar as readable sentences ("Combined with customers: each order has one customer") with a remove button, and the `fan_out` message from the compile response as `role="status"` text when it is `many`. Maximum four joins; at four the "Add" buttons are disabled with the reason "At most 4 tables can be combined." Alias is generated from the table name (`uniqueAlias`: lowercase, non-alphanumeric to `_`, `_2` on collision, 32 characters max) and is never shown outside Show SQL. After a join, the column picker offers the joined table's columns and the grid shows them.

### 5.8 Summarize

`SummarizePanel` (collapsible section under the toolbar, existing `Collapsible`). Controls: measure (function `Select`: Number of rows, Unique values of, Total of, Average of, Smallest, Largest; column `Select` filtered by kind) and "by" (column `Select`, and a bucket `Select` shown for date kinds: Day, Week, Month, Quarter, Year). A live sentence shows what will be computed ("Total of amount by month"). It applies as the controls change (no Apply button, so the view keeps one primary action) and "Clear" returns to the detail grid. In summary mode the grid is read-only (sort works on outputs; column filters apply to the source columns before summarizing, shown in the sentence as "of rows where ..."). Bucket choices not offered by the dialect (from `capabilities`) are disabled with the reason as visible text.

### 5.9 Show SQL

`ShowSql` is a Collapsible under the toolbar: a read-only `<pre>` in `--font-mono` with the `sql` from the latest successful compile, a "Values" definition list (`p1` and its value), and a ghost "Copy" button. It is never editable. If the compile returned `errors`, the same region lists them with their paths translated to column names and no SQL is shown. A spec that cannot compile disables the grid with the first error message and a "Clear filters" recovery action when a filter is the cause.

### 5.10 Advanced SQL

An `Advanced` DropdownMenu in the strip's right side holds a checkbox item "SQL editor". When checked, `AdvancedSql` opens as a Radix Popover with `modal={false}`, `onInteractOutside` prevented so the grid stays usable, anchored below the strip, width `min(720px, 100%)`, closable with Esc or a close button. Off by default. The open or closed choice is remembered per user (`localStorage`, key `termx.data.advanced.{ownerId}`, boolean only; SQL text stays in memory). The panel holds the editor (`SqlEditor`, CodeMirror 6 with `@codemirror/lang-sql`, loaded with `React.lazy` so the main chunk does not grow), the existing "Parameters (JSON object)" field, "Run" (the panel's single primary button), "Save query", and cancel and export controls. The editor content element carries `aria-label="SQL query"` and `id="data-sql"`. Results appear in the same grid area of a new tab of kind `sql` (not a separate table). Saved queries open in the same panel and also appear under Saved queries in the sidebar. Viewers never see the menu item (existing behaviour: viewers see no SQL input).

### 5.11 Styling alignment

`data-studio.css` is rewritten against the shell tokens (`--bg`, `--surface`, `--surface-2`, `--border`, `--border-strong`, `--text`, `--text-2`, `--text-3`, `--accent`, `--accent-hover`, `--accent-fg`, `--agent-focus`). The file stops redefining tokens (`.data-studio{--bg:var(--background)...}` goes away). Removed: `button{min-height:44px}`, `.button-icon{width:44px;height:44px}`, the `.button-primary{background:var(--text)}` override, `border-color:transparent` on secondary buttons and inputs, every `!important`, the `outline:2px solid var(--text)` focus override (use the shared `--control-focus-ring`), and the `html[data-app-workspace=data] ... height:44px` block. Added: 44px targets only inside `@media (pointer: coarse)`. Inputs use the shared `.input` (1px `--border-strong`, `--surface`, 40px). The Data header becomes the canvas tab strip; the shell header stays 48px.

The Copilot panel stays functional and gets restyle only: `--surface`, 1px left `--border`, a 48px header with title and close, fixed width `clamp(320px, 30vw, 440px)`, and the hand-built `.data-resize` separator and `resize()` pointer handler are deleted from `DataStudio.tsx` (the `copilotWidth` state goes with them). Below 1024px it overlays from the right as today. `DataCopilot.tsx` logic and props are untouched.

### 5.12 Existing selectors, kept or changed

Kept as is: region "Data Studio"; `aria-label="Data workspace"` select; buttons "Create workspace", "Rename workspace", "Workspace settings", "Connect database", "Test connection", "Load schema", "Run", "Export CSV", "Copilot", "Close Copilot" (from the mocked child); `complementary` "Data Copilot"; the Copilot, Reports (mocked) regions; texts "Preview truncated" and "CSV contains the available preview".

Changed in the same commit as the markup they pin:

| Old | New | Files |
|---|---|---|
| `getByLabelText('SQL query')` toHaveValue / `fireEvent.change` | Open Advanced > "SQL editor" first; read and write through `typeSql('SQL query', text)` and `sqlValue('SQL query')` from `test-utils/codemirror.ts` | `data-studio.test.tsx`, `database-studio.test.tsx` |
| `getByLabelText('Parameters (JSON object)')` | Same label, now inside the Advanced panel | same files |
| Sidebar buttons "Database", "BI", "Team" and `data_view=bi` | "Reports" row opens the BI view and still writes `data_view=bi`; "Members" opens Team | `data-studio.test.tsx` |
| Initial render with SQL box visible | SQL panel closed by default; table overview visible | `database-studio.test.tsx` ("contacts no source until..." keeps its zero-request assertion) |
| Viewer test (no SQL input) | Same assertion, plus no Advanced menu item | `database-studio.test.tsx` |

Task F0 greps `getBy|findBy|queryBy` calls in `features/data/*.test.tsx` and `bi-copilot.test.tsx` and records any further selector in the PR description with its disposition. `#data-sql` (used by `tests/workspace_data_studio_e2e.py`) is kept on the editor content element; the e2e script is updated in task I1.

### 5.13 States and keyboard, per view

| View | Loading | Empty | Error | Disabled |
|---|---|---|---|---|
| Sidebar | 36px skeleton rows | "Connect database" (no source) / "Load schema" | inline alert + "Try again" | read-only for viewers |
| Overview | tile/row skeletons | one sentence + "Load schema" | inline alert + "Try again"; 409 shows "Test connection" | unreadable tables: reason text, disabled "Open" |
| Table tab grid | skeleton rows at the final column layout (`aria-busy`) | "No rows match these filters." + "Clear filters" | compile errors or job error inline + "Try again" | unsupported engine: reason + "Open Advanced SQL" |
| Join panel | skeleton suggestion rows | "No suggested links. Choose a table below." | inline alert + retry | at 4 joins, with reason |
| Summarize | n/a | n/a | inline | bucket not offered, with reason |
| Advanced panel | spinner inside Run only | empty editor | inline alert, SQL kept | Run disabled with no source |

Keyboard path for a table tab: tab strip (arrows, Delete) then toolbar buttons, chips (Enter edits, Delete removes), column header sort buttons, header filter buttons, the focusable results container, and the footer buttons. Every popover and menu closes on Esc and returns focus to its trigger. Focus uses the shared `--control-focus-ring`; nothing removes it. Reduced motion: no transitions beyond opacity (120 to 200ms, ease-out), and none when `prefers-reduced-motion` is set.

## 6. Tests

Work test-first on both lanes. A task's first commit is its failing tests.

### 6.1 Backend fixture

`tests/data_query_fixture.py` (new; `tests/test_data_runtime.py` is not modified). It builds the same identity and `DataService` as `test_data_runtime.fixture`, plus a SQLite file:

- `customers(id INTEGER PRIMARY KEY, name TEXT, region TEXT, created_on DATE)`: 40 rows including `'Éclair'`, `'100%_done'`, `'a!b'`, mixed case, NULLs.
- `orders(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id), status TEXT, amount REAL, ordered_at DATETIME, note TEXT, paid BOOLEAN)`: 1,305 rows with `ordered_at` covering 2020-12-31, 2021-01-03, 2024-02-29, 2026-01-01, 2026-11-12 14:30:00, 2027-01-01 and NULLs.
- `order_items(order_id INTEGER REFERENCES orders(id), sku TEXT, qty INTEGER, PRIMARY KEY(order_id, sku))`.
- A column literally named `we:ird` on a side table, and a view, for the `unsupported_name` and unreadable-table cases.

Roles: owner (alice), editor (bob), viewer (a third member), outsider (not a member).

### 6.2 Backend tests

`tests/test_data_validate_read.py` (B1)
- `AND`, `OR`, nested groups, `strftime`, `to_char`, `date_format`, `quarter`, `printf` pass `validate_read` for the right engines (characterization first: they fail on `ebc9ddd`).
- `xor`, `load_extension`, `pg_read_file`, `lo_import`, `sleep` still rejected.

`tests/test_data_dialects.py` (B2 to B5, B11)
- Per-dialect unit tests: `quote` doubles the quote character; `escape_like`; `classify_type` for each engine's declared types (`tinyint(1)` is boolean on MySQL only, `timestamptz`, `varchar(20)`, SQLite empty type is `other`); `coerce_param` (bool to int on SQLite, numeric strings, date normalisation, invalid input raises); `order_item` emulation vs native, including SQLite below 3.30 (monkeypatch the version); `bool_literal`; `limit_offset`; `in_list`; `between` inclusive and exclusive.
- Date-bucket golden text: for each dialect and each bucket the exact SQL from section 1.4.
- Capability matrix: `capabilities()` of each dialect equals the table in 1.5; `GET /capabilities` response validates against the TS-mirrored JSON schema.
- Registry: unknown engine raises 422 with the exact message; `dialect_or_none` returns None; every `engines()` entry that is `ready` and relational has a dialect (so a future engine cannot be marked ready without one and silently fail).
- Conformance corpus (`CORPUS`, at least 40 specs) run for all four dialects: every op, nested AND/OR, joins (inner, left, four joins, fan-out), each bucket, each aggregate, grouped sort by output and by column, paging with tie-breaker, boolean, date-only ranges, `contains` on a non-text column. For each (dialect, spec): compile succeeds; `validate_read(sql, engine, parameters)` passes; `sqlglot.parse_one(sql, read=dialect.sqlglot_dialect)` has no errors and `transpile(sql, read=d, write=d)` reparses; the set of `:pN` placeholders equals the parameter keys; no value text appears in `sql`; quote characters match the dialect (no `"` identifiers in MySQL output); no output alias appears in `ORDER BY` or `GROUP BY`.
- SQLite executes for real through `SQLiteConnector` (authorizer on) and results are compared with a Python oracle over the fixture data: every filter op incl. NULL rules for `ne`/`not_in`, LIKE escaping (`%`, `_`, `!` in values), case-insensitive ASCII `contains`, date-only ranges on datetime, `COUNT(DISTINCT)`, `SUM(CASE WHEN ...)`, null ordering last in both directions, stable paging (page 1 plus page 2 equals the unpaged order), joins and fan-out counts.
- Bucket labels on SQLite for every date in 2019-12-20..2028-12-31: day, week (`isocalendar`), month, quarter, year equal the oracle.
- Live containers (env-gated, `pytest.mark.skipif(not os.environ.get(...))`): `TERMX_TEST_PG_URL` (for example `postgresql+psycopg://termx:termx@127.0.0.1:55432/termx`), `TERMX_TEST_MYSQL_URL` (`mysql+pymysql://root:termx@127.0.0.1:53306/termx`), `TERMX_TEST_MARIADB_URL` (`mysql+pymysql://root:termx@127.0.0.1:53307/termx`). Each loads the same fixture rows into a uniquely named schema or database (dropped in teardown), compiles the corpus for that dialect, executes with SQLAlchemy `text()` using the compiled SQL and parameters, and compares to the same oracle, including the bucket labels for the whole date range and `ONLY_FULL_GROUP_BY` (MySQL default, set explicitly on MariaDB for the run). One test also runs the corpus through `NetworkSQLConnector.execute` when `TERMX_TEST_NETWORK_ROLE=1` and the account is a restricted read-only role. Container commands, documented at the top of the module and not run by CI: `docker run -d --rm --name termx-test-pg -e POSTGRES_PASSWORD=termx -e POSTGRES_USER=termx -e POSTGRES_DB=termx -p 55432:5432 postgres:16`, `docker run -d --rm --name termx-test-mysql -e MYSQL_ROOT_PASSWORD=termx -e MYSQL_DATABASE=termx -p 53306:3306 mysql:8.4`, `docker run -d --rm --name termx-test-mariadb -e MARIADB_ROOT_PASSWORD=termx -e MARIADB_DATABASE=termx -p 53307:3306 mariadb:10.11`, removed with `docker rm -f termx-test-pg termx-test-mysql termx-test-mariadb`.

`tests/test_data_query_spec.py` (B7 to B9)
- Model: `extra='forbid'` (unknown key, `pivot` key), more than 4 joins, join without `on`, limit 0 and 1001, negative offset, bool/float limit, `between` with one value, `in` with 0 and 201 values, `contains` with a number, `is_null` with a value, depth 5 groups, 51 leaves, empty nested group, non-finite floats.
- Names: unknown table, unknown column, wrong case (`Orders`), table from another alias, duplicate alias differing in case, join `on` referencing a later join, `join_type_mismatch` (text to integer), `sum` on text, `bucket` on text for PostgreSQL and MySQL but allowed with a warning on SQLite, `summary_conflict`, `duplicate_label`.
- Injection attempts. Each case asserts one of two outcomes: a 422 or `ok:false` error, or an executed statement in which the hostile text appears only inside a quoted identifier or a bound parameter. Cases: table `orders" ; DROP TABLE orders; --`; column `id") OR 1=1 --`; column `amount; DELETE FROM orders`; alias `a" OR "1"="1` (fails the regex); alias with a space or Unicode; label `x" FROM sqlite_master --`; label `a:b` and `p1`-lookalike `:p1`; label with NUL, newline and 61 characters; sort `output` `amount; DELETE`; sort column that is not selected; sort direction `desc; DROP` (rejected by the Literal); filter value `' OR 1=1 --`, `\`, `%`, `_`, a 500-character string, a NUL byte; `in` values containing quotes; `table` `sqlite_master` and `main.sqlite_master` and `sqlite_schema` (unknown, because the schema excludes `sqlite_%`); column `load_extension('x')`; schema-qualified column `main.orders.id`; Unicode confusables (full-width quote U+FF02) in names. After every case the fixture DB is byte-identical to its pre-test hash, no new table exists, and `sqlite_master` was never read.
- Values: every value reaches the statement as `:pN`; `parameters` keys are exactly `p1..pN`; the same spec twice yields byte-identical SQL and parameters; `limit` and `offset` are the only integers rendered into SQL.
- Fan-out: ON covering the whole composite PK is `none`, partial is `many`, no PK is `unknown`; the `fan_out` warning appears only when aggregates are present.
- Relations: declared FK; composite FK; name match for `customer_id` to `customers.id` including plural forms; same-name PK match; no duplicate for a declared pair; `?table=` filter; cap of 200; deterministic order.
- Schema annotation: `value_kind` on every column; `foreign_keys` on SQLite through the authorizer's new pragma (the authorizer still denies other pragmas such as `table_info` outside schema mode and `writable_schema` always); `row_estimate` null on SQLite.
- Unsupported engine: monkeypatched ready engine without a dialect gives 422 on compile, run and relations with the exact message, and 200 `supported:false` on capabilities.

`tests/test_data_query_http.py` (B10)
- Roles on all four routes: owner and editor succeed; viewer 403; outsider 404; unauthenticated 401; locked session 423; a connection owned by another user and not shared 403; a connection from another workspace 404.
- Network engine (stubbed connector, as in `test_data_runtime`) without a verified `read_policy`: `run` returns 409 "Test this source first to verify its current read-only role and session."; `compile`, `relations` and `capabilities` succeed (matching `/schema`) and `capabilities.source_tested` is false; after a test it flips to true; changing the credential generation flips it back and run returns 409 again.
- Idempotency: the same `request_id` and spec returns the same job id and one job row; the same `request_id` with a different spec returns 409 "This request_id was already used with a different query."; two different request ids create two jobs.
- Run returns the same job keys as `POST /query-runs`; polling to completion yields `result.rows` of `fetch_limit` rows for `limit: 200` and `truncated: true` for `limit: 1000` on 1,305 rows; cancel and CSV export work on a spec job.
- Authority changes between compile and run (member removed, source updated) discard the result exactly as the existing runtime test expects.
- Schema cache: a second compile does not re-open the source (spy on the connector); a connection revision bump or a credential change invalidates it; an expired entry reloads; a cache hit still enforces role and share checks.
- Contract fixtures: every file in `desktop/workspace/src/lib/query-contract-fixtures/` is loaded; each `compile-*.json` request is compiled against the fixture database and the response equals the stored response (SQL text, parameters, columns, joins, warnings; `schema_hash` is checked for presence and format only); `relations.json`, `capabilities.json` and `schema.json` likewise. This is the guard that keeps the two lanes aligned.
- `/api/data/bootstrap` reports `capabilities.visual_query` true.

### 6.3 Frontend tests

Vitest on Node 22. Component tests render with `render()`, mock `request` from `lib/api` as the existing tests do, and run `axe-core` (as `components/ui/form-field.test.tsx` does) on every new view in each state.

- `components/ui/{tabs,popover,dropdown-menu}.test.tsx`: roles, arrow-key and Esc behaviour, focus returns to the trigger, axe, layer registration with `navigation-layer.ts` (a floating sidebar does not close while a popover is open).
- `lib/query-spec.test.ts`: `describeFilter` for every op and kind, `uniqueAlias`, `emptySpec`, row-window helpers (`hasMore`, `nextOffset`) including the probe row and `truncated` from the byte cap.
- `lib/data-query.test.ts`: against the shared fixtures. `compileSpec` debounces and aborts a stale call; `runSpecToCompletion` posts one `request_id` per submit, polls with backoff, cancels on abort, and handles failed, cancelled and unknown job states; a double-click submits once; 409 surfaces the "Test connection" action; `errors` map to chips and fields.
- `lib/data-tabs.test.ts`: open, focus existing, close selects the neighbour, persistence stores ids only (assert no filter values, SQL or results in `localStorage`), tolerance for blocked storage.
- `DataNavigation.test.tsx`: groups and 36px row class, search filters tables and saved queries, virtualisation above 200 tables, viewer view, all states, keyboard (arrow focus between rows is not required; Tab order and Enter are), portal into `navigationHost` and inline fallback.
- `DataTabs.test.tsx`: arrows, Delete closes, close buttons labelled, axe, narrow-width overflow.
- `TablesOverview.test.tsx`: list/grid toggle and remembered preference, "Count rows" for SQLite (issues a count spec and announces the number), empty, loading, error, 409, unreadable table.
- `QueryGrid.test.tsx` (with `test-utils/virtual.ts`): only the visible window renders for 1,000 rows; header sort cycles and shift-click adds keys; `aria-sort`, `aria-rowcount`; right-aligned numbers; empty cell label; filter button opens the popover and Esc returns focus.
- `ColumnFilter.test.tsx`: operators by `value_kind` and by `filter_ops`; list parsing for "is any of"; date and boolean inputs; invalid value shows an inline error and does not add a chip.
- `FilterChips.test.tsx`: sentences, edit, remove (button and Delete key), Match all/any switch, nested group, 50-leaf cap message.
- `JoinPanel.test.tsx`: suggestions render with "Likely" label for name matches, adding a suggestion produces the join and refreshes columns, fan-out message is announced, four-join cap, manual builder validation.
- `SummarizePanel.test.tsx`: sentence preview, live apply, bucket disabled with reason for an unsupported dialect, clear returns to the detail grid, `fan_out` warning shown.
- `ShowSql.test.tsx`: shows compile SQL read-only (no textbox), values list, copy, compile-error state.
- `AdvancedSql.test.tsx` (with `test-utils/codemirror.ts`): off by default; menu item opens a non-modal panel (the grid stays interactive and focusable); Esc closes; the open preference is remembered per owner and never stores SQL; the editor has `aria-label="SQL query"` and `id="data-sql"`; Run submits one parameterised job; parameters validation; viewer has no menu item; lazy chunk loading state.
- `data-studio.test.tsx` and `database-studio.test.tsx`: the updated versions described in 5.12, with every existing behavioural assertion preserved (zero source contact before an explicit action, stale response discarded, lock clears drafts, owner change clears drafts, stale workspace snapshot discarded, double submit, CSV scope text and URL revocation, viewer restrictions, unavailable engines, revision change clears results).
- Styling guards (`data-styles.test.ts`): reads `data-studio.css` and `bi-studio.css` and asserts no `!important`, no `min-height:44px` outside a `pointer: coarse` or `max-width` touch block, no `gradient`, no hex colour literals, no `.data-resize`.
- Type-check (`tsc -b`) and the hex/arbitrary-value `rg` checklist command must be clean.
- Manual and browser QA (task F13): 375px and 1280px, light and dark, each state in 5.13, keyboard-only pass, reduced motion; screenshots listed in the PR with any gap named. `tests/workspace_data_studio_e2e.py` is extended in I1.

## 7. Tasks in dependency order

Commit prefix per lane: `data(be):` and `data(fe):`. Each task starts with its failing tests.

### T0. Contract freeze (first commit on the PR branch; unblocks both lanes)

Owner: frontend engineer, reviewed by backend engineer; 1 hour.
1. Add `src/lib/query-spec.ts` types exactly as in 4.6.
2. Add `src/lib/query-contract-fixtures/`: `compile-detail.json`, `compile-summary.json`, `compile-errors.json`, `run-job-queued.json`, `run-job-completed.json`, `relations.json`, `capabilities-sqlite.json`, `capabilities-unsupported.json`, `schema.json` (the fixture schema with `value_kind`, `foreign_keys`, `row_estimate`). Each file is `{request, response}` using the examples in section 4.
3. After T0, backend owns the fixtures. A field change needs a commit that edits section 4 of this plan and the fixtures together.

### Backend lane

- **B1. Validator characterization and allowlist.** Tests in `test_data_validate_read.py` (fail on current code), then the `SAFE_FUNCTIONS` additions from 1.3. Depends on nothing.
- **B2. Dialect base and registry.** `base.py`, `__init__.py`, shared helpers, abstract interface tests, unsupported-engine message. Depends on B1.
- **B3. SQLite dialect.** Includes the week and quarter expressions, version-gated null ordering, real-execution bucket oracle. Depends on B2.
- **B4. PostgreSQL dialect.** Golden SQL and sqlglot checks. Depends on B2. Parallel with B3 and B5.
- **B5. MySQL and MariaDB dialects.** `_mysql_family.py`, `mysql.py`, `mariadb.py`, golden SQL and sqlglot checks for both. Depends on B2.
- **B6. Schema enrichment.** `foreign_keys` and `row_estimate` in both connectors, authorizer pragma, `value_kind` annotation in `DataRuntime.schema()`. Depends on B2 for `classify_type`; parallel with B3 to B5.
- **B7. QuerySpec models.** Structural validation tests and models. Independent; can start with B1.
- **B8. Compiler.** Name resolution, joins, filters, summary mode, sort, paging, injection suite, corpus on SQLite. Depends on B3, B6, B7. The corpus for PostgreSQL and MySQL/MariaDB joins in as B4 and B5 land.
- **B9. Relations.** Depends on B6.
- **B10. Service and routes.** `query_service.py`, `query_http.py`, `mount_data` and bootstrap flag, HTTP, role, 409, idempotency and cache tests, contract-fixture test. Depends on B8, B9.
- **B11. Conformance suite and live tests.** Parametrised corpus for all four dialects, env-gated container tests, docs in the module header. Depends on B4, B5, B8. Run the live tests locally once with containers before merging and remove the containers afterwards.

### Frontend lane

- **F0. Dependencies and wrappers.** `pnpm install --frozen-lockfile`, `pnpm add` the four packages, `ui/tabs`, `ui/popover`, `ui/dropdown-menu` with tests. Selector inventory for the existing Data tests (5.12). Independent.
- **F1. API client and helpers.** `lib/query-spec.ts` helpers, `lib/data-query.ts`, `lib/data-tabs.ts`, `lib/data-grid.ts`, `test-utils/`, tests against the T0 fixtures. Depends on T0.
- **F2. Extract `DatabaseStudio`.** `SourceDialog.tsx`, `use-sql-run.ts`; no behaviour change; existing tests unchanged and green. Depends on nothing.
- **F3. Shell and navigation.** `App.tsx` host and sidebar state, `DataNavigation`, `DataStudio` composition, `data-studio.css` rewrite (5.11), Copilot restyle and resizer removal, style guard test. Depends on F0, F2.
- **F4. Tab strip and Overview.** `DataTabs`, `TablesOverview`. Depends on F0, F1, F3.
- **F5. Table tab and grid.** `TableTab`, `QueryGrid`, `ColumnFilter`, paging, CSV export, states. Depends on F1, F4.
- **F6. Chips and groups.** Depends on F5.
- **F7. Join panel.** Depends on F5.
- **F8. Summarize and Show SQL.** Depends on F5. Parallel with F6 and F7.
- **F9. Advanced SQL.** `SqlEditor`, `AdvancedSql`, test updates from 5.12. Depends on F2, F3.
- **F10. QA pass.** Axe in every state, keyboard pass, 375px and 1280px in light and dark, the design checklist, `tsc -b`, full vitest run on Node 22. Depends on F5 to F9.

### Integration

- **I1. End to end.** After B10 and F5: run the real backend and frontend together, update `tests/workspace_data_studio_e2e.py` (selectors from 5.12; add a visual-query path: open a table, add a filter, sort, combine, summarize, Show SQL, CSV), confirm the 409 "Test connection" path with a network-engine stub. Depends on B10, F10.

### Parallelism

After T0, B1, B7, F0 and F2 start immediately and touch disjoint files. The first point where one lane needs the other is I1; until then the frontend builds on the fixtures and the backend proves the same fixtures.

## 8. Verification commands

Backend, from the worktree root:

```
uv sync --all-extras --group dev
uv run pytest tests/test_data_validate_read.py tests/test_data_dialects.py tests/test_data_query_spec.py tests/test_data_query_http.py tests/test_data_core.py tests/test_data_runtime.py tests/test_data_bi.py tests/test_data_copilot.py -q
TERMX_TEST_PG_URL=... TERMX_TEST_MYSQL_URL=... TERMX_TEST_MARIADB_URL=... uv run pytest tests/test_data_dialects.py -q    # local, containers up
```

Frontend, from `desktop/workspace`:

```
pnpm install --frozen-lockfile
npx -y -p node@22 node node_modules/vitest/vitest.mjs run src/features/data src/lib/query-spec.test.ts src/lib/data-query.test.ts src/lib/data-tabs.test.ts src/components/ui
npx -y -p node@22 node node_modules/typescript/bin/tsc -b
rg -n '#[0-9a-fA-F]{3,8}\b|-\[(#|\d)[^\]]*\]' src --glob '!*.css'     # new files: no matches
```

## 9. Decisions to confirm

These are choices this plan made where the spec was silent or wrong. Each is reversible inside one module.

1. `ne` and `not_in` include rows where the column is empty. Standard SQL would drop them.
2. `contains` and `starts_with` are case-insensitive on every engine through `LOWER()` on both sides. This cannot use an index; it is accepted for a preview-sized builder. SQLite folds ASCII only, and the capability matrix says so.
3. A date-only filter value against a datetime column means the whole day.
4. SQLite text columns may be grouped by date (with a warning), because most SQLite files store dates as text. PostgreSQL and MySQL/MariaDB require a date or datetime type.
5. Fetch limit is `limit + 1` so the grid knows when more rows exist.
6. `compile` and `relations` do not require a verified source test (they match `/schema`); `run` does. `capabilities.source_tested` lets the UI ask for the test before the first run.
7. The validator fix (section 0, item 1) ships in this PR because nothing compiled can run without it.
8. `@codemirror/lang-sql` is added as a fourth dependency.
9. The `row_estimate` and `foreign_keys` additions to `/schema` are additive and needed by the overview and join suggestions.
10. The repo's neutral-chrome `--accent` is kept; only Data's own overrides are removed (section 0, item 8).
