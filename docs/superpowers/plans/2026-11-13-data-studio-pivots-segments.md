# Data Studio pivots, segments and audit (PR 2) implementation plan

Date: 2026-11-13. Spec: `docs/superpowers/specs/2026-11-12-data-studio-query-ux-design.md` (approved). Builds on PR 1: `docs/superpowers/plans/2026-11-12-data-studio-foundation.md` (branch `worktree-wf_5c5c02ca-43a-9`, already merged into this worktree). Scope: PR 2 only. The Copilot sidebar (PR 3) and BI reports built on views (PR 4) are out of scope.

Two lanes can start after task T0. The backend lane touches `src/termx/data/**` and `tests/**` (Python). The frontend lane touches `desktop/workspace/**`. They meet at one contract (section 4), frozen by T0 and checked on both sides against the same JSON fixtures, exactly as in PR 1.

## 0. Findings from the code that shape this plan

Checked against the merged tree (`4d6dc68`).

1. **Jobs are private to the actor who started them, and viewers cannot start any.** `DataRuntime._authorize_job` returns 404 unless `job.actor_id == actor.id`, and `_source` calls `service.require(..., write=True)` unless `from_report` is set. Views need a third authority path next to "editor reads" and "report reads": a **view read** (`view_id` on the run). A viewer may run a view only when the view's connection has `shared_read`. The `_view_id` is stored on the job (server managed, like `_from_report`) so every poll, cancel and export re-checks it.
2. **`QueryService._authorize_connection` demands `write=True`, and `DataRuntime.schema` too.** A viewer running a view still needs a schema index to validate names. The schema read is split: `DataRuntime.schema` keeps its role checks and calls a new `schema_for_authorized(connection, wid)` that does the source work. Viewers never receive the schema; only the compiler sees it.
3. **`DataStore` has one generic table (`data_records`) and a single `user_version` guard (`> 1` refuses to open).** Views reuse `data_records` with `kind='view'`, which gets the revision counter, the 409 and the archived flag for free. Edit history needs a new append-only table. That bumps `user_version` to 2, so a PR 1 build refuses to open a migrated store with its existing "requires a newer TermX version" 409. This is deliberate and documented (section 2.3); the alternative (leaving the version at 1) lets an old build silently ignore history rows.
4. **`_RESERVED` forbids `id`, `workspace_id`, `revision`, `created_at`, `updated_at` in a record body, and the store key `kind` is a column.** The view body therefore uses `view_kind` for table/pivot/segment. The API exposes it as `kind`.
5. **The compiler is pure and must stay pure.** Pivot columns need data (the distinct values) and segments need other records. Both arrive as arguments: `compile_spec(spec, schema, dialect, *, segments, pivot_values)`. A new `compile_pivot_probe` builds the bounded distinct query. No I/O in `query_spec.py`.
6. **`Dialect.conditional_aggregate` exists but is wrong for `avg`.** It emits `AVG(CASE ...)`, bypassing `avg_expr`, so PostgreSQL returns `numeric` and MySQL `decimal` where SQLite returns a double. Task B-P1 routes `avg` through `avg_expr` and adds a test on all four dialects. `capabilities().dialect.pivot` is `False` today and becomes `True`.
7. **Output labels are the SQL aliases today (`_Out.label`), and `finish_labels` truncates PostgreSQL aliases at 63 bytes.** A pivot column label is user data (`"North"`, `"2026-01"`, 200 characters, `a:b`, an empty string). Pivot SQL aliases are therefore always neutral (`pv_r1`, `pv_c1` ... `pv_c12`, `pv_total`) and the display label travels as metadata (`columns[].label`). CSV headers come from that metadata. This also avoids two labels that differ only by case colliding as MySQL aliases.
8. **Segment expansion happens by inlining the segment's filter tree, not by a subquery.** Same base table, no `IN (SELECT ...)`, so `validate_read` and the network connector's base-table-only rule (`NetworkSQLConnector.execute` rejects anything but registered base tables) need no change. `NOT` membership uses `CASE WHEN (<expr>) THEN 1 ELSE 0 END = 0`, which is correct under SQL three-valued logic on all four engines. A plain `NOT (<expr>)` drops rows where the expression is NULL.
9. **The existing `ApiError` carries only a string.** For object `detail` values it stores `JSON.stringify(detail)`. View routes return `{"detail": {"code", "message", ...}}` for coded errors, and `lib/data-views.ts` parses that message (`viewError()`). `lib/api.ts` is not changed.
10. **Two phase-1 leftovers are real.** `DataStudio.tsx` line 230 sets `notice` ("Connection saved. Test it when you are ready.") and nothing clears it on navigation, workspace change or a later successful action; and `TablesOverview.tsx` line 108 renders the "Rows" `<th class="data-number">` inside a table that clips at 375px (section 5.9).

## 1. Scope and non-goals

In: saved views (table, pivot, segment), their history and run history, pivot compilation on four engines, segments and `{segment}` filter nodes, drill-down, CSV export of view runs, the sidebar groups and tabs, the Audit panel, 409 handling, and the two leftovers.

Out (each is a follow-up, listed so nobody assumes it):
- A bottom "Total" row for pivots. Non-additive aggregates (average, distinct count) cannot be summed on the client, and a second statement per run is not worth it in this PR. Pivots get a right-hand **Total** column computed by a plain aggregate.
- More than 12 pivot columns, an "Other" bucket, two column dimensions, and more than two row dimensions.
- Segments over a pivot or a summary (a segment is always a filter set over a base table plus its joins).
- Materialized segment membership snapshots (spec: out of scope).
- Restoring an archived view and restoring an old revision. History is read-only this PR.
- Retention for run history. Jobs already keep their result for ever; see section 12 risk R3.
- Copilot tools and BI reports built on views.

## 2. Storage

### 2.1 Records (no schema change)

`data_records` row, `kind='view'`, `workspace_id=wid`, `id` = uuid4 hex. `body` (JSON):

```json
{
  "view_kind": "pivot",
  "name": "Sales by region and month",
  "name_key": "sales by region and month",
  "description": "",
  "connection_id": "c1f0...",
  "base_table": "sales",
  "spec": { "...QuerySpec...": "validated, stored as sent after Pydantic normalisation" },
  "references": ["<segment id>", "..."],
  "created_by": "<principal id>",
  "created_by_name": "Alice",
  "updated_by": "<principal id>",
  "updated_by_name": "Alice",
  "archived": false
}
```

- `name_key` is `name.strip().casefold()`. Names are unique per `(workspace, view_kind)` among non-archived views. The check runs inside the write transaction (409 `name_taken`).
- `references` is the sorted list of segment ids found in `spec.filters` (direct references only). It lets "who uses this segment" be answered with one `json_each` scan, and it is recomputed on every write.
- Limits: 500 non-archived views per workspace (409 `view_limit`), spec at most 100,000 bytes serialised, name 1 to 200 characters, description at most 1,000.
- The spec always carries `source` (the connection id). `connection_id` is derived from it and never accepted separately.

### 2.2 New table and index (additive)

```sql
CREATE TABLE IF NOT EXISTS data_view_revisions (
    view_id TEXT NOT NULL, revision INTEGER NOT NULL,
    workspace_id TEXT NOT NULL, actor_id TEXT NOT NULL, actor_name TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('created','updated','archived')),
    created_at TEXT NOT NULL,
    name TEXT NOT NULL,
    snapshot TEXT NOT NULL CHECK(json_valid(snapshot)),   -- {view_kind,name,description,spec}
    changes  TEXT NOT NULL CHECK(json_valid(changes)),    -- list of Change, section 4.7
    PRIMARY KEY (view_id, revision)
);
CREATE INDEX IF NOT EXISTS data_view_revisions_ws ON data_view_revisions(workspace_id, view_id, revision DESC);
CREATE INDEX IF NOT EXISTS data_job_view ON data_records(json_extract(body,'$.view_id'), created_at)
    WHERE kind='job' AND json_extract(body,'$.view_id') IS NOT NULL;
```

The revision row is written in the same `BEGIN IMMEDIATE` transaction as the record write, with the record's new `revision` number. A failure on either rolls back both.

### 2.3 Migration (`DataStore.__init__`)

1. Open as today. If `PRAGMA user_version` is greater than **2**, refuse with the existing 409.
2. If `user_version == 1` and `data_records` holds any row, first copy the file with `sqlite3.Connection.backup` to `data.sqlite3.pre-views-v1` in the same private directory (mode 0600 via `protect_private_path`), unless that file exists. This is the "back up before a migration" step; it is never deleted by TermX.
3. In one transaction: `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` as above, then `PRAGMA user_version=2`. No existing table or row is altered. Re-running is a no-op.
4. Jobs written before the migration have no `view_id`, so they never appear in a view's run history. Nothing is back-filled.

Downgrade: a PR 1 build refuses to open a version 2 store (clear 409, no writes). Restoring `data.sqlite3.pre-views-v1` reverts to the PR 1 state, losing views made since.

### 2.4 Job body additions (additive)

Stored on `kind='job'` records created through a view or `query-specs/run`:

| Key | Visible | Meaning |
| --- | --- | --- |
| `_view_id` | no | Server-managed authority, like `_from_report`. |
| `view_id`, `view_revision` | yes | Which view and which revision ran. Indexed. |
| `mode` | yes | `rows` or `count`. |
| `output` | yes | `{columns, warnings}` copied from the compile (labels, kinds, pivot metadata). The UI renders from this, never from `result.columns[].name`. |
| `duration_ms` | yes | Monotonic time around `execute_read`, written with the terminal status. |

`DataRuntime._public_job` adds `view_id`, `view_revision`, `mode`, `output`, `duration_ms` to its allowed keys. `_read_definition` is unchanged; the extra data travels as keyword arguments to `DataRuntime.run(..., view_id=None, extra=None)`. `view_id` is part of the request digest, so the same `request_id` on a different view or revision is a 409.

## 3. QuerySpec changes

### 3.1 Models (`src/termx/data/query_spec.py`)

```python
class FilterSegment(Spec):
    segment: str = Field(min_length=1, max_length=64)
    negate: bool = False

# _filter_tag: 'segment' if the dict has a 'segment' key, 'group' if op in (and, or), else 'leaf'.
# FilterNode becomes Union[leaf, group, segment].

class PivotDim(Spec):
    table_alias: Alias
    column: Name
    bucket: Bucket | None = None
    label: Label | None = None

class PivotValue(Spec):                      # same shape and rules as Aggregate
    fn: Literal['count','count_distinct','sum','avg','min','max']
    table_alias: Alias | None = None
    column: Name | None = None
    label: Label | None = None

class PivotSort(Spec):
    by: Literal['rows', 'total'] = 'rows'
    direction: Literal['asc', 'desc'] = 'asc'

class PivotSpec(Spec):
    rows: list[PivotDim] = Field(min_length=1, max_length=2)
    columns: PivotDim | None = None
    value: PivotValue
    totals: bool = True
    sort: PivotSort = PivotSort()

class QuerySpec:      # one new optional field
    pivot: PivotSpec | None = None
```

`PIVOT_MAX_COLUMNS = 12`, `PIVOT_PROBE_ROWS = 13`, `SEGMENT_MAX_DEPTH = 3`, `SEGMENT_MAX_EXPANSIONS = 20`, `SEGMENT_MAX_LEAVES = 200` live in `query_spec.py`.

Spec-level model validation (`extra='forbid'` stays):
- `pivot` present: `select`, `group_by`, `aggregates` and `sort` must be empty (error `pivot_conflict`, path of the offending section). `limit` is the row limit (default 200, max 1000); `offset` pages rows.
- A pivot with `columns` null and `totals` false has no value column: model error.
- Segment filter nodes count as one leaf each toward the existing 50-filter cap and add to nesting depth like a leaf.

### 3.2 Compiler rules (new `src/termx/data/pivot.py`, `src/termx/data/segments.py`)

`compile_spec(spec, schema, dialect, *, segments: SegmentSet | None = None, pivot_values: PivotValues | None = None)`.
`PivotValues = list[PivotValue]` where each item is `{value: Scalar | None, empty: bool}`; `None` means "validate structure only" (used when saving a view; the SQL has no cell columns and `columns` metadata lists only rows and total).

Pivot (all errors use the existing `SpecError` shape):

| Code | Path | Rule |
| --- | --- | --- |
| `pivot_conflict` | `select`/`group_by`/`aggregates`/`sort` | Must be empty when `pivot` is set. |
| `pivot_duplicate_dimension` | `pivot.rows[i]` / `pivot.columns` | The same column+bucket cannot appear twice across rows and columns. |
| `pivot_needs_bucket` | `pivot.columns.bucket` | A `date`/`datetime` column dimension (rows or columns) without a bucket. Rows without a bucket are allowed for date kinds (one row per value); columns are not. |
| `pivot_column_kind` | `pivot.columns.column` | Allowed kinds for the column dimension: `text`, `integer`, `boolean`, `uuid`, and `date`/`datetime`/`text`/`other` with a bucket. Refused: `number` (decimal equality is not exact across drivers), `json`, `binary`, `array`, `time`, `other` without a bucket. |
| `pivot_too_many_columns` | `pivot.columns` | The probe found 13 distinct values. Message: "Month has more than 12 different values. Pivots show at most 12 columns. Group by a larger step, or filter first." |
| `pivot_value_*` | `pivot.value` | Reuses the aggregate rules: `sum`/`avg` need a number column, `min`/`max` an orderable kind, `count_distinct` not on JSON. |
| `label_*` | `pivot.rows[i].label`, `pivot.value.label` | Existing label rules (length, colon, control characters). Labels are metadata only, so the 63-byte PostgreSQL limit does not apply. |

Statement shape (all engines; dialect fragments in section 3.3):

```
SELECT <row expr 1> AS "pv_r1" [, <row expr 2> AS "pv_r2"]
     , <fn>(CASE WHEN <col pred 1> THEN <value> END) AS "pv_c1" ... up to "pv_c12"
     , <fn>(<value>) AS "pv_total"                       -- when totals, or when columns is null
FROM <base + joins>
WHERE <filters> [AND (<col expr> IN (<probe values>) [OR <col expr> IS NULL])]
GROUP BY <row exprs, via dialect.group_by_items>
ORDER BY <pivot.sort, then row exprs as tie-breakers, via dialect.order_item>
LIMIT <limit+1> OFFSET <offset>
```

- `count` without a column: `COUNT(CASE WHEN p THEN 1 END)`. Cells with no rows are `0` for the count family and `NULL` for `sum`, `avg`, `min`, `max`, on every engine.
- The WHERE restriction to the probed values keeps the right-hand total equal to the sum of the visible cells when rows arrive between the probe and the run.
- Every user-derived value (probe values, filter values) is a bound `:pN`. Labels, row values and column values never enter SQL text. Quoted text in SQL is only dialect-quoted identifiers and the fixed `pv_*` aliases.
- Column order is deterministic and engine independent: non-null values sorted in Python (`bool` false before true, numbers ascending, strings by code point, bucket labels as strings, which sorts canonical labels chronologically), then the NULL column last. The probe query has no `ORDER BY`, so the database collation never decides which 12 values appear.
- The NULL column is labelled "(empty)" and flagged `column_empty: true` in metadata. A real text value `(empty)` is distinguishable by that flag.
- Row order: `pivot.sort.by = rows` orders by the row expressions (NULLS LAST, existing `order_item`); `total` orders by the total expression, ties broken by the row expressions. Text row order follows each engine's grouping collation as in PR 1 (MySQL family binary, SQLite binary, PostgreSQL its database collation). Documented, not unified.

Probe (`compile_pivot_probe`): `SELECT <col expr> AS "v" FROM <base + joins> WHERE <filters> GROUP BY <col expr> LIMIT 13` (via `group_by_items`). The caller executes it with `runtime.execute_read(..., max_rows=13)`: same read-only validation, deadline, source authority and fingerprint recheck as every run. 13 rows returned means `pivot_too_many_columns`; no subset is ever chosen.

Segments (`segments.py`):

```python
@dataclass(frozen=True)
class ResolvedSegment:
    id: str; name: str; revision: int; source: str
    base: TableRef; joins: tuple[Join, ...]; filters: FilterGroup

SegmentSet = dict[str, ResolvedSegment]     # the transitive closure, loaded by the service
```

`expand_segments(spec, segments)` runs inside the compiler before `filters()`:
- Each `{segment: id}` is replaced by the segment's filter tree with aliases remapped into the consumer's namespace: the segment's base alias maps to the consumer's base alias; the segment's table must equal the consumer's base table, else `segment_base_mismatch` (names both tables).
- Each segment join must exist in the consumer (same kind, same table, same ON pairs after alias mapping), under the same alias or another one; the mapping follows the equivalent join. A missing join is `segment_join_missing` ("This segment needs the Regions table combined first"). An alias already used for a different table is `segment_alias_conflict`.
- Cycle detection by an id stack (`segment_cycle`, message names the loop with segment names), depth limit 3 (`segment_depth`), at most 20 expansions and 200 leaves after expansion (`segment_too_large`), a segment from another source is `segment_source_mismatch`, an id missing from `segments` (unknown, archived, other workspace, or not visible to the actor) is `segment_missing`. All carry the `filters.items[i]` path of the outermost reference.
- Negation: `negate=false` renders the expanded expression as is (parenthesised when it has more than one part). `negate=true` renders `CASE WHEN (<expr>) THEN 1 ELSE 0 END = 0`. An empty segment (no filters) matches every row; `negate` on it matches none. The compile result carries the warning `segment_empty`.
- The only new warning added to `CompiledQuery.warnings` is `segment_empty`; date-text warnings reuse `text_date`.

### 3.3 Per-dialect behaviour (normative)

Everything below is a method on the dialect, so one engine difference stays a one-class change. New methods are marked.

| Concern | SQLite | PostgreSQL | MySQL | MariaDB |
| --- | --- | --- | --- | --- |
| Cell aggregate | `SUM(CASE WHEN p THEN v END)` | same | same | same |
| `avg` cell and total | `AVG(CASE ...)` (double) | `CAST(AVG(CASE ...) AS DOUBLE PRECISION)` | `CAST(AVG(CASE ...) AS DOUBLE)` | same as MySQL |
| Cell predicate, text | `x = :p` | `x = :p` | `x COLLATE utf8mb4_bin = :p` (`text_operand`, or `CONVERT(x USING utf8mb4) COLLATE utf8mb4_bin` for another charset) | same as MySQL |
| Cell predicate, bucket | `strftime(...) = :p` | `to_char(x,'...') = :p` | `date_format(...) = :p`; quarter via `CONCAT` | same as MySQL |
| NULL column | `expr IS NULL` | `expr IS NULL` | `expr IS NULL` | `expr IS NULL` |
| Boolean column value bind | `1` / `0` | `true` / `false` | `1` / `0` (driver) | `1` / `0` (driver) |
| Column dim kinds with bucket | `date`, `datetime`, `text`, `other` | `date`, `datetime` | `date`, `datetime` | `date`, `datetime` |
| Probe | `GROUP BY expr LIMIT 13` | same | `GROUP BY` through `group_by_items` (null-flag companion expression for derived expressions, `ONLY_FULL_GROUP_BY`) | `group_by_items` also adds the bare column |
| `GROUP BY` for rows | `group_by_items` | `group_by_items` | adds `(expr IS NULL)` for derived expressions | also adds the bare column |
| Order of NULL | `NULLS LAST` (or emulated below 3.30) | `NULLS LAST` | `(expr IS NULL), expr` | same |
| Statement deadline for the probe and the run | progress handler | `statement_timeout` | `MAX_EXECUTION_TIME` | `max_statement_time` |
| Week label | ISO `YYYY-Www` | `IYYY-"W"IW` | `%x-W%v` | same |
| Result number types | double / int | `numeric` and `bigint` converted by `column_kinds` | `decimal` converted by `column_kinds` | same |
| New dialect method | | | | |
| `conditional_aggregate(fn, value, predicate)` | base | base, `avg` via `avg_expr` | base, `avg` via `avg_expr` | base |
| `pivot_cell_value(kind, value)` returns the bound Python value | `bool -> int` | `bool` | `bool` | `bool` |
| `capabilities().dialect.pivot` | `{max_columns:12, max_row_dims:2, column_kinds:[...]}` | same shape | same shape | same shape |

An engine without an adapter fails closed: `compile`, `run` and every `/views/*` route that needs a dialect return the existing 422 "The visual query builder supports ..." message, and `capabilities` returns `supported:false`. A view can still be listed and its history read on such a source.

### 3.4 Segment and view semantics

- A **table** view stores a detail or summary spec (everything a Table tab can build today).
- A **pivot** view stores a spec with `pivot`.
- A **segment** view stores a spec with `from`, `joins` and `filters` only. `select`, `group_by`, `aggregates`, `sort` and `pivot` must be empty (422 `invalid_view`); `limit` is ignored on save (stored as 200). Running a segment returns member rows (all columns of the base table and joins). A count run returns one row `{ "Members": n }`.
- Drill-down (section 4.6) works for pivot views and for table views that group; a segment's "underlying rows" is its own rows run.
- A segment may reference other segments (depth 3). A pivot or table view may reference any segment on the same source whose base table equals its base table.
- Editing a segment's filters or name is allowed while referenced; the response says how many views use it (`affected_views`). Changing a segment's base table, joins or source while it is referenced is 409 `segment_in_use`. Archiving a referenced segment is 409 `segment_in_use` and lists the users of it. A segment referenced only by an archived view counts as unreferenced.
- Reference integrity is enforced **inside the write transaction** (the same `BEGIN IMMEDIATE` that writes the record), so two concurrent saves cannot create a cycle or archive a segment under a new reference.

## 4. REST contract

All routes are under `/api/data/workspaces/{wid}`, behind the existing `managed_actor` (TLS, bearer or cookie plus CSRF, lock handling). Bodies go through `read_body` (1,000,000 bytes, depth 32, finite numbers, UTF-8). Unknown fields are 422. Coded errors use `{"detail": {"code": "...", "message": "...", ...}}`; the plain 401/403/404 strings stay as they are today.

Shared shapes:

```json
// Person
{ "id": "p_8f1c", "name": "Alice" }

// ViewSummary (list rows; no spec)
{
  "id": "v_3a9e", "kind": "pivot", "name": "Sales by region and month", "description": "",
  "connection_id": "c_91b2", "base_table": "sales", "revision": 3,
  "created_by": { "id": "p_8f1c", "name": "Alice" }, "updated_by": { "id": "p_8f1c", "name": "Alice" },
  "created_at": "2026-11-13T09:12:44.120934+00:00", "updated_at": "2026-11-13T10:01:09.552310+00:00",
  "used_by_count": 0,
  "can_edit": true, "can_run": true
}

// ViewDetail = ViewSummary plus:
{
  "spec": { "...QuerySpec, section 4.8...": true },
  "references": [ { "id": "v_77aa", "name": "Big spenders", "state": "ok" } ],     // state: ok | missing
  "used_by": [ { "id": "v_3a9e", "kind": "pivot", "name": "Sales by region and month" } ]   // editors and owners only; viewers get []
}
```

`can_edit` is true for owner/editor on an accessible connection. `can_run` is true when the actor may run this view now (viewer on a shared connection, or editor/owner on an accessible one).

### 4.1 `GET /views?kind=pivot&connection_id=c_91b2`

Both filters optional. Returns only non-archived views the actor can run (or, for editors, edit). A view on a connection the actor may not use is omitted, never listed as forbidden.

```json
{ "views": [ { "...ViewSummary..." } ] }
```

Roles: owner, editor, viewer (viewer sees only views on `shared_read` connections). Outsider 404, unauthenticated 401.

### 4.2 `POST /views` (201)

```json
{
  "kind": "pivot",
  "name": "Sales by region and month",
  "description": "",
  "spec": { "version": 1, "source": "c_91b2", "...": "section 4.8" }
}
```

Response: `ViewDetail` at `revision: 1`. Validation, in order: kind/spec shape (3.4), the connection is usable by the actor (`require_connection`, non-viewer rules), schema compile in validate mode (pivot values `None`), segment references (exist, same source, same base table, no cycle, depth), name uniqueness, caps. Failures:

| Status | `code` | When |
| --- | --- | --- |
| 403 | (string) | Viewer; connection private to someone else and not shared. |
| 404 | (string) | Outsider; unknown or foreign-workspace connection. |
| 409 | `name_taken` | Another view of this kind has that name (compared case-insensitively). |
| 409 | `view_limit` | 500 views already. |
| 409 | `source_untested` | Network source without a verified read policy and the schema is not cached (same rule as `run`). |
| 422 | `invalid_spec` | `errors: SpecError[]` from the compiler, including `segment_*` and `pivot_*` codes. |
| 422 | `invalid_view` | Kind and spec disagree (pivot kind without `pivot`, segment with `select`, ...). |
| 422 | (string) | Engine without an adapter (existing message). |

### 4.3 `GET /views/{id}` and `PATCH /views/{id}`

`GET` returns `ViewDetail`. A view that is archived, in another workspace, or on a connection the actor cannot use is 404.

`PATCH`:

```json
{ "revision": 3, "name": "Sales by region", "description": "Weekly review", "spec": { "...": "optional, whole spec" } }
```

At least one of `name`, `description`, `spec` is required besides `revision`. `kind` cannot change; `spec.source` cannot change to a connection the actor cannot use. The response is `ViewDetail` at `revision + 1`, plus for segments:

```json
{ "...": "ViewDetail", "affected_views": 2 }
```

A patch that changes nothing returns 200 with the same revision and writes no history row.

Revision conflict (stale `revision`):

```json
HTTP 409
{ "detail": { "code": "revision_conflict", "message": "Bob changed this view at 10:14. Reload to see the latest version.",
  "current_revision": 5, "updated_by": { "id": "p_21d0", "name": "Bob" }, "updated_at": "2026-11-13T10:14:02.004100+00:00" } }
```

Other coded errors: `name_taken`, `segment_in_use` (changing a referenced segment's base table, joins or source), `invalid_spec`, `invalid_view`. Roles: owner and editor; viewer 403.

### 4.4 `DELETE /views/{id}?revision=3`

Archives (soft delete). Allowed for the workspace owner and for the view's creator. Another editor gets 403 "Only the workspace owner or the person who created this view can delete it." Response 200 `ViewDetail` with the archived revision.

```json
HTTP 409
{ "detail": { "code": "segment_in_use", "message": "2 views use this segment. Remove it from them first.",
  "used_by": [ { "id": "v_3a9e", "kind": "pivot", "name": "Sales by region and month" }, { "id": "v_c04d", "kind": "table", "name": "Orders east" } ] } }
```

### 4.5 `GET /views/{id}/history?limit=50&before=3`

Newest first. `limit` 1 to 200 (default 50). `before` is a revision number (exclusive).

```json
{
  "view_id": "v_77aa",
  "revisions": [
    { "revision": 3, "action": "updated", "at": "2026-11-13T10:01:09.552310+00:00",
      "actor": { "id": "p_21d0", "name": "Bob" }, "name": "Big spenders",
      "changes": [
        { "field": "filters", "op": "added", "after": { "field": { "table_alias": "sales", "column": "amount" }, "op": "gte", "value": 5000 } },
        { "field": "filters", "op": "removed", "before": { "field": { "table_alias": "sales", "column": "amount" }, "op": "gte", "value": 1000 } }
      ] },
    { "revision": 2, "action": "updated", "at": "...", "actor": { "id": "p_8f1c", "name": "Alice" }, "name": "Big spenders",
      "changes": [ { "field": "name", "op": "changed", "before": "Large orders", "after": "Big spenders" } ] },
    { "revision": 1, "action": "created", "at": "...", "actor": { "id": "p_8f1c", "name": "Alice" }, "name": "Large orders", "changes": [] }
  ],
  "next_before": null
}
```

`Change` (also section 8): `{field, op: 'added'|'removed'|'changed', before?, after?}` where `field` is one of `name`, `description`, `from`, `joins`, `filters`, `filters.match`, `select`, `group_by`, `aggregates`, `sort`, `limit`, `pivot.rows`, `pivot.columns`, `pivot.value`, `pivot.totals`, `pivot.sort`. Rules: scalar fields compare by value; `joins`, `select`, `group_by`, `aggregates`, `sort`, `pivot.rows` compare as ordered lists of canonical JSON and report each added/removed item (a reordering alone is one `changed` entry with the whole lists); `filters` flattens leaves and segment nodes into canonical JSON and reports each added/removed one, with `filters.match` for a changed `and`/`or`; unchanged fields produce nothing. The server never writes sentences; the client renders them with `describeFilter` and friends. The `archived` revision has `changes: []`.

Roles: owner and editor on an accessible connection. Viewer 403 ("Edit history is available to editors and owners.").

### 4.6 `GET /views/{id}/runs?limit=50&before=<cursor>`, `POST /views/{id}/run`, `POST /views/{id}/drill`, export

**Run**

```json
POST /views/v_3a9e/run
{ "request_id": "5d7e...", "mode": "rows", "offset": 0 }
```

`mode` is `rows` (default) or `count` (segments and table views without a pivot; a pivot with `count` is 422). `offset` (0 to 1,000,000) overrides the spec offset for paging. Response: the same job object as `POST /query-specs/run`, plus the additive keys:

```json
{
  "id": "j_0b1c", "status": "queued", "connection_id": "c_91b2", "created_at": "...", "updated_at": "...",
  "preview_limit": 1000, "cancel_requested": false,
  "view_id": "v_3a9e", "view_revision": 3, "mode": "rows",
  "output": { "columns": [ "...OutputColumn, section 4.8..." ], "warnings": [] }
}
```

Steps: view loaded and authorised (4-way permission table, section 6), segments closure loaded, schema index (internal path for viewers), `require_verified_read`, pivot probe when needed, compile, safety-net `validate_read`, `runtime.run(..., view_id=..., extra={...})`. Idempotent: same `request_id` and same compiled statement returns the same job id (job ids are `identifier_for('query-run', wid, actor, request_id)` as today); the same `request_id` for a different statement, view or revision is 409 "This request_id was already used with a different query." Capacity limits and 429 are the existing ones. Poll with the existing `GET /jobs/{jid}`, cancel with `POST /jobs/{jid}/cancel`; both work for a viewer's own view job because authority is re-derived from `_view_id`.

**Run history**

```json
GET /views/v_3a9e/runs?limit=50
{
  "view_id": "v_3a9e",
  "runs": [
    { "job_id": "j_0b1c", "actor": { "id": "p_21d0", "name": "Bob" }, "mine": false, "view_revision": 3, "mode": "rows",
      "status": "completed", "started_at": "2026-11-13T10:20:01.100000+00:00", "finished_at": "2026-11-13T10:20:01.480000+00:00",
      "duration_ms": 312, "row_count": 4, "truncated": false, "error": null }
  ],
  "next_cursor": null
}
```

`started_at` is the job's `created_at`, `finished_at` its `updated_at` at a terminal status (null while running). `row_count` and `truncated` come from the job result without exposing it. `cursor` is opaque (`created_at|id`). Owners and editors see every actor's runs of the view; a viewer sees only their own (`mine: true`). Failed runs show the existing safe error text. No cell values ever appear.

**Drill**

Coordinates are the positions the person clicked, taken from `output.columns` metadata:

```json
POST /views/v_3a9e/drill
{ "coordinates": { "rows": ["East"], "column": { "value": "2026-01", "empty": false } }, "request_id": "optional" }
```

- Pivot views: `rows` has one value per row dimension (JSON scalar or null); `column` is `null` for the Total column, `{value, empty:false}` for a cell column, `{value:null, empty:true}` for the empty column.
- Table views that group: `{ "groups": ["East", "2026-01"] }`, one value per `group_by` entry, in order.
- Segment views: `{}`.

Response:

```json
{
  "spec": { "version": 1, "source": "c_91b2", "from": { "table": "sales", "alias": "sales" }, "joins": [], "select": [],
    "filters": { "op": "and", "items": [
      { "segment": "v_77aa", "negate": false },
      { "field": { "table_alias": "sales", "column": "region" }, "op": "eq", "value": "East" },
      { "field": { "table_alias": "sales", "column": "day" }, "op": "gte", "value": "2026-01-01" },
      { "field": { "table_alias": "sales", "column": "day" }, "op": "lt", "value": "2026-02-01" } ] },
    "group_by": [], "aggregates": [], "sort": [], "limit": 200, "offset": 0 },
  "exact": true,
  "notes": [],
  "job": null
}
```

The drill spec is the view's `filters` plus one condition per coordinate. A bucketed coordinate becomes a half-open range on the underlying column: `day` -> `eq` the date; `week` -> Monday (inclusive) to next Monday (exclusive) from the ISO label; `month`, `quarter`, `year` likewise. Date-only strings work on `date` and `datetime` columns through the existing whole-day rules, and as ISO text on SQLite text columns. An empty coordinate is `is_null`. Row values of kind `text` use `eq` (exact comparison per dialect); a null row value is `is_null`. `exact` is false only when the drilled coordinate is an empty date bucket on a SQLite text date column, where unparseable text also lands in the empty bucket; `notes` then says so in one sentence. With `request_id` the server also runs the spec (as a `rows` run attributed to the view, `view_revision` set) and returns the job in `job`. A drill row count equals the pivot cell when the value is `count`; this is a test on every engine.

Coordinates are validated against the view: wrong number of row values, a column coordinate on a pivot without columns, a column value not in the last output, or a `groups` array on a pivot is 422 `bad_coordinates`. Viewers: 403 "Underlying rows are available to editors and owners." (a drill reveals rows the view's aggregate hides).

**Export**

`GET /views/{id}/runs/{jid}/export.csv`. The job must belong to that view (404 otherwise) and to the caller (as for `/jobs/{jid}/export.csv`). Same CSV safety as today (formula-prefixed text, truncation flags). Headers come from `output.columns[].label`, never from the SQL aliases. Response headers: `Content-Disposition: attachment; filename="<slug of view name>.csv"`, `X-Data-Truncated`, `X-Data-Export-Scope: view-run`, `X-Data-CSV-Safety`, `Cache-Control: no-store`. The generic `/jobs/{jid}/export.csv` also switches to `output.columns[].label` when present.

### 4.7 Changed existing endpoints (additive only)

- `POST /query-specs/compile` and `/run` accept `spec.pivot` and `{segment}` filter nodes. For a pivot, **compile executes one bounded read** (the 13-row probe) and needs the same authority as `run`, including a verified read policy (409 otherwise). This is the one exception to "compile never executes", stated in the response: `"pivot": {"probed": true, "column_count": 3, "limit": 12}`.
- The compile response `columns[]` gains optional fields; a non-pivot response is byte-identical to PR 1 except `warnings` may contain `segment_empty`.
- `GET /connections/{cid}/capabilities`: `dialect.pivot` is an object (was `false`).
- Job objects gain `view_id`, `view_revision`, `mode`, `output`, `duration_ms` (null or absent for older jobs).
- `/api/data/bootstrap` `capabilities` gains `"views": true`, `"pivot": true`, `"segments": true`.
- `GET /api/data/workspaces/{wid}` (snapshot) is **not** extended; the client lists views with `GET /views`.

### 4.8 Pivot compile example

Request (editor, SQLite, the e2e data set):

```json
POST /query-specs/compile
{ "spec": {
  "version": 1, "source": "c_91b2", "from": { "table": "sales", "alias": "sales" }, "joins": [], "select": [],
  "filters": { "op": "and", "items": [] }, "group_by": [], "aggregates": [], "sort": [], "limit": 200, "offset": 0,
  "pivot": {
    "rows": [ { "table_alias": "sales", "column": "region" } ],
    "columns": { "table_alias": "sales", "column": "day", "bucket": "month" },
    "value": { "fn": "sum", "table_alias": "sales", "column": "amount", "label": "Total amount" },
    "totals": true, "sort": { "by": "rows", "direction": "asc" } } } }
```

Response:

```json
{
  "ok": true, "dialect": "sqlite",
  "sql": "SELECT \"sales\".\"region\" AS \"pv_r1\",\nSUM(CASE WHEN strftime('%Y-%m', \"sales\".\"day\") = :p1 THEN \"sales\".\"amount\" END) AS \"pv_c1\",\nSUM(CASE WHEN strftime('%Y-%m', \"sales\".\"day\") = :p2 THEN \"sales\".\"amount\" END) AS \"pv_c2\",\nSUM(CASE WHEN strftime('%Y-%m', \"sales\".\"day\") = :p3 THEN \"sales\".\"amount\" END) AS \"pv_c3\",\nSUM(\"sales\".\"amount\") AS \"pv_total\"\nFROM \"sales\" AS \"sales\"\nWHERE strftime('%Y-%m', \"sales\".\"day\") IN (:p4, :p5, :p6)\nGROUP BY \"sales\".\"region\"\nORDER BY \"sales\".\"region\" ASC NULLS LAST\nLIMIT 201 OFFSET 0",
  "parameters": { "p1": "2026-01", "p2": "2026-02", "p3": "2026-03", "p4": "2026-01", "p5": "2026-02", "p6": "2026-03" },
  "columns": [
    { "label": "Region", "alias": "pv_r1", "value_kind": "text", "role": "pivot_row", "table_alias": "sales", "column": "region" },
    { "label": "2026-01", "alias": "pv_c1", "value_kind": "integer", "role": "pivot_cell", "fn": "sum", "column_value": "2026-01", "column_empty": false },
    { "label": "2026-02", "alias": "pv_c2", "value_kind": "integer", "role": "pivot_cell", "fn": "sum", "column_value": "2026-02", "column_empty": false },
    { "label": "2026-03", "alias": "pv_c3", "value_kind": "integer", "role": "pivot_cell", "fn": "sum", "column_value": "2026-03", "column_empty": false },
    { "label": "Total amount", "alias": "pv_total", "value_kind": "integer", "role": "pivot_total", "fn": "sum" }
  ],
  "limit": 200, "offset": 0, "fetch_limit": 201, "joins": [], "warnings": [], "schema_hash": "9c1d5a7e3b2f4a60",
  "pivot": { "probed": true, "column_count": 3, "limit": 12 }
}
```

(Parameter numbering follows rendering order; golden tests pin it per dialect. The column header for the total uses the value label; with `columns` null the only value column is `pv_total` and it carries `role: "pivot_total"` with the value label.)

Segment filter in a spec, and the error a missing segment returns:

```json
{ "segment": "v_77aa", "negate": true }
```

```json
{ "ok": false, "errors": [ { "code": "segment_missing", "path": "filters.items[0]", "message": "The segment this filter uses was deleted. Remove the filter or pick another segment." } ] }
```

## 5. Backend

### 5.1 Module layout

```
src/termx/data/
  store.py          # migration 1 -> 2, revision table helpers (_revision_insert, _revisions, _jobs_for_view)
  views.py          # ViewService: CRUD, validation, references, history, run history, run/drill/export
  view_diff.py      # pure: diff(old_snapshot, new_snapshot) -> list[Change]
  view_http.py      # build_view_router(service, actor); reuses read_body from query_http
  pivot.py          # PivotSpec compile (outputs, probe, cell predicates), pure
  segments.py       # ResolvedSegment, expand_segments, closure walk helpers (pure) and load_closure(store, wid, ids) (reads)
  drill.py          # pure: coordinates + view spec -> drill QuerySpec, bucket label -> half-open range
  query_spec.py     # models (FilterSegment, Pivot*), compile_spec signature, hooks into pivot.py and segments.py
  query_service.py  # compile/run accept segments and pivot values; schema_index_for_view
  runtime.py        # view authority in _source/_authorize_job, run(view_id, extra), duration_ms, export labels
  service.py        # DataService.views property, require_view_connection()
  http.py           # mount_data includes build_view_router; bootstrap flags
  dialects/         # conditional_aggregate avg fix, pivot_cell_value, capabilities().pivot
```

`ViewService` talks to `DataStore` through `store.transaction()` and the private `_get/_list/_create/_update/_audit` helpers, exactly as `DataService` does. Every write also calls `store._audit(db, actor, wid, 'view.created'|'view.updated'|'view.archived'|'view.run', id, {...})`.

### 5.2 `ViewService` responsibilities

- `list(actor, wid, kind, connection_id)`, `get`, `create`, `update`, `archive`, `history`, `runs`, `run`, `drill`, `export_csv`.
- `authorize(actor, wid, view, *, edit=False, rows=False)`: resolves role and connection once, used by every method. See section 6.
- `_prepare(actor, wid, spec, kind, view_id=None)`: Pydantic parse (in `view_http`), schema index, segment closure, `compile_spec(..., pivot_values=None)` validate mode, returns `references`.
- `_check_references(db, wid, view_id, spec)` inside the transaction: re-reads the segment records from the same connection, checks existence, source, base table, cycle (stack seeded with `view_id`, so a segment cannot reference itself), depth.
- `_check_referrers(db, wid, segment_id, changes)` inside the transaction for archive and incompatible edits (`json_each(body, '$.references')` over non-archived views of the workspace).
- Run-time failure handling: the segment closure and the view record are re-read on every run, so a segment archived between save and run yields the `segment_missing` compile error, never a stale result.
- Actor names in history are the display names at write time (`actor_name`), so a renamed or removed user does not rewrite history.

### 5.3 `DataRuntime` changes

- `_source(actor, wid, definition, from_report=False, view_id=None)`: when `view_id` is set, `service.views.authorize_run(actor, wid, view_id)` returns the connection; the report rule for viewers (`report=True` in `require_connection`) is reused, so a viewer needs `shared_read`. `execute_read`, `_authorize_job`, `_worker` pass `job['_view_id']` through.
- `run(..., view_id=None, extra=None)`: `extra` is merged into the job body (`view_id`, `view_revision`, `mode`, `output`) and `_view_id` is stored.
- `_worker`: wraps `execute_read` with `time.monotonic()`; the terminal update writes `duration_ms`.
- `export_csv`: header labels from `job['output']['columns']` when present (same length as `result.columns`, else fall back to the SQL names).
- `schema_for_authorized(connection, wid)`: the body of `schema()` after role checks. `schema()` calls it.

### 5.4 Permissions

See section 6. The only new privilege path is "viewer runs a view on a shared connection". It re-checks on every call: workspace membership and role (live), view not archived and in this workspace, connection exists, not archived, not revoked, `shared_read` true, engine adapter present, source fingerprint equal to the job's. Revoking sharing or removing the member ends access on the next poll, and the finished result is discarded exactly as for reports (`execute_read` rechecks the fingerprint after the read).

## 6. Permissions per role

Connection column: "accessible" means `require_connection` passes for a non-viewer (creator, or `shared_read`). "shared" means `shared_read` is true. Others' private connections are never accessible to anyone but their creator, including the workspace owner.

| Action | Owner | Editor | Viewer | Outsider |
| --- | --- | --- | --- | --- |
| List views | views on accessible connections | views on accessible connections | views on shared connections | 404 |
| Get a view | accessible | accessible | shared | 404 |
| Create, update | accessible connection | accessible connection | 403 | 404 |
| Archive | any view (accessible) | only views they created (accessible) | 403 | 404 |
| Run, count, poll, cancel, export own job | accessible | accessible | shared | 404 |
| Drill (spec or run) | accessible | accessible | 403 | 404 |
| Edit history | accessible | accessible | 403 | 404 |
| Run history | all actors' runs | all actors' runs | own runs only | 404 |
| Reference a segment in a filter | same source and base table | same | n/a (cannot save) | n/a |
| Run a spec with a `{segment}` filter via `query-specs/run` | accessible | accessible | 403 (generic spec runs stay editor-only) | 404 |

A view on a connection that later becomes inaccessible (sharing turned off, connection archived or revoked) disappears from lists for those who lost access and returns 404 on direct access. Its history is retained in the store.

Segments referenced by a view the actor can run are resolved server-side without checking that the actor could list the segment separately: the segment is on the same connection, so access is identical by construction.

## 7. TypeScript types (`desktop/workspace/src/lib/query-spec.ts` additions and new `lib/data-views.ts`)

```ts
// query-spec.ts additions
export type FilterSegment = {segment: string; negate?: boolean};
export type FilterNode = FilterLeaf | FilterGroup | FilterSegment;           // was FilterLeaf | FilterGroup
export type PivotDim = {table_alias: string; column: string; bucket?: DateBucket; label?: string};
export type PivotValue = {fn: AggregateFn; table_alias?: string; column?: string; label?: string};
export type PivotSpec = {rows: PivotDim[]; columns: PivotDim | null; value: PivotValue; totals: boolean; sort: {by: 'rows' | 'total'; direction: 'asc' | 'desc'}};
export type QuerySpec = {/* PR 1 fields */ pivot?: PivotSpec};
export type OutputRole = 'column' | 'group' | 'aggregate' | 'pivot_row' | 'pivot_cell' | 'pivot_total';
export type OutputColumn = {label: string; value_kind: ValueKind; role: OutputRole; alias?: string; table_alias?: string; column?: string;
  fn?: AggregateFn; bucket?: DateBucket; column_value?: Scalar | null; column_empty?: boolean};
export type SpecWarning = {code: 'fan_out' | 'text_date' | 'many_to_many' | 'row_multiplication' | 'segment_empty'; message: string; join_alias?: string};
export type CompileOk = {/* PR 1 fields */ pivot?: {probed: true; column_count: number; limit: number}};
export type DialectCapabilities = {/* PR 1 fields */ pivot: {max_columns: number; max_row_dims: number; column_kinds: ValueKind[]}};
export const isSegmentFilter = (node: FilterNode): node is FilterSegment => 'segment' in node;
export const isFilterLeaf = (node: FilterNode): node is FilterLeaf => 'field' in node;
// isFilterGroup in query-builder.ts keeps testing node.op === 'and' | 'or' and must not match a segment node.

// data-views.ts
export type ViewKind = 'table' | 'pivot' | 'segment';
export type Person = {id: string; name: string};
export type ViewSummary = {id: string; kind: ViewKind; name: string; description: string; connection_id: string; base_table: string; revision: number;
  created_by: Person; updated_by: Person; created_at: string; updated_at: string; used_by_count: number; can_edit: boolean; can_run: boolean};
export type ViewRef = {id: string; name: string; state: 'ok' | 'missing'};
export type ViewDetail = ViewSummary & {spec: QuerySpec; references: ViewRef[]; used_by: {id: string; kind: ViewKind; name: string}[]; affected_views?: number};
export type Change = {field: 'name' | 'description' | 'from' | 'joins' | 'filters' | 'filters.match' | 'select' | 'group_by' | 'aggregates' | 'sort' | 'limit'
  | 'pivot.rows' | 'pivot.columns' | 'pivot.value' | 'pivot.totals' | 'pivot.sort'; op: 'added' | 'removed' | 'changed'; before?: unknown; after?: unknown};
export type RevisionRow = {revision: number; action: 'created' | 'updated' | 'archived'; at: string; actor: Person; name: string; changes: Change[]};
export type HistoryPage = {view_id: string; revisions: RevisionRow[]; next_before: number | null};
export type RunRow = {job_id: string; actor: Person; mine: boolean; view_revision: number; mode: 'rows' | 'count'; status: DataJob['status'];
  started_at: string; finished_at: string | null; duration_ms: number | null; row_count: number | null; truncated: boolean | null; error: string | null};
export type RunsPage = {view_id: string; runs: RunRow[]; next_cursor: string | null};
export type DrillCoordinates = {rows?: (Scalar | null)[]; column?: {value: Scalar | null; empty: boolean} | null; groups?: (Scalar | null)[]};
export type DrillResult = {spec: QuerySpec; exact: boolean; notes: string[]; job: DataJob | null};
export type ViewErrorCode = 'revision_conflict' | 'name_taken' | 'view_limit' | 'segment_in_use' | 'invalid_spec' | 'invalid_view' | 'bad_coordinates' | 'source_untested';
export type ViewError = {status: number; code?: ViewErrorCode; message: string; current_revision?: number; updated_by?: Person; updated_at?: string;
  used_by?: {id: string; kind: ViewKind; name: string}[]; errors?: SpecError[]};
export const viewError: (reason: unknown) => ViewError;      // parses ApiError.message JSON detail; plain strings keep status and message

// DataJob additions (lib/data-studio.ts)
export type DataJob = {/* PR 1 fields */ view_id?: string; view_revision?: number; mode?: 'rows' | 'count'; output?: {columns: OutputColumn[]; warnings: SpecWarning[]}; duration_ms?: number | null};
```

Client functions: `listViews`, `getView`, `createView`, `updateView`, `archiveView`, `fetchHistory`, `fetchRuns`, `runView`, `runViewToCompletion` (reuses the job polling loop), `drillView`, `viewExportPath`.

## 8. Frontend

Read first: `/Users/jai/.agents/skills/frontend-design-spec/SKILL.md` and `/Users/jai/.agents/DESIGN.md`. Rules that bind every file below: tokens only (no hex, no `!important`, no gradients), Lucide icons at 16px/1.5 stroke, sentence case, one primary action per view, all states (loading skeleton shaped like the content, empty with one action, inline error with recovery, disabled with a reason), keyboard path for every action, Radix for overlays (Dialog, Popover, DropdownMenu, Tabs), non-technical wording (no "SQL", "join", "WHERE", "CASE", "aggregate" in default UI; use "Combine", "Match", "Total of", "Rows", "Columns", "Value").

### 8.1 Files

```
src/lib/data-views.ts                 client + types + viewError (section 7)
src/lib/pivot.ts                      emptyPivot(spec), pivotSentence(), valueOptions(kind), humanBucket(), cell/total helpers, drill coordinates from a clicked cell
src/lib/segments.ts                   segmentCompatible(view, base table), mergeSegmentJoins(spec, segment), describeSegment(spec, labels) -> string[]
src/lib/data-tabs.ts                  kinds + 'pivot' | 'segment' | 'drill'; ids; persistence of ids only
src/lib/query-builder.ts              describeFilter for segment nodes; isFilterGroup stays strict; countFilters counts segments
src/features/data/use-views.ts        ViewsController: list, refresh, create/update/archive, per-workspace cache; aborts on scope change
src/features/data/DataNavigation.tsx  Pivots and Segments groups; New pivot button
src/features/data/PivotTab.tsx        pivot tab (builder + table + toolbar)
src/features/data/PivotBuilder.tsx    Rows / Columns / Value pickers in plain language
src/features/data/PivotTable.tsx      accessible grid with roving focus; cell click and Enter drill
src/features/data/SegmentTab.tsx      definition, members, actions, Audit panel host
src/features/data/AuditPanel.tsx      Radix Tabs: Revisions | Runs; paged lists
src/features/data/SaveViewDialog.tsx  create/rename (Radix Dialog), name_taken and generic errors
src/features/data/ConflictBanner.tsx  409 revision_conflict banner with "Reload latest" and "Save as a new view"
src/features/data/SegmentFilterMenu.tsx  "In segment..." / "Not in segment..." items inside the Add filter popover
src/features/data/DrillTab.tsx        thin wrapper that opens TableWorkspace seeded from a drill spec
src/features/data/TableTab.tsx        "Save as segment", "Save view" (table kind), seed prop, segment chips
src/features/data/FilterChips.tsx     renders segment nodes; toggles In / Not in; missing-segment state
src/features/data/DataStudio.tsx      views controller, new tab kinds, notice lifecycle fix
src/features/data/TablesOverview.tsx  "Pivot" tile action; Rows header fix
src/features/data/data-query.css      new classes (data-pivot-*, data-audit-*), no new colours
```

### 8.2 Sidebar

Groups in this order: Tables, **Pivots**, **Segments**, Saved queries, Reports, Dashboards, Team. Pivots and Segments list the views for the selected source (same `connection_id`), 36px rows like tables, searchable with the existing search box (its status line also counts pivots and segments). The Pivots heading row has a "New pivot" icon button (Plus, `aria-label="New pivot"`), which opens a small Popover listing the source's tables (type to filter); choosing one opens an unsaved pivot tab on that table. Segments have no "new" button: a segment is created from a table's filters. Empty states: "No pivots yet. Start one from a table." and "No segments yet. Add filters to a table, then choose Save as segment." Viewers see the groups for views they can run, with the same selectable rows; their tabs are read-only (section 8.7). A viewer-only user sees "Tables" replaced as today by the note.

Rows are buttons with `aria-current="page"` for the active tab. A segment row shows `used_by_count` as secondary text ("Used in 2") only when above 0.

### 8.3 Tabs

`DataTab` gains `viewId` and kinds `pivot`, `segment`, `drill`.
- Saved pivot: id `pivot:view:<viewId>`; saved segment: `segment:view:<viewId>`.
- Unsaved pivot: id `pivot:new:<connectionId>:<tableKey>:<n>` (n increments per open so two drafts can coexist). On reload only the id persists; the restored tab is an empty draft pivot on that table (filters and pickers are memory only, as for tables).
- Drill: id `drill:<n>`, never persisted (`sanitize` drops it); it holds the drill spec in memory and renders `TableWorkspace` seeded with it, labelled "<row> · <column>" (for example "East · 2026-01"), truncated with a title.
- Persistence stays ids only: assert no spec, filter value or label reaches `localStorage` beyond the view id.
- `pruneTabs` drops a view tab whose view disappeared from the list (archived by someone else) after a refresh, with a one-line status "A view you had open was deleted."

### 8.4 Pivot tab

Layout (top to bottom): header (view name or "New pivot on Sales", subtitle with the plain-language sentence, for example "Total amount by Region and month of Day"); toolbar; **builder row**; filter chips; results region.

- **Builder** (`PivotBuilder`): three labelled groups using the existing `Select` (native, 40px/32px compact): **Rows** (one required, a second optional via "Add another"), **Columns** (optional; "None" keeps a one-dimension summary), **Value** (measure then column). Value wording: "Number of rows", "Total of", "Average of", "Smallest", "Largest", "Unique count of" (the existing `MEASURES`); the column list shows only compatible columns (numbers for total and average). A date column in Rows or Columns shows a "by" select (Day, Week, Month, Quarter, Year; required for Columns, default Month). Column choices exclude unsupported kinds with the reason in the option's disabled text ("Decimal numbers can't be used as columns"). Columns from combined tables use the "Table · Column" labels from PR 1.
- **Filters:** the existing `FilterChips`, plus segment chips (8.5).
- **Results:** `PivotTable` renders from `job.output.columns` and `result.rows`: a sticky first column (row labels), 12 value columns, a Total column. Empty cells show an en dash with `aria-label="No data"`; the empty column header reads "(empty)"; null row values read "(empty)". Numbers are right aligned and formatted with `toLocaleString`; counts and sums keep integers integers. States: loading skeleton shaped like a table (3 header cells, 5 rows); empty ("No rows match these filters." with "Clear filters"); error (`role="alert"`, text from the server, "Try again", plus "Test connection" on 409 `source_untested`); too many columns (`pivot_too_many_columns` message with a "Group by month" or "Add a filter" shortcut: the first sets the bucket one step coarser and re-runs); disabled with a reason (engine unsupported: capabilities reason, "Open Advanced SQL" like PR 1); truncated notice (`Showing the first 200 rows.` with Previous/Next like the grid).
- **Drill:** every value cell and the Total cells are buttons. Activating one calls `drillView` (saved view) or builds the drill spec locally with `lib/pivot.drillSpec` for an unsaved pivot (same rule, tested against the server fixtures) and opens a `drill` tab. Row total cell drills with `column: null`.
- **Keyboard:** the table is `role="grid"` with a roving tabindex: Tab enters at the first cell, arrow keys move by cell, Home/End to row ends, Ctrl+Home/End to corners, Enter or Space drills, Esc leaves the grid. `aria-rowcount`, `aria-colcount`, row headers are `rowheader`, column headers `columnheader`.
- **Actions:** one primary at a time. Unsaved or dirty saved pivot: **Save view** (new view: opens `SaveViewDialog`; saved and dirty: **Save changes**, a PATCH with the held revision). Clean saved pivot: no primary. Secondary: **Export CSV** (runs through the view run for saved pivots so it appears in run history; for an unsaved pivot it exports the shown job), **Show SQL** (existing `ShowSql`, text copy), **Reset** (to the saved spec), and the view menu (Rename, Delete with confirm Dialog). Export CSV is disabled with a reason until a result is shown.
- **Saved pivot runs:** a clean saved pivot runs through `runView` (so the run lands in history). While the person edits the pickers, runs go through `query-specs/run` (live preview, not attributed to the view). A "Last run" line under the title shows who and when from the first run-history row (editors).

### 8.5 Segments in tables

- **Save as segment** (Table tab toolbar, secondary): enabled when the filter tree has at least one filter (counts segment nodes); otherwise disabled with the visible reason "Add a filter first". Opens `SaveViewDialog` (kind segment, name field, helper "A segment is a saved set of filters you can reuse."). The stored spec is `{from, joins, filters}` of the current tab; on success a status message announces it ("Saved segment Big spenders") and focus returns to the button; the sidebar refreshes.
- **Use a segment:** inside the Add filter popover, a second section "Segments" with "In segment" and "Not in segment" choices opens a listbox of the source's segments for this table (same base table), searchable. Choosing one adds a chip "In segment Big spenders" / "Not in segment Big spenders" (remove button and Delete key as for other chips; clicking the chip text toggles In/Not in through a small Popover with two radio options). Applying adds the segment's required joins to the tab automatically (`mergeSegmentJoins`) and says so ("Combined Regions so this segment works.") in a status line. Incompatible segments (different table) are listed disabled with "Built on Orders".
- A chip for a deleted segment shows "Segment removed" with a warning icon and the remove button; the run shows the `segment_missing` message with a "Remove filter" button.
- A saved table or pivot that references a segment shows a "Uses segment Big spenders" chip row in its Audit panel too (from `references`).

### 8.6 Segment tab

Header: name, subtitle "Segment on Sales · revision 3". Sections:
1. **Definition:** the filters as plain sentences (list, one per leaf, grouped with "Match all of these/Match any of these") using `describeSegment`; referenced segments appear as "In segment X" with a link that opens that segment's tab. Editors get an **Edit filters** toggle that reveals the same `FilterChips` editor; a dirty edit shows **Save changes** (primary) and Reset.
2. **Members:** "Count members" (secondary button, runs `mode:'count'` on demand; result "1,284 members" in a `role="status"`; a stale count is dimmed after an edit). No count runs automatically.
3. **Actions:** **Show underlying rows** (primary when the definition is clean; opens a drill tab with the segment spec via `drillView` with `{}`), **Use as source for a pivot** (opens an unsaved pivot on the segment's base table with the segment filter pre-applied and a visible chip), **Export CSV** (a `rows` run, then export), overflow menu: Rename, Delete.
4. **Audit panel** (`AuditPanel`, Radix Tabs "Revisions" and "Runs"):
   - Revisions: newest first, each row "Revision 3 · Bob · 13 Nov 2026, 10:01" and the changes as sentences ("Added filter: Amount is at least 5,000", "Removed filter: ...", "Renamed from Large orders", "Now matches any of the filters"). Created row reads "Created by Alice". "Show earlier" pages with `before`. Viewers see "Edit history is available to editors and owners."
   - Runs: columns Who, When, Rows, Time (duration, "0.3 s"), Result (Completed, Failed with the error, Cancelled), revision used. Failed rows show the safe message. "Show earlier" pages with `cursor`. Viewers see only their own runs and the heading says "Your runs".
   - Empty: "Nothing here yet." with the reason ("This segment has not been run."). Loading: skeleton rows. Error: inline with Try again.
5. **Used by** (editors): a list of views that use this segment, each opening its tab.

### 8.7 Conflicts, viewers, notices

- **409 `revision_conflict`** on Save changes, Rename or Delete: `ConflictBanner` (`role="alert"`, focus moves to its heading) reads "Bob changed this view at 10:14. Your edits have not been saved." Actions: **Reload latest** (primary; refetches the view, replaces the spec and revision, clears the dirty state, and announces "Reloaded revision 5"), **Save as a new view** (secondary; opens the dialog with the local spec), and Dismiss. Nothing is retried silently.
- `name_taken` shows inline under the name field in the dialog. `segment_in_use` on delete shows the list of users inside the confirm dialog with links. `invalid_spec` shows the first error message.
- **Viewer:** pivot and segment tabs open read-only: pickers and chips are replaced by the plain-language sentence, no Save, no drill, no Audit revisions; actions are Run (refresh), Export CSV, and Runs (own). Sidebar rows exist only for views on shared sources.
- **Notices (leftover 1):** the `notice` state gets a lifecycle: it clears on tab change, workspace or source change, any new mutation, and 8 seconds after it appears; the Dismiss button stays. The text "Connection saved. Test it when you are ready." renders in the existing `role="status"` banner and is never written to storage or the URL. Tests: dismiss, tab switch, source switch, and timer.
- **Clipped Rows header (leftover 2):** at 375px the list view of `TablesOverview` clips "Rows". Fix in `data-query.css`: the table sits in a `.data-table-scroll` wrapper with `overflow-x: auto`, `th` cells use `white-space: nowrap`, the numeric columns get `min-width: 5.5rem` and the first column `min-width: 8rem` with ellipsis, and the Actions column stops using a fixed width. Test: jsdom cannot measure, so the guard is a CSS assertion (`data-styles.test.ts`: no fixed width on the Rows/Columns headers, wrapper has `overflow-x: auto`) plus the browser e2e check `th.scrollWidth <= th.clientWidth` for every header at 375px.

### 8.8 Styling

Reuse `.data-toolbar`, `.data-chip`, `.data-results`, `.data-inline-error`, `.data-empty-inline`, `.data-skeleton-*`, `.data-muted`. New classes only use existing tokens: grid borders `var(--border)`, header text `var(--text-muted)`, cell hover `var(--surface-hover)`, focus ring `var(--agent-focus)`, banner `var(--danger)` border for conflicts. No hex, no gradients, no emoji, no `!important`. The 44px minimum applies in the existing `@media (pointer: coarse)` block only. Pivot columns are 7.5rem to 12rem wide, tabular numerals, the table scrolls horizontally inside its own wrapper at 375px with the first column sticky.

## 9. Integration and end-to-end

`tests/workspace_data_studio_e2e.py` (extend; keep the compact style). Synthetic SQLite from `business_file()`: `regions(1 to 4)`, `customers(50)`, `sales(ROWS)`. Change `sales.day` to spread across `2026-01` to `2026-03` (`'2026-0'+str(1+n%3)+'-'+str(1+n%28).zfill(2)`) and keep `region` East/West and `amount = n`; check that `EXPECTED` and the existing journeys do not read `day`; recompute any expected number from the file with the Python oracle (`expected_by_region`) rather than literals.

New journey `pivot_segment_journey(page, api, data, wid, cid, ...)` after `visual_journey`, run in all four themes/widths:

1. Open **sales**, press **New pivot** (or the tile Pivot action), choose Rows = Region, Columns = Day by Month, Value = Total of Amount. Assert the table headers (`East`/`West` rows, `2026-01..03`, Total), the cell values equal the oracle, and the compile `parameters` contain the month labels as values (`/api/data/.../query-specs/compile` response).
2. Click the East / 2026-02 cell. A drill tab opens named "East · 2026-02"; its row count equals the oracle count; the first request carries only coordinates (assert no SQL in the request body).
3. Back on the pivot, **Save view** as "Sales by region and month"; assert `POST /views` 201, sidebar Pivots shows it, `aria-current` on its row.
4. Open **customers** (or sales), add filter Amount at least 2000, **Save as segment** "Big sales"; Segments group shows it; **Count members** returns the oracle count.
5. Open a second tab on **sales**, Add filter, "In segment", Big sales: the grid row count equals the oracle; "Not in segment" gives the complement (counts sum to ROWS, including NULL-safe behaviour).
6. In the segment tab edit the filter to 3000, **Save changes**. Audit > Revisions shows revision 2 by the current user with "Removed filter ... 2,000" and "Added filter ... 3,000"; revision 1 "Created". Audit > Runs shows the count run and the rows run with duration and row count.
7. A second browser context (user SECOND, editor via Team) loads the segment, then the first context edits it again; the second context saves with its stale revision and sees the conflict banner; **Reload latest** shows revision 3 and clears the banner. (Also asserted at API level with `request(..., code=409)`.)
8. A viewer (third user, role viewer) with the connection shared: sidebar shows the pivot, the tab is read-only, **Export CSV** works and produces the same bytes as the editor's export, Drill and Audit revisions are absent; API: `PATCH`, `POST /views`, `DELETE`, `drill`, `history` all 403; `query-specs/run` 403; with sharing turned off the view returns 404 and the sidebar row disappears after refresh. A private (unshared) source's views are invisible to the editor who did not create the source.
9. Delete protection: archiving "Big sales" while the saved table view uses it shows the in-use list; after removing the reference it succeeds.
10. Leftovers: the "Connection saved" banner is dismissible, disappears after switching tabs, and is not present after a reload; the 375px overview table headers are not clipped.
11. Per state, existing helpers: `capture()` screenshots (`pivot-ready`, `pivot-too-many-columns`, `drill-tab`, `segment-tab`, `segment-audit-revisions`, `segment-audit-runs`, `conflict-banner`, `viewer-pivot`), `focus_sample` on the builder selects, Save dialog, cell grid, Audit tabs, conflict buttons; axe through `report['states']` as today; keyboard walk through the pivot grid.
12. At the end: the source file hash is unchanged (`before`), as the current test asserts.

Docs: update `docs/data-studio.md`: new "Pivots, segments and audit" section (what they are, roles, the 12-column limit and "(empty)" column, drill-down, segments in filters and the "in use" rules, history and run history, CSV, 409 behaviour), a table row for view features per engine, and the migration/downgrade note (section 2.3). Replace "Existing selectors" claims only where they changed. Remove the pivot line from the "Decisions" section if added there.

## 10. Tests

Work test-first on both lanes. A task's first commit is its failing tests. Backend fixtures extend `tests/data_query_fixture.py` (no change to existing rows): add `regions`, a `sales(id, region, amount, day, customer_id)` table with enough month spread, NULL days, NULL amounts, 13+ distinct values column, mixed-case text, an `(empty)` text value, and a boolean column; roles owner (alice), editor (bob), second editor (carol), viewer (dave), outsider (erin).

### 10.1 Backend

`tests/test_data_store_migration.py` (B-S1)
- A literal version-1 DDL file with workspace, member, connection, query, report and job rows opens with the new store: `user_version == 2`, every pre-existing row byte-identical (`SELECT * ... ORDER BY id` hash before/after), new table and index exist, `data.sqlite3.pre-views-v1` exists with the old rows and mode 0600, a second open is a no-op and does not re-copy, an empty v1 store skips the backup.
- `user_version == 3` refuses with 409; a PR 1 style check (`> 1`) on a v2 file would refuse (simulated), documenting the downgrade.
- Revision insert and record update are atomic: a failure injected after the record write leaves neither.
- The job index exists and `EXPLAIN QUERY PLAN` for the run-history query uses it.

`tests/test_data_view_diff.py` (B-V1): every `Change.field`; filter leaf added/removed; `and`/`or` flip; reorder of select is one `changed`; segment node added; no-op yields `[]`; unicode and big values; deterministic order of changes.

`tests/test_data_pivot.py` (B-P1 to B-P4)
- Models: `pivot` with `select`/`group_by`/`aggregates`/`sort` set is `pivot_conflict`; 0 and 3 row dims; `columns` null with `totals` false; unknown keys; both dims same column; value shape rules; `extra=forbid`.
- Golden SQL per dialect for: rows only, rows+columns text, rows+columns bucketed month/week/quarter/year, two row dims, boolean columns, NULL column, `avg`, `count_distinct`, `min`/`max` on dates, with joins and filters. Each asserts: bound parameters only (no probe value or label text in SQL), neutral aliases, the restriction clause, `group_by_items` output per dialect (MySQL null-flag companion, MariaDB bare column), `text_operand` collation on MySQL/MariaDB for text columns and charset conversion for latin1 columns, no output alias in ORDER BY/GROUP BY, `validate_read` passes for the engine, sqlglot reparses.
- Probe SQL golden per dialect, `LIMIT 13`, no ORDER BY, 13 rows returns `pivot_too_many_columns` for 13 distinct, passes for exactly 12, NULL counts as one of the 12.
- Column order determinism: shuffled probe results yield the same columns; text sorted by code point regardless of engine; bool false before true; NULL last.
- Execution on SQLite against a Python oracle (pandas-free): cell values for count, sum, avg, min, max, count_distinct equal the oracle for every (row, column) including empty cells (`0` for count, `NULL` otherwise), the NULL bucket, rows with NULL row dimension, and the Total column equal to the plain aggregate over the restricted rows; total equals the sum of cells for sum/count.
- Race: insert a new distinct value after the probe and before the run; the result still has only probed columns and the total equals the sum of visible cells.
- Kind rules: `number` column dimension refused, `json`, `binary`, `time`; date without bucket in columns refused; text-as-date bucket allowed on SQLite with the `text_date` warning and refused on the other three.
- Label rules: row/value labels with `:` or control characters rejected (`unsupported_name`), 61 characters rejected, 60 accepted; column values containing quotes, `%`, `--`, backticks, emoji, 200 characters, the string `(empty)`, empty string, `pv_c1`, `:p1` and `Robert'); DROP TABLE sales;--` as real data: all execute, appear only in `columns[].label`/`column_value` and bound parameters, the source file hash is unchanged.
- Dialect capabilities: `dialect.pivot` shape for all four; `conditional_aggregate('avg', ...)` uses `avg_expr` on PostgreSQL and MySQL/MariaDB (regression for finding 6).
- Live (env-gated, skipped when unset): `TERMX_TEST_PG_URL`, `TERMX_TEST_MYSQL_URL`, `TERMX_TEST_MARIADB_URL`. Load the pivot fixture into a uniquely named schema/database, compile the pivot corpus per dialect, run probe and pivot through SQLAlchemy `text()`, compare with the same oracle: all aggregates, bucket labels over 2019-12-20 to 2028-12-31 as column values, exact text (`Alice` vs `alice`) producing separate columns on MySQL/MariaDB, `ONLY_FULL_GROUP_BY` on, NULL bucket, boolean columns, decimal sums and averages arriving as the oracle's numbers through `column_kinds`. Container commands (module header, not run by CI): the PR 1 `docker run` lines for `termx-test-pg` (postgres:16, 55432), `termx-test-mysql` (mysql:8.4, 53306), `termx-test-mariadb` (mariadb:10.11, 53307); remove with `docker rm -f termx-test-pg termx-test-mysql termx-test-mariadb`. Run them locally once before merging and leave no container behind.

`tests/test_data_segments.py` (B-G1 to B-G3)
- Expansion golden SQL per dialect: single segment, negated, nested two levels, three levels, segment inside an `or` group, segment + other filters; `NOT` form uses `CASE WHEN ... THEN 1 ELSE 0 END = 0`.
- Semantics on SQLite vs oracle: members and complement partition the table exactly (counts add up) even when the segment expression is NULL for some rows (NULL amounts); empty segment matches all, negated empty none, with the `segment_empty` warning.
- Cycles: self reference, A to B to A on save (in-transaction), A to B to C to A at compile (seeded corrupt store); depth 4 refused; 21 expansions refused; 201 leaves refused.
- Compatibility: other base table `segment_base_mismatch`; missing join `segment_join_missing`; same join under a different alias is remapped and the SQL uses the consumer's alias; alias used for another table `segment_alias_conflict`; other source `segment_source_mismatch`; archived, unknown, other-workspace and invalid-id references `segment_missing` (same message for all, no existence leak).
- Concurrency: two threads each saving the half of a cycle: exactly one succeeds, the other gets `invalid_spec` with `segment_cycle`; archive of a segment racing a new reference: either the archive 409s or the reference save fails, never both succeed (loop 50 times with a barrier).
- Parameters: expanded values bound with consecutive `pN`, total count cap enforced after expansion.

`tests/test_data_views.py` (B-V2 to B-V6)
- CRUD per kind with the persisted body, `references`, name uniqueness (case-insensitive, per kind, not against archived), 500 cap, 100,000 byte spec cap, whitespace/NUL/emoji names, 201-character name, description cap, extra fields 422.
- Kind rules (3.4): segment with select/group_by/pivot refused; pivot kind without pivot; table kind with pivot; kind change on PATCH refused.
- Revisions: every create/update/archive writes exactly one `data_view_revisions` row in the same transaction; no-op patch writes none and keeps the revision; `history` paging with `before`; actor names fixed at write time; segment edit increments `used_by` responses and `affected_views`.
- Optimistic concurrency: stale revision 409 `revision_conflict` carries `current_revision`, `updated_by`, `updated_at`; two simultaneous patches with the same revision: one 200, one 409 (threads); revision as bool/float/string/0/negative 422; delete with stale revision 409.
- Archive rules: referenced segment 409 `segment_in_use` listing users; after the referrer is archived it succeeds; changing a referenced segment's base table or joins 409; editing its filters 200 with `affected_views`.
- Roles on every route (matrix in section 6): owner, editor (creator and non-creator for delete), viewer (list/get/run/own runs allowed on shared; 403 on create/update/delete/history/drill/other actors' runs hidden), outsider 404, unauthenticated 401, locked 423; private connection of another user: editor gets 404 on get, absent from list, 403 on create; workspace owner also cannot use another user's private connection; connection from another workspace 404; sharing turned off between list and run: 404/403 and a running job's result is discarded (fingerprint); member removed mid-run discards.
- Run: idempotent `request_id` (same id same job, same id other view/revision/mode 409, two ids two jobs), job carries `view_id`, `view_revision`, `mode`, `output`, `duration_ms` after completion; count mode on segment returns one row; count on pivot 422; paging `offset`; network source without verified policy 409 `source_untested`; the pivot run uses the probe, labels in `output`; a segment archived after save yields `segment_missing` on run; unsupported engine (monkeypatched ready engine without a dialect): lists/history work, run/drill/create 422 with the exact message.
- Run history: ordered newest first, `mine`, pagination cursor, viewers see only own, durations are non-negative integers, failed and cancelled runs listed with safe errors, no cell values anywhere in the payload (assert by scanning the JSON for known row values), jobs without `view_id` never appear, index used.
- Drill: pivot cell coordinates for every bucket kind (day, week incl. year boundary 2020-12-31 / 2021-W53 and 2026-01-01, month, quarter, year), text with `%`, `_`, quotes, case-variant values, NULL row value, empty column, Total cell, two row dims; the drilled row count equals the pivot count cell on SQLite for every cell of the oracle fixture; group coordinates for summary table views; segment `{}`; wrong arity/unknown column value/`groups` on pivot 422 `bad_coordinates`; viewer 403; drill with `request_id` returns a job attributed to the view; `exact:false` note for SQLite text-date empty bucket.
- Export: view-run CSV headers use labels (pivot columns, label with comma and quote are CSV-escaped), formula-prefixed cells are neutralised, job of another view 404, job of another actor 404, truncation header, filename slug is `[a-z0-9-]` only (name `../../etc/passwd` and emoji become safe), `Cache-Control: no-store`.
- Adversarial inputs for all routes through `read_body`: NaN/Infinity, depth 33, 1,000,001 bytes, lone surrogate names, 100,000-item arrays, `id`/`revision` smuggled in bodies, `segment` ids with SQL text (only ever looked up by exact id), `kind` values outside the enum, `request_id` of 201 characters.
- Contract fixtures: every file in `desktop/workspace/src/lib/view-contract-fixtures/` is loaded; each request is replayed against the fixture database and the response equals the stored one (ids and timestamps normalised by a documented placeholder map). This keeps both lanes aligned.

`tests/test_data_query_http.py` additions: `query-specs/compile` and `run` with a pivot (probe executed, 409 when untested, viewer 403), with `{segment}` nodes, `capabilities().dialect.pivot`, bootstrap flags; the existing PR 1 contract fixtures still pass unchanged.

### 10.2 Frontend (Vitest on Node 22, jsdom, `axe-core` on every state)

- `lib/data-views.test.ts`: against the shared fixtures; `viewError` on plain strings, JSON details, malformed JSON, unknown codes; `runViewToCompletion` posts one `request_id` per submit, polls, cancels on abort, handles failed/cancelled/unknown; 409 mapping.
- `lib/pivot.test.ts`: `pivotSentence`, bucket step-up for "Group by month", value options by kind, `drillSpec` equals the server fixture for each bucket, NULL, total cell.
- `lib/segments.test.ts`: `segmentCompatible`, `mergeSegmentJoins` (adds only missing joins, alias collisions get a unique alias), `describeSegment` sentences including nested segments and groups.
- `lib/data-tabs.test.ts`: new kinds, ids, persistence ids only, drill tabs never persisted, tolerant of blocked storage and bad entries.
- `lib/query-builder.test.ts`: `describeFilter` for segment nodes, `countFilters`, `isFilterGroup` rejects segment nodes, add/replace/remove helpers keep segment nodes.
- `DataNavigation.test.tsx`: Pivots/Segments groups, filter by source, search status counts them, empty/loading/error, "New pivot" popover with keyboard (Enter, arrows, Esc returns focus), viewer view, `aria-current`, "Used in N".
- `PivotBuilder.test.tsx`: plain wording (assert none of the banned terms), compatible columns per measure, bucket select appears for dates and is required for columns, disabled options carry reasons, adding a second row dimension, removing columns.
- `PivotTable.test.tsx`: grid roles and counts, roving focus and every key in 8.4, Enter/Space/click drills, empty cell dash with label, "(empty)" column and row, integer vs decimal formatting, 12 columns plus total at 375px scroll wrapper, axe, `aria-sort` absent (not sortable in this PR; sort lives in the builder).
- `PivotTab.test.tsx`: states (loading, empty, error with Try again/Test connection, too many columns shortcut, truncated, unsupported engine), one primary action in each state (assert exactly one `.button-primary`), Save view → dialog → POST body, Save changes → PATCH with the held revision, 409 shows `ConflictBanner`, Reload latest replaces the spec and clears dirty, Export CSV goes through a view run, drill opens a `drill` tab, request cancelled on tab switch/unmount, stale response dropped.
- `SegmentTab.test.tsx`: definition sentences, nested segment link, Count members only on click, dirty edit shows Save changes as the only primary, Show underlying rows opens a drill tab, Use as source for a pivot opens a pivot with the chip, Delete with in-use list, viewer variant (read-only, no history), 409 flow.
- `AuditPanel.test.tsx`: Tabs keyboard (arrows, Home/End), revision sentences for every `Change.field`, created/archived rows, paging with Show earlier, run rows with duration and failure text, viewer messages, empty/loading/error, axe in each tab.
- `SaveViewDialog.test.tsx`: Radix focus trap and Esc, labelled name field, `name_taken` inline, double submit sends once, dismiss returns focus to the trigger.
- `FilterChips.test.tsx` additions: segment chip sentence, toggle In/Not in, remove by button and Delete, missing-segment chip, segment menu lists compatible segments, adds joins and announces it.
- `TableTab.test.tsx` additions: Save as segment disabled with reason and enabled with filters, segment filter runs `query-specs/run` with the node, seeded drill tab, Save view for table kind.
- `TablesOverview.test.tsx`: Pivot tile action; header cells have no fixed width; wrapper scrolls.
- `data-studio.test.tsx`: notice lifecycle (dismiss, tab switch, source switch, timer, not in `localStorage` or URL); views refresh after create/delete; archived view prunes its tab with a status message; workspace switch aborts view requests.
- `data-styles.test.ts` / `design-review-css.test.ts`: new CSS has no `!important`, hex, gradient, `min-height:44px` outside coarse-pointer blocks; the overview wrapper rule.
- Banned-term guard (`data-plain-language.test.ts`): renders each new view and asserts the visible text contains none of `SQL` (except the existing "Show SQL"/"Advanced SQL" controls), `JOIN`, `WHERE`, `CASE`, `GROUP BY`, `aggregate`, `query-spec`.
- `tsc -b` clean; the PR 1 hex/arbitrary-value `rg` check clean for new files.

## 11. Tasks in dependency order

Commit prefixes `data(be):`, `data(fe):`, `data(e2e):`, `docs:`. Each task starts with its failing tests. Commit after every task.

### T0. Contract freeze (first commit; unblocks both lanes)

Owner: frontend engineer, reviewed by backend engineer; about 2 hours.
1. Apply the `query-spec.ts` additions (section 7) and create `lib/data-views.ts` types.
2. Add `src/lib/view-contract-fixtures/`: `view-create-pivot.json`, `view-create-segment.json`, `view-get.json`, `view-patch.json`, `view-conflict.json`, `view-delete-in-use.json`, `views-list.json`, `history.json`, `runs.json`, `run-job.json`, `drill-pivot-cell.json`, `drill-total.json`, `drill-segment.json`, `compile-pivot.json`, `compile-pivot-too-many.json`, `compile-segment-missing.json`, `capabilities-sqlite-pivot.json`. Each is `{request, response}` copied from section 4. Placeholders for ids and times: `"<id:view>"`, `"<time>"`.
3. After T0 the backend owns the fixtures; a field change edits section 4 and the fixtures in one commit.

### Backend lane

- **B-S1. Store migration.** Version 2, backup, revision table, job index, tests. Independent.
- **B-P1. Pivot models and dialect hooks.** `PivotSpec`, `FilterSegment`, `conditional_aggregate` avg fix, `pivot_cell_value`, capabilities, tests for model rules. Depends on nothing (PR 1 merged).
- **B-P2. Pivot compiler and probe.** `pivot.py`, golden SQL for four dialects, SQLite oracle, race, label and injection suites. Depends on B-P1.
- **B-G1. Segment expansion.** `segments.py`, compiler hook, golden SQL, oracle, cycle/depth/compat tests. Depends on B-P1.
- **B-V1. Diff module.** `view_diff.py`. Independent.
- **B-V2. ViewService CRUD and revisions.** `views.py` create/get/list/update/archive/history with in-transaction reference checks and referrer protection, `service.views`, role matrix tests. Depends on B-S1, B-V1, B-G1.
- **B-R1. Runtime view authority.** `_source/_authorize_job/_worker/run/export_csv`, `schema_for_authorized`, `duration_ms`, public job keys. Depends on B-S1.
- **B-V3. Run, count, runs history, export.** `views.run`, `runs`, `export_csv`, `QueryService` changes (segments, probe, viewer schema path). Depends on B-P2, B-G1, B-R1, B-V2.
- **B-V4. Drill.** `drill.py` + endpoint, parity tests (drill count equals pivot cell on all fixture cells). Depends on B-V3.
- **B-H1. Routes.** `view_http.py`, `mount_data`, bootstrap flags, `query-specs/*` pivot and segment tests, contract-fixture replay. Depends on B-V3, B-V4.
- **B-L1. Live conformance.** `tests/test_data_pivot.py` live classes for PostgreSQL, MySQL and MariaDB (and the segment corpus), run once locally with containers, then remove the containers. Depends on B-P2, B-G1.

### Frontend lane

- **F1. Client and helpers.** `data-views.ts`, `pivot.ts`, `segments.ts`, `query-builder.ts` and `data-tabs.ts` changes with tests against the T0 fixtures. Depends on T0.
- **F2. Leftovers.** Notice lifecycle and the Rows header fix with tests. Independent; ship first (small, reviewable).
- **F3. Views controller and sidebar.** `use-views.ts`, `DataNavigation` groups, new-pivot popover, tab kinds in `DataStudio`. Depends on F1.
- **F4. Pivot tab.** `PivotBuilder`, `PivotTable`, `PivotTab`, drill tab, `SaveViewDialog`, `ConflictBanner`. Depends on F1, F3.
- **F5. Segments in tables.** Save as segment, segment menu and chips, `mergeSegmentJoins`, `TableTab` seed prop. Depends on F1, F3. Parallel with F4.
- **F6. Segment tab and Audit panel.** Depends on F5 (definition editing) and F1; Audit can start on F1.
- **F7. Viewer and conflict pass.** Read-only variants, 409 flows, error states. Depends on F4, F6.
- **F8. QA pass.** axe on every state, keyboard pass, 375px and 1280px in light and dark, style guards, banned-term test, `tsc -b`, full Vitest on Node 22. Depends on F4 to F7.

### Integration

- **I1. End-to-end.** After B-H1 and F8: extend `tests/workspace_data_studio_e2e.py` per section 9, run all four theme/width cases, attach screenshots, fix findings. Depends on B-H1, F8.
- **I2. Docs.** `docs/data-studio.md`. Depends on I1.

### Parallelism

After T0: B-S1, B-P1, B-V1, F1 and F2 start immediately on disjoint files. The lanes first need each other at I1; before that the frontend builds on the fixtures and the backend proves the same fixtures.

## 12. Verification commands

Backend, from the worktree root:

```
uv sync --all-extras --group dev
uv run pytest tests/test_data_store_migration.py tests/test_data_view_diff.py tests/test_data_pivot.py tests/test_data_segments.py tests/test_data_views.py tests/test_data_query_http.py tests/test_data_query_spec.py tests/test_data_dialects.py tests/test_data_core.py tests/test_data_runtime.py tests/test_data_bi.py tests/test_data_copilot.py -q
TERMX_TEST_PG_URL=postgresql+psycopg://termx:termx@127.0.0.1:55432/termx TERMX_TEST_MYSQL_URL=mysql+pymysql://root:termx@127.0.0.1:53306/termx TERMX_TEST_MARIADB_URL=mysql+pymysql://root:termx@127.0.0.1:53307/termx uv run pytest tests/test_data_pivot.py tests/test_data_segments.py -q   # local, containers up
docker rm -f termx-test-pg termx-test-mysql termx-test-mariadb
```

Frontend, from `desktop/workspace`:

```
pnpm install --frozen-lockfile
npx -y -p node@22 node node_modules/vitest/vitest.mjs run src/features/data src/lib src/components/ui
npx -y -p node@22 node node_modules/typescript/bin/tsc -b
rg -n '#[0-9a-fA-F]{3,8}\b|-\[(#|\d)[^\]]*\]' src --glob '!*.css'     # new files: no matches
```

End to end, from the worktree root (builds the UI first, as PR 1 does):

```
cd desktop/workspace && pnpm build && cd ../..
.venv/bin/python -B tests/workspace_data_studio_e2e.py /tmp/data-studio-pr2-e2e desktop/workspace/dist
```

After every phase commit the app is restarted from the branch (`uv run termx` with the built UI) so the latest version can be tried by hand.

## 13. Risks and decisions to confirm

1. **R1. `compile` now executes one bounded read for pivots.** Reason: the column list comes from data. Alternatives: a separate `pivot-columns` endpoint (two round trips per edit) or pinning columns in the spec (stale on new data). The read is read-only, 13 rows, under the same deadline, authority and rate caps as a run. Reversible inside `QueryService.compile`.
2. **R2. `user_version` 2 blocks a PR 1 build from opening the store.** The pre-migration backup and the clear 409 make this safe, not silent. Keeping version 1 would let an old build ignore history rows and write jobs without `view_id` while views exist.
3. **R3. Run history keeps job results for ever** (existing behaviour: `result` is stored on the job). With views this grows with every run of a popular view. Not fixed here to avoid changing report/job retention. Follow-up: store only `row_count` after 30 days, or purge results older than N days for view runs. Needs an owner decision.
4. **R4. Viewers cannot drill.** Drill reveals base rows that an aggregate hides. If product wants viewer drill, it must be a per-view setting ("Allow viewers to see rows"). Not built.
5. **R5. Editors may edit any view; only owner or creator may delete.** Mirrors saved queries for edit and is stricter for delete because segments are shared building blocks.
6. **R6. Pivot rows are ordered by each engine's grouping collation** for text. Columns are deterministic across engines (sorted in Python). Making rows engine independent needs a binary order on PostgreSQL (`COLLATE "C"`) and a new dialect method; deferred.
7. **R7. Empty date bucket drill on SQLite text dates is approximate** (`exact:false`). PostgreSQL, MySQL and MariaDB typed columns are exact.
8. **R8. Segment joins are matched, not merged.** If a consumer lacks a join the segment needs, the UI adds it (never the server). A different solution (segments as row-id sets) was rejected: it needs subqueries or temp tables.
9. **R9. The `FilterNode` TypeScript union widens**; every consumer that did `isFilterGroup(node) ? ... : (node as FilterLeaf)` must narrow with `isFilterLeaf`. `tsc -b` finds them; F1 fixes them in one commit.
10. **Design review needed on one point**: the pivot grid is `role="grid"` with a roving tabindex because every value cell is interactive. A plain `<table>` with 100+ button tab stops fails the keyboard requirement. The frontend engineer should confirm the pattern against the repo's existing `QueryGrid` before F4.
