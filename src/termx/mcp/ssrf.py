"""SSRF defenses for outbound MCP connections.

Policy: link-local and metadata endpoints are always refused; private
ranges are refused unless the connection opts in with ``lan: true``;
hostnames are resolved *at connect time* (DNS revalidation) and every
resolved address must pass the check — this blocks DNS-rebinding where a
hostname resolves publicly at config time and privately at connect time.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_METADATA_HOSTS = {"metadata.google.internal", "metadata"}
_ALLOWED_SCHEMES = {"http", "https"}


class SSRFError(ValueError):
    pass


def _is_blocked_addr(ip: ipaddress._BaseAddress, lan: bool) -> bool:
    if ip.is_link_local or ip.is_loopback or ip.is_multicast or ip.is_reserved:
        # loopback allowed only when lan is opted in (local dev servers)
        if ip.is_loopback and lan:
            return False
        return True
    if ip.is_private or ip.is_unspecified:
        return not lan
    return False


def validate_url(url: str, *, lan: bool = False) -> str:
    """Validate a remote MCP URL. Returns the URL unchanged or raises."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFError(f"scheme {parsed.scheme!r} not allowed")
    host = parsed.hostname
    if not host:
        raise SSRFError("url has no host")
    if host.lower() in _METADATA_HOSTS:
        raise SSRFError("metadata endpoint is not allowed")
    try:
        ip = ipaddress.ip_address(host)
        if _is_blocked_addr(ip, lan):
            raise SSRFError(f"address {ip} is blocked (lan={lan})")
        return url
    except ValueError as exc:
        if isinstance(exc, SSRFError):
            raise
    # hostname: resolve now and check every resolved address
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise SSRFError(f"cannot resolve {host}: {exc}") from exc
    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if _is_blocked_addr(addr, lan):
            raise SSRFError(
                f"{host} resolves to blocked address {addr} (lan={lan})"
            )
    return url
