# Living characters

Open **Settings → Characters** to preview Sprout, the original robot bundled with TermX. Enable **Let characters explore** to meet the agents in your conversations. Roaming starts disabled and preferences belong to the signed-in user on this host.

Each conversation keeps its pet across turns; supervised subagents get separate pets. Four pets roam at once, with pins and agents needing attention taking priority. Additional agents live in the expandable home dock. Click a pet to open its conversation, drag it to reposition it, or open its context menu to pause, bring home, or hide it. **Bring pets home** also recovers pets after display changes.

The desktop shell draws native transparent sprites using AppKit on macOS, Win32 layered windows on Windows, and GTK/Cairo on X11. The browser workspace and restricted Wayland desktops provide an in-app habitat. Animation does not take keyboard focus or operate other applications. Window geometry stays inside the native process; generation never receives desktop screenshots or window titles. Lock, sign-out, backend disconnection, and OS session inactivity hide the native pets.

## Create your own

1. Select a configured image account and an explicit image model. Describe and name the character, acknowledge provider billing, then generate its canonical reference.
2. Inspect the reference and create its first animation. The provider generates an evenly spaced sheet; TermX compiles and aligns the frames into a draft.
3. Preview clips, pause or scrub frames, replace individual PNG frames, adjust timing and the foot anchor, or regenerate a selected clip using the original reference.
4. Review any shared fallback poses and accept the revision. Assign the accepted character to a conversation. Later drafts keep the previous accepted artwork active.

Generation uses the existing host credential store and media service. Cancelling a request cannot undo a provider charge, so interrupted outcomes are retained for inspection and the same request ID is never automatically rebilled. Providers must support the selected image-generation/edit operation. Supported GPT image models request transparent PNG output explicitly; **Remove solid background** is an optional exact-color matte removal step for other artwork. Always inspect the result before activation.

You can also import/export `.termxpet` packs or build a sheet from your own art. See [character pack tooling and format](character-packs.md) for the Python compiler/CLI, schema, limits, and editable pack directories.

## Agent integration

Authoritative task snapshots and events drive thinking, working, waiting, success, error, paused, and idle animations. Movement has independent walk, hop, fall, sit, drag, and sleep clips. Approval and recovery states take priority over emotes; completion celebrations expire instead of replaying after reconnect.

ACP, Claude, and Codex tool events use each adapter's actual call identifiers and
result envelopes. Parallel tool calls keep the character working until all calls
finish; duplicate completions, failed calls, interruption, and terminal task events
clear activity without treating ordinary message or reasoning items as tools.

The Internal engine exposes `companion_act`, `character_generate`, `character_build`, and `character_validate`. Generation requires approval for the selected provider/model. Tools retain the originating managed session and project authority, and recheck it after provider work and compilation. Emotes are bounded intents such as wave, sit, hop, sleep, home, and approach; they cannot approve a task or click other applications. Other engines' ordinary task events drive the same animations.

HTTP interfaces are owner-scoped: `/api/characters` lists the library and saved operations, `/api/companions/runtime` supplies recoverable snapshots, and `/api/companions/preferences` updates local preferences. Pack endpoints serve manifests, atlases and exports, accept reviewed revisions, adjust clips and replace individual frames. The native shell polls through its Rust-owned authenticated bridge, so animation continues while the main workspace is hidden.

## Verification

Run pack, service and authority coverage with:

```sh
.venv/bin/python -m pytest tests/test_character_packs.py tests/test_characters.py tests/test_character_security.py tests/test_character_media.py
```

Workspace tests cover frame timing, state priority, generation consent, draft acceptance, assignments, frame replacement and lifecycle cleanup. `desktop/src-tauri/native/README.md` describes native diagnostics. Installed Windows/Linux desktop behavior and OS-specific permissions still require platform qualification; a passing unit test or build is not that qualification. Delivery binaries continue to come from GitHub CI.
