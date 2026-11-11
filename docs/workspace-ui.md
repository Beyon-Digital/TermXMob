# Coding workspace and chat

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

Conversation content sits in a centered column up to 736px: plain assistant prose, right-aligned rounded user requests, collapsed activity and a rounded composer with one quiet control row. Ordinary usage and context details live in Session options. Active execution, approvals, recoverable errors and Stop remain visible. Coordination lives in the right-hand Subagents sidebar, with critical decisions and recovery still inline. Routine chat footer chrome recedes when there is no capture status; capture monitoring remains mounted and every active-capture or unavailable-capture control remains visible. Connection, Commands, locking and fullscreen remain accessible through Workspace actions.

Settings use a full-height desktop surface with a 240px category sidebar, functional category search, a readable 728px form column and grouped rows with controls on the right. Narrow screens use a native category selector through the same unsaved-change guard. Projects use a searchable folder/conversation list; update dates derive from actual related conversations, with an unavailable marker when no timestamp exists. No project metadata or conversation previews are fabricated.

The existing React/Vite and Radix/shadcn components remain; the app uses the native system font first with the installed Geist fallback. Drafts, references, unsaved buffers, terminal processes, account configuration and provider settings remain intact. The earlier neutral-Agent evidence snapshot recorded **1,501 passing tests across 182 files**, with TypeScript and a production build. **71 rendered states and 53 interaction checks** cover both themes, desktop/phone, narrow and short panes, keyboard navigation, retained drafts/buffers, configured/disconnected composer states and visible capture controls beneath a genuine draft-conflict notice. Automated axe reported zero violations, with 30 states retaining incomplete checks; this is not a claim of complete accessibility certification. The [verification record](implementation-status.md#verification) separates isolated browser evidence from local activation. No paid provider calls were made.

The [local activation receipt](implementation-status.md#verification) records that earlier build and its preserved host profile. Its native renderer refresh was unverified because macOS ScreenCaptureKit reported capture error -3811. Build, browser and native-activation evidence remain separate; historical counts and receipts do not establish validation of the newer Music implementation.

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

Shared controls use short state transitions: 140ms feedback, 180ms entry/layout changes and 120ms exits. Radix disclosures retain keyboard semantics and a quiet content rail. Closing sidebars, dialogs and animated disclosure content become inert immediately; painted exit retention does not keep their controls interactive. Rapid reopen cancels a pending sidebar removal. Reduced motion removes animation without weakening hidden/inert or focus boundaries. Motion never substitutes for a server-reported task or generation outcome.

## Conversation tools and file references

Active or critical task state stays visible while routine usage and context live in Session options. **Review changes**, **Browser**, **Terminal**, and **Files** open the corresponding workspace surface without replacing the conversation. Completed responses may offer a host-backed changed-file review action. Task approvals and recovery decisions remain separate from reviewing file changes.

File references in assistant text, including inline code such as `src/App.tsx:25` and local Markdown file links, open the editor at the referenced file and line. The host resolves them inside the selected conversation's checkout. Ordinary web links remain links.

## Tabs, full view, and floating chat

- Use **+ → New workspace tab** in a panel header to open Chat, Files, Changes, Browser, Terminal, Computer, or Artifacts. Selecting an already-open surface activates its existing tab. Surfaces sharing a region use a tab strip; Left/Right and Home/End navigate it with the keyboard. Narrow windows combine the open surfaces into one workspace tab strip.
- The panel actions menu offers **Open in side panel**, **Open in bottom panel**, and **Open in full view**, alongside placement and separate-window commands. Dragging a panel header also changes its placement. Panel contents remain mounted when switching tabs or regions, preserving editor buffers and conversation drafts.
- **Close tab** hides that workspace surface. **Reopen closed tab** restores the most recently closed surface in the current window. Closing a workspace tab does not close the Browser's individual page tabs, end a terminal process, or cancel an agent task. The last workspace surface remains open.
- **Expand panel** fills the workspace with the selected surface. When Chat is open, its same composer floats near the bottom; its text and attachments are the existing draft. The chevron shows or collapses the transcript, and the chat icon hides or restores the floating chat. The other open tool tabs stay available across the expanded header. **Restore split view**, or Escape from workspace chrome, returns to the previous panel arrangement.

**Toggle bottom panel** (`Mod+J`) hides and restores docked bottom surfaces as well as the editor console, retaining their state. Moving the central tool to a side or bottom panel keeps Chat, or another open surface, in the center.

**Toggle window full screen** is a separate command. It uses the native window API in the desktop host and the browser's Fullscreen API on the web; expanding a panel changes only the workspace layout.

## Review checkout changes

**Changes** reviews the actual authorized conversation checkout, including a selected worktree. The file list includes staged, unstaged, and untracked files; the text diff compares the current file against `HEAD`, combining staged and working-tree edits rather than presenting them as pending agent proposals. Untracked text files appear as additions. This review is read-only; staging, committing, and delivery remain in their existing workflows.

Select a changed file to inspect its numbered unified diff. **Review actions** switches to a split before/after diff or hides the file list, and the file header can open the current file in the editor. Refresh updates the checkout snapshot; visible unlocked workspaces also poll for changes. Binary files, unavailable runner checkouts, non-Git folders, stale file selections, and limited previews have explicit states. The list is bounded to 2,000 files and text previews to 1 MB, with partial counts and truncation identified in the UI.

The read-only APIs are `GET /api/workspace/sessions/{id}/changes` and `GET /api/workspace/sessions/{id}/changes/diff?path=...`. A selected worktree supplies its `worktree_id` on both requests. The host checks the conversation's `git-read` authority and checkout binding before and after reading.

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

## Sidebar behavior

Use **Workspace actions → Sidebar** to choose **Pinned**, **Collapsed**, or **Float on hover**. The choice is stored per account on this device.

The 52px application rail stays available in both workspaces, including when the sidebar is collapsed. In hover mode, entering the rail reveals a floating sidebar without moving the editor or transcript. Leaving it closes it after a short delay; keyboard focus keeps it open. Escape or clicking the work area dismisses it. Pinning returns it to the layout. On narrow screens, use the sidebar button and dismiss backdrop rather than relying on hover. The sidebar shortcut remains Command/Ctrl+B.

Code and Review start with the conversation sidebar collapsed for more editing space. Saved arrangements continue to preserve panels, widths, and navigation visibility.

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
