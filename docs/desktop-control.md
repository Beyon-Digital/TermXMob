# Desktop control

Agents can read and operate other applications through their accessibility
trees. It uses [agent-desktop](https://github.com/vercel-labs/agent-desktop)
(Apache-2.0, Vercel Labs), a command line program that reads macOS AX, Windows
UI Automation and Linux AT-SPI and prints one JSON object per command. TermX does
not take screenshots for this, so any model that can call tools can use it; the
model does not need to see images.

## What the model gets

| Tool | Does |
|---|---|
| `desktop_apps` | Lists the applications the task may control (opaque `app_id` handles), or the windows of one. |
| `desktop_snapshot` | Reads an application's controls as a tree with refs such as `@s1abc2def3ghi:e7`. |
| `desktop_find` | Searches by role, name, text or value. |
| `desktop_get` | Reads the full text or value of one control. |
| `desktop_act` | One action on a ref: click, right_click, set_value, type, clear, select, toggle, check, uncheck, expand, collapse, scroll, scroll_to, focus. |

Refs belong to one snapshot and stop working when the screen changes. The model
takes a new snapshot after every change; the host never retries by itself.
`computer` stays the tool id in presets, ceilings and bot settings and expands to
these five tools. The tools are hidden from Ask and Plan modes, from isolated
runners and from restricted browser tasks, and are only offered when desktop
control is set up (a provider needs the `functions` capability; the older
`computer` capability is treated as `functions` and otherwise ignored).

Not available, and not planned for the first version: keyboard shortcuts and key
chords, pointer coordinates, drag, hover, double click, clipboard, launching or
closing applications, batches, and `--headed` physical input. Actions that need
physical input on an operating system are refused before anything runs, with the
alternative (on Windows `type` becomes `set_value`; on Linux `scroll` and `focus`
are unavailable).

## Install and pin

TermX is built for agent-desktop 1.2.0, envelope version `2.6`. The pins in
`src/termx/desktop/agent_desktop_pins.json` list each release archive's sha256
and, per platform, the sha256 of every file inside it. Only macOS arm64 has
per-file hashes today; other platforms report `pin_incomplete` until a maintainer
downloads each archive, checks it against the release's `checksums.txt` and the
build attestation, and records the per-file hashes (not done yet). A program is found in this order, and a candidate that fails any check is
reported and skipped:

1. `TERMX_AGENT_DESKTOP`, an absolute path set by whoever starts the backend (not a
   config file the agent could edit). It is not hash checked, so it is reported as
   unverified, but its version, envelope and helper are still checked.
2. The managed copy at `<config>/desktop-control/bin/1.2.0/`.
3. `PATH` (absolute entries only, never the current directory). A file whose hash
   equals the pin is copied into the managed directory and the copy runs; the
   `PATH` file is never executed.

A file is hashed before it is executed, and again whenever its size, mtime or inode
change. A mismatch stops all calls (`hash_mismatch`). The macOS helper
(`agent-desktop-macos-helper`) is hashed with the program. A downloading
installer and its settings screen are a separate change.

The program runs with a small allow-listed environment, a private state directory
(`<config>/desktop-control/home/<owner>`, mode 0700) and one CLI session per task.
Session directories hold element names, so they are removed when a task ends and
swept after 24 hours.

## Permissions per operating system

- **macOS**: the Accessibility permission, granted to the application that started
  TermX (Terminal, iTerm, the TermX app), not to TermX or the program. On macOS 27
  and later it is called Device Control and Data Access. Screen Recording is not
  needed. A backend started by a LaunchAgent has no grant.
- **Windows**: nothing at normal integrity. Elevated windows, a locked screen and
  other users' sessions are not supported. Minimised windows cannot be driven.
- **Linux**: a graphical session with the accessibility bus (at-spi2-core, Ubuntu
  24.04 or newer) and a glibc system. Wayland-only sessions cannot scroll or focus.
  Chromium and Qt applications may need their accessibility option turned on.

Reading a Chromium or Electron application makes the program switch that
application's accessibility on. A snapshot is therefore not side-effect free.

## Security model

- Authorization lives in the policy engine (capability `computer.control`,
  host-mediated). The decision is synchronous and made before any process starts;
  the CLI is not a second line of defence.
- The model never names an application. A ref resolves to its application through
  the host's table of snapshots it delivered. A ref the host did not deliver, from
  another task, expired or pruned from the output is refused with no process.
- Application identity is the OS-reported name plus bundle ID or executable and
  process instance. Before a read and before an action the host re-checks that the
  same single process is still there, and it discards a read when the process
  changed while it ran. A small window of tens of milliseconds remains because the
  CLI resolves the ref itself.
- Applications that are TermX itself, its ancestors, or can run commands or change
  security settings are denied or asked every time (see
  [Autonomous agents](autonomous-agents.md)).
- The TermX workspace also runs in a browser tab, which the accessibility tree
  shows as an ordinary browser window. Every use of a browser asks, cannot be
  remembered and is denied in scheduled runs, and a browser with a window titled
  for TermX is treated as TermX itself. The title check is a heuristic: a tab
  that is not frontmost, or a renamed one, is not seen, so the per-use approval
  is what protects TermX's own approval buttons.
- Labels that start with, or in a short label contain, words such as send, submit,
  approve, allow, run, delete, pay, install, reset or sign out count as
  consequential (English only).
- Typed text and screen text are not written to execution checkpoints. Replaying
  a typing step after a restart is refused and the model takes a new snapshot.
- Accessibility text is untrusted. It reaches the model between `SCREEN` markers
  with a fresh random id, control and bidirectional characters stripped, secrets
  redacted, strings and totals capped and secure fields shown as `[hidden]`. This
  reduces the injection surface; it does not remove it. Anything on screen can
  still influence a model, which is why risky steps ask every time.
- Typed text goes to the program on stdin and never appears in argv, events,
  approvals or logs. Snapshots, listings, clipboard and CLI messages are never
  logged. Events and the persisted tool result carry counts and a title only.
  Snapshot text is sent to the configured model provider, as screenshots were.
- A result that reports physical (synthetic) input stops further actions in that
  task.
- Private browser or window capture pauses desktop control, and a result read
  after private mode began is discarded.

## Limits compared with screenshots

No vision (canvas apps, games, maps, charts, remote-desktop windows and
applications with poor accessibility give empty trees); no coordinates, drag,
hover or shortcuts; no visual evidence for the owner; applications with several
copies running are refused; identical twin controls on Windows and Linux have no
ref; applications that only live in the menu bar or Dock are not listed.

## Writing skills

Describe goals and control names ("press the Save button in the Notes toolbar"),
not coordinates or key chords. A skill with an application list can only act in
those applications; scheduled runs can never send, delete or enter passwords.

## Verified against the real program, and not

Tests run against a fake program (`tests/fixtures/agent_desktop/`) that behaves
identically on macOS, Linux and Windows hosts. Not verified by tests: real tree
shapes, the exact action names the program advertises, the role names of secure
fields, `--app` with a bundle ID, Windows and Linux `list-apps` identity fields,
the action success envelopes, and whether a snapshot opens network sockets. Run
`tests/test_agent_desktop_real.py` with `TERMX_REAL_AGENT_DESKTOP=<path>` on a
throwaway window to check the read-only parts.
