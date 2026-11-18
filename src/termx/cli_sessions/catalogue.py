"""Known interactive coding CLIs and the typed argv builders that launch them.

Argv is never taken from the model or a client. A caller picks a catalogue id and a
few typed options (a prompt, a mode, a model name); this module turns them into the
argument vector. Anything that lifts a CLI's own permission checks is a *bypass
flag*: the builders refuse it unless the caller states that a human approved it and
that the session runs in a restricted sandbox profile, and ``assert_no_bypass`` is
the independent check the service runs on the finished vector.

Flag names marked ``verified`` were checked against ``--help`` and parser errors of
the CLI versions installed when this was written (claude 2.1.326, codex-cli 0.169.0).
``best-effort`` entries come from the tools' published conventions and could not be
exercised on this machine; they degrade to "not installed" when the executable is
absent and never block the verified ones.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from termx.engines.env import probe_version, resolve_executable

MAX_PROMPT_CHARS = 64_000
# A prompt this long, starting with "-", or a single word is typed into the TUI instead of the argv
# (an option-looking value must never be parsed as a flag, nor a bare word as a subcommand, by the CLI).
ARGV_PROMPT_CHARS = 4_000
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,127}$")
_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

MODES = ("default", "read_only", "bypass")


class CatalogueError(ValueError):
    """The request cannot be turned into a launch vector."""


class BypassRefused(CatalogueError):
    """A permission-bypass option was requested without sandbox and human approval."""


class UnknownCli(CatalogueError):
    pass


@dataclass(frozen=True)
class CliSpec:
    id: str
    label: str
    executable: str
    version_args: tuple[str, ...] = ("--version",)
    # "positional": `<cli> [flags] -- <prompt>`; "flag": `<cli> [flags] <flag> <prompt>`; "pty": typed after start.
    prompt: str = "positional"
    prompt_flag: str | None = None
    separator: bool = True
    cwd_flag: str | None = None
    model_flag: str | None = "--model"
    # Mode -> extra flags. A mode absent here is unsupported for this CLI.
    modes: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Flags (exact tokens or "--flag=value" prefixes) that lift the CLI's own safety checks.
    bypass_flags: frozenset[str] = frozenset()
    session_id_flag: str | None = None
    resume_args: tuple[str, ...] | None = None  # + (<native id>,)
    continue_args: tuple[str, ...] | None = None
    hooks: str | None = None  # "claude-settings" | "codex-notify" | None
    # The CLI's own credential variables; everything else credential-shaped is scrubbed.
    env_passthrough: tuple[str, ...] = ()
    # Names inherited from a parent coding agent that would confuse a fresh instance.
    env_strip_prefixes: tuple[str, ...] = ()
    interrupt: bytes = b"\x03"
    verified: bool = False  # flags checked against the installed CLI
    note: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "hooks": self.hooks,
            "resume": self.resume_args is not None or self.continue_args is not None,
            "modes": sorted(set(self.modes) | {"bypass"}),
            "bypass_flags": sorted(self.bypass_flags),
            "verified": self.verified,
        }


CATALOGUE: dict[str, CliSpec] = {
    "claude": CliSpec(
        id="claude",
        label="Claude Code",
        executable="claude",
        # --permission-mode, --session-id, --settings, --resume, --continue, --model: verified.
        modes={"default": (), "read_only": ("--permission-mode", "plan")},
        bypass_flags=frozenset(
            {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "bypassPermissions"}
        ),
        session_id_flag="--session-id",
        resume_args=("--resume",),
        continue_args=("--continue",),
        hooks="claude-settings",
        env_passthrough=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_OAUTH_TOKEN"),
        env_strip_prefixes=("CLAUDECODE", "CLAUDE_CODE_", "CLAUDE_AGENT_"),
        verified=True,
    ),
    "codex": CliSpec(
        id="codex",
        label="Codex CLI",
        executable="codex",
        cwd_flag="-C",
        # -s/--sandbox, -C/--cd, -c, resume [ID] [PROMPT], resume --last: verified.
        modes={"default": (), "read_only": ("-s", "read-only")},
        bypass_flags=frozenset(
            {"--dangerously-bypass-approvals-and-sandbox", "--yolo", "danger-full-access", "--dangerously-bypass-hook-trust"}
        ),
        resume_args=("resume",),
        continue_args=("resume", "--last"),
        hooks="codex-notify",
        env_passthrough=("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"),
        env_strip_prefixes=("CODEX_",),
        verified=True,
        note="Status hooks come from Codex's `notify` program, which only reports finished turns; approvals use output patterns.",
    ),
    "gemini": CliSpec(
        id="gemini",
        label="Gemini CLI",
        executable="gemini",
        # `-i/--prompt-interactive`, `--yolo`, `--approval-mode`: best-effort, not installed here.
        prompt="flag",
        prompt_flag="--prompt-interactive",
        modes={"default": ()},
        bypass_flags=frozenset({"--yolo", "-y", "yolo"}),
        env_passthrough=("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"),
        note="Flags are best-effort; the CLI was not installed to check them.",
    ),
    "aider": CliSpec(
        id="aider",
        label="Aider",
        executable="aider",
        # Aider has no interactive-with-initial-prompt flag (`--message` exits): the prompt is typed after start.
        prompt="pty",
        modes={"default": ()},
        bypass_flags=frozenset({"--yes-always", "--yes"}),
        env_passthrough=("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"),
        note="Flags are best-effort; the CLI was not installed to check them.",
    ),
}


def spec_for(cli: str) -> CliSpec:
    spec = CATALOGUE.get(str(cli or ""))
    if spec is None:
        raise UnknownCli("Choose one of: " + ", ".join(sorted(CATALOGUE)))
    return spec


@dataclass(frozen=True)
class LaunchPlan:
    cli: str
    argv: list[str]  # argv[0] is the resolved executable
    # Text to type into the TUI after it starts (CLIs without a safe argv prompt, or an unsafe prompt).
    typed_prompt: str | None
    native_session_id: str | None
    mode: str
    prompt_in_argv: bool
    # Everything except the prompt, for display. Never includes the prompt or a settings path.
    display: str
    # argv[:fixed_len] is the launch vector without the prompt: what the bypass check inspects.
    fixed_len: int = 0

    def check(self, *, allow_bypass: bool = False) -> None:
        assert_no_bypass(self.cli, self.argv[: self.fixed_len or len(self.argv)], allow=allow_bypass)


def validate_prompt(prompt: str | None) -> str | None:
    if prompt is None:
        return None
    if not isinstance(prompt, str):
        raise CatalogueError("The prompt must be text")
    prompt = prompt.strip("\r\n\t ")
    if not prompt:
        return None
    if len(prompt) > MAX_PROMPT_CHARS:
        raise CatalogueError(f"Keep the prompt under {MAX_PROMPT_CHARS} characters")
    if "\x00" in prompt:
        raise CatalogueError("The prompt cannot contain NUL")
    return prompt


def _bypass_present(spec: CliSpec, argv: list[str]) -> list[str]:
    hits = []
    for token in argv:
        for flag in spec.bypass_flags:
            if token == flag or (flag.startswith("--") and token.startswith(flag + "=")):
                hits.append(flag)
    return hits


def assert_no_bypass(cli: str, argv: list[str], *, allow: bool = False) -> None:
    """Independent check on a finished vector: refuse any bypass token unless ``allow``."""
    spec = spec_for(cli)
    if allow:
        return
    hits = _bypass_present(spec, list(argv[1:]))
    if hits:
        raise BypassRefused("Refused permission-bypass option: " + ", ".join(sorted(set(hits))))


def build_launch(
    cli: str,
    *,
    executable: str,
    working_dir: str,
    prompt: str | None = None,
    mode: str = "default",
    model: str | None = None,
    native_session_id: str | None = None,
    extra_args: tuple[str, ...] = (),
    allow_bypass: bool = False,
) -> LaunchPlan:
    """Typed argv for a fresh interactive session. ``allow_bypass`` must come from the host (sandbox + human)."""
    spec = spec_for(cli)
    if mode not in MODES:
        raise CatalogueError("Choose a mode: " + ", ".join(MODES))
    prompt = validate_prompt(prompt)
    argv = [executable]
    if spec.cwd_flag:
        argv += [spec.cwd_flag, working_dir]
    if mode == "bypass":
        if not allow_bypass:
            raise BypassRefused(
                "Permission-bypass mode needs a restricted sandbox profile and explicit human approval"
            )
        argv += _bypass_args(spec)
    elif mode not in spec.modes:
        raise CatalogueError(f"{spec.label} has no verified {mode.replace('_', ' ')} mode")
    else:
        argv += list(spec.modes[mode])
    if model:
        if not spec.model_flag:
            raise CatalogueError(f"{spec.label} does not take a model option here")
        if not _SAFE_TOKEN.match(model):
            raise CatalogueError("The model name has unsupported characters")
        argv += [spec.model_flag, model]
    session_id = None
    if spec.session_id_flag:
        session_id = native_session_id or str(uuid.uuid4())
        if not re.match(r"^[0-9a-fA-F-]{36}$", session_id):
            raise CatalogueError("The native session id must be a UUID")
        argv += [spec.session_id_flag, session_id]
    argv += list(extra_args)
    display = " ".join(argv[1:])
    fixed_len = len(argv)
    assert_no_bypass(cli, argv, allow=allow_bypass and mode == "bypass")
    typed = None
    in_argv = False
    if prompt:
        # A bare word can be a subcommand name even after `--` (`claude -- mcp` runs `claude mcp`, verified
        # on 2.1.326), so only multi-word prompts travel on the command line.
        safe = len(prompt) <= ARGV_PROMPT_CHARS and not prompt.startswith("-") and any(ch.isspace() for ch in prompt)
        if spec.prompt == "positional" and safe:
            argv += (["--"] if spec.separator else []) + [prompt]
            in_argv = True
        elif spec.prompt == "flag" and spec.prompt_flag and safe:
            argv += [spec.prompt_flag, prompt]
            in_argv = True
        else:
            typed = prompt
    return LaunchPlan(cli, argv, typed, session_id, mode, in_argv, display, fixed_len)


def _bypass_args(spec: CliSpec) -> list[str]:
    if spec.id == "claude":
        return ["--dangerously-skip-permissions"]
    if spec.id == "codex":
        return ["--dangerously-bypass-approvals-and-sandbox"]
    if spec.id == "gemini":
        return ["--yolo"]
    if spec.id == "aider":
        return ["--yes-always"]
    raise CatalogueError(f"{spec.label} has no bypass option")


def build_resume(
    cli: str,
    *,
    executable: str,
    working_dir: str,
    native_session_id: str | None,
    settings_args: tuple[str, ...] = (),
    mode: str = "default",
    allow_bypass: bool = False,
) -> LaunchPlan:
    """Argv that reopens the CLI's own saved conversation.

    A session that ran in ``bypass`` mode resumes in it only when the host says a human re-confirmed it and the
    session runs in a restricted sandbox (``allow_bypass``), exactly as for ``build_launch``; otherwise it is refused.
    """
    spec = spec_for(cli)
    if mode not in MODES:
        raise CatalogueError("Choose a mode: " + ", ".join(MODES))
    mode_args: list[str] = []
    if mode == "bypass":
        if not allow_bypass:
            raise BypassRefused("Permission-bypass mode needs a restricted sandbox profile and explicit human approval")
        mode_args = _bypass_args(spec)
    elif mode not in spec.modes:
        raise CatalogueError(f"{spec.label} has no verified {mode.replace('_', ' ')} mode")
    else:
        mode_args = list(spec.modes[mode])
    if native_session_id:
        if not _SESSION_ID.match(native_session_id):
            raise CatalogueError("The native session id has unsupported characters")
        base = spec.resume_args
        tail = (native_session_id,) if base else ()
    else:
        base, tail = spec.continue_args, ()
    if base is None:
        raise CatalogueError(f"{spec.label} cannot resume a saved conversation")
    argv = [executable]
    if spec.cwd_flag:
        argv += [spec.cwd_flag, working_dir]
    # Codex takes global flags before the `resume` subcommand; claude takes them anywhere.
    argv += mode_args
    argv += list(settings_args)
    argv += list(base) + list(tail)
    assert_no_bypass(cli, argv, allow=allow_bypass and mode == "bypass")
    return LaunchPlan(cli, argv, None, native_session_id, mode, False, " ".join(argv[1:]), len(argv))


def scrub_environment(spec: CliSpec, base: dict[str, str]) -> dict[str, str]:
    """The child's environment: no TermX variables, no credential-shaped names except the CLI's own."""
    from termx.sandbox.environment import _SENSITIVE_ENV

    allowed = set(spec.env_passthrough)
    env: dict[str, str] = {}
    for name, value in base.items():
        if name in allowed:
            env[name] = value
            continue
        upper = name.upper()
        if upper.startswith("TERMX") or upper.startswith(spec.env_strip_prefixes):
            continue
        if _SENSITIVE_ENV.search(name):
            continue
        env[name] = value
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("COLORTERM", "truecolor")
    return env


async def detect_installed(env: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Installed CLIs and their versions (bounded; a missing or hung CLI never blocks the rest)."""
    import asyncio

    async def one(spec: CliSpec) -> dict[str, Any]:
        path = resolve_executable(spec.executable, env=env)
        version = None
        if path:
            try:
                version = await asyncio.wait_for(probe_version(path, list(spec.version_args), timeout=4.0), timeout=6.0)
            except (TimeoutError, asyncio.TimeoutError):
                version = None
        return {**spec.public(), "installed": bool(path), "version": version, "note": spec.note}

    return list(await asyncio.gather(*(one(spec) for spec in CATALOGUE.values())))
