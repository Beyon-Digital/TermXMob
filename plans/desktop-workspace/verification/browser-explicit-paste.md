# Explicit clipboard-event paste

`browser-explicit-paste.json` records the exact frozen production bundle and every production file hash. The real authenticated BrowserSurface control stream typed the explicit DOM ClipboardEvent fixture text into the actual managed Chromium page; the host-side page input was verified equal. Two loaded axe states, actual Tab/focus including the remote input and workspace return, reduced motion, no overflow, no JavaScript errors, zero provider calls.

The paste event reads only event-owned `text/plain`, enforces a client bound and current human control, and sends text as a literal `type` action. Ctrl/Meta+V is no longer forwarded to the host Chromium clipboard. Unit regression also verifies oversized and denied-control pastes send no action. Existing host authority rechecks remain in force.

The clipboard event is explicitly synthesized with a DataTransfer in the isolated browser to avoid reading or changing the user's OS clipboard. This proves the application's event/control path and literal page result. It does not qualify OS/native clipboard integration. Source: `BrowserSurface.tsx` and `features/browser.test.tsx`.
