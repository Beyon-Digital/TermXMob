# Data Studio: no-SQL query experience

Date: 2026-11-12. Status: draft for review. Path: architectural.

## Outcome

A non-technical person opens a database, browses its tables, combines them, filters, sorts, summarizes and pivots, builds named segments, and asks questions in plain language, without seeing SQL. SQL is an optional Advanced input, off by default. The Data tab looks and behaves like the Agent, Studio and Projects tabs.

Success looks like this:

- Every capability that needs SQL today (see data, save a query, build a report) works from the visual UI.
- All generated queries pass the existing read-only validator and run through the existing durable, idempotent job runner. No safety rule is relaxed.
- The Copilot, the grid, saved views and reports share one query model.
- Existing SQL saved queries, reports and tests keep working.

## Decisions already made

- Joins are in the first version. One base table plus up to four joined tables.
- Sidebar lists tables as selectable items. Each opens as a tab in the canvas.
- Pivot tables and segments are first-class, with audit.
- An optional grid (tile) view shows all tables.
- Three Radix packages are added (popover, dropdown-menu, tabs), because the design rules forbid hand-rolled overlays.
- Advanced SQL is a floating, non-modal panel, optional and off by default.
- Existing test labels and selectors are kept. Tests change together with any markup they pin.

## Assumptions to confirm during review

1. "Grid view for all tables" means an overview of every table in the source as tiles (name, columns, estimated rows, quick actions), with a list/grid toggle. It is not a multi-table spreadsheet.
2. Segment audit means the definition, its edit history, run history (who, when, rows, duration) and drill-down to the underlying rows. It does not mean materialized snapshots of membership.
3. SQLite first. PostgreSQL and MySQL use the same path after their existing source test.

## Query model

One JSON `QuerySpec`, validated server-side with Pydantic (`extra=forbid`):

- `source`: connection id.
- `from`: base table.
- `joins`: list of `{kind: inner | left, table, alias, on: [{left, right}]}`, equality on columns only. At most 4.
- `select`: columns `{table_alias, column, label}`.
- `filters`: a group `{op: and | or, items: [filter | group]}`. A filter is `{field, op, value(s)}` with ops eq, ne, gt, gte, lt, lte, between, in, not_in, contains, starts_with, is_null, not_null.
- `group_by`: columns, optionally with a date bucket (day, week, month, quarter, year).
- `aggregates`: count, count_distinct, sum, avg, min, max over a column.
- `sort`, `limit` (max 1000 preview), `offset`.
- `pivot` (optional): `{rows, columns, value}`. The column dimension is limited to 12 distinct values and expanded with conditional aggregation (`CASE`).

Compilation, new module `src/termx/data/query_spec.py`:

1. Every table and column is checked against the connection's introspected schema. Unknown names are rejected.
2. Identifiers are quoted with `quote_identifier`. Values are bound as named parameters, never interpolated.
3. The result is passed through `validate_read` again, then run with `runtime.run`, so the job, idempotency, cancel and result caps all apply.
4. Joins are guarded: an ON clause is required, no cross joins, and a fan-out hint is shown when a join multiplies rows.
5. Date bucketing is per dialect: `strftime` (SQLite), `date_trunc` (PostgreSQL). MySQL uses `year`/`month`/`day` unless `date_format` is added to the function allowlist, a one-line change reviewed in the plan.

Endpoints, under `/api/data/workspaces/{wid}`:

- `POST /query-specs/compile` returns the SQL for "Show SQL" and validation errors. It does not execute.
- `POST /query-specs/run` runs a spec (idempotent `request_id`).
- `GET|POST|PATCH /views` stores saved views (kinds: `table`, `pivot`, `segment`) with revision, owner, role-based access, and run history.
- `GET /connections/{id}/relations` returns suggested joins from foreign keys and matching column names.

BI reports may use a view as their dataset, wrapping the compiled SQL the way `compile_visual` wraps saved SQL today.

## Experience

Sidebar (same pattern as Studio, 260px, 36px rows, shell sidebar):
source switcher, search, then groups: Tables, Pivots, Segments, Saved queries, then Reports, Dashboards, Team. Selecting an item opens a tab in the canvas. The tab strip (Radix tabs) is closable and persisted by id.

Table tab:
- Virtualized grid with sortable column headers and a per-column filter popover.
- Filter chips in plain language ("Amount is greater than 100", "Date is between...", "Status is any of..."). Chips can be grouped with "Match all / Match any".
- "Combine with another table" suggests joins and shows them as a readable sentence.
- "Summarize" panel: pick measure and grouping ("Total of amount by month").
- Column picker, row count, truncation notice, CSV export, "Save view", "Make a chart".
- "Show SQL" reveals the generated SQL read-only.

Pivot tab: rows, columns and value pickers, filters, a live table, and click-through on any cell to open the underlying rows in a grid tab.

Segment: a named filter set over a table or view. It can be reused as a filter in any other view, exported as CSV, used as the source of a pivot or report, or saved as a view. This is the "further processing" path. The audit panel lists the definition, revisions with who and when, run history (rows, duration), and drill-down.

All tables overview: a list/grid toggle in the main area of the source home. Tiles show name, column count, estimated rows and actions: Open, Pivot, Combine.

Advanced: an "Advanced" menu entry opens the SQL panel (CodeMirror 6, floating, non-modal, closable, remembered per user). SQL results appear in the same grid. SQL saved queries keep working and appear under Saved queries.

Copilot sidebar: a right panel on the shared chat components, consistent with the other tabs. Proposals are structured specs shown as editable chips, with "Apply to table", "Open as pivot" and "Save as segment". Consent switches for schema and results stay. Tools added: `propose_query_spec`, `propose_pivot`, `propose_segment`. SQL proposals remain for the Advanced panel.

States for every view: loading skeletons matching the layout, empty (one sentence and one action), error inline with a recovery action, disabled with a reason. Engines needing a source test show the 409 as a "Test connection" action.

## Styling alignment

Data adopts the shared tokens and shell: shell sidebar and 48px header, 40px controls (32px compact), `--accent` primary, 1px `--border-strong` inputs, accent focus ring from `--agent-focus`, underline tabs matching Studio. The 44px forced buttons, near-white primary buttons, borderless inputs, `!important` overrides and the hand-built Copilot resizer are removed. Keep 44px on touch only (`pointer: coarse`).

## Phasing (four PRs, each reviewable and shippable)

The earlier proposal had three PRs. Pivots, segments and audit are new scope and become their own PR.

1. Foundation: styling, sidebar and table tabs, overview grid, QuerySpec with joins, filters, sort, summarize, "Show SQL", and moving raw SQL behind Advanced.
2. Pivots, segments and audit (views storage, drill-down, run history).
3. Copilot sidebar with structured proposals.
4. Advanced SQL floating panel polish and BI reports built on views.

## Testing

- Backend, test-first: spec validation, unknown column rejection, bound parameters, injection attempts in identifiers and values, join guard and fan-out, every filter op, pivot expansion, per-dialect bucketing, `validate_read` on every compiled output, idempotency and permission denial per role.
- Frontend: component tests with axe checks, keyboard paths, all states. Update pinned labels (`#data-sql`, "SQL query", region and button names) together with their tests.
- Browser: extend `workspace_data_studio_e2e.py`, and verify manually at 375px and 1280px in light and dark.
- CI note: frontend tests run on Node 22.

## Out of scope

Non-equality joins, subqueries, window functions in the visual builder (SQL still allows them), materialized segment snapshots, write access to sources, and engines beyond the qualified set.
