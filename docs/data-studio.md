# Data Studio

Open **Data** in the app rail, or use `/?workspace=data`. The Data sidebar lists the workspace, the source, its tables, saved queries, reports, dashboards and the team. Each table or saved query opens as a closable tab next to the **Overview** tab; open tabs are remembered by id only. **Copilot** opens the right panel.

## Use it

1. Create a Data workspace. It does not need an Agent project or folder.
2. Choose **Connect database**. SQLite uses the host filesystem chooser. Network profiles accept an explicit server, database, account and password.
3. Press **Test connection**, choose **Load schema** to list the tables (Overview offers a list or tile layout), then open **New query** in the sidebar, enter a read query and press **Run**. Save reusable queries by name. Parameters are JSON values bound by the adapter.
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

## Decisions

- The sidebar Tables group shows 200 tables at a time with "Show more tables", not a virtualized list. Search filters the whole list, and virtualization is deferred until a source needs it.
- The shared theme tokens keep the repository's monochrome accent. Moving Data to the DESIGN.md accent is a decision for the design owner, because Agent and Studio share the same tokens.
