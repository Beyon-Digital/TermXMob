---
name: testing-termx
description: How to run the termx server and drive its web terminal UI for end-to-end testing (session creation, key bar buttons, signal/sleep tests).
---

# Testing termx end to end

## Start the server

`uv` lives at `~/.local/bin/uv` (add to PATH). `uv sync` may already be done
(`.venv` exists); Python >= 3.14 is required and uv manages it.

```sh
cd /Users/devin/repos/TermXMob
export PATH="$HOME/.local/bin:$PATH"
TERMX_CONFIG_DIR="$HOME/.config/termx-dev" nohup uv run termx --host 127.0.0.1 --port 8787 > /tmp/termx-server.log 2>&1 &
```

The banner prints `local http://127.0.0.1:8787` and `passcode off` (unless
`--passcode` is given). `TERMX_CONFIG_DIR` isolates test state from the user's
real config. Health check: `curl -s http://127.0.0.1:8787/api/health`.

## Which UI to test

- `http://127.0.0.1:8787/` serves the Expo app from `desktop/web/` (heavier).
- `http://127.0.0.1:8787/_/app.html` serves the bundled terminal UI
  (`src/termx/static/index.html`) — simplest deterministic surface.
- With no passcode the page auto-passes the gate, auto-creates a `/bin/zsh -l`
  session and attaches its WS — a prompt appears with no clicks.

## Driving the terminal UI

- Click the black terminal area once to focus xterm's hidden textarea before
  typing; keystrokes are sent as `{type:"input"}` WS messages.
- The key bar at the bottom has `ESC TAB CTRL ALT ^C BKSP` / arrows / `CLR / -`.
  In Chrome fullscreen at 1024x768 the `^C` button is at ~(802, 694); buttons
  stretch with window width, so verify position with a `zoom` into the key-bar
  region before clicking — they respond to `pointerdown`, so a normal click works.

## Adversarial signal testing (important)

The `^C` button (`data-key="INT"`) sends BOTH `{type:"signal","name":"int"}` AND
a raw `\x03` byte as input (`static/index.html` `applyExtraKey`). With the tty's
default `isig`, the `\x03` alone makes the line discipline SIGINT the foreground
pgrp — so `sleep 60` + `^C` would pass even with a broken WS-signal path.

To isolate the WS-signal path, run the job with isig disabled inside a child
shell so the `\x03` byte is inert input:

```
sh -c 'stty -isig; stty -a; sleep 60'
```

The on-screen `stty -a` output shows `-isig` in `lflags` as recorded proof. Then
click `^C`: the prompt returns in ~1s iff `send_signal` correctly targets
`os.tcgetpgrp(master_fd)`; the queued `\x03` visibly echoes as a literal `^C`
glyph at the *new* prompt (and may pollute the next typed command, e.g.
`command not found: ^Cecho` — cosmetic, just retype). On a broken implementation
the prompt stays blocked for the full 60s. Restore default with `stty isig`.

## Devin secrets needed

- None for this flow (no passcode, no tunnel, no AI provider).
