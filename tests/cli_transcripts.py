"""Terminal transcripts authored for the CLI-session status tests.

These are NOT recordings of any real CLI and make no claim about how one renders: they are small
inputs that exercise the pattern tables in ``termx.cli_sessions.status`` (escape sequences, redraws,
stale text) so the heuristics stay unit-testable. Real wording drifts; the patterns are best-effort.
"""

CLAUDE_APPROVAL = (
    "\x1b[2J\x1b[H\x1b[1mBash command\x1b[0m\n  git status\n\nDo you want to proceed?\n"
    "\x1b[36m❯ 1. Yes\x1b[0m\n  2. Yes, and don't ask again for git commands\n"
    "  3. No, and tell Claude what to do differently (esc)\n"
)
CLAUDE_PROMPT = (
    "\x1b[2J\x1b[HDone. I updated the file.\n\n╭────────╮\n"
    "│ >      │\n╰────────╯\n  ? for shortcuts\n"
)
CLAUDE_WORKING = "\x1b[2K\r✢ Thinking… (3s · esc to interrupt)\r\n"
# The dialog was answered and the screen redrawn: the old question is earlier in the stream than the prompt.
CLAUDE_STALE_APPROVAL_THEN_PROMPT = (
    "Do you want to proceed?\n 1. Yes\n 2. No\n\x1b[2J\x1b[HRan git status.\n  ? for shortcuts\n"
)
CODEX_APPROVAL = (
    "\x1b[1mWould you like to run the following command?\x1b[0m\n  $ make clean\n\n"
    "› 1. Yes, proceed (y)\n  2. No, and tell Codex what to do differently (esc)\n"
)
CODEX_PROMPT = "\x1b[2J\x1b[H› \n  ? for shortcuts\n"
AIDER_YN = "Add src/app.py to the chat? (Y)es/(N)o [Yes]: "
AIDER_PROMPT = "Tokens: 2.1k sent, 120 received.\n\n> "
PLAIN_QUIET = "building...\nlinking...\n"
GENERIC_YN = "Overwrite existing file? [y/N] "

# Words placed one by one with CSI n G (cursor horizontal absolute) and no space characters, which is how
# Claude Code 2.1.326 drew its folder-trust dialog when it was launched under TermX. Authored from that
# observation, not pasted from it.
CLAUDE_TRUST_POSITIONED = (
    "\x1b[2G\x1b[1mAccessing\x1b[12Gworkspace:\x1b[22m\r\r\n"
    "\x1b[2GQuick\x1b[8Gsafety\x1b[15Gcheck:\x1b[22GIs\x1b[25Gthis\x1b[30Ga\x1b[32Gproject\x1b[40Gyou"
    "\x1b[44Gcreated\x1b[52Gor\x1b[55Gone\x1b[59Gyou\x1b[63Gtrust?\r\r\n\r\r\n"
    "\x1b[2G\x1b[36m❯ No, exit\x1b[39m\r\r\n\x1b[5GYes,\x1b[10GI\x1b[12Gtrust\x1b[18Gthis\x1b[23Gfolder\r\r\n"
    "\r\r\n\x1b[2GEnter\x1b[8Gto\x1b[11Gconfirm\x1b[19G·\x1b[21GEsc\x1b[25Gto\x1b[28Gcancel"
)
# A choice menu that is not a permission question.
CLAUDE_MENU_POSITIONED = (
    "\x1b[2GClaude\x1b[9Gin\x1b[12GChrome\r\r\n\r\r\n\x1b[2GEnter\x1b[8Gto\x1b[11Gconfirm\x1b[19G·\x1b[21GEsc\x1b[25Gto\x1b[28Gcancel"
)
# An in-place redraw: the dialog is erased (cursor up, erase below) and the prompt takes its place.
CLAUDE_REDRAWN_IN_PLACE = (
    "Do you want to proceed?\r\n 1. Yes\r\n 2. No\r\n\x1b[3A\r\x1b[JRan git status.\r\n  ? for shortcuts\r\n"
)
