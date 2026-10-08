"""MCP connection definitions (``~/.agents/mcp/<id>.json``)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..discovery.safety import safe_read_text, slugify

SCHEMA = 1


class ConnectionError_(ValueError):
    pass


@dataclass
class ConnectionDef:
    id: str                          # connection.<slug>
    slug: str
    label: str = ""
    transport: str = "http"          # stdio|http|sse
    command: list[str] = field(default_factory=list)
    url: str = ""
    enabled: bool = True
    owner: str = "user"              # user|project:<id>
    allowed_projects: list[str] = field(default_factory=list)
    auth_method: str = "none"        # none|env|oauth
    env_names: list[str] = field(default_factory=list)
    scopes: list[str] = field(default_factory=list)
    secret_refs: list[str] = field(default_factory=list)
    header_secret_refs: dict[str,str] = field(default_factory=dict)
    credential_revision: str = ''
    approved_tools: list[str] = field(default_factory=lambda: ["*"])
    trust: str = "untrusted"         # trusted|untrusted
    lan: bool = False                # SSRF: opt-in to private ranges
    client_id: str | None = None     # manual issuer-bound registration
    client_secret_ref: str | None = None
    client_metadata_url: str | None = None  # CIMD
    path: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def trusted(self) -> bool:
        return self.trust == "trusted"

    @property
    def spawnable(self) -> bool:
        """Consented to spawn/connect. Discovery alone never qualifies."""
        return self.trusted

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "transport": self.transport,
            "command": list(self.command),
            "url": self.url,
            "enabled": self.enabled,
            "owner": self.owner,
            "allowed_projects": list(self.allowed_projects),
            "auth": {
                "method": self.auth_method,
                "env_names": list(self.env_names),
                "scopes": list(self.scopes),
            },
            "secret_refs": list(self.secret_refs),
            "header_secret_refs": dict(self.header_secret_refs),
            "credential_revision":self.credential_revision,
            "approved_tools": list(self.approved_tools),
            "trust": self.trust,
            "lan": self.lan,
            "client_id": self.client_id,
            "client_secret_ref":self.client_secret_ref,
            "client_metadata_url": self.client_metadata_url,
            "path": self.path,
        }


def validate_connection(data: dict[str, Any], *, slug_hint: str = "") -> ConnectionDef:
    if not isinstance(data, dict):
        raise ConnectionError_("connection must be a JSON object")
    slug = slugify(str(data.get("id") or data.get("label") or slug_hint).removeprefix("connection."))
    transport = str(data.get("transport") or ("stdio" if data.get("command") else "http"))
    if transport not in {"stdio", "http", "sse"}:
        raise ConnectionError_(f"transport must be stdio|http|sse, got {transport!r}")
    command = [str(c) for c in (data.get("command") or [])]
    url = str(data.get("url") or "")
    if transport == "stdio" and not command:
        raise ConnectionError_("stdio connection requires a command")
    if transport in {"http", "sse"} and not url:
        raise ConnectionError_(f"{transport} connection requires a url")
    if transport in {"http", "sse"} and not url.startswith(("http://", "https://")):
        raise ConnectionError_("url must be http(s)")
    auth = data.get("auth") or {}
    if not isinstance(auth, dict):
        raise ConnectionError_("auth must be an object")
    method = str(auth.get("method") or "none")
    if method not in {"none", "env", "oauth", "manual"}:
        raise ConnectionError_("auth.method must be none|env|oauth|manual")
    trust = str(data.get("trust") or "untrusted")
    if trust not in {"trusted", "untrusted"}:
        raise ConnectionError_("trust must be trusted|untrusted")
    secret_refs = [str(s) for s in (data.get("secret_refs") or [])]
    for ref in secret_refs:
        if not re.fullmatch(r'mcp\.[A-Za-z0-9_.:-]{1,250}',ref):
            raise ConnectionError_(f"secret_ref '{ref}' must live under 'mcp.' namespace")
    client_secret_ref=data.get('client_secret_ref')
    if client_secret_ref is not None and (not isinstance(client_secret_ref,str) or not re.fullmatch(r'mcp\.[A-Za-z0-9_.:-]{1,250}',client_secret_ref)):
        raise ConnectionError_('client_secret_ref must be an MCP credential reference')
    header_refs=data.get('header_secret_refs') or {}
    if not isinstance(header_refs,dict) or len(header_refs)>16 or any(not isinstance(k,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}',k) or k.lower() in {'host','connection','content-length','cookie','origin','referer','transfer-encoding'} or not isinstance(v,str) or not v.startswith('mcp.') for k,v in header_refs.items()):
        raise ConnectionError_('header_secret_refs must contain safe header names and MCP credential references')
    owner = data.get('owner') or 'user'
    if not isinstance(owner,str) or not (owner == 'user' or re.fullmatch(r'project:[A-Za-z0-9_.:-]{1,256}',owner)):
        raise ConnectionError_('owner must be user or project:<enrolled project id>')
    projects = data.get('allowed_projects', [])
    if not isinstance(projects,list) or len(projects)>128 or any(not isinstance(p,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}',p) for p in projects):
        raise ConnectionError_('allowed_projects must be a bounded list of project IDs')
    projects = sorted(set(projects))
    if owner.startswith('project:'):
        project = owner.removeprefix('project:')
        if projects and projects != [project]:
            raise ConnectionError_('project owner conflicts with allowed_projects')
        projects = [project]
    approved=data.get('approved_tools',['*'])
    if not isinstance(approved,list) or len(approved)>512 or any(not isinstance(t,str) or not t or len(t)>200 for t in approved):
        raise ConnectionError_('approved_tools must be a bounded list of tool names')
    return ConnectionDef(
        id=f"connection.{slug}",
        slug=slug,
        label=str(data.get("label") or slug),
        transport=transport,
        command=command,
        url=url,
        enabled=bool(data.get("enabled", True)),
        owner=owner,
        allowed_projects=projects,
        auth_method="oauth" if method == "manual" else method,
        env_names=[str(e) for e in (auth.get("env_names") or [])],
        scopes=[str(s) for s in (auth.get("scopes") or [])],
        secret_refs=secret_refs,
        header_secret_refs=dict(header_refs),
        credential_revision=str(data.get('credential_revision') or '')[:128],
        approved_tools=list(approved),
        trust=trust,
        lan=bool(data.get("lan", False)),
        client_id=(data.get("client_id") or (auth.get("client_id") if method == "manual" else None)),
        client_secret_ref=client_secret_ref,
        client_metadata_url=data.get("client_metadata_url"),
        created_at=float(data.get("created_at") or 0),
        updated_at=float(data.get("updated_at") or 0),
    )


def load_connection_file(path: str | Path) -> ConnectionDef:
    text = safe_read_text(str(path))
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConnectionError_(f"invalid JSON: {exc}") from exc
    conn = validate_connection(data, slug_hint=Path(path).stem)
    conn.path = str(path)
    return conn
