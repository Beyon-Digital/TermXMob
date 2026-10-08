# Independent Browser and Computer docking

Production `/assets/index-szNKBjZr.js`, HTML SHA `cc5f43ea01e244e4ef5545eaddb767fb0177aec6c5f148c73b3be3a33b799552`; exact dist copied to the isolated host before startup. Asset manifest records 64 unchanged production files. This supplements the prior t8fN browser/Computer authority matrices; it does not rewrite their bundle identity.

The focused localhost proof passed 12 loaded axe states and 53 actual Tab/focus checks in dark/light at100%/200%, with reduced motion, no violations, no prohibited-name ARIA incomplete results, and no JavaScript errors. Minimum measured keyboard text contrast 4.84. Axe color-contrast incomplete results for disabled controls/remote capture are retained in JSON.

- Computer has an independent dock panel, preset and header. A legacy three-panel Computer saved layout migrates placement without moving the other panes.
- Actual Focus chord and Escape restore preserve the exact controlled managed viewer; no remote OS input is substituted and no Browser canvas replaces Computer.
- At100%, independently docked Browser(main) and Computer(bottom) both show live frames intersecting their real panel viewports by over60 CSS pixels. At200%, Computer Focus/restore and exact popup identity are proven; simultaneous geometry is not claimed there.
- Per-panel Detach opens a Computer URL, canvas and header; the popup begins stopped/watch-first and inherits no Computer control. The source viewer remains the exact current controlled connection.
- Native pickers use Playwright `select_option` after real Tab focus; commands, buttons and Focus chord use actual keyboard events. No diagnostic CSS/source transforms were applied.

Computer capture/input ports are explicit generated JPEG/test ports, while the managed Browser uses real isolated Chromium against a localhost page. No personal display capture, paid provider request, installed Tauri/multi-monitor result, or screen-reader speech qualification is claimed. The fixture had zero provider turns and no website side effect beyond loading its local page.

Source: `src/lib/docking.ts`, `components/DockWorkspace.tsx`, narrow App docking/deep-link/window identity integration, Focus computer command.17 targeted docking/window/keybinding regressions and TypeScript passed. Reproduction: `TERMX_A11Y_COMPUTER_DOCKING_ONLY=1 PLAYWRIGHT_BROWSERS_PATH=/tmp/termx-managed-chromium PYTHONPATH=src:tests .venv/bin/python tests/workspace_browser_accessibility_e2e.py /tmp/termx-computer-independent-proof /tmp/termx-axe-proof/node_modules/axe-core/axe.min.js`.
