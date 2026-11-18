# Workspace UI: the agent-first IDE shell

TermX is a multi-agent control surface and a coding environment in one window. The workspace (`desktop/workspace`, React 19 and Vite 7) draws one shell: a title bar, an activity bar, a side bar of views, a split tree of tab groups, a bottom panel and a status bar. This page describes what ships. The visual rules are the shared DESIGN.md (calm, content first, warm neutrals, one terracotta accent), applied through `src/styles/tokens.css` with the IDE density extensions listed below.

The IDE layout is the default. The earlier dock of seven panels is the **classic layout**: deprecated, kept for one release as a rollback path, and described only under [Flags and deprecation](#flags-and-deprecation).

## The frame

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│ [≡] termxmob ▸ main        [ Search files and commands  ⌘P ]      [▯][▁] 2 need you│ 40
├────┬───────────────────┬──────────────────────────────────┬───────────────────────┤
│ Ag │ Agents       [+]  │ Main agent × │ api.ts ● │ +      │ Changes × │ Browser × │ 36
│ Ex │ Needs you (2)     ├──────────────────────────────────┼───────────────────────┤
│ Se │ Main agent working│      ‹ 720px chat column ›       │ src/api.ts   +12 −3   │
│ SC │ Sub-agents        │  ▸ 12 actions · Completed        │ @@ -10,6 +10,9 @@     │
│ Si │ CLI sessions      │  ┌ composer ─────────────────┐   │                       │
│ Da │  Claude Code  appr│  │ [+]      model ▾   [Send] │   │                       │
│ St │  Codex        done│  └───────────────────────────┘   │                       │
│    │ Chats             ├──────────────────────────────────┴───────────────────────┤
│ Pr │                   │ Terminal │ Problems │ Preview     zsh × │ pnpm dev × │ +  │ 36
│ ⚙  │                   │ $ pnpm test                                               │
├────┴───────────────────┴───────────────────────────────────────────────────────────┤
│ ● Connected · this Mac   main*   0 problems        Main agent working   Lock      │ 24
└───────────────────────────────────────────────────────────────────────────────────┘
  48        260                      flex                          380
```

- **Title bar** (40px, `components/shell/TitleBar.tsx`). Side bar toggle, `project > branch`, the command center ("Search files and commands", with the Quick open key), the bottom panel toggle, an "N need you" badge that appears only when something needs the owner (bots waiting, sub-agents needing attention, blocked task-board nodes, CLI sessions needing a person) and the account menu (Lock workspace, Settings, Sign out). The window has no custom drag region; the native title bar moves it.
- **Activity bar** (48px). One vertical toolbar with a roving tab stop. Top: Agents (the default), Explorer, Search, Source control, Sites, Data, Studio. Bottom: Projects, Settings, Account. The selected view has a `--surface-2` fill and an accent bar; choosing it again hides the side bar. Agents carries a dot, and "N need you" in its name, when something needs the owner.
- **Side bar** (260px, resizable 200 to 420 by pointer or keyboard; Home, End and Enter reset). One view at a time, below.
- **Tab groups** (`components/shell/TabGroups.tsx`). A recursive split tree, below.
- **Bottom panel.** Its own tab group: Terminal, Problems (language server diagnostics for open files), Preview (the dev-server preview) and Run and debug. Output has no data source today, so it is not offered.
- **Status bar** (24px). Host health dot and name (a button that opens the host popover), branch with `*` when the checkout is dirty, problems (hidden at 0), active capture controls, "N need you", Main agent state, Ln and Col with the language for the focused file tab, Lock. An item is a button only when it does something.
- **Phone** (under 768px). A 48px top bar (hamburger, the active tab's title, a tab switcher that also closes the tab on screen), one tab stack, a bottom navigation of Agents, Files, Git, Term and More with 44px targets, the side bar as a Radix drawer (Data's navigation lives only there), and a status bar reduced to the health dot, the capture controls and the "N need you" badge. The projection is made at render time; choosing a tab there never writes the stored layout, and opening a tab (Settings from More, a file from the drawer) takes the stack.

Chrome is separated by 1px borders: no shadow, gradient or blur. Overlays (menu, popover, dialog) use a border and `--shadow-overlay`.

### Side bar views

| View | What it shows |
| --- | --- |
| **Agents** | New chat (the view's one primary button), Needs you, Main agent with its state, Sub-agents (a compact list that keeps updating while the Chat tab is behind another tab), CLI sessions, and the chat list at 36px rows. |
| **Explorer** | The project and checkout, an ARIA tree of 32px rows with a source control letter on changed files. Enter opens a file tab. With no project it says so and offers Add project; Search and Source control do the same. |
| **Search** | Workspace text search through the host's file search. A result opens its file at the line. |
| **Source control** | Branch, commit box and the changed files, below. |
| **Sites** | Pinned, saved, suggested and open pages, below. |
| **Data**, **Studio** | Their own navigation (Data's connections and views; Studio's Create, Music, Library, Saved assets and projects). Choosing either also opens its singleton tab. |

## Layout engine

The layout is `Layout {version: 2, root, panel, sideBar, focus, maximized}` (`src/lib/layout/model.ts`). `root` is a split tree: a `Split {id, dir, sizes, children}` or a `Group {id, tabs, active}`. `panel` is the bottom panel (its own group, a height in px, visible or not); `sideBar` has a view, a width and visibility.

- **Tabs.** A tab is `{id, kind, ref?, pinned?}` with 13 kinds. Multi-instance kinds (one tab per `ref`): `chat`, `file`, `diff`, `terminal`, `cli`, `browser`. Singletons: `computer`, `artifacts`, `data`, `studio`, `settings`, `projects`, `workflows`. The `ref` retargets a singleton: Settings shows the category in its `ref`, Projects one of Overview, Projects and Activity, Workflows a run. Opening an existing tab reveals it instead of adding a second.
- **Validation** (`validateLayout`). At most 6 levels, 12 groups, 64 tabs and 256 KB; tabs of unknown kind or with a `ref` that no longer resolves are dropped; split sizes are normalised to sum to 1 (no pane under 5%); empty groups and one-child splits collapse; the root is never empty (it falls back to the Chat tab). A book holds the current layout and up to 30 named layouts.
- **Reducer** (`reduce.ts`). Pure. Open, close with a reopen stack of 20, move, split, resize, maximize and restore, set active, cycle, focus, side bar and panel toggles and sizes, saved layouts, reset, and an undo stack of 20 (a drag of one splitter is one step).
- **Rendering.** `layoutRects` gives every group, splitter and drop zone its rectangle. Tab content renders in one flat layer keyed by tab id, so a terminal, an editor or a browser stream stays mounted when a tab moves, splits or hides; only its rectangle changes. Inactive tabs are `hidden` and `inert`. A tab strip is a Radix tablist (arrows, Home, End) with a `+` menu and a Tab actions menu (also on right click). Splitters are 1px lines with an 8px hit area, `role="separator"`, resized with the arrow keys (Shift for larger steps), Home, End, and Enter to reset. A tab is dragged to one of five drop zones; touch and keyboard use the menu (Split right, Split down, Move to other group, Maximize group, Move to new window, Reveal).
- **Storage** (`store.ts`). `termx-layout-v2:<json [owner, device]>`. Closing Settings, Close others, Close group, Saved layouts and Reset layout ask first when they would drop unsaved edits (`SETTINGS_LEAVE`); file tabs and terminal tabs ask when they hold unsaved edits or a live shell. A detached window shows one tab and never writes the layout.
- **Migration** (`migrate-v1.ts`). On the first launch with no v2 book, the profile's v1 presets (`termx-layout-presets-v1:`) become the layout it last used and named layouts: regions become a row of groups, pixel widths become fractions of a 1280px reference window, `left` becomes the side bar, `bottom` becomes the panel, and the eight built-in presets are added (Chat, Code + chat, Browser + chat, Computer + chat, Artifacts + chat, Review + chat, Terminal + chat, Focus). A saved layout with a built-in name wins. The v1 key is never written or removed, so rolling back to the classic layout finds its own data. `tree-shell.test.tsx` runs the committed v1 fixture through the real app and checks that the profile's own presets arrive as named layouts.
- **Detach.** "Move to new window" and the Window menu use the native detach flow. `shell/window-panel.ts` maps a kind to one of the seven `valid_panel` strings (`file` to `workbench`, `diff` to `review`, `cli` to `terminal`, `browser` to `browser`); Settings, Projects, Workflows, Data and Studio cannot move. A detached window opens that panel's tab with no `ref`.

## Tab kinds

| Kind | Content |
| --- | --- |
| `chat` | The conversation (720px column, one-line "N actions" summary, composer on `--surface`, a token-styled effort control). Follows the selected conversation. |
| `file` | One CodeMirror editor per file with the TermX theme, language from the extension, find and replace, folding, breadcrumbs, a dirty dot, Mod+S, and an Editor actions menu (definition, documentation, completions, format). Buffers live in one store above the tabs, so a tab that moves keeps its edits and the detach and redock payload (`{version: 1, sessionId, buffers, draft}`) is unchanged. |
| `diff` | A staged or unstaged diff of one file (`staged:<path>` or `worktree:<path>`): inline or side by side, per-hunk Stage and Unstage, F7 and Shift+F7 between hunks. |
| `terminal` | One xterm per shell (16-colour ANSI theme from tokens, search and web links) in the bottom panel by default. Closing a tab ends its shell. |
| `cli` | A CLI session (Claude Code, Codex and others): a slim header (status, Interrupt, Stop, Resume) over the CLI's own terminal. Closing the tab never stops the session. |
| `browser` | A saved site's built-in browser tab, or the whole browser with its inner tabs when it has no `ref`. |
| `computer`, `artifacts` | The computer-use view and the artifact canvas. |
| `data`, `studio` | The Data workspace and Studio. `?workspace=data`, `?workspace=studio` and `/manage/data`, `/manage/media` open the view and its tab once the layout is restored. |
| `settings`, `projects`, `workflows` | The control-plane pages as tabs: no blocking modal and no "Back to..." overlay. `/manage/<page>` opens its tab. |

## Source control

The Source control view (`features/scm/ScmView.tsx`) has a branch row (ahead and behind in words, Refresh, and a menu with Fetch, Pull (fast-forward only) and Push), the conversation's checkout picker, a multi-line commit message with a 72-column subject hint, and the view's one primary button. It reads **Commit**, disabled with its reason in one sentence; when the checkout is clean and ahead it becomes **Push**. Merge conflicts, Staged changes and Changes are collapsible groups of 32px rows (file icon, name, folder, a status letter with its word for a screen reader). A row opens its diff; Open file, Stage or Unstage, and Discard changes show on hover and keyboard focus (always on touch). Pull request, Branches and worktrees, and Recent operations are collapsed forms. Every write is the host's delivery API, prepared then executed once; Push, pull request writes, removing a worktree and discarding wait for a confirmation (`OperationDialog`) that names every value.

**Discard** (`discard` delivery operation, `development/discard.py`). Row action "Discard changes" in Changes, "Discard staged changes" in Staged, and "Discard all" in Changes. The host prepares a dry run (`review.files[]` with kind modified, deleted, staged or untracked, folders, and paths that are already clean); the dialog lists exactly those files and says the changes cannot be undone, and only its final button is destructive. Unstaged tracked changes are restored with `git restore --worktree`; staged work stays unless the staged side is discarded (`--staged --worktree`); untracked files are removed with `git clean -f` (never `-x`; `-d` only for an untracked folder chosen explicitly and entirely untracked). Every path is resolved inside the checkout (no absolute paths, `..`, `.git`, or symlink escapes), pathspecs are literal, at most 500 paths, merge conflicts are refused, and execution re-checks HEAD and a digest of the reviewed content, so only what was shown can be lost. It needs the same `git-write` scope as the other delivery writes, is audit-logged (`delivery_discard`), and a re-execution returns the stored receipt without replaying. An open file tab's buffer is not reloaded after a discard.

## Sites, CLI sessions and host health

These talk to the host through the REST and GraphQL contracts in [autonomous-agents.md](autonomous-agents.md) and [cli-sessions.md](cli-sessions.md), with the scopes `desktop-view` and `desktop-control` (sites), `terminal-view` and `terminal-control` (CLI) and `machine-view` (host health). `lib/graphql-subscription.ts` is one graphql-transport-ws client with backoff; a slow poll is the fallback. A call the host refuses with 403 leaves its surface quiet.

- **Sites** (`shell/views/SitesView.tsx`). "Add site" is the one primary button. Sections: Pinned (the owner's order), Saved, Suggested (visited three or more times, each with Pin) and Open tabs. Rows are 36px; reorder a pinned row by dragging, Alt+ArrowUp and Alt+ArrowDown, or Move up and Move down. Notes open inline ("You" and "Agent (untrusted)", plain text, 2000 characters). Removing a site asks first. Opening a site shows its built-in browser tab; "Add current page to sites" is a command.
- **CLI sessions** (`features/cli/*`). The Agents view lists sessions that need a person first with a status badge (an icon and words, never colour alone). "New CLI session" is a ghost button that opens a dialog (CLI, project, first instruction, project folder or a separate worktree, permissions Default or Read only; bypass is never offered). Nothing is ever approved for the user: the tab's hint offers Focus terminal.
- **Task board** (`features/TaskBoard.tsx`). Each node shows its executor ("Agent", "Engine: codex", "CLI: claude"), a Blocked status with its reason in words, and in the row menu Open CLI session, Resume session, Retry and Cancel. Blocked nodes count toward "N need you".
- **Host health** (`components/shell/HostStatusPopover.tsx`). Read only: uptime, service state, who owns the terminals (a downgrade always shows its reason), recovering tasks, lost or resumable CLI sessions, keep-awake state and the heartbeat age. A host silent for 30 seconds shows a danger dot and "Disconnected".

## Settings

Settings is a tab (`features/SettingsSurface.tsx`, presented as a page; the classic layout shows the same forms in a dialog). Categories: General, Characters, Models & engines, Voice, Cloud accounts, Agents, Bots, Plugins & skills, Tools & connections, Memory, Hooks, Automations, Runners, Access & sessions, Safety & auto review. Under 768px the category list becomes a Category select. Search matches descriptions and keywords ("anthropic", "web search", "keep awake").

- **General** has Account, Appearance (Theme: System, Light, Dark) and Workspace layout: "Use the classic workspace layout (deprecated)", off by default, disabled while the address sets `?shell=`.
- **Anthropic (Claude) accounts** (`AnthropicAccountForm.tsx`). The API key is write-only ("Key saved", "Replace key"); the seeded models are offered until Refresh models lists what the key can use; the API address is under Advanced. Anthropic accounts appear in every picker that lists provider accounts except dedicated runners, the browser reviewer, live voice, the media studio and Studio, which only speak the OpenAI wire (`lib/provider-kinds.ts`).
- **Web search** (Tools & connections). Shows whether the host offers the tool and where the key comes from; saves or removes it through `set_web_search_key` and `clear_web_search_key` (never returns the key).
- **Always-on host** (Access & sessions). Read-only facts from `host_status` and a form for `update_host_settings` (Keep this computer awake; Terminals when the app or service stops). A person without `host-admin` sees the reason and no controls.

## Commands and keys

One registry (`src/shell/commands.ts`) feeds the command palette, the key settings, the shortcut dispatcher, the title bar and the native menus. `Mod` is Command on macOS and Control elsewhere. Bindings can be changed in Settings > General; the palette and the menus keep every action when a browser reserves a key. A command has an `engine` (exists only in one layout) or `keysIn` (its default key is live only in one layout), so the two layouts never shadow each other.

| Action | Key |
| --- | --- |
| Command palette / Quick open file | `Mod+Shift+P` / `Mod+P` (in the command center a leading `>` lists commands only) |
| Toggle side bar / bottom panel | `Mod+B` / `Mod+J` |
| Explorer / Search / Source control | `Mod+Shift+E` / `Mod+Shift+F` / `Ctrl+Shift+G` |
| Settings | `Mod+,` |
| Split group right / down | `Mod+\` / `Mod+K Mod+\` |
| Focus group 1 to 3 | `Mod+1` to `Mod+3` |
| Maximize group, restore | `Mod+K Z`, Escape |
| Next / previous tab | `Ctrl+Tab` / `Ctrl+Shift+Tab` |
| Close tab / reopen closed tab | `Mod+W` / `Mod+Shift+T` |
| Save file | `Mod+S` |
| Focus terminal / new terminal | `Ctrl+backtick` / `Ctrl+Shift+backtick` |
| Next / previous change (diff tab) | `F7` / `Shift+F7` |
| New chat | `Mod+Shift+N` |
| Hold to dictate, tap to start and stop | `Mod+Shift+M` |
| Toggle window full screen | `Ctrl+Meta+F` |
| New / close / reopen browser page tab | `Mod+Alt+T` / `Mod+Alt+W` / `Mod+Alt+Shift+T` |
| Undo layout / reset layout / saved layouts | `Mod+Alt+Z` / `Mod+Alt+R` / `Mod+Alt+L` |
| Find in a file / in a terminal | `Mod+F` (Control+Shift+F off macOS in a terminal; plain Control+F stays shell input) |

Palette-only commands: Show Agents, Show Sites, Show Data, Show Studio, New chat with options, Projects overview, Activity, Commit staged changes, Stage all changes, Refresh source control, Go to next and previous change, New CLI session, Focus CLI session, Add current page to sites, Show host status, Detach workspace, Return to main workspace (detached windows), Recover unsaved windows (browser).

A focused terminal keeps every key except the commands that opt in (the palette, the panel toggles, tab switching, Focus terminal, New terminal); inside a terminal `Mod` means the platform command key only, so Ctrl+B, Ctrl+J and Ctrl+P still reach the shell on a Mac. Browser and Computer remote input keeps every key. The grammar (`lib/keybindings.ts`) accepts modifiers, then a letter, digit, `` ` ``, `,`, `\`, Tab, Escape or F1 to F24; chord steps are space separated.

## Native menus

`desktop/src-tauri/src/menu.rs` builds the menus; the Rust side never runs an action. A workspace item has the id `cmd:<registry id>` and emits `termx-menu-command` with `{"id": "<registry id>"}` to the focused workspace window (falling back to `main`, shown first when it was hidden in the tray). `shell/use-shell-commands.ts` receives it (`lib/native-menu.ts`) and runs it through the same path as the palette, refusing it while the workspace is locked or for an id the menu does not list. In a browser there is no native menu and listening is a no-op. Accelerators are the registry's default keys converted once, in `menu_commands.rs::accelerator` (`Mod` becomes `CmdOrCtrl`, `Meta` becomes `Super`, punctuation is spelled out as `Backquote`, `Comma`, `Backslash`; two-step chords have no accelerator). The keys and ids live in `src/shell/native-menu.json`, generated from the registry (`UPDATE_NATIVE_MENU=1 pnpm test native-menu`); Rust tests and `native-menu.test.ts` pin the JSON, the registry and the items `menu.rs` builds so they cannot drift. The menu is built after the path state exists (`main.rs`), which keeps the macOS startup-order fix.

| Menu | Item | Registry id | Accelerator |
| --- | --- | --- | --- |
| TermX | About, Quit | native | |
| TermX | Settings... | `settings` | `CmdOrCtrl+,` |
| TermX | Lock | `lock` | |
| File | New Chat | `newConversation` | `CmdOrCtrl+Shift+N` |
| File | Open Project... | `open-project` | |
| File | Save | `saveFile` | `CmdOrCtrl+S` |
| File | Close Tab | `closeTab` | `CmdOrCtrl+W` |
| Edit | Undo, Redo, Cut, Copy, Paste, Select All | native | |
| View | Command Palette... | `palette` | `CmdOrCtrl+Shift+P` |
| View | Side Bar / Panel | `left` / `bottom` | `CmdOrCtrl+B` / `CmdOrCtrl+J` |
| View | Explorer / Search / Source Control | `explorer` / `search` / `sourceControl` | `CmdOrCtrl+Shift+E` / `CmdOrCtrl+Shift+F` / `Ctrl+Shift+G` |
| View | Agents | `view-agents` | |
| View | Full Screen | `fullscreen` | `Ctrl+Super+F` |
| Go | Go to File... | `quickFile` | `CmdOrCtrl+P` |
| Go | Next Tab / Previous Tab | `nextTab` / `previousTab` | `Ctrl+Tab` / `Ctrl+Shift+Tab` |
| Go | Focus Group, First / Second / Third | `focusGroup1` to `focusGroup3` | `CmdOrCtrl+1` to `CmdOrCtrl+3` |
| Terminal | New Terminal / Toggle Terminal | `newTerminal` / `focusTerminal` | `Ctrl+Shift+Backquote` / `Ctrl+Backquote` |
| Window | Minimize, Zoom | native | |
| Window | Detach Workspace / Return to Main Workspace | `detach-workspace` / `return-to-main` | |
| Help | Open in Browser, Open Logs Folder | native | |

Limits: the menu is static, so Focus Group, New Terminal and Agents do nothing under `?shell=classic`; a native accelerator is consumed by the OS menu before the page, so a Ctrl-based key is swallowed even while a terminal has focus; accelerators show the defaults, not a user's rebound keys. The native window titles read "TermX".

## Flags and deprecation

- **Default: the IDE layout.** `?shell=ide` forces it.
- **Classic layout (deprecated).** `?shell=classic` (or `legacy`) in the address, or `localStorage['termx-new-workspace-layout'] = 'off'` (Settings > General > "Use the classic workspace layout (deprecated)"), selects the dock of seven panels. The address wins over the stored choice and disables the toggle. The toggle takes effect at once, without a reload. A stored `on` (the earlier opt-in) still means the IDE layout. `lib/shell-engine.ts` reads and writes it.
- **What is deprecated.** `lib/docking.ts`, `lib/layout-presets.ts`, `components/DockWorkspace.tsx`, `components/WorkspaceModeSwitch.tsx`, `shell/use-legacy-layout.ts`, `shell/WorkspaceHeader.tsx`, `shell/Rail.tsx`, the old Changes panel and Delivery form (`features/ChangeReview.tsx`, `features/Delivery.tsx`), and `styles/legacy-aliases.css`. Each source file carries an `@deprecated` header. **Removal is scheduled for the release after this one**, together with the v1 migration that reads the classic layout's types.
- **Rollback.** Switching to the classic layout finds its own presets under `termx-layout-presets-v1:`; the IDE layout never writes that key.
- **Tests and proofs for the classic layout.** Unit tests pin it with `test-utils/classic-shell.ts`; opt-in e2e proofs written for it call `snapshot_ui(..., classic=True)` (`tests/workspace_ui_snapshot.py`).

## Visual baseline

- **Tokens** (`src/styles/tokens.css`). The DESIGN.md `:root` and `.dark` blocks, scale tokens (`--space-*`, `--text-xs` to `--text-3xl` with `--line-*`, `--control`, `--control-compact`, `--control-touch`, `--row`) and a fenced IDE block. Light is the default; dark applies for `.dark` or `[data-theme=dark]`, and for the system preference until the theme hook has resolved one. `lib/theme.ts` sets both; a new profile defaults to System.
- **Type.** Inter and JetBrains Mono, bundled through fontsource variable packages (no CDN). Mono is for code only.
- **Layers.** `@layer legacy, primitives, shell, surfaces;` is declared once. The older component CSS is in `legacy`; new shared controls go in `primitives`, the IDE frame in `shell`, content surfaces in `surfaces`.
- **Design guard** (`src/design-guard.test.ts`, `src/design-budget.json`). Counts per file of hex colours outside `tokens.css`, `!important`, off-scale font sizes, radii and spacing, bare `outline: none`, gradients, `backdrop-filter`, weights above 600 and uppercase text. Counts may fall, never rise, and a new file starts at zero. Tighten with `UPDATE_DESIGN_BUDGET=1 pnpm --dir desktop/workspace test design-guard`.
- **Contrast.** `src/styles/tokens.test.ts` resolves both themes and fails if any text, syntax or ANSI colour falls below 4.5:1 on `--bg` or `--surface-2`.

### IDE extensions to DESIGN.md (owner-approved)

Desktop pointer only. Touch keeps 44px controls.

| Item | Value |
| --- | --- |
| Tab strip and panel header | 36px |
| Tree, source control and search rows | 32px |
| Status bar | 24px |
| Tab close and row actions | 24 x 24 |
| Dense inputs | 32px, 14px text |
| Code type | Editor and diff 14/20, terminal 12/16 |
| Side bar | 260px default, 200 to 420px resizable |
| Syntax hues | Strings use the success value; constants `#7A5A12` / `#D9B36C`; entities `#2F6B66` / `#86BDB4` |
| Terminal | 16-colour ANSI palette derived from tokens, with muted blue (`#2F5F85` / `#8FB1CC`) and magenta (`#8A4470` / `#C9A0B8`) |
| Diff | 12% mixes of the success and danger tokens |

The phone breakpoint is 768px and the reading column is 720px.

### Remaining design debt

The guard's budget records what the older component CSS still breaks (see `design-budget.json` for the per-file counts): `!important`, off-scale font sizes, radii and spacing, bare `outline: none`, hex colours, a few uppercase labels and weights above 600. None of it is new, none of it is in the IDE frame, and the budget only ratchets down. The retired variable names in `styles/legacy-aliases.css` are still referenced by that CSS.

## Behaviour that carries over

These behaviours predate the IDE shell and are unchanged; where an older name appears, read it as the capability, which now lives in the Agents view, a tab or the command palette.

**New chats, titles and checkouts.** **New chat** creates an empty conversation directly and focuses its composer; it asks for no title and dispatches no task. The first accepted message supplies a deterministic title; manual renames stay manual. New chat with options (palette) keeps project, agent preset and folder selection. New local conversations in registered Git projects use an automatic checkout: before the first Plan or Agent turn the host prepares a separate enrolled worktree from committed `HEAD` (uncommitted and untracked files stay in the original folder). Ask stays read-only. Scratch folders, non-Git folders, browser workflows and remote runners report their real context. A **Workspace checkout details** disclosure above the composer shows the working folder, branch, source folder and base revision; a missing worktree stops the send with an actionable error and never falls back to the original folder. Auto needs the built-in local Agent and a ready Git worktree.

**Sub-agents and task supervision.** The main agent uses its coordinator's child records and plan board; ordinary conversations use the selected conversation's recent execution roots (up to ten roots, up to 100 displayed descendants, through an authorized bounded reader). Rows are grouped Active and Done, ages come from reported timestamps, groups show ten rows with Show more, and selecting a row opens the task-supervision dialog. Approvals, recovery confirmation and child ownership stay available inline. Inspecting a child never approves work, changes control ownership or starts a retry.

**Voice and typed content.** Dictation stages text in the draft for review; Send is separate. Responses expose Read aloud and Stop reading. Live voice is a separate session. Responses keep prose, lists and code and give real file, link and media metadata typed presentations; tool activity and raw details expand on demand. Known, bounded media loads a verified inline preview; unknown-size assets need Load preview. Active HTML and SVG stay download-only. File references (`src/App.tsx:25`, local Markdown links) open the editor at the line, resolved inside the conversation's checkout.

**Motion.** 120ms for feedback and exits and 160ms for entry and layout changes (the `--duration-*` tokens), ease-out, opacity and transform only. Closing sidebars, dialogs and disclosures become inert immediately. `prefers-reduced-motion` removes animation (the streaming caret stops) without weakening `hidden`, `inert` or focus boundaries.

**Agent details and effort.** A child agent's header keeps its ownership status and Take control compact; Engine details opens in a scrollable dialog. Linked engines with reasoning controls open **Model and effort**, a token-styled rail (a filled track up to a round thumb in the one accent; arrow keys, Home and End adjust only advertised choices); the model name opens a separate model list. Workspace, account, session and lock changes close the picker.

**Projects, Activity and Workflows.** The Projects tab has Projects, Activity, Workflows and Overview navigation. The folder catalog separates generated workspaces behind a toggle and keeps the active context. **Add project** registers an existing folder and offers **Open project** for one that exists. Workflows (Library and Runs) are saved JavaScript workflows run through the same `WorkspaceService.send` path as chat, with typed inputs, Validate, Save, Run, a durable run view and Stop; scripts have no filesystem, network or import access; definitions are owner scoped with optimistic concurrency and idempotent run starts; interrupted runs need review and never replay. See [implementation-status.md](implementation-status.md#workflows).

**Execution and path selection.** Coding tasks continue until completion, cancellation, provider failure or revoked authority; normal permissions, sandboxing and approvals stay enforced. Provider account **Limits** (response output tokens, request timeout) are unset by default. **Add project** and **New chat** use a filesystem folder picker (breadcrumbs, parent and home navigation, filtering, hidden folders, pagination); host browsing needs the same host-wide authority as project registration. See [implementation-status.md](implementation-status.md#execution-and-path-selection).

## Evidence and tests

- **Unit and component tests** (`pnpm --dir desktop/workspace test`, colocated `*.test.ts(x)`), including `shell/tree-shell.test.tsx` (the flag, deep links, v1 migration, phone), `shell/commands.test.ts`, `shell/native-menu.test.ts`, `features/scm/*` and the design guard.
- **Real-host browser proofs** (`tests/`, Playwright against a built `dist`): `workspace_ide_shell_e2e.py` (chrome, views, tabs, splits, terminal, reload, phone), `workspace_scm_e2e.py` (source control, diff tabs, discard), `workspace_agent_surfaces_e2e.py` (Sites, CLI sessions, board, host health), `workspace_settings_providers_e2e.py`, and `workspace_ide_design_e2e.py` (empty states, one primary action per view, composer, keyboard paths, collapsed activity, the streaming caret under both motion settings, errors and approvals, capture status, Studio, Data and Projects as tabs, narrow and short windows, axe). CI runs `workspace_accessibility_e2e.py`, `workspace_settings_tls_e2e.py`, `workspace_chat_recovery_e2e.py` and `updater_ui_e2e.py` against the default layout.
- **Screenshots** are stored under `docs/evidence/ide-shell/<slice>/` at 375 x 812 and 1280 x 900 in light and dark, reviewed against the DESIGN.md checklist. `u7/` is the final sweep: first run, Agents, editor split, Source control, Sites, a CLI session, Settings, Data, Studio and the command center.
- **Historical verification records** for the chat-first shell are in `docs/verification/` and [implementation-status.md](implementation-status.md#verification); they describe the retired shell and are not evidence for this one.
