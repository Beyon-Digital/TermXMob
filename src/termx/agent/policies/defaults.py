"""Capability inference and risk classification for policy intents.

This module answers two questions conservatively:

- what sandbox capabilities does this action *probably* need (so the engine
  can separate execution approval from capability grants), and
- what risk class does it belong to (so `autonomous` approval mode knows which
  actions must still ask).

Heuristics only feed the *intent*; the sandbox is the boundary that enforces.
"""
from __future__ import annotations

import re
import shlex
from pathlib import Path

from termx.agent.policy import _outside_paths, is_mutating_shell
from termx.agent.policies.models import PolicyIntent

# Programs that obviously need outbound network regardless of arguments.
_NET_PROGRAMS = frozenset(
    {
        "curl", "wget", "ssh", "scp", "sftp", "rsync", "ftp", "nc", "ncat",
        "ping", "traceroute", "dig", "nslookup", "host", "telnet",
        "gh", "glab", "flyctl", "fly", "vercel", "netlify", "wrangler",
        "terraform", "ansible", "docker", "podman",
        "aws", "gcloud", "az", "kubectl", "helm",
        "apt", "apt-get", "brew", "dnf", "yum", "pacman", "apk",
    }
)
# Package/tool managers: only networked for fetch/install/publish subcommands.
_NET_SUBCOMMANDS = re.compile(
    r"(?i)\b(install|add|publish|update|upgrade|uninstall|remove|fetch|download|dlx|exec|sync|clone|push|pull|ls-remote|get|outdated|audit|login|logout|whoami|deploy|release)\b"
)
_PKG_PROGRAMS = frozenset(
    {
        "npm", "pnpm", "yarn", "npx", "bun", "bunx", "deno",
        "pip", "pip3", "pipx", "uv", "uvx", "poetry", "hatch",
        "cargo", "gem", "bundle", "composer", "dotnet", "nuget",
        "go", "rustup", "gem", "cpan", "cpanm",
    }
)
_PUBLISH = re.compile(
    r"(?i)(?:^|[\s;&|])(?:git\s+push|npm\s+publish|pnpm\s+publish|yarn\s+publish|"
    r"twine\s+upload|cargo\s+publish|gem\s+push|docker\s+push|podman\s+push)\b"
)
_PRIVILEGE = re.compile(r"(?i)(?:^|[\s;&|])(?:sudo|doas|pkexec|su\b|runas)\b")
_HOST_SIGNAL = re.compile(r"(?i)(?:^|[\s;&|])(?:kill|pkill|killall|taskkill)\b")
_CREDENTIAL = re.compile(r"(?i)(?:^|[\s;&|])(?:passwd|security|secret-tool|ssh-add|keychain|kinit|vault)\b")
_DOCKER = re.compile(r"(?i)(?:^|[\s;&|])(?:docker|podman|nerdctl)\b")
_EXTERNAL_SUBMIT = re.compile(
    r"(?i)\bgh\s+(?:pr|issue|release)\s+(?:create|merge|close)|\bglab\s+(?:mr|issue)\s+(?:create|merge)"
)


def shell_capabilities(command: str) -> tuple[str, ...]:
    """Capabilities a shell command probably needs inside a restricted sandbox."""
    caps = {"process.execute", "process.children"}
    try:
        first = command.strip().split()[0]
    except IndexError:
        return tuple(sorted(caps))
    program = first.split("/")[-1]
    if program in _NET_PROGRAMS or program in _PKG_PROGRAMS and _NET_SUBCOMMANDS.search(command):
        caps.add("net.outbound:any")
    if _PUBLISH.search(command):
        caps.add("git.publish" if "git" in command else "package.publish")
        caps.add("net.outbound:any")
    if _EXTERNAL_SUBMIT.search(command):
        caps.add("external.submit")
        caps.add("net.outbound:any")
    if _PRIVILEGE.search(command):
        caps.add("privilege.elevate")
    if _HOST_SIGNAL.search(command):
        caps.add("process.host_signal")
    if _CREDENTIAL.search(command):
        caps.add("credentials.host")
    if _DOCKER.search(command):
        caps.add("docker.socket")
    if is_mutating_shell(command):
        caps.add("fs.workspace.write")
    else:
        caps.add("fs.workspace.read")
    return tuple(sorted(caps))


def tool_capabilities(tool: str) -> tuple[str, ...]:
    """Capabilities for a non-shell Agent tool call."""
    if tool == "git_push":
        return ("process.execute", "git.publish", "net.outbound:any")
    if tool in {"git_fetch", "git_pull"}:
        return ("process.execute", "git.local", "net.outbound:any")
    if tool.startswith("git_"):
        return ("process.execute", "git.local")
    if tool == "run_runbook":
        return ("process.execute", "process.children", "fs.workspace.write")
    if tool == "computer":
        return ("computer.control",)
    return ("process.execute",)


# Regex-policy reason → intent risk class (drives `autonomous` approvals and
# approval payloads). Keep ordered: earlier entries win when several match.
_REASON_RISK = {
    "Privilege elevation": "privilege",
    "External publication": "publication",
    "Credential access": "sensitive",
    "Sensitive file access": "sensitive",
    "Sensitive text entry": "sensitive",
    "Provider safety check": "sensitive",
    "Data transmission": "external",
    "Form submission": "external",
    "Unknown target application": "external",
    "Outside selected project": "outside_root",
}
# Risk classes that `autonomous` mode may never auto-approve (publication and
# external submits stay explicit; privilege/credential/sensitive too).
AUTONOMOUS_BLOCKED_RISKS = frozenset(
    {"privilege", "sensitive", "publication", "external", "outside_root", "delegation", "destructive"}
)
# Capabilities the host process exercises itself, never inside a sandboxed spawn.
# A sandbox profile neither grants nor limits them, so the envelope check skips
# them; capability denies and the remembered-rule precedence still apply.
HOST_MEDIATED_CAPABILITIES = frozenset({"computer.control"})
# Capabilities remembered allow rules may never imply by side effect.
UNGRANTABLE_CAPABILITIES = frozenset(
    {
        "privilege.elevate",
        "process.host_signal",
        "docker.socket",
        "device.access",
        "credentials.host",
        "broker.control",
    }
)


# A task worktree shares its `.git` directory with the base checkout, so
# commands that rewrite refs, prune objects or change repository config can
# damage the user's main checkout. `reset --hard` and `clean -f` discard work.
_SHARED_GIT = re.compile(
    r"(?i)(?:^|[\s;&|(])git\s+(?:-[^\s]+\s+)*(?:"
    r"branch\s+(?:[^;&|\n]*\s)?-[a-z]*[dmcf][a-z]*|tag\s+(?:[^;&|\n]*\s)?-[a-z]*[df][a-z]*|"
    r"update-ref|symbolic-ref|gc|prune|reflog|worktree|remote|config|filter-branch|replace|"
    r"reset\s+[^;&|\n]*--hard|clean\s+[^;&|\n]*-[a-z]*f[a-z]*)\b"
)
_GIT_REDIRECT = re.compile(r"(?i)\bgit\s+(?:-[^\s]+\s+)*(?:-C|--git-dir|--work-tree)\b")
_TRAVERSAL = re.compile(r"(?:^|[\s;&|=])\.\.(?:[/\\]|\s|$|[;&|])")
_HOME_OR_VAR = re.compile(r"(?:^|[\s;&|=\"'])(?:~|\$\{?[A-Za-z_]+\}?)")


def shell_hazard(command: str, cwd: str) -> str | None:
    """Risk class for a shell command that reaches beyond the task worktree.

    Heuristic like the rest of this module: it only decides whether Auto may
    approve on the user's behalf; it never grants anything.
    """
    if _TRAVERSAL.search(command) or _HOME_OR_VAR.search(command):
        return "outside_root"
    try:
        shlex.split(command, posix=True)
    except ValueError:
        return "outside_root"
    if _GIT_REDIRECT.search(command):
        return "outside_root"
    if _outside_paths(command, Path(cwd or ".").resolve()):
        return "outside_root"
    if _SHARED_GIT.search(command):
        return "destructive"
    return None


def risk_class_for_reason(reason: str) -> str:
    return _REASON_RISK.get(reason, "consequential" if reason else "safe")


def intent_risk(intent: PolicyIntent, base_reason: str | None = None, *, approval_required: bool = False) -> str:
    if base_reason:
        risk = _REASON_RISK.get(base_reason)
        if risk:
            return risk
    if intent.tool == "run_shell":
        hazard = shell_hazard(intent.display, intent.cwd)
        if hazard:
            return hazard
    if intent.risk_class != "safe":
        return intent.risk_class
    if _PUBLISH.search(intent.display) or intent.tool == "git_push":
        return "publication"
    if _EXTERNAL_SUBMIT.search(intent.display):
        return "external"
    if _PRIVILEGE.search(intent.display):
        return "privilege"
    if _CREDENTIAL.search(intent.display):
        return "sensitive"
    if approval_required or is_mutating_shell(intent.display):
        return "consequential"
    return "safe"
