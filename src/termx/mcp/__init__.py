"""MCP connectivity for TermX (tech-specs §5).

``~/.agents/mcp/<id>.json`` files declare connections — no secrets in files
(``secret_refs`` point at CredentialStore keys). A connection must be
``trust=trusted`` before TermX spawns its process or opens its socket;
declaring a URL/command is not consent.
"""

from .client import McpPool
from .defs import ConnectionDef, ConnectionError_, load_connection_file, validate_connection
from .oauth import CredentialTokenStorage, LoopbackCallback
from .ssrf import SSRFError, validate_url

__all__ = [
    "ConnectionDef",
    "ConnectionError_",
    "CredentialTokenStorage",
    "LoopbackCallback",
    "McpPool",
    "SSRFError",
    "load_connection_file",
    "validate_connection",
    "validate_url",
]
