# Data Studio delivery and verification record

Recorded 2026-11-14 (local date; underlying runs completed 2026-11-13 UTC). This is a dated record of the delivered stack, not a statement that every later main or documentation revision passed these tests.

## Delivered revisions

| Delivery | Tested source head | Merge on main |
| --- | --- | --- |
| Foundation, #54 | See #54 history | `67ff5c5efccfc6b85698c0caf6e39e61dcd1f023` |
| Pivots, segments and audit, #55 | `b3f890cc010aa1154726a8706869e6ee583ae7d7` | `e3ffad17b5b505f612260d304f972746341d75f2` |
| Copilot, BI on views and Advanced SQL, #56 | `fc0ac616d59c71e35fe33d4b77d4ba6c88aab1db` | `b59b054017ac721533b542644c3df84aa4f8d1f8` |

#55 was merged before #56. Git tree comparison proved #55's merge equals its tested head. After that base change, #56's computed merge tree and final main tree both equaled its tested tree, `a8052a3fb4fae76c9b8d726bc0ec1b78f2412851`. The final Data/migration and four browser journeys were also checked after the #55 merge. The documentation update preserves these revisions as evidence; it does not relabel their tests as new documentation-head runs.

The delivered fixes include friendly view-chart labels, protected archive behavior for published view reports, an app-native accessible report Data picker, editable Copilot values reaching all explicit actions, preserved proposal row limits, keyboard focus corrections and valid result row/cell roles. See the [user guide](../data-studio.md) for behavior and remaining limitations.

## Native CI on the final implementation heads

Full `workflow_dispatch` matrices were verified, including Windows rather than only the smaller PR-triggered workflow. All five jobs passed in each run: [#55 run 43506002290](https://github.com/Beyon-Digital/TermXMob/actions/runs/43506002290) and [#56 run 43506040083](https://github.com/Beyon-Digital/TermXMob/actions/runs/43506040083).

| Check | #55 passed / skipped | #56 passed / skipped |
| --- | --- | --- |
| Ubuntu Python | 4,346 / 188 | 4,385 / 191 |
| macOS Python | 4,344 / 190 | 4,383 / 193 |
| Windows Python | 4,287 / 247 | 4,326 / 250 |
| Frontend | 2,248 tests, 244 files | 2,319 tests, 251 files |
| Linux desktop capture | 9 / 1 | 9 / 1 |

TypeScript and Vite builds passed in both workspace jobs. The Linux capture skip is a macOS-only numeric-display case. Python skips include optional platform/engine dependencies; passing totals are not a claim that skipped tests ran.

**Subsequent main result:** the post-merge [Ubuntu job 132213590235](https://github.com/Beyon-Digital/TermXMob/actions/runs/43520546972/job/132213590235) at `b59b054017ac721533b542644c3df84aa4f8d1f8` reported `ProcessLookupError` in `tests/test_sandbox.py::test_host_runner_terminate_kills_process_tree` (1 failed, 4,384 passed, 191 skipped). It is a separate unresolved post-merge CI issue at the time of this record. The earlier successful matrices do not erase that failure, and this documentation update does not include a process-tree fix.

## Local Data, frontend and migration checks

All runtime checks used isolated test profiles and synthetic sources. No real desktop database was migrated for testing. Existing profile-backup metadata and hash were checked before/after; the backup and unrelated worktrees were preserved.

| Check | #55 head | #56 head |
| --- | --- | --- |
| Data Python suite | 771 passed, 42 skipped | 810 passed, 45 skipped |
| Affected native Python suites | 210 passed, 2 optional WebRTC skips | 210 passed, 2 optional WebRTC skips |
| Frontend and build | 2,248 tests / 244 files; build passed | 2,319 tests / 251 files; build passed |

The final Data suite passed again after the #55 base change: 810 passed, 45 skipped. Its seven migration/history cases cover preserving v1 rows, one-time backup, no-op reopen without recopying, empty/new-store backup behavior, newer-schema refusal, atomic revision writes, and indexed/actor-filtered run history. Durable request-id, cancel and repeated-operation behavior remain covered in the Data suites.

Recorded command families were `.venv/bin/python -B -m pytest -q` with explicit Data/affected-module lists, JUnit output and a scratch `--basetemp`; and `pnpm test` / `pnpm run build` in `desktop/workspace`. Node was 22.23.5 and pnpm 10.20.0. For a fresh checkout, the following reproduces the Data selection, with all state isolated (it does not itself provision live network engines):

```sh
qa_root=$(mktemp -d /tmp/termx-data-qa.XXXXXX)
export TERMX_CONFIG_DIR="$qa_root/config"
export TERMX_AGENTS_DIR="$qa_root/agents"
export TERMX_ACP_HOME="$qa_root/acp"
export TERMX_ENGINE_STARTUP_REFRESH=0
export TERMX_PROVIDER_STARTUP_REFRESH=0
export TERMX_AGENT_DESKTOP_STARTUP_REFRESH=0
unset TERMX_AGENT_DESKTOP
unset TERMX_TEST_PG_URL TERMX_TEST_MYSQL_URL TERMX_TEST_MARIADB_URL TERMX_TEST_SERVER_VERSION
uv sync --locked --extra development --extra media
uv run --no-sync python -B -m pytest -q tests/test_data_*.py \
  --junitxml="$qa_root/data.xml" --basetemp="$qa_root/pytest"
```

Check the collected modules when reproducing on a later revision; a glob can select more tests as the repository grows. Missing live-engine URLs cause explicit skips and do not replace the engine gate.

## Browser journeys and accessibility

The real-host Data harness ran at 375px/light, 1280px/dark, 1280px/light and 375px/dark against a synthetic SQLite source and mocked inference.

| Evidence | #55 head | #56 head, also after #55 base change |
| --- | --- | --- |
| Captured states | 128 | 184 |
| Functional checks | 36 | 48 |
| Focus checks | 32 | 40 |
| Retained screenshots | 192 | 248 |
| Axe violations / overflow / page errors / external browser requests | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| Axe incomplete entries retained for review | 82 | 166 |

Journeys cover table browsing/joins/filtering, pivot totals and drill-down against the source, reusable segments, revisions/run audit, conflict/revocation behavior, Copilot value edits and actions, report picker keyboard/focus behavior, friendly chart labels, archive/publication fallback and Advanced SQL.

The browser was Chromium 145.0.7632.6 with Playwright 1.58.0 and axe 4.10.3. Automated checks and screenshots do not establish VoiceOver behavior, physical-device testing, or packaged desktop release qualification. Axe incomplete entries were retained, not counted as passed rules.

After building `desktop/workspace`, reproduce with:

```sh
uv run --no-sync playwright install chromium
uv run --no-sync python -B tests/workspace_data_studio_e2e.py \
  "$qa_root/browser" desktop/workspace/dist
```

The harness creates its own isolated host state and synthetic database. Keep its output/report/screenshots for review; do not point it at a real profile or source.

## Live engine coverage

Selected live tests passed on seven disposable servers per implementation head: [#55 engine run 43501186522](https://github.com/Beyon-Digital/TermXMob/actions/runs/43501186522) and [#56 engine run 43501189373](https://github.com/Beyon-Digital/TermXMob/actions/runs/43501189373).

| Family | Actual versions |
| --- | --- |
| PostgreSQL | 12.22, 16.16 |
| MySQL | 8.0.17, 8.4.13 |
| MariaDB | 10.6.28, 10.11.19, 11.8.9 |

#55's engine PR merge checkout was `566b3d4a0d093f5bff95d19274cc46bc742a0bac`, with the same tracked tree as `b3f890c`. #56's was `3b829c24ccc0247cc4ead90a5476fb30794ade99`, with the same tracked tree as `fc0ac61`. Aggregate counts across the seven selected rows were #55: 1,859 passed / 199 skipped; #56: 2,076 passed / 213 skipped, with zero failures/errors. Other-engine tests are explicitly skipped per row; no required selected-engine live check was skipped.

Coverage includes schema/relationships, typed results, exact text comparisons, date boundaries, statement deadlines, joins, pivots, segments and reports on views. It does not establish every intermediate version, charset/collation configuration, production dataset, or complete TLS/account enrollment path. The catalogue still marks network drivers installed/unqualified; source-specific test and read-only authority remain required. See [compatibility and reproduction commands](../data-engine-compatibility.md).

## Current limits

- Publications preserve definitions and labels, not a snapshot of source rows or a permanent permission grant.
- Schema/Results consent starts off, but enabled switches currently persist within the same context despite the “next message only” hint. Results consent can permit new authorized source queries. This is documented, not repaired by this documentation change.
- Preview/CSV bounds are distinct from report aggregation over the full source definition.
- No write access, materialized segment memberships, full Power BI parity, Microsoft/Fabric compatibility or packaged-device qualification is claimed.

The original [design](../superpowers/specs/2026-11-12-data-studio-query-ux-design.md), [foundation plan](../superpowers/plans/2026-11-12-data-studio-foundation.md) and [pivots/segments plan](../superpowers/plans/2026-11-13-data-studio-pivots-segments.md) remain available for historical decisions and implementation sequencing.
