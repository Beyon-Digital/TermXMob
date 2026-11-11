# Studio, Agent and Data implementation status

This change adds independent Agent, media Studio and Data workspaces. It also adds custom workflow execution and the supporting project/session UX. The implementation is usable locally; the advanced BI roadmap and universal connector coverage remain incomplete.

## Design references

The shared app frame uses a compact rail, quiet navigation, focused content, anchored composer menus and inset keyboard focus. Studio and Agent keep independent navigation and composers. Collapsible panels support reduced motion. Local reference screenshots and development captures are not redistributed in the repository.

## Agent workspace

- Project navigation, registration and folder selection use an explicit filesystem picker.
- Coding conversations can create and use isolated Git worktrees automatically.
- Conversation titles derive from the first message. Delegated agents have a dedicated side panel.
- Response actions, typed content previews, model selection and the animated effort control replace crowded composer controls.
- Task and native-worker count/runtime ceilings have been removed. Explicit provider controls and optional manually selected workflow limits remain available.

## Media Studio

Studio includes creation, library/assets, project management and settings. Provider/model capability routing selects the configured adapter rather than hard-coding one upstream service.

Image, video and editing operations remain adapter-specific. A model name does not imply that its API supports generation or a particular editing operation. Unsupported operations stay explicit. OpenRouter is used only for supported media contracts; other adapters require their own configured connection.

Music supports ElevenLabs and supported OpenRouter Lyria music operations. Text-to-speech models are not offered as music generators. Music model controls follow actual provider capabilities. Accounts, enabled models, defaults and optional generation limits are managed in Studio settings.

## Workflows

The workflow editor supports saved custom definitions and run receipts. Coding agents can create and run workflows through scoped tools. The JavaScript worker uses the pinned QuickJS runtime and an explicit host-tool bridge. It does not expose arbitrary host execution through JavaScript globals.

Manual runs can have optional limits. Agent-triggered workflows have no automatic task runtime/tool-round ceiling. Explicit provider controls and bounded transport/result memory still apply. Unknown interrupted outcomes are recorded rather than silently replayed.

## Execution and path selection

Filesystem controls browse the host's authorized paths instead of requiring a raw path string. Coding worktrees and workflow source paths retain their existing authorization boundary. Local source selection does not grant unrelated filesystem or database write access.

## Data Studio

Open **Data** in the rail or navigate to `/?workspace=data`. Create a workspace, then use:

1. **Database:** connect a source, inspect schema, run parameterized read SQL, save queries and export the available result preview.
2. **BI:** create a report from a saved query, choose a visual, save/run it, publish a version and compose dashboards.
3. **Team:** add existing authenticated host users as owner/editor/viewer. Source sharing is explicit.
4. **Copilot:** choose an existing account/model and opt into schema/results context. Query/report proposals require an explicit action before saving or publication.

SQLite is verified with actual synthetic business files. PostgreSQL, MySQL and MariaDB have installed fixed adapters with verified-TLS and live read-only-role checks; real-server/version qualification is pending. Other catalog engine families remain unsupported until their adapters are implemented and qualified.

Reports support table, single-dimension pivot, KPI, bar, line, area, scatter and pie. Typed filters and aggregates execute over the full saved source query, rather than summing a truncated preview. Published versions pin SQL/parameters. CSV and SVG export are implemented. Responsive chart labels remain 12px at the verified widths.

Team collaboration shares one authenticated TermX host. Membership, source grants, revision conflicts and published-reader rules are enforced at runtime. This does not introduce hosted SaaS deployment, distributed tenancy or multi-host synchronization.

The current slice is read-only. Source DML/DDL, administration, backup/restore, preparation/extract pipelines, relationship models, reusable semantic/time-intelligence expressions, model RLS/column policies, multi-page report canvases, drill/bookmarks, scheduled refresh, alerts/goals, paginated/PDF/XLSX outputs, Microsoft-format compatibility and Fabric/distributed workloads remain unimplemented or partial.

## Verification

Local macOS evidence for the publication candidate:

| Check | Result and scope |
| --- | --- |
| Full frontend suite | 1,877 passed; 267 suites. |
| TypeScript and production UI build | Passed using the project's Node 24 runtime. |
| Full backend suite | 3,552 passed, 148 skipped, 48 warnings. Skips include platform-specific and opt-in native/browser/container integrations. |
| Final focused Data backend suite | 102 passed, no skips. |
| Frozen parser regression | 5 passed, 1 platform skip using the pinned freezer; all three supported SQL dialects load in the built executable. |
| Data browser proof | 60 captured states, 12 full journeys and 32 inset-focus checks at 375px/1280px in light/dark. Zero axe violations; incomplete automated checks remain inconclusive. |
| Data query/BI correctness | Actual 2,501-row SQLite file; 1,000-row truncated preview; full-source aggregate 3,128,751; source bytes unchanged. |
| Copilot integration | Actual provider adapter/tool loop over 20 mocked HTTP requests; actual SQLite tool reads and typed proposals. No paid inference. |
| Native Mac app | Tested assets served by the native-owned backend; authenticated Data landing and Create workspace observed. Existing profile/providers preserved. |

The native observation establishes the landing screen, not a full real-user database/provider mutation journey. Synthetic browser fixtures establish the query/report/team/Copilot journeys. No real business database or paid generation was used for those checks. A frozen installer, signing/notarization, production team hosting and physical devices require separate evidence.

Publication CI runs the repository's workspace, Linux Python and relevant Windows contract checks. Installer workflows and additional main-branch platform jobs have different triggers; a passing PR does not establish a packaged release on every OS.
