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
