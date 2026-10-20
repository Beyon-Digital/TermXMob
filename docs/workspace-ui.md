# Coding workspace and chat

The desktop workspace follows the proportions and hierarchy of the actual [VS Code workbench](https://code.visualstudio.com/docs/editing/getting-started/userinterface), [Cursor Agents Window](https://cursor.com/changelog/3-0), and the [official Codex introduction workshop](https://d2xo500swnpgl1.cloudfront.net/uploads/oaiacademy/Codex-101-Introduction-Demo-32ffbb3c-85da-4da9-973d-5b39ea7d39fd-1773866180169.pdf). The references were inspected before implementation.

The review and content-panel flows also use the actual [Codex app first-look demonstration](https://www.youtube.com/watch?v=0e-Brv-gS9Q), [April browser demonstration](https://www.youtube.com/watch?v=Lm7-yFZ5fZQ), and [July developer update](https://www.youtube.com/watch?v=eiQgljOrkWU). These show review beside chat, a browser expanding into the workspace, and a floating composer that keeps the conversation available. Tab behavior was checked against the installed Codex desktop bundle as well as the [official app commands](https://learn.chatgpt.com/docs/reference/commands).

- **Chat** is a single readable transcript with a compact composer. Tool activity expands inline. Hover or focus a response for copy and inspection actions. Approvals, failures, and recovery stay visible, with a Review action by the composer when attention is needed.
- **Code** gives most of the window to the editor, with a compact expandable file tree, file tabs, breadcrumbs, optional chat, and a bottom panel for terminals, diagnostics, debugging, preview, and delivery. Editor, checkout, docking, and detaching commands live in ellipsis menus.
- **Workspace actions** in the top right opens Browser, Computer, Artifacts, Review, panel controls, saved layouts, recovery, appearance, action review, and workspace locking. All existing keyboard shortcuts and command palette routes remain available.

## Conversation tools and file references

The chat header shows the current task state and a compact summary of the selected conversation's checkout changes. **Review changes**, **Browser**, **Terminal**, and **Files** open the corresponding workspace surface without replacing the conversation. Task approvals and recovery decisions remain separate from reviewing file changes.

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
| Toggle window full screen | `Ctrl+Meta+F` (Control+Command+F on macOS) |
| New / close Browser page tab | `Mod+Alt+T` / `Mod+Alt+W` |
| Reopen Browser page tab | `Mod+Alt+Shift+T` |

## Sidebar behavior

Use the sidebar footer or **Workspace actions → Sidebar** to choose **Pinned**, **Collapsed**, or **Float on hover**. The choice is stored per account on this device.

The 44 px navigation rail stays available when collapsed. In hover mode, entering the rail reveals a floating sidebar without moving the editor or transcript. Leaving it closes it after a short delay; keyboard focus keeps it open. Escape or clicking the work area dismisses it. Pinning returns it to the layout. On narrow screens, use the sidebar button and dismiss backdrop rather than relying on hover. The sidebar shortcut remains Command/Ctrl+B.

Code and Review start with the conversation sidebar collapsed for more editing space. Saved arrangements continue to preserve panels, widths, and navigation visibility.

## Verification

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
