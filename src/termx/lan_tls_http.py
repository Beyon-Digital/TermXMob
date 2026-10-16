"""Local-TLS configuration is staged, applied only on the next host start."""
from __future__ import annotations
import json
from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, Field
from termx.config import atomic_write, config_dir
from termx.host_instance import private_owned
from termx.lan_tls import supported_name, advertised_names

class LanSettings(BaseModel):
    enabled: bool = False
    host: str = '0.0.0.0'
    port: int = Field(default=8788, ge=1, le=65535)
    names: list[str] = Field(default_factory=list, max_length=32)


def mount_lan_tls(app, state):
    def admin(request):
        from termx.identity_http import ACCESS_COOKIE
        from termx.auth import extract_passcode
        token = extract_passcode(request.headers.get('x-termx-passcode'), request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        actor = state.identity.resolve(token)
        if not actor or 'host-admin' not in actor.principal.scopes:
            raise HTTPException(403, 'Host administrator sign-in required')
        return token

    def settings():
        path = config_dir() / 'lan-tls-settings.json'
        if not path.exists():
            return LanSettings().model_dump()
        private_owned(path)
        return LanSettings.model_validate_json(path.read_text()).model_dump()

    @app.get('/auth/lan-tls')
    def status(request: Request, response: Response):
        admin(request)
        response.headers['Cache-Control'] = 'no-store'
        identity = getattr(state, 'lan_identity', None)
        return {'configuration': settings(), 'active': identity.metadata(state.lan_port) if identity else {'enabled': False}, 'restart_required': True}

    @app.put('/auth/lan-tls')
    def configure(body: LanSettings, request: Request, response: Response):
        token = admin(request)
        from termx.identity_http import same_origin
        if not request.headers.get('authorization') and (not same_origin(request.headers.get('origin'), request.url.scheme, request.headers.get('host','')) or not state.identity.valid_csrf(token, request.headers.get('x-termx-csrf'))):
            raise HTTPException(403, 'Same-origin request and CSRF token required')
        if body.host not in {'0.0.0.0', '::'}:
            try:
                body.host = supported_name(body.host)
                import ipaddress
                ipaddress.ip_address(body.host)
            except ValueError as exc:
                raise HTTPException(400, 'Bind address must be a private IP, 0.0.0.0 or ::') from exc
        try:
            body.names = sorted({supported_name(name) for name in body.names})
            advertised_names(body.host, body.names)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        path = config_dir() / 'lan-tls-settings.json'
        if path.exists() or path.is_symlink():
            private_owned(path)
        atomic_write(path, json.dumps(body.model_dump()))
        response.headers['Cache-Control'] = 'no-store'
        return {'configuration': body.model_dump(), 'restart_required': True, 'message': 'Saved. Restart the host to apply LAN exposure changes. No trust root was installed.'}

    @app.get('/auth/lan-tls/ca.pem')
    def ca_certificate(request: Request):
        admin(request)
        identity = getattr(state, 'lan_identity', None)
        if identity is None:
            raise HTTPException(409, 'Enable LAN TLS and restart this host first')
        from cryptography.hazmat.primitives.serialization import Encoding
        return Response(identity.ca.public_bytes(Encoding.PEM), media_type='application/x-pem-file', headers={'Cache-Control': 'no-store', 'Content-Disposition': 'attachment; filename="termx-lan-ca.pem"'})
