# Linux packaging qualification resume

Frozen at the quota boundary. No repair, commit, push, dispatch, cancellation, or installer download was performed by this agent after the freeze request.

## Exact checkpoint

- Source: `1094609da6b390ec46438f9cd32f5cb3dfa1acad`.
- Artifact-only run: https://github.com/Beyon-Digital/TermXMob/actions/runs/37693649699
- Linux job: https://github.com/Beyon-Digital/TermXMob/actions/runs/37693649699/job/113039789528
- Outcome: failed only at **Verify installed Linux native workspace**. Packaging succeeded.
- Frontend: 245 passed. Complete Linux backend: 1124 passed, 112 skipped, 30 warnings, 149.76 seconds.
- Actual AV closure: staged **32 exact AV wheel libraries** with relative dependency paths; staging checks every actual library with clean `ldd` before copying/signing/frozen smoke.
- Frozen runtime executed Node, five language servers, both debug adapters, Chromium rendering, and FFmpeg successfully.
- Real owned X11, native bridge, OS credential service, debug adapters, browser contracts, and rendered accessibility gates succeeded.
- All three installers built: DEB 594.55 MiB, RPM 595.64 MiB, AppImage 544.57 MiB. This closes the previous missing hashed AV dependency at the packaging gate. It does not prove installed AppImage GUI lifecycle.

## Remaining installed DEB fixture failure

The DEB was installed and the native GUI reached `terminal_transport` after sign-in. Its cleanup request failed schema validation at `desktop/scripts/native_gui_smoke.py:247`:

```graphql
mutation($id:String!){deleteSession(sessionId:$id){ok}}
```

The response was HTTP 200 with GraphQL errors: `Cannot query field 'deleteSession' on type 'Mutation'. Did you mean 'delete_session', 'create_session', or 'rename_session'?`.

The source resolver is `src/termx/graphql/domains/workspace.py:134`: `delete_session(self, info, session_id: str) -> T.Ok`; the installed host exposes snake-case fields. Expected replacement to verify against generated schema:

```graphql
mutation($id:String!){delete_session(session_id:$id){ok}}
```

**Repair status: not started; not checked.** The freeze request superseded the repair authorization before any script/test edit. Resume by inspecting the live `delete_session` resolver/schema and matching both field and argument casing, then add a narrow direct schema contract check and run focused tests. The exception is raised in cleanup, so do not infer that all terminal transport assertions succeeded from the traceback alone. Preserve all other native GUI lifecycle assertions and rerun installed qualification through CI; no local Rust delivery build.

## Evidence retained

- `/tmp/termx-linux-109-artifact.json`: complete job step outcomes/exact source.
- `/tmp/termx-linux-109-artifact.log`: completed Linux log, includes AV count, package outputs and traceback.
- `/tmp/termx-linux-109-artifact-watch.log`: only meaningful stage changes.
- Small GUI artifact available: `workspace-native-gui-linux`, artifact ID `11516417237`, 90,681 bytes. **Not downloaded before freeze.** Fetch only this report/diagnostic artifact if needed; do not download the 1.81 GB installer artifact.
- Old 7f run `37687927763` was cancelled by root during `apt-get update`; no runtime/bundle/GUI qualification. `/tmp/termx-linux-7f-artifact.{json,log}` records this incomplete cancellation separately.

## Exact log excerpts

```text
2026-10-07T22:04:18.3857153Z       Tests  245 passed (245)
2026-10-07T22:07:41.9825494Z 1124 passed, 112 skipped, 30 warnings in 149.76s (0:02:29)
2026-10-07T22:08:30.5280693Z staged 32 exact AV wheel libraries with relative dependency paths
2026-10-07T22:08:42.3076485Z Frozen sidecar executed Node, five language servers, both debug adapters, Chromium rendering and FFmpeg successfully
2026-10-07T22:49:44.7884467Z     Finished [tauri_bundler::bundle] 3 bundles at:
2026-10-07T22:49:44.7885155Z         /home/runner/work/TermXMob/TermXMob/desktop/src-tauri/target/release/bundle/deb/Termx_0.2.6_amd64.deb (594.55 MiB)
2026-10-07T22:49:44.7885936Z         /home/runner/work/TermXMob/TermXMob/desktop/src-tauri/target/release/bundle/rpm/Termx-0.2.6-1.x86_64.rpm (595.64 MiB)
2026-10-07T22:49:44.7886859Z         /home/runner/work/TermXMob/TermXMob/desktop/src-tauri/target/release/bundle/appimage/Termx_0.2.6_amd64.AppImage (544.57 MiB)
2026-10-07T22:50:23.1975216Z AssertionError: {'status': 200, 'body': {'errors': [{'locations': [{'column': 23, 'line': 1}], 'message': "Cannot query field 'deleteSession' on type 'Mutation'. Did you mean 'delete_session', 'create_session', or 'rename_session'?"}], 'data': None}}
```
