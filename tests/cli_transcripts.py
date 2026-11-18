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
