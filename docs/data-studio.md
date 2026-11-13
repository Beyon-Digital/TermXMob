# Data Studio

Open **Data** in the app rail, or use `/?workspace=data`. The Data sidebar lists the workspace, the source, its tables, saved queries, reports, dashboards and the team. Each table or saved query opens as a closable tab next to the **Overview** tab; open tabs are remembered by id only. **Copilot** opens the right panel.

## Use it

1. Create a Data workspace. It does not need an Agent project or folder.
2. Choose **Connect database**. SQLite uses the host filesystem chooser. Network profiles accept an explicit server, database, account and password.
3. Press **Test connection**, choose **Load tables** to list them (opening a table or a saved view loads them for you) (Overview offers a list or tile layout), then open **New query** in the sidebar, enter a read query and press **Run**. Save reusable queries by name. Parameters are JSON values bound by the adapter.
4. Open Reports, create a report from a saved query, choose a visual and run it. Save changes before running a changed definition. Publish a version for viewers or add the report to a dashboard.
5. In Team > Members, add an existing host username as editor or viewer. Sources require their creator to enable shared read access. Viewers run published reports; they cannot run arbitrary SQL or see unpublished edits.
6. Open Copilot and choose an existing account/model in its settings. Account sharing is explicit. Schema and Results context are optional. Query/report proposals require **Open query** or **Save report**; they do not silently publish.

## Current support

| Engine | Status |
| --- | --- |
| SQLite | Actual synthetic-file read, schema, cancellation, CSV and BI workflows verified. |
| PostgreSQL | Fixed driver installed. Test/Run require explicit credentials, verified TLS and a strict live read-only role. Real-server qualification pending. |
| MySQL / MariaDB | Fixed driver installed with verified TLS and restricted read-only account checks. Real-server qualification pending. |
| Other listed engine families | Explicitly unsupported until their adapters are implemented and qualified. |

The qualified operation is read-only. Source writes, migrations, backup/restore and arbitrary connector plugins are unavailable. SQLite source enrollment also requires live host file-read authority; Data membership alone does not grant host filesystem access.

## Results and reports

Query results show up to 1,000 rows within a bounded payload. Truncation is visible; CSV exports contain that available preview. BI aggregates run against the full saved source query, rather than summing the preview.

Reports support table, one-dimension pivot, KPI, bar, line, area, scatter and pie views, with typed filters. Published versions pin their SQL/parameters. Dashboards run the selected published reports. CSV and SVG exports are implemented.

Advanced relationship models, DAX/M, preparation/extract pipelines, scheduled refresh, alerts, goals, paginated/PDF/XLSX outputs and Microsoft/Fabric format/service compatibility remain outside this usable slice. This is not full Power BI parity.

## Collaboration and execution

Teams share the same authenticated TermX host. The release adds logical workspaces, owner/editor/viewer roles, revision conflicts, live revocation and publication boundaries. It does not deploy a hosted service or add multi-host synchronization.

Jobs and Copilot receipts persist in the host's separate Data store. Interrupted host runs become unknown and are not automatically replayed. Query cancellation has an acknowledged SQLite path; network cancellation waits for the driver to return and discards the result. No arbitrary job time deadline is imposed.

Existing account secrets stay in the host credential service. No provider calls run on initialization. The integration was tested with synthetic databases and mocked inference, without paid requests or real-source access.

[Verification and screenshots](implementation-status.md#verification) · [Canonical plan and remaining scope](implementation-status.md#data-studio)

## Visual query behavior across engines

SQLite, PostgreSQL, MySQL and MariaDB each have their own adapter (`src/termx/data/dialects`). The same table view returns the same rows and the same JSON types on all four.

SQL feature minimums are SQLite 3.30, PostgreSQL 12, MySQL 8.0.17 and MariaDB 10.6.
See the [version matrix and reproduction commands](data-engine-compatibility.md) for exact coverage and its limits.

- **Run limits.** Every source read has a server-side deadline: 30 seconds by default, set with `TERMX_DATA_STATEMENT_TIMEOUT_SECONDS` (0.1 to 600). SQLite uses a progress handler, PostgreSQL `statement_timeout`, MySQL `MAX_EXECUTION_TIME` and MariaDB `max_statement_time`. A run that hits it ends as failed with a plain message. One person can have 4 runs in flight and one connection 8; more get a 429. Source reads use their own pool of 16 threads.
- **Text is compared exactly.** MySQL and MariaDB columns are case- and often accent-insensitive by default. Equality, "is any of", "contains", Group by, unique counts and matches between tables use a binary collation there, so `Alice` and `alice` are different values on every engine.
- **Result types.** Whole-number results are integers, decimal and average results are numbers, yes/no columns are true or false, and datetimes read `YYYY-MM-DDTHH:MM:SS`.
- **Names.** PostgreSQL keeps 63 bytes of a result name. A longer name you type is refused; a longer default name is shortened.
- **Values.** UUID columns need a valid UUID. List columns can only be filtered by empty or by text they contain. A whole number past 64 bits is compared as a decimal number.
- **Combining tables.** The builder warns when neither side of a match is a key, and when a match can repeat rows.

## Pivots, segments and audit

**Saved views** are named, versioned queries in the Data store: a *table* (anything the Table tab builds), a *pivot* or a *segment*. Each keeps one validated query, a revision number, its creator and timestamps.

- **Pivots** show rows by columns with one value (a count, total, average, smallest, largest or unique count) and a Total column. Columns come from the data and are limited to **12 different values**; the empty value is a column of its own, labelled "(empty)". When the data has more, the pivot is refused with a message that suggests a larger step or a filter, and no subset is chosen. Column order does not depend on the engine (text by code point, dates in time order, empty last). Rows of text follow each engine's grouping order.
- **Drill-down** returns the rows behind a pivot cell, a Total, or a group of a grouped table. A month, week or other date step becomes a start (included) and end (excluded) date range. Only editors and owners can drill: it reveals rows that a total hides. An empty date step on text dates in SQLite is approximate, and the result says so.
- **Segments** are saved sets of filters on one table. A filter can say "in segment" or "not in segment"; the server puts the segment's own filters into the same statement, checks that its table and combined tables are present, and refuses loops, more than 3 levels and more than 20 segments in one query. "Not in" keeps rows where the segment test is empty, so a segment and its complement always add up to the whole table.
- **Delete protection.** A segment that other views use cannot be deleted, and its table or combined tables cannot change; the response lists the views that use it. Editing its filters is allowed and reports how many views are affected.
- **History.** Every create, edit and delete of a view writes a revision with who, when and what changed, in the same transaction as the change. Saving with an old revision returns a 409 that names who changed it. Run history lists who ran a view, when, how many rows and how long it took, from the existing jobs; it never contains cell values. Editors and owners see everyone's runs, viewers only their own.
- **Roles.** Owners and editors create and edit views on connections they can use. The workspace owner or the view's creator can delete it. Viewers can run, count and export views on shared connections, and see their own runs; they cannot create, edit, drill, read edit history or run other queries. Another person's private connection is never usable, including by the workspace owner.
- **CSV export** of a view run uses the display names of the columns (a pivot's SQL names are neutral).

| Engine | Pivot cell | Notes |
| --- | --- | --- |
| SQLite | `SUM(CASE WHEN ... THEN ... END)`, `strftime` date steps | Text dates can be used as a column with a warning. |
| PostgreSQL | same, `to_char` date steps | Averages are cast to a double. |
| MySQL | same, `date_format` date steps | Text compared with a binary collation, as in tables. |
| MariaDB | same as MySQL | Groups on the bare column as well, for `ONLY_FULL_GROUP_BY`. |

An engine without an adapter fails closed: views can be listed and their history read, but they cannot be created, run or drilled.

**Upgrading.** The Data store moves from schema version 1 to 2 (new history table and a run index; nothing existing is changed). A non-empty version 1 store is copied to `data.sqlite3.pre-views-v1` first. A TermX build from before saved views refuses to open a version 2 store; to go back, restore that copy (views made since are lost).

### Using pivots, segments and audit

1. **Pivot.** In the sidebar, choose the plus next to **Pivots** and pick a table. Under **Rows** pick a column, under **Columns** a column (a date column is grouped by month unless you change **Columns by**), and under **Value** choose **Show** (Number of rows, Total of, Average of, Smallest, Largest, Unique values of) and the column. The pivot updates as you change the controls. **Combine with another table** and filters work as in a table.
2. **Drill into a number.** Select any number, or move with the arrow keys and press Enter, to open the rows behind it in a new tab. For a saved pivot the server answers; for an unsaved one the rows are built from the same definition on the page. Only editors and owners can drill.
3. **Save as a view.** **Save view** names the pivot and adds it to the **Pivots** group. After that a changed pivot shows **Save changes**, and **Reset** goes back to the saved version. Opening a saved pivot runs it as that view, so the run is attributed to it.
4. **Segment.** In a table, add filters and choose **Save as segment**. Segments appear in the **Segments** group with "Used in N" once other views use them. In any table or pivot tab, **Add segment** keeps rows in or outside a segment. **Edit filters** in the segment tab changes the definition; **Save changes** writes a new revision. **Count members** counts the rows without showing them.
5. **Audit.** Each segment tab has an **Audit** section. **Revisions** (editors and owners) lists who changed the segment, when, and what changed in sentences. **Runs** lists who ran it, when, how many rows, how long it took, the result and the revision it ran on. Viewers see **Your runs** only.
6. **Conflicts.** If someone saves a newer revision while you edit, your save is refused and a banner names who changed it. **Reload latest** discards your edits and shows the new revision; nothing is overwritten.
7. **Viewers.** A viewer sees the shared source and its pivots and segments, can count and export them, and sees their own runs. Editing, drilling, revisions and other people's runs are not offered, and the server refuses them with 403.

The viewer's workspace snapshot lists a shared source with its name and engine only. The file path, account and read policy stay hidden; they were never part of what a viewer can open.

**Verification.** `tests/workspace_data_studio_e2e.py` runs these flows in a real browser against a running host and a synthetic SQLite file with `customers`, `regions` and `sales`: pivot of the total of `amount` by region and month checked against the file, a drill whose row count equals the file, a saved view, a segment reused in a second table tab, a revision and runs read back in the Audit section, a delete refused while a view uses the segment, a viewer who cannot edit, and a 409 from a second editor. It runs in light and dark at 375 and 1280 px, and every state is checked with axe.

## Decisions

- The sidebar Tables group shows 200 tables at a time with "Show more tables", not a virtualized list. Search filters the whole list, and virtualization is deferred until a source needs it.
- The shared theme tokens keep the repository's monochrome accent. Moving Data to the DESIGN.md accent is a decision for the design owner, because Agent and Studio share the same tokens.
- Pivots have a Total column but no bottom total row, because averages and unique counts cannot be added up. Run history keeps job results as long as jobs are kept; a retention rule needs an owner decision.
