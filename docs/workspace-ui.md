# Workspace UI: agent-first IDE shell

TermX is moving from a ChatGPT-style chat frame to an **agent-first IDE shell**: a multi-agent control surface and a coding environment in one window. This page is the UI direction and the contract for the redesign. Sections marked **Superseded** describe the chat-first shell that still ships until each slice below lands; they are kept so existing behaviour can be traced, not as a target.

## Status

| Slice | Scope | State |
| --- | --- | --- |
| **U0 Foundations** | One token file, Inter and JetBrains Mono, CSS layers, design guard, 720px chat column | Landed in this branch |
| U1 Editor and terminal polish | Editor and ANSI themes, languages, find, fold, terminal tabs | Planned |
| **U2 Decompose `App.tsx` and commands** | `shell/` modules, one command registry feeding the palette, the key settings and the shortcut dispatcher | Landed in this branch (behaviour-neutral except the registry additions below) |

| **U1 Editor and terminal polish** | Editor and ANSI themes, languages, find, fold, terminal tabs | Landed in this branch, on the current shell |
| U2 Decompose `App.tsx` and commands | `shell/routes.ts`, one command registry feeding palette, keys and menus | Planned |
| **U3 Layout engine** | Recursive split tree, tab groups, v1 layout migration, detach mapping | Landed in this branch, behind `?shell=ide` and a Settings toggle (see below) |
| **U4 Chrome, Explorer, file and terminal tabs** | Activity bar, side bar views, title and status bars, mobile projection; Explorer, one tab per file, one tab per shell, bottom panel tabs | Landed in this branch, in the new layout only (see below) |
| U5 Source control | Merged source control view and diff tab | Planned |
| U6 Agent surfaces | Sites view, CLI session tabs and badges, needs-you approvals, health in the status bar | U6b (Settings) landed in this branch; the rest planned |

| **U6a Agent surfaces** | Sites view and browser tabs, CLI sessions (list, tab, new-session dialog), executors and Blocked on the task board, host health popover, needs-you counts | Landed in this branch, in the new layout only (see below) |
| U6b Settings pages | The host settings forms (`update_host_settings`) | Planned |
| U7 Completion | Studio and Data as views, native menus, flip the flag, delete the old dock and aliases, guard budgets to zero | Planned |

The IDE shell is built behind `?shell=ide` and a Settings toggle until U7. The running app is still the chat-first shell, restyled by U0 only through tokens, fonts and one column width. No layout, dock, editor or terminal behaviour changed in U0.

### U1: editor and terminal (current shell)

- **Editor** (`src/features/Workbench.tsx`, `src/features/editor/`). `editor-theme.ts` is a CodeMirror theme made only of CSS variables (`--bg`, `--text`, `--surface-2`, `--selection`, `--syntax-*`, the diff tints), so light, dark and system switch without reconfiguring or re-creating the editor; the theme test fails on a literal colour. The grammar comes from the file extension through the lazily loaded `@codemirror/language-data` (`language.ts`); an unknown extension is plain text. The editor has line numbers, active-line highlight, a fold gutter, bracket matching, find and replace (`Mod+F`; `Mod+Alt+F` focuses replace) and breadcrumbs under the tab strip. Code is 14/20 mono; the caret stops blinking under `prefers-reduced-motion`.
- **Terminal** (`src/features/TerminalPanel.tsx`, `src/features/terminal/`). `terminal-theme.ts` reads the 16 `--ansi-*` colours, `--bg`, `--text`, `--accent` and `--selection` from computed variables and re-applies them when `data-theme`, the `dark` class or the system colour scheme changes. Sessions are Radix tabs (36px, arrow keys, Home, End) with one xterm per PTY, each kept mounted and connected while its tab is hidden. `+` opens a terminal, the tab mark or the toolbar's **Close terminal** ends the shell (`delete_session`). `Mod+F` in the terminal opens find (Control+Shift+F off macOS; plain Control+F stays shell input); web links open on Command-click or Control-click. The PTY websocket protocol is unchanged.

### U3: layout engine (preview)

The tab-group layout is a preview: it is used when the address has `?shell=ide`, or when **Settings > General > Use the new workspace layout (preview)** is on (stored in `localStorage` under `termx-new-workspace-layout`; `?shell=classic` forces the old dock). The old dock stays the default, keeps its own storage, and is removed in U7. The toggle takes effect at once, without a reload.

- **Model** (`src/lib/layout/model.ts`). `Layout {version: 2, root, panel, sideBar, focus, maximized}`. `root` is a split tree: a `Split {id, dir, sizes, children}` or a `Group {id, tabs, active}`. A `Tab` is `{id, kind, ref?, pinned?}` with 13 kinds: `chat`, `file`, `diff`, `terminal`, `cli`, `browser`, `computer`, `artifacts`, `data`, `studio`, `settings`, `projects`, `workflows`. `panel` is the bottom panel (its own group, a height in px, visible or not); `sideBar` has a view, a width and visibility. `validateLayout` rejects more than 6 levels, 12 groups, 64 tabs or 256 KB; it drops tabs of unknown kind or with a `ref` that no longer resolves, normalises split sizes to sum to 1 (no pane under 5%), collapses empty groups and one-child splits, and never leaves an empty root (it falls back to the Chat tab). A book holds the current layout and up to 30 named layouts.
- **Reducer** (`reduce.ts`). Pure. Open (reveals an existing tab of the kind instead of adding a second), close with a reopen stack of 20, move, split, resize, maximize and restore, set active, cycle, focus, side bar and panel toggles and sizes, saved layouts, reset, and an undo stack of 20 (a drag of one splitter is one step). Empty groups collapse on their own, except the focused one, so a split of a lone tab leaves an empty group you can fill.
- **Singletons.** Every kind is a singleton in this release, because each panel the layout wraps is one component with its own inner tabs (terminal sessions, editor files, browser tabs). The `ref` retargets the one tab: the Chat tab follows the selected conversation, Settings shows the category in its `ref`, Projects one of Overview, Projects and Activity, Workflows a run. The reducer supports multi-instance kinds (dedupe by kind and `ref`, `forceNew`); add a kind to `MULTI_INSTANCE_KINDS` when its host renders one instance per `ref`.
- **Rendering** (`components/shell/TabGroups.tsx`, `ContentLayer.tsx`). `layoutRects(layout, width, height)` gives every group, splitter and drop zone its rectangle. Tab content is rendered in one flat layer, keyed by tab id and kept in first-shown order, and only its rectangle changes when a tab moves, so a terminal, an editor or a browser stream stays mounted. Inactive tabs are `hidden` and `inert`. A tab strip is a Radix tablist (arrows, Home, End, automatic activation) with a `+` menu and a Tab actions menu; the same menu is on right click. Splitters are 1px lines with an 8px hit area, `role="separator"`, and resize with the arrow keys (Shift for larger steps), Home, End, and Enter to reset. A tab is dragged with the pointer to one of five drop zones (centre, or the outer quarter of a side to split that side); touch and keyboard use the menu (Split right, Split down, Move to other group, Maximize group, Move to new window, Reveal).
- **Phone.** Under 768px the tree is projected, at render time only, into one tab stack with the split commands hidden. Switching tabs there never writes to the stored layout.
- **Storage and migration** (`store.ts`, `migrate-v1.ts`). `termx-layout-v2:<json [owner, device]>`, the same owner and device keying as `termx-layout-presets-v1:`. The first read with no v2 book migrates the v1 presets (regions become a row of groups, pixel widths become fractions of a 1280px reference window, `left` becomes the side bar, `right` keeps or drops the right group, `bottom` becomes the panel) and adds the eight built-in presets as named layouts (Chat, Code + chat, Browser + chat, Computer + chat, Artifacts + chat, Review + chat, Terminal + chat, Focus; Focus is the editor maximized beside the chat). The v1 key is never written. A detached window shows one tab and never writes the layout.
- **Settings, Projects and Workflows as tabs.** They render the same components without dialog chrome. Closing a Settings tab with unsaved edits asks first, through the same discard dialog (`SETTINGS_LEAVE`); so do Close others, Close group, Saved layouts and Reset layout when they would drop it.
- **Detach.** "Move to new window" calls the existing detach flow. `shell/window-panel.ts` maps a kind to one of the seven `valid_panel` strings (`file` to `workbench`, `diff` to `review`, `cli` to `terminal`); Settings, Projects, Workflows, Data and Studio cannot move. The redock payload is unchanged.
- **Keys** (new layout only unless noted). `Mod+\` split right, `Mod+K Mod+\` split down, `Mod+1` to `Mod+3` focus a group, `Mod+K Z` maximize group (Escape restores), `Ctrl+Shift+backtick` new terminal; `Mod+W` closes the active tab, `Shift+Mod+T` reopens it, `Ctrl+Tab` and `Ctrl+Shift+Tab` cycle tabs, `Mod+B` side bar and `Mod+J` bottom panel work in both layouts. In the old dock `Mod+1` to `Mod+3` still open Chat, Workbench and Browser; commands carry the engine they belong to, so the two sets never shadow each other.
- **Not in the preview yet.** Source control is a read-only list of changed files until U5. The agent surfaces (Sites, CLI sessions, host health) are described under U6a.

### U4: chrome, Explorer, file tabs and terminal tabs (preview)

Everything in this section is the new layout only (`?shell=ide` or the Settings toggle). The old dock, its header, rail, Workbench and Terminal panel are unchanged and keep their tests.

- **Frame** (`shell/IdeFrame.tsx`, `components/shell/*`). Title bar 40px, activity bar 48px, side bar 260px (resizable 200 to 420 by pointer or keyboard, Home, End and Enter reset), the tab groups, the bottom panel group and a 24px status bar. Chrome is separated by 1px borders: no shadow, gradient or blur. The window has no custom drag region (neither had the old header), so the native title bar is the one that moves it.
- **Title bar.** Side bar toggle, `project > branch`, the command center ("Search files and commands", with the Quick open key), the bottom panel toggle, an "N need you" badge that appears only when something needs the owner (bots waiting plus sub-agents needing attention), and the account menu (Lock workspace, Settings, Sign out).
- **Activity bar.** One vertical toolbar with a roving tab stop. Top: Agents (the default), Explorer, Search, Source control, Sites, Data, Studio. Bottom: Projects and Settings (tabs), Account. The selected view has a `--surface-2` fill and an accent bar; choosing it again hides the side bar. Agents carries a dot, and "N need you" in its name, when something needs the owner.
- **Side bar views** (`shell/views/*`, `features/explorer`, `features/search`). Agents: New chat (the view's one primary button), Needs you, Main agent with its state, Sub-agents (the orchestrator draws into the same target the old aside used), CLI sessions (see U6a) and the chat list at 36px rows. Explorer: project and checkout, an ARIA tree of 32px rows with a source control letter on changed files; Enter opens a file tab. Search: workspace text search through the host's file search; a result opens its file at the line. Source control: the changed files from the Changes source, read only. Sites: see U6a. Data and Studio: the same navigation as before (the Data workspace draws it into the side bar; choosing either also opens its singleton tab; they are no longer workspace modes in this layout, and `?workspace=data` or `studio` opens the view and the tab once the layout is restored).
- **Status bar.** Host health dot and name, branch with `*` when the checkout is dirty, problems (hidden at 0), capture status, "N need you", Main agent state, Ln and Col with the language for the focused file tab, Lock. An item is a button only when it does something.
- **Command center** (`shell/dialogs/QuickOpenDialog.tsx`). Plain text finds files in the conversation's checkout (the same quick-open call as before) and a few matching commands; a leading `>` (or Shift+Mod+P) lists commands only. Mod+P opens it; a result opens a file tab.
- **File tabs** (`features/editor/FileTab.tsx`, `buffer-store.tsx`). One CodeMirror editor per tab with the U1 theme, breadcrumbs, a dirty dot, Mod+S, and the old Editor actions menu (definition, documentation, completions, format). The buffers live in one store above the tabs, so a tab that moves, splits or hides keeps its edits; the store publishes and persists through `lib/window-state.ts`, so the detach and redock payload is unchanged (`{version: 1, sessionId, buffers, draft}`), and a buffer with unsaved edits always has a tab, even after a restore or a handoff. Closing a tab with unsaved edits asks first ("Discard unsaved changes?"); so do Close others and Close group. There is no split-editor `select`: splitting a file is a normal tab split. A restored file tab reads its file again when it belongs to the conversation's checkout, and says so when it does not.
- **Terminal tabs** (`features/terminal/TerminalTab.tsx`). One xterm per shell, in the bottom panel by default. `+` in the panel, the New terminal command and Control+Shift+backtick start a shell in the conversation's checkout and open its tab. The tab's own toolbar has find, Interrupt and Close terminal. Closing a tab ends its shell; a tab whose shell ended says so and offers Close tab. The PTY protocol is unchanged.
- **Bottom panel.** The panel group offers Terminal, Problems (the language servers' diagnostics for open files), Preview (the dev-server preview) and Run and debug. Output has no data source today, so it is not offered.
- **Phone (under 768px).** A 48px top bar (hamburger, the active tab's title, a tab switcher) over one tab stack, a bottom navigation of Agents, Files, Git, Term and More with 44px targets, the side bar as a Radix dialog drawer, and a status bar reduced to the health dot and the "N need you" badge. The projection is made at render time; switching tabs never writes the stored layout.
- **Retired in this layout.** The Workspace actions menu (every item has a command or a place, see the table in the U4 tests: `shell/workspace-actions-parity.test.tsx`), sidebar hover mode, Focus mode and the floating chat. The floating companion habitat is off; Settings > Characters has a per-device switch that shows one still character beside the Main agent row.
- **Commands.** New: Show Agents, Show Sites, Show Data, Show Studio, New chat with options, Projects overview, Activity, Detach workspace, Return to main workspace (detached windows only), Recover unsaved windows (browser only). Explorer, Search and Source control (Shift+Mod+E, Shift+Mod+F, Control+Shift+G) show their side bar view.

### U6b: Settings for the new backend capabilities (both layouts)

These pages are shared by the classic dialog and the new layout's Settings tab, so they changed in both. Evidence: `docs/evidence/ide-shell/u6b/`; real-host proof: `tests/workspace_settings_providers_e2e.py`.

- **Anthropic (Claude) accounts** (Models & engines; `AnthropicAccountForm.tsx`, routed from `ProviderAccountForm.tsx`). Adding offers **Provider > Anthropic (Claude)**, and **Edit account** is now available for the `anthropic` kind. The API key is write-only: a saved key shows **Key saved** with **Replace key** (and **Keep saved key** while replacing) and is never rendered. The four seeded models (`claude-opus-5-5` first) are offered until **Refresh models** lists what the key can use; **Default model** puts one first. The request format (`messages`) is fixed, so the selector is hidden; the API address (default `https://api.anthropic.com`) is under Advanced and a pasted `/v1` is normalised by the host. Only Shell tools and Function tools can be declared. The Models API reports no prices, so a model with no value of your own reads **Cost unpriced** (here and in Model routing); Override values still takes a price. Capability hints ("Thinking", "Image input") appear only when the discovery payload names them. The existing **Test connection** confirmation says Anthropic's test only lists models. Validation errors are inline; the form uses `useDirtyForm` like its siblings.
- **Where Anthropic accounts appear.** In every picker that lists provider accounts by `secret_configured`: the chat composer's provider and model menus, Model routing, the task board's reassign menu, and the main-agent defaults. They stay out of dedicated runners, the browser reviewer (`SafetyManager`), live voice, the media studio and Studio, because the host only speaks the OpenAI wire there (`runners/agent.py`, `browser/router.py`, `voice/service.py`, `media/*`); the kind lists are in `lib/provider-kinds.ts`.
- **Web search** (Tools & connections > Web search; `WebSearchSettings.tsx`). Shows whether the host offers the tool, the provider (Brave Search) and where the key comes from, and saves or removes the key through GraphQL `set_web_search_key` and `clear_web_search_key` (`ai-settings`, never returns the key). A key set with `TERMX_AI_WEB_SEARCH_API_KEY` is reported and cannot be changed here. One line says agents ask before each new website. There is no "recently allowed hosts" list: the host has no cheap read for remembered `net.outbound:<host>` approvals.
- **Always-on host** (Access & sessions > Always-on host; `HostAlwaysOnSettings.tsx`). Read-only facts from `host_status` (service state, terminal mode and the host's reason for a downgrade, the helper process and its terminals, keep awake, the health file) and a form for `update_host_settings`: **Keep this computer awake** and **Terminals when the app or service stops** (Automatic, Keep running, End with the host). Only changed fields are sent. A person without `host-admin` sees the reason and no controls. `holder.setting` and `holder.env_override` tell the page when `TERMX_PTY_HOLDER` overrides the saved choice.
- **Settings search.** The category search matches descriptions and `keywords` (`lib/settings-categories.ts`), so "anthropic", "web search", "keep awake" and "heartbeat" find their pages.
- **Memory.** No change: ranking is behind `memory_read`, and the page keeps its "No matches" copy. A usage history view is not offered because `usage_query` is an agent tool with no REST or GraphQL read.

### U6a: agent surfaces (preview)

New layout only (`?shell=ide` or the Settings toggle). The classic shell is unchanged. Everything here talks to the host through the REST and GraphQL contracts in [autonomous-agents.md](autonomous-agents.md) (saved sites, host health), [cli-sessions.md](cli-sessions.md) and [main-agent.md](main-agent.md) (task-graph executors); the scopes are the existing ones (`desktop-view` and `desktop-control` for sites, `terminal-view` and `terminal-control` for CLI sessions, `machine-view` for host health).

- **Live data.** `lib/graphql-subscription.ts` is one graphql-transport-ws client with backoff. `browser_site_events`, `cli_session_events` and `host_status_events` keep their lists current while a subscription is live; a slow REST or query poll stays as the fallback (the native shell has no socket) and carries what the streams do not (suggestions, open tabs). A subscription or call the host refuses with 403 leaves its surface quiet: the Sites view says saved sites are unavailable, the CLI section is not drawn, and the status bar keeps its plain health text.
- **Sites view** (`shell/views/SitesView.tsx`, `features/sites/*`). "Add site" is the one primary button (address, optional title, a browser profile only when there is more than one; a host with no profile gets a "Default" one). Sections: Pinned (the owner's order), Saved (not pinned), Suggested (what was visited three or more times, each with Pin) and Open tabs. Rows are 36px with a Lucide Globe, the title and the origin in `--text-3`; the notes toggle and an actions menu (Open, Edit title and open mode, Move up, Move down, Pin or Unpin, Remove site) appear on hover and focus for a mouse and always on touch. Reorder by dragging a pinned row, with Alt+ArrowUp and Alt+ArrowDown on the row, or with Move up and Move down; the host stores absolute positions, so every row whose position changed is written, and focus returns to the row that moved. Notes open inline: author labels "You" and "Agent (untrusted)", plain text only, 2000 characters, delete per note. Removing a site asks first.
- **Browser tabs** (`browser` is now a multi-instance kind). Opening a site calls `POST /sites/{id}/open` and shows its built-in tab in the editor area through the existing `BrowserSurface` in single-tab mode (`tabId`): no tab strip of its own, a "Close page" button, "Add current page to sites" (a ghost icon button that posts the page with the tab's profile) and a closed-page state. The tab is named after its page. A `browser` tab with no `ref` is still the whole browser with its inner tabs. `Add current page to Sites` is also a command. Moving a browser or CLI tab between groups keeps it mounted, so the live view does not reconnect.
- **CLI sessions** (`features/cli/*`, the Agents view). One `CliSessionsProvider` holds the list for the window. The CLI sessions section lists sessions that need a person first (needs approval, then waiting for input, then the rest, ended last) with a status badge: an icon and words, never colour alone (working spins, waiting is quiet, needs approval is a shield in `--accent`, done is `--success`, failed and lost are `--danger`). "New CLI session" is a ghost button that opens a dialog: CLI (not-installed CLIs are disabled with the reason), project, optional first instruction, where it works (project folder or a separate worktree) and permissions (Default or Read only; bypass is never offered). A `cli` tab (multi-instance by session id) has a slim header (status, detail, Interrupt, Stop, Resume) over the CLI's own terminal, which is the same `TerminalSession` and `/api/sessions/{pty_session_id}/pty` socket every shell uses, so typing in it is how input reaches the CLI. Needs approval shows a one-line hint with Focus terminal and nothing is ever approved for the user. Closing the tab never stops the session; Stop and Resume ask first; an ended session shows how it ended in place of the terminal. A session that needs a person feeds the "N need you" counts (title bar, status bar, the activity bar dot) and the Needs you list.
- **Task board** (`lib/orchestrator-board.ts`, `features/TaskBoard.tsx`). A node reads `executor`, `blocked_reason`, `session_id` and `resumable`. Each row has an executor chip ("Agent", "Engine: codex", "CLI: claude"), a Blocked status with its reason in words, a Blocked note, and in the row menu Open CLI session (when the layout provides it), Resume session for a lost resumable node (the existing `retry_node`), Retry and Cancel. Only internal nodes can be reassigned. Blocked nodes count toward "N need you". In this layout the Sub-agents list is one compact list under the view's own heading (`CoordinationSidebar` `compact`), and it keeps updating while the Chat tab is behind another tab.
- **Host health** (`features/host/use-host-status.ts`, `components/shell/HostStatusPopover.tsx`). The status bar's health item is a button that opens a popover: uptime, service state, who owns the terminals (a downgrade to direct mode always shows its reason, with an icon and words), recovering tasks and lost or resumable CLI sessions (with a link to the CLI sessions list), keep-awake state and its reasons, and the heartbeat age. It is read only; durability settings belong to Settings. The query is polled every 10 seconds even while the subscription is live. A host that has not answered for 30 seconds shows a danger dot and "Disconnected" and keeps retrying; a sign-in without `machine-view` (or without `host-admin` for a managed user) hides the popover quietly.
- **Commands.** New CLI session, Focus CLI session (opens the one that needs a person, else shows the list), Add current page to Sites and Show host status (palette entries, no keys) and Show Sites, all new-layout only. They raise a request (`lib/agent-surface-requests.ts`) that the frame answers, because the registry runs above the state those surfaces own.
- **Phone.** More lists Sites, CLI sessions and Host status; the drawer shows the Sites view and the Agents view with the CLI list; targets are 44px under `pointer: coarse`. The status bar's health dot opens the same popover.
- **Detach.** `cli` maps to the `terminal` window panel and `browser` to `browser` (`shell/window-panel.ts`, pinned to `valid_panel` in `workspace.rs` by a test). A detached window opens the panel's tab with no `ref`, so a CLI or a single page does not follow the move yet.

## Visual baseline

The single visual baseline is the shared DESIGN.md: calm, content-first, warm neutrals, one terracotta accent, type and space carry hierarchy, borders instead of shadows (overlays excepted), no gradients or blur, weights 400 to 600, a 4 to 64 spacing scale, radius 8 and 12. Nothing in this repository overrides it except the fenced IDE extensions below.

- **Tokens** live in `desktop/workspace/src/styles/tokens.css`: the DESIGN.md `:root` and `.dark` blocks, scale tokens (`--space-*`, `--text-xs` to `--text-3xl` with `--line-*`, `--control`, `--control-compact`, `--control-touch`, `--row`), and a fenced IDE block. Light is the default. Dark applies for `.dark` or `[data-theme=dark]` on `<html>`, and for the system preference until the theme hook has resolved one.
- **Theme.** `lib/theme.ts` sets both `data-theme` and the `dark` class. A new profile defaults to **System**; an existing stored Light or Dark choice wins.
- **Type.** Inter and JetBrains Mono, bundled through fontsource variable packages (no CDN). Mono is for code only.
- **Layers.** `@layer legacy, primitives, shell, surfaces;` is declared once in `tokens.css`. All existing component CSS is wrapped in `legacy` and keeps its behaviour. New shared controls go in `primitives`, the IDE frame in `shell`, content surfaces in `surfaces`. `!important` inverts layer order, so remove it only from files that have left `legacy`.
- **Aliases.** `src/styles/legacy-aliases.css` maps the retired names (`--background`, `--foreground`, `--secondary`, `--muted`, `--accent-soft`, `--surface-raised`, `--sidebar`, `--border-subtle`, `--destructive`, `--warning`, `--agent-*`, `--radius-sm`, `--radius-md`, `--motion-*`) to tokens. It is temporary: delete a line when nothing uses the name, and the file in U7.
- **Design guard.** `src/design-guard.test.ts` counts, per file, hex colours outside `tokens.css`, `!important`, off-scale font sizes, radii and spacing, bare `outline: none`, gradients, `backdrop-filter`, weights above 600 and uppercase text. `src/design-budget.json` is the ratchet: counts may fall, never rise, and a new file starts at zero. After removing violations run `UPDATE_DESIGN_BUDGET=1 pnpm --dir desktop/workspace test design-guard` to tighten it.
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

Breakpoint 850 moves to 768, and the reading column is 720px.

## Target layout (1280px)

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
│ Pr │                   │ Terminal │ Problems │ Output      zsh × │ pnpm dev × │ +   │ 36
│ ⚙  │                   │ $ pnpm test                                               │
├────┴───────────────────┴───────────────────────────────────────────────────────────┤
│ ● Connected · this Mac   main*   0 problems        Main agent working   Lock      │ 24
└───────────────────────────────────────────────────────────────────────────────────┘
  48        260                      flex                          380
```

- **Activity bar views.** Agents (default: needs-you inbox, Main agent, Sub-agents, CLI sessions with status badges, Chats), Explorer, Search, Source control, Sites (pinned, suggested, open, per-site notes), Data and Studio. At the bottom: Projects, Settings, Account.
- **Tabs.** `{id, kind, ref}`. Chat, file, diff, terminal, CLI session and browser tabs are multi-instance. Data, Studio, Settings and Projects are singletons. Settings, Projects and Workflows become tabs, with no blocking modal and no "Back to..." overlay. Studio and Data stop being workspace modes; `?workspace=...` deep links still resolve.
- **Layout.** A recursive split tree replaces the preset dock. Content renders in a flat keyed layer so the editor, terminal and browser stream stay mounted as tabs move. Layout storage becomes `termx-layout-v2`; the eight presets migrate to named layouts and the v1 key stays read-only for one release.
- **375px.** One tab stack, a drawer side bar and a bottom navigation (Agents, Files, Git, Term, More), projected at render time. The stored layout is not changed.
- **One primary action per view.** New chat, Send, Commit, Approve, Open site, Add project or Run. Everything else is a ghost button. One command registry feeds the palette, keybindings, title bar and native menus.
- **Chat.** A 720px column, a one-line "N actions" summary, a composer on `--surface`, and a token-styled effort control in place of the gradient slider. The companion habitat is off by default; sidebar hover mode, Focus mode and the floating chat are removed.

### Names

TermX, Chat, Agents, Main agent, Sub-agents, CLI sessions, Explorer, Editor, Search, Source control, Browser, Sites, Terminal, Computer, Artifacts, Data, Studio, Projects, Workflows, Settings. Retire Workbench, Files & code, Review changes, Git changes, Delivery and Workspace actions.

### Keys (VS Code conventions; the tab-group keys are live in the preview)

`Mod+P` and `Mod+Shift+P`, `Mod+B`, `Mod+J`, `Ctrl+backtick` terminal, `Mod+Shift+E`, `Mod+Shift+F` and `Ctrl+Shift+G` for Explorer, Search and Source control, `Mod+,` settings, `Mod+\` split, `Mod+1` to `Mod+3` focus a group, `Ctrl+Tab` next tab, `Mod+W` closes the active tab, `Mod+F` find. Terminal focus stops disabling shell shortcuts; an allowlist replaces the blanket bail-out. Until U2 and U4 land, the shortcut table under **Superseded** remains the shipped behaviour.

## Evidence and references

Reference images for the IDE chrome have not been added yet; see `docs/design-refs/README.md`. Slice evidence is stored under `docs/evidence/ide-shell/<slice>/` as screenshots at 375 x 812 and 1280 x 900 in light and dark, reviewed against the DESIGN.md checklist. U4 captures the Agents view, Explorer with two file tabs split, Search results (and none), the bottom terminal, the status bar, the command center, the keyboard focus ring and the phone drawer, files, terminal and command center; `tests/workspace_ide_shell_e2e.py` regenerates them. U6a captures the Sites view (pinned, suggested, open tabs, notes), a browser tab, the CLI list with statuses, a CLI tab with its header and approval hint, the New CLI session dialog, the task board with executor chips and a blocked node, and the host popover (plus the Disconnected status bar), at 375 x 812 and 1280 x 900 in both themes; `tests/workspace_agent_surfaces_e2e.py` regenerates them into `docs/evidence/ide-shell/u6a/`.

---

## Behaviour that carries over

The sections below describe behaviour that the redesign keeps. Where a section names the old chrome (a 336px right sidebar, Workspace actions), read it as the capability, which moves into the Agents view, tabs and command palette.

## New chats, titles and checkouts

**New chat** creates an empty conversation directly and focuses its composer. It does not ask for a title or dispatch a provider task. The first accepted user message supplies a durable, deterministic title; explicit manual renames remain manual. **Workspace actions → New chat with options** retains project, agent-preset and folder selection without making naming a required step. Creation and delayed history reads cannot rebind the composer to the previous conversation; a newer explicit navigation wins over a late creation response.

New local conversations in registered Git projects use automatic checkout policy. Before the first Plan or Agent turn, the host prepares a separate enrolled worktree from committed `HEAD`, subject to the existing Git-write authority. Uncommitted and untracked source files remain in the original folder and are not copied. Plan still inspects code and proposes work before editing project files; preparing its checkout keeps a later approval on that same reviewed baseline. Ask remains read-only. Scratch folders, non-Git folders, browser workflows and remote runners report their actual context rather than claiming Git isolation.

A compact **Workspace checkout details** disclosure above the composer exposes the host-reported working folder, branch, source folder and base revision when present. Pending preparation visibly discloses the committed baseline and retained source edits. Missing or inaccessible worktrees stop sends with actionable errors; there is no silent fallback to editing the original folder. Existing advanced checkout selection remains available in Session options.

Auto requires the built-in local Agent and a reported ready Git worktree. Pending automatic preparation explains that the worktree is created on the first planning or coding turn; it does not enable Auto before the checkout is ready. Non-Git and scratch contexts explain the Git requirement without making manual worktree creation mandatory. Restoring a preserved conversation does not start a new agent turn, create a worktree or replay an uncertain dispatch.

## Subagents and task supervision

**Subagents** in the Agent header opens a 336px right sidebar. It opens once when actual child tasks or plan content first appears, then respects the account's explicit open/closed preference. Closing keeps the portal target mounted for state preservation, makes it inert immediately and returns focus to the toggle when appropriate. Studio, settings and locked workspaces do not expose the hidden sidebar.

The main agent uses its coordinator's child records and plan board. Ordinary conversations use only the selected conversation's recent execution roots: up to ten roots, with an authorized bounded tree reader and up to 100 displayed descendants. A retained snapshot from a different conversation is not a source for the newly selected conversation. Failed authority reads stop publishing the cached source rows. Sidebar movement and visibility do not instantiate duplicate copies of the same observer.

Rows are grouped into **Active** and **Done**. Active includes nonterminal waiting, running, paused, approval and recovery states; Done distinguishes completed, failed and cancelled tasks. Ages derive from reported update timestamps, not invented durations. Groups initially show ten rows with Show more; truncation is explicit. Selecting an ordinary task opens the existing task-supervision dialog. Its retained inspection is fenced by account, selected conversation, workspace visibility and lock state.

Approvals, recovery confirmation, child ownership and unavailable-state feedback remain available inline when the sidebar is closed. Opening or inspecting a child does not itself approve work, change control ownership or start a retry.

## Voice and typed content

Dictation is an explicit action that stages text in the current draft for review; Send remains a separate action. Assistant responses expose **Read aloud** and **Stop reading**, with Voice settings available when configuration or consent is missing. The chat does not expose automatic-send or automatic-read checkboxes and does not rewrite the saved account preferences. Live voice remains a separate explicit session; microphone ownership, conversation/account changes, lock, hidden workspace and live-voice fences still apply.

Responses preserve readable prose, lists and code while giving real file, link and media metadata typed presentations. Tool activity and raw details expand on demand. File references resolve inside the authorized selected checkout. Known, bounded media can load a verified inline preview while visible; unknown-size assets require Load preview, and audio/video playback remains explicit. Active HTML and SVG stay download-only. Unsafe or unsupported content falls back to an explanation or download rather than a fabricated preview. A response's Review changes action uses the host's actual checkout-change count, not a claim that all changes were produced by that response.

## Motion and disclosure lifecycle

Shared controls use short state transitions: 120ms feedback and exits and 160ms entry and layout changes (the `--duration-*` tokens; DESIGN.md allows 120 to 200ms, ease-out, opacity and transform only). Radix disclosures retain keyboard semantics and a quiet content rail. Closing sidebars, dialogs and animated disclosure content become inert immediately; painted exit retention does not keep their controls interactive. Rapid reopen cancels a pending sidebar removal. Reduced motion removes animation without weakening hidden/inert or focus boundaries. Motion never substitutes for a server-reported task or generation outcome.

## Conversation tools and file references

Active or critical task state stays visible while routine usage and context live in Session options. **Review changes**, **Browser**, **Terminal**, and **Files** open the corresponding workspace surface without replacing the conversation. Completed responses may offer a host-backed changed-file review action. Task approvals and recovery decisions remain separate from reviewing file changes.

File references in assistant text, including inline code such as `src/App.tsx:25` and local Markdown file links, open the editor at the referenced file and line. The host resolves them inside the selected conversation's checkout. Ordinary web links remain links.

## Review checkout changes

**Changes** reviews the actual authorized conversation checkout, including a selected worktree. The file list includes staged, unstaged, and untracked files; the text diff compares the current file against `HEAD`, combining staged and working-tree edits rather than presenting them as pending agent proposals. Untracked text files appear as additions. This review is read-only; staging, committing, and delivery remain in their existing workflows.

Select a changed file to inspect its numbered unified diff. **Review actions** switches to a split before/after diff or hides the file list, and the file header can open the current file in the editor. Refresh updates the checkout snapshot; visible unlocked workspaces also poll for changes. Binary files, unavailable runner checkouts, non-Git folders, stale file selections, and limited previews have explicit states. The list is bounded to 2,000 files and text previews to 1 MB, with partial counts and truncation identified in the UI.

The read-only APIs are `GET /api/workspace/sessions/{id}/changes` and `GET /api/workspace/sessions/{id}/changes/diff?path=...`. A selected worktree supplies its `worktree_id` on both requests. The host checks the conversation's `git-read` authority and checkout binding before and after reading.

## Verification

The records below are historical verification snapshots, not a combined claim about every later change. Current builds and Music acceptance require their own exact-source evidence. The ordinary-conversation sidebar history fence has a dedicated App regression: while a browser-history selection waits for its snapshot, previous conversation roots are omitted; only the newly selected roots appear after resolution.

Targeted component tests cover hover reveal, delayed dismissal, focus retention, Escape, outside clicks, narrow navigation, account-scoped persistence, editor tree expansion and stale responses, docking and unsaved buffers, transcript virtualization, drafts, queued prompts and interruption, model controls, approvals, and recovery.

For the earlier workspace refresh, the production TypeScript/Vite build and 90 targeted tests passed. Real-app Chrome checks at 1440 × 960 and 390 × 844 covered Code/Chat navigation, real file opening, hover overlay geometry, Command+B dismissal, narrow menu availability, touch dismissal, desktop preference restoration, tool expansion, approval review access, and light/dark themes. The fixture used a scratch Git project and seeded task history; no external model or deployment was invoked.

The final production TypeScript/Vite build passed. The broad frontend regression sweep covered the original 92 test files; fresh final-source reruns passed all 14 tests for workspace tabs, accessible file references, and external editor requests. The separately refined browser transport and denial checks passed all 5 targeted cases.

Follow-up backend integration checks passed all 8 checkout-review cases, including staged and untracked changes, checkout and permission revalidation, unsafe paths, and disabling external Git helpers. Frontend checks cover unified/split parsing, scoped changes polling, panel expansion/restoration, close/reopen, bottom visibility, full-view tab selection, file-reference accessibility, exact editor line navigation, dirty buffers, stale reads, and browser reconnect/error boundaries.

Real-app Chrome checks at 1440 × 960 and 390 × 844 exercised modified and untracked file diffs, unified/split modes, close/reopen with the same Browser URL, visible floating drafts, conversation expansion/restoration, hiding/restoring floating Chat, bottom Terminal visibility, narrow full-width tool tabs, actual browser fullscreen enter/exit, and reopening chat file references with unsaved edits retained. A final production-build smoke check also switched tool tabs within full view and opened a pending task review from the floating conversation without approving the action. The managed Browser rendered a real localhost demo using a dedicated Chromium profile. The fixture used scratch state without user browser cookies, external model calls, or deployment.

The native fullscreen and window-identity bridge passed `cargo check --locked --tests`; the installed desktop application was not rebuilt or exercised for native fullscreen in this run.

## PR integration, 20 October 2026

TermXMob PR #42 is merged at `7abc0a8`. The local workspace and character changes
are integrated on top of that commit. This retains the PR's password-session
refresh, serialized conversation writes, native credential handling, immutable
running-task settings, and lazy loading of saved tool and child-agent activity.

The combined desktop TypeScript/Vite build and native `cargo check --locked
--tests` passed. Targeted frontend validation passed 99 chat/authentication tests
and 89 workspace, browser, editor, review, and character tests. Three timing
failures in the concurrent add-on run passed unchanged with one worker.

The existing real-daemon chat recovery regression now follows the new workspace
navigation and session controls. It passed eight desktop, narrow, and short
viewport/theme combinations and 13 accessibility audits. It also verified real
password login and access-cookie refresh, draft preservation, saved nested
activity, model/mode/preset changes, linked-engine fork review, native effort
persistence, and running-task controls. There were no page exceptions, external
requests, or inference dispatches. The light-theme secondary text token was
adjusted after this check caught a code-language label with insufficient contrast.

Backend integration checks also caught a native engine shutdown race that could
discard a final stderr diagnostic. Cleanup now allows a bounded drain within its
remaining graceful budget and avoids signaling a child that has already exited
while a descendant retains stderr. The affected engine, JSON-RPC, gateway, and
ACP regression group passed all 39 tests, including deterministic in-flight and
inherited-pipe cases, cancelled-close cleanup, and the sanitized read-only startup
error.

The separate Expo controller's PRs #26 and #28 are merged at `c399c5b`. Their
managed-auth and native LAN-trust changes are integrated in that repository;
Expo's generated web assets are not copied into the desktop renderer.
On the final merged Expo checkout, TypeScript, 90 tests across 13 files, and the
web export passed. Its three existing local probe scripts were preserved.

## Agent details and effort

The child-agent header keeps its ownership status and Take control action compact. **Engine details** opens the current capabilities and limits in a scrollable dialog. Escape closes the dialog and returns to its trigger.

The main agent and runner chats open the available model list directly, using configured or discovered provider catalogs. Unbound chats show provider-labelled models; only an explicit choice changes the provider/model route. Linked engines with reasoning controls open **Model and effort** as a borderless compact card anchored directly above its selector button. A lightning mark, selected effort, model name and reset sit above a thick gradient slider with contained particles. Arrow keys, Home and End adjust only advertised effort choices. The model-name control replaces the effort card with a separate model list and selected checkmark; account selection and favorites remain available. Pointer opening adds no focus frame; keyboard navigation retains an inset indicator. Short opening, view and thumb transitions respect reduced motion. Workspace, account, session and lock changes close the picker.

Response Read aloud and Stop actions use icons in the response toolbar, with accessible labels and hover titles.

Projects now uses persistent **Projects / Activity / Workflows / Overview** navigation. The folder catalog separates generated workspaces through a discoverable toggle, retaining the active context and explicit search matches. **Add project** registers an existing folder, protects pending submissions and offers **Open project** for an existing path. Project workbench navigation is explicit and survives browser history/reload without being replaced by automatic main chat. Activity uses responsive rows and a focused, internally scrollable details dialog. Catalog, configuration, pins and chat failures remain independent. See [the UX audit and evidence](implementation-status.md#verification).

## Custom workflows

Open **Projects → Workflows** or search for Workflows in Commands. The Library contains saved JavaScript workflows; Runs includes saved and inline runs created by coding agents. Create a workflow, choose a template or edit its async function body, optionally define typed inputs, then Validate, Save and Run. Manual runs can opt into individual limits in the Run dialog; each starts off, including Run again. Review opens the actual child chat for its existing plan or tool approvals. The run view shows durable activity, results and Stop; Run again creates a new execution.

Scripts receive immutable `args` plus `agent`, `parallel`, `pipeline`, `phase` and `log`. They coordinate agents through the same `WorkspaceService.send` path used by chat. They have no filesystem, network, import or arbitrary tool bridge. Task usage quotas and agent-triggered workflow quotas are disabled, including legacy saved values. Only an explicit manual run can set agent-call, concurrency or elapsed-time limits; omitted or null values are unlimited. Existing definitions are preserved, and their former limits are ignored at execution. Source, frame, heap and stack integrity checks remain separate from run quotas. Nested workflow recursion protections remain. Native engine tool steps remain unmeasured by TermX.

Coding agents can list, get, save, validate, run, inspect and cancel workflows through seven fixed tools. Newly saved definitions and inline scripts run immediately without new tool registration. Internal agents use the ordinary tool registry; Codex uses task-scoped dynamic tools on new threads; Claude uses its SDK MCP server. ACP currently has no authenticated workflow callback transport, and old Codex threads cannot gain dynamic tools on resume. Start a new Codex conversation to use them. Native protocol behavior is fixture-tested without paid inference.

Definitions and history are owner scoped; saved revisions use optimistic concurrency and run starts use idempotency keys. Archive preserves history. Interrupted runs require review and never replay automatically. The runtime fails closed when its required OS restrictions or pinned QuickJS dependency are unavailable. The current Mac development launcher uses the existing profile and project Python. Frozen-worker entry and bundled assets are tested; a complete frozen installer build remains a separate release gate.

[Research and execution contract](implementation-status.md#workflows) · [Verification evidence](implementation-status.md#verification)


## Execution and path selection

Coding tasks continue until completion, explicit cancellation, provider failure or revoked execution authority. Tool/step, active-time, Auto usage and total-worker quotas no longer pause them. Native workers also have no application worker-count, 15-minute active-time or 24-hour elapsed deadline. Normal permissions, sandboxing, approval decisions and cancellation remain enforced. Explicit command timeouts remain available. Legacy scheduled command runbooks retain their existing deadlines pending separate authorization to remove them; unattended grant expiry and run-occurrence authorization remain in force.

Provider account **Limits** optionally configures response output tokens and request timeout, both unset by default. These controls apply to coding-provider inference. No generic output-token or model-request timeout fallback is imposed. Intrinsic provider/model constraints still apply. Existing stored usage is retained for reporting.

**Add project** and **New chat** use a filesystem folder picker with breadcrumbs, parent/home navigation, filtering, hidden folders and pagination. Project context transfer uses the same picker for files inside the authorized conversation checkout, with a selected-file list. Optional typed paths remain under **Enter path**. Host browsing requires the same host-wide authority as project registration; project file browsing stays inside its project/worktree root.

[Limits and filesystem verification](implementation-status.md#execution-and-path-selection)

---

# Superseded: the chat-first (ChatGPT-style) shell

> **Superseded by the agent-first IDE shell above.** Everything below describes the presentation that currently ships and is replaced slice by slice: the 52px application rail and Agent/Studio mode switch, the inset canvas, the right-hand Subagents sidebar, Workspace actions, floating chat and expand-panel, sidebar hover mode, the Pinned/Collapsed/Float choice, the shortcut table, and the ChatGPT, Suno, Cursor and VS Code reference language. Do not extend it. The visual values here (Geist type, graphite and green palette, 736px column) are retired: the baseline is now DESIGN.md through `src/styles/tokens.css`, Inter and JetBrains Mono, and a 720px reading column (changed in U0). Behaviour that survives was moved above.

## Coding workspace and chat (superseded)

The shared app frame follows the user's [ChatGPT native reference](implementation-status.md#design-references); the code workspace retains the established proportions and hierarchy of the actual [VS Code workbench](https://code.visualstudio.com/docs/editing/getting-started/userinterface), [Cursor Agents Window](https://cursor.com/changelog/3-0), and the [official Codex introduction workshop](https://d2xo500swnpgl1.cloudfront.net/uploads/oaiacademy/Codex-101-Introduction-Demo-32ffbb3c-85da-4da9-973d-5b39ea7d39fd-1773866180169.pdf). The references were inspected before implementation.

The review and content-panel flows also use the actual [Codex app first-look demonstration](https://www.youtube.com/watch?v=0e-Brv-gS9Q), [April browser demonstration](https://www.youtube.com/watch?v=Lm7-yFZ5fZQ), and [July developer update](https://www.youtube.com/watch?v=eiQgljOrkWU). These show review beside chat, a browser expanding into the workspace, and a floating composer that keeps the conversation available. Tab behavior was checked against the installed Codex desktop bundle as well as the [official app commands](https://learn.chatgpt.com/docs/reference/commands).

- **Chat** is a single readable transcript with a compact composer. Tool activity expands inline. Hover or focus a response for copy and inspection actions. Approvals, failures, and recovery stay visible, with a Review action by the composer when attention is needed.
- **Code** gives most of the window to the editor, with a compact expandable file tree, file tabs, breadcrumbs, optional chat, and a bottom panel for terminals, diagnostics, debugging, preview, and delivery. Editor, checkout, docking, and detaching commands live in ellipsis menus.
- **Workspace actions** in the top right opens Browser, Computer, Artifacts, Review, panel controls, saved layouts, recovery, appearance, action review, and workspace locking. All existing keyboard shortcuts and command palette routes remain available.

## Agent and Studio workspaces

The workspace switcher separates two primary jobs. **Agent** contains conversations, code and execution tools; **Studio** fills the working area with media creation and its library. Studio does not display the agent transcript or dock chrome beside its canvas. Returning to Agent restores the same dock arrangement, editor buffers and conversation draft. Existing terminal processes continue running. Capture indicators remain visible in either workspace.

Agent and Studio share a persistent 52px application rail, neutral light/dark chrome and an inset canvas. The rail exposes Agent, Studio, Projects and Settings without a second workspace switch in the header. Agent shows conversations; Studio shows Create, Music, Library, Saved assets and project context. New sidebar layouts use 240px while existing saved Agent widths remain valid. The workspace buttons expose selected state and support horizontal/vertical arrow keys, Home and End. Browser links can open Studio with `?workspace=studio`; normal conversation and settings routes remain available.

Studio keeps its prompt, model and Generate action in a compact bottom-centred dock beside Image/Video switching. References, camera/lens presets and generation settings open focused panels; selected reference summaries and relevant input errors remain visible. Create shows a large selected output with recent takes; Library and Saved assets have their own navigation. Advanced workflows stay in Tools. The agent welcome keeps examples optional, account setup expands in place, and the composer uses one quiet surface. Both areas use the shared native-first typography, installed Geist fallback and existing Lucide controls, alongside original TermX workspace marks and deterministic subagent glyphs. Studio retains its specialized creation workflow within the shared neutral frame.

The earlier Studio iteration used the local `ui-ux-pro-max` skill's verified minimalism guidance, the user's `craft-premium-ui` preferences, and direct visual review of [Higgsfield Cinema Studio](https://higgsfield.ai/generate). The public Image/Video composers and Camera panel were inspected; reference selection required sign-in, so its full authenticated flow was not claimed as observed. The generated landing-page pattern, replacement fonts and decorative animation suggestions were not applicable and were not adopted. Its disclosure search had no verified match; disclosure behavior follows the explicit premium-UI guidance and is checked against actual flows.

That earlier Studio refinement passed all 1,456 frontend tests and TypeScript checking. After the final CSS-only dialog sizing change, the production build and 74-state rendered matrix passed again across dark/light desktop, phone, narrow and short viewports. The exact tested asset manifest was applied to the running local app without restarting it or changing the existing profile. See [the verification record](implementation-status.md#verification) for before/after screenshots, fixture boundaries, accessibility results and the local application receipt. Paid generation and authenticated native Studio actions remain unverified.

Studio now has a dedicated **Settings** view with **Providers**, **Models & defaults**, and **Generation limits**. Provider connections reuse the host credential store and refresh the shared Agent catalog on save; Studio model selections and local quotas remain separate. The settings route is `?workspace=studio&studio=settings`. Entering or leaving settings retains the creation draft and references. The dedicated settings evidence snapshot recorded 1,481 passing frontend tests; the Studio backend suites passed 316 tests. The final dedicated settings browser matrix covered 20 states and real isolated-host settings/provider writes, conflicts, quota refusal before provider dispatch, and desktop/narrow layouts. See [Studio Settings evidence](implementation-status.md#verification) for the exact tested build, accessibility tool limits, and separate local activation receipt. Authenticated native settings actions and paid generation were not exercised in the real profile.

## Current shared design

The shared frame follows the user's [ChatGPT native screenshot reference](implementation-status.md#design-references). A persistent 52px application rail separates Agent, Studio, Projects and Settings. The conversation sidebar uses a 240px default for new layouts while retaining saved widths. Neutral dark/light surfaces, an inset content canvas and quieter chrome replace the previous warm/copper styling. Studio keeps its specialized media creation layout. Its Music section uses the separately recorded [Suno layout reference](implementation-status.md#design-references), with original TermX branding and only host-supported controls.

Conversation content sits in a centered column up to 736px (720px since U0): plain assistant prose, right-aligned rounded user requests, collapsed activity and a rounded composer with one quiet control row. Ordinary usage and context details live in Session options. Active execution, approvals, recoverable errors and Stop remain visible. Coordination lives in the right-hand Subagents sidebar, with critical decisions and recovery still inline. Routine chat footer chrome recedes when there is no capture status; capture monitoring remains mounted and every active-capture or unavailable-capture control remains visible. Connection, Commands, locking and fullscreen remain accessible through Workspace actions.

Settings use a full-height desktop surface with a 240px category sidebar, functional category search, a readable 728px form column and grouped rows with controls on the right. Narrow screens use a native category selector through the same unsaved-change guard. Projects use a searchable folder/conversation list; update dates derive from actual related conversations, with an unavailable marker when no timestamp exists. No project metadata or conversation previews are fabricated.

The existing React/Vite and Radix/shadcn components remain; the app uses the native system font first with the installed Geist fallback. Drafts, references, unsaved buffers, terminal processes, account configuration and provider settings remain intact. The earlier neutral-Agent evidence snapshot recorded **1,501 passing tests across 182 files**, with TypeScript and a production build. **71 rendered states and 53 interaction checks** cover both themes, desktop/phone, narrow and short panes, keyboard navigation, retained drafts/buffers, configured/disconnected composer states and visible capture controls beneath a genuine draft-conflict notice. Automated axe reported zero violations, with 30 states retaining incomplete checks; this is not a claim of complete accessibility certification. The [verification record](implementation-status.md#verification) separates isolated browser evidence from local activation. No paid provider calls were made.

The [local activation receipt](implementation-status.md#verification) records that earlier build and its preserved host profile. Its native renderer refresh was unverified because macOS ScreenCaptureKit reported capture error -3811. Build, browser and native-activation evidence remain separate; historical counts and receipts do not establish validation of the newer Music implementation.

## Tabs, full view, and floating chat

- Use **+ → New workspace tab** in a panel header to open Chat, Files, Changes, Browser, Terminal, Computer, or Artifacts. Selecting an already-open surface activates its existing tab. Surfaces sharing a region use a tab strip; Left/Right and Home/End navigate it with the keyboard. Narrow windows combine the open surfaces into one workspace tab strip.
- The panel actions menu offers **Open in side panel**, **Open in bottom panel**, and **Open in full view**, alongside placement and separate-window commands. Dragging a panel header also changes its placement. Panel contents remain mounted when switching tabs or regions, preserving editor buffers and conversation drafts.
- **Close tab** hides that workspace surface. **Reopen closed tab** restores the most recently closed surface in the current window. Closing a workspace tab does not close the Browser's individual page tabs, end a terminal process, or cancel an agent task. The last workspace surface remains open.
- **Expand panel** fills the workspace with the selected surface. When Chat is open, its same composer floats near the bottom; its text and attachments are the existing draft. The chevron shows or collapses the transcript, and the chat icon hides or restores the floating chat. The other open tool tabs stay available across the expanded header. **Restore split view**, or Escape from workspace chrome, returns to the previous panel arrangement.

**Toggle bottom panel** (`Mod+J`) hides and restores docked bottom surfaces as well as the editor console, retaining their state. Moving the central tool to a side or bottom panel keeps Chat, or another open surface, in the center.

**Toggle window full screen** is a separate command. It uses the native window API in the desktop host and the browser's Fullscreen API on the web; expanding a panel changes only the workspace layout.

## Default shortcuts

`Mod` means Command on macOS or Ctrl on Windows/Linux. Bindings can be changed in Settings, and equivalent actions remain in the Commands menu when an external browser reserves a shortcut.

| Action | Shortcut |
| --- | --- |
| Chat / Code / Browser / Changes | `Mod+1` / `Mod+2` / `Mod+3` / `Mod+4` |
| Terminal workspace tab | `Mod+7` |
| Close / reopen workspace tab | `Mod+W` / `Mod+Shift+T` |
| Toggle sidebar / bottom panel | `Mod+B` / `Mod+J` |
| Workspace commands / quick file open | `Mod+Shift+P` / `Mod+P` |
| New chat | `Mod+Shift+N` |
| Hold to dictate / tap to start and stop | `Mod+Shift+M` |
| Toggle window full screen | `Ctrl+Meta+F` (Control+Command+F on macOS) |
| New / close Browser page tab | `Mod+Alt+T` / `Mod+Alt+W` |
| Reopen Browser page tab | `Mod+Alt+Shift+T` |
| Focus terminal (from a terminal, back to Chat) | `Ctrl+backtick` |
| Explorer / Search files / Source control | `Mod+Shift+E` / `Mod+Shift+F` / `Ctrl+Shift+G` (Explorer and Search open Files; Source control opens Changes) |
| Settings | `Mod+,` |
| Next / previous workspace tab | `Ctrl+Tab` / `Ctrl+Shift+Tab` |

The command registry (`shell/commands.ts`) is the single list behind the palette, these bindings and the shortcut dispatcher. A focused terminal keeps every key except the command palette, the panel toggles, tab switching and Focus terminal; inside a terminal `Mod` means the platform command key only (Command on macOS, Control elsewhere), so Ctrl+B, Ctrl+J and Ctrl+P still reach the shell on a Mac. Browser and Computer remote input keeps every key.

## Sidebar behavior

Use **Workspace actions → Sidebar** to choose **Pinned**, **Collapsed**, or **Float on hover**. The choice is stored per account on this device.

The 52px application rail stays available in both workspaces, including when the sidebar is collapsed. In hover mode, entering the rail reveals a floating sidebar without moving the editor or transcript. Leaving it closes it after a short delay; keyboard focus keeps it open. Escape or clicking the work area dismisses it. Pinning returns it to the layout. On narrow screens, use the sidebar button and dismiss backdrop rather than relying on hover. The sidebar shortcut remains Command/Ctrl+B.

Code and Review start with the conversation sidebar collapsed for more editing space. Saved arrangements continue to preserve panels, widths, and navigation visibility.
