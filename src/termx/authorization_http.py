"""Managed host access administration and explicit local recovery."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from termx.auth import extract_passcode
from termx.audit import log_event, read_events
from termx.authorization import ROLES
from termx.identity import Identity, LoginLimited
from termx.identity_http import ACCESS_COOKIE, loopback, same_origin


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class PrincipalInput(Input):
    name: str = Field(min_length=1, max_length=128)
    password: str | None = Field(default=None, min_length=12, max_length=1024)
    role: Literal['admin', 'operator', 'viewer'] = 'viewer'
    trusted_execution: bool = False


class RoleInput(Input):
    role: Literal['admin', 'operator', 'viewer']
    trusted_execution: bool = False


class GrantInput(Input):
    scopes: list[str] = Field(max_length=32)
    expires: float | None = None


class BindingInput(Input):
    issuer: str = Field(min_length=1, max_length=1024)
    subject: str = Field(min_length=1, max_length=1024)


class RecoveryInput(Input):
    recovery_code: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=12, max_length=1024)


class AdapterInput(Input):
    enabled: bool
    session_policy: Literal['expire', 'revoke'] = 'revoke'


class AdapterConfigurationInput(Input):
    configuration: dict
    session_policy: Literal['expire', 'revoke'] = 'revoke'


class AdapterTestInput(Input):
    configuration: dict
    adapter_id: str = Field(max_length=64)
    evidence: dict[str,str] = Field(default_factory=dict)
    action: str = 'machine-view'
    project_id: str | None = None


class OrganizationInput(Input):
    label: str = Field(min_length=1,max_length=128)


class GroupInput(OrganizationInput):
    identifier: str | None = Field(default=None,max_length=64)
    revision: int | None = Field(default=None,ge=1)
    organization_id: str | None = Field(default=None,max_length=64)
    role: Literal['viewer','operator','admin'] = 'viewer'
    trusted_execution: bool = False


class GroupMemberInput(Input):
    principal_id: str = Field(min_length=1,max_length=64)
    present: bool
    expires: float | None = None


class GroupMappingInput(Input):
    issuer: str = Field(min_length=1,max_length=1024)
    claim_value: str = Field(min_length=1,max_length=256)
    present: bool


class AuditRetentionInput(Input):
    retention_days: int = Field(strict=True,ge=1,le=3650)
    max_bytes: int = Field(strict=True,ge=65536,le=64*1024*1024)
    max_events: int = Field(strict=True,ge=100,le=100000)


class SessionPolicyInput(Input):
    revision: int = Field(strict=True,ge=1)
    idle_ttl_seconds: int = Field(strict=True,ge=600,le=30*86400)
    absolute_ttl_seconds: int = Field(strict=True,ge=600,le=365*86400)


def mount_authorization(app, state):
    router = APIRouter(prefix='/auth')
    identity, authz = state.identity, state.authorization
    from termx.identity_lifecycle import AdapterLifecycle
    lifecycle=AdapterLifecycle(identity,authz)
    state.identity_lifecycle=lifecycle

    def credential(request):
        return extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)

    def current(request, *, admin=True):
        if request.url.scheme != 'https' and (not request.client or not loopback(request.client.host)):
            raise HTTPException(403, 'TLS required')
        raw = credential(request)
        session = identity.resolve(raw)
        if not session:
            raise HTTPException(401, 'managed sign in required')
        if admin:
            authz.require(raw, 'host-admin')
        if request.method not in {'GET', 'HEAD'} and not request.headers.get('authorization'):
            if not same_origin(request.headers.get('origin'), request.url.scheme, request.headers.get('host','')) or not identity.valid_csrf(raw, request.headers.get('x-termx-csrf')):
                raise HTTPException(403, 'invalid same-origin CSRF request')
        return session

    @router.get('/access')
    def access(request: Request, response: Response):
        session = current(request, admin=False)
        response.headers['Cache-Control'] = 'no-store'
        return {'role': authz.role(session.principal.id), 'projects': authz.project_grants(session.principal.id),
                'runtime_boundary': 'trusted-shared-machine', 'tenant_isolation': False,
                'policy_version': session.principal.policy_version}

    @router.get('/admin/principals')
    def principals(request: Request):
        current(request)
        return authz.list_principals()

    @router.get('/admin/session-policy')
    def session_policy(request:Request,response:Response):
        current(request)
        response.headers['Cache-Control']='no-store'
        return identity.session_policy.inventory()

    @router.put('/admin/session-policy')
    def session_policy_update(request:Request,body:SessionPolicyInput):
        try:
            with identity._lock:
                actor=current(request)
                return identity.session_policy.update(**body.model_dump(),actor_id=actor.principal.id)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @router.get('/admin/groups')
    def groups(request:Request):
        current(request)
        return identity.groups.inventory()

    def group_call(request,fn,*args,**kwargs):
        try:
            with identity._lock:
                current(request)
                return fn(*args,**kwargs)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc

    @router.post('/admin/organizations')
    def organization(request:Request,body:OrganizationInput):
        actor=current(request)
        return group_call(request,identity.groups.organization,body.label,actor.principal.id)

    @router.post('/admin/groups')
    def save_group(request:Request,body:GroupInput):
        actor=current(request)
        return group_call(request,identity.groups.save,**body.model_dump(),actor_id=actor.principal.id)

    @router.put('/admin/groups/{group_id}/members')
    def group_member(group_id:str,request:Request,body:GroupMemberInput):
        actor=current(request)
        group_call(request,identity.groups.member,group_id,**body.model_dump(),actor_id=actor.principal.id)
        return {'ok':True,'affected_sessions_revoked':True}

    @router.put('/admin/groups/{group_id}/mappings')
    def group_mapping(group_id:str,request:Request,body:GroupMappingInput):
        actor=current(request)
        group_call(request,identity.groups.mapping,group_id,**body.model_dump(),actor_id=actor.principal.id)
        return {'ok':True}

    @router.put('/admin/groups/{group_id}/projects/{project_id}')
    def group_project(group_id:str,project_id:str,request:Request,body:GrantInput):
        actor=current(request);state.projects.project(project_id)
        group_call(request,identity.groups.grant,group_id,project_id,**body.model_dump(),actor_id=actor.principal.id)
        return {'ok':True,'affected_sessions_revoked':True}

    @router.post('/admin/principals')
    async def create_principal(body: PrincipalInput, request: Request):
        actor = current(request)
        try:
            if body.password:
                principal = await asyncio.to_thread(identity.create_local_user, body.name, body.password, list(ROLES[body.role]))
            else:
                principal = identity.create_principal(body.name, list(ROLES[body.role]))
            authz.set_role(principal.id, body.role, trusted_execution=body.trusted_execution, actor_id=actor.principal.id)
            return asdict(principal)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc

    @router.put('/admin/principals/{principal_id}/role')
    def role(principal_id: str, body: RoleInput, request: Request):
        actor = current(request)
        try:
            authz.set_role(principal_id,body.role,trusted_execution=body.trusted_execution,actor_id=actor.principal.id)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        return {'ok':True}

    @router.delete('/admin/principals/{principal_id}')
    def disable(principal_id: str, request: Request):
        actor = current(request)
        if authz.role(principal_id) == 'owner':
            raise HTTPException(409,'owner cannot be disabled')
        identity.disable(principal_id)
        log_event('auth_principal_disabled',principal_id=principal_id,actor_id=actor.principal.id)
        return {'ok':True}

    @router.post('/admin/principals/{principal_id}/bindings')
    def binding(principal_id: str, body: BindingInput, request: Request):
        actor = current(request)
        try:
            identity.map_identity(Identity(body.issuer,body.subject,'admin-binding'),principal_id)
        except (ValueError, __import__('sqlite3').IntegrityError) as exc:
            raise HTTPException(400,'identity binding unavailable') from exc
        log_event('auth_identity_bound', principal_id=principal_id, actor_id=actor.principal.id)
        return {'ok':True}

    @router.put('/admin/principals/{principal_id}/projects/{project_id}')
    def grant(principal_id: str, project_id: str, body: GrantInput, request: Request):
        actor = current(request)
        state.projects.project(project_id)
        try:
            authz.grant_project(principal_id,project_id,body.scopes,expires=body.expires,actor_id=actor.principal.id)
        except ValueError as exc:
            raise HTTPException(400,str(exc)) from exc
        return {'ok':True}

    @router.delete('/admin/principals/{principal_id}/projects/{project_id}')
    def revoke_grant(principal_id: str, project_id: str, request: Request):
        actor = current(request)
        authz.revoke_project(principal_id,project_id,actor_id=actor.principal.id)
        return {'ok':True}

    @router.get('/admin/principals/{principal_id}/sessions')
    def sessions(principal_id: str, request: Request):
        current(request)
        return identity.list_sessions(principal_id)

    @router.delete('/admin/principals/{principal_id}/sessions')
    def revoke_sessions(principal_id: str, request: Request):
        current(request)
        return {'revoked':identity.revoke_all(principal_id)}

    @router.post('/admin/recovery-code')
    def recovery_code(request: Request, response: Response):
        actor = current(request)
        if authz.role(actor.principal.id) != 'owner':
            raise HTTPException(403,'only the owner can provision recovery')
        response.headers['Cache-Control']='no-store'
        return {'recovery_code':authz.new_recovery_code(actor_id=actor.principal.id)}

    @router.post('/recovery')
    async def recover(body: RecoveryInput, request: Request):
        if not request.client or not loopback(request.client.host) or not same_origin(request.headers.get('origin'),request.url.scheme,request.headers.get('host','')):
            raise HTTPException(403,'recovery requires local same-origin host access')
        try:
            identity._attempt(request.client.host)
            await asyncio.to_thread(authz.recover_owner,body.recovery_code,body.password)
        except LoginLimited as exc:
            raise HTTPException(429,'too many recovery attempts',headers={'Retry-After':'60'}) from exc
        except ValueError as exc:
            raise HTTPException(401,'recovery failed') from exc
        return {'ok':True, 'sign_in_required':True}

    @router.post('/admin/migration/end')
    def end_migration(request: Request):
        current(request)
        identity.end_legacy_migration()
        return {'ok':True}

    @router.get('/admin/adapters/configuration')
    def configuration(request: Request, response: Response):
        current(request)
        response.headers['Cache-Control']='no-store'
        return lifecycle.configuration()

    @router.post('/admin/adapters/configuration/test')
    async def test_configuration(body: AdapterTestInput, request: Request):
        actor=current(request)
        return await lifecycle.test(actor,body.configuration,body.adapter_id,body.evidence,
                                    action=body.action,project_id=body.project_id)

    @router.post('/admin/adapters/configuration/begin')
    async def begin_configuration(body: AdapterTestInput, request: Request, response: Response):
        actor=current(request)
        candidate=next((a for a in lifecycle.prepare(body.configuration) if a.id==body.adapter_id),None)
        expected=str(request.base_url).rstrip('/')+f'/auth/oidc/{body.adapter_id}/callback'
        if not candidate or not hasattr(candidate,'config') or candidate.config.redirect_uri!=expected:
            raise HTTPException(400,'use the host identity callback URI')
        url,binding=await lifecycle.begin(actor,body.configuration,body.adapter_id)
        response.set_cookie(f'termx_oidc_{body.adapter_id}',binding,max_age=300,httponly=True,
                            secure=request.url.scheme=='https',samesite='lax',path=f'/auth/oidc/{body.adapter_id}')
        response.headers['Cache-Control']='no-store'
        return {'authorization_url':url,'test_only':True}

    @router.put('/admin/adapters/configuration')
    def activate_configuration(body: AdapterConfigurationInput, request: Request):
        actor=current(request)
        return lifecycle.activate(actor,body.configuration,session_policy=body.session_policy)

    @router.get('/admin/adapters')
    def adapters(request: Request):
        current(request)
        with identity._db() as db:
            statuses={r['adapter_id']:dict(r) for r in db.execute('SELECT * FROM adapter_status')}
        return [{'id':a.id,'label':a.label,**statuses.get(a.id,{'enabled':True,'version':1,'health':'unknown','checked':None})} for a in identity.adapters.values()]

    @router.post('/admin/adapters/{adapter_id}/health')
    async def health(adapter_id: str, request: Request):
        current(request)
        adapter=identity.adapters.get(adapter_id)
        if not adapter:
            raise HTTPException(404,'adapter not found')
        from time import time
        try:
            if hasattr(adapter,'_metadata'):
                await adapter._metadata()
            result='healthy'
        except Exception:
            result='unavailable'
        with identity._db() as db:
            db.execute("INSERT INTO adapter_status (adapter_id,health,checked) VALUES (?,?,?) ON CONFLICT(adapter_id) DO UPDATE SET health=excluded.health,checked=excluded.checked",(adapter_id,result,time()))
        log_event('auth_adapter_health',adapter_id=adapter_id,health=result)
        return {'health':result,'login_verified':False}

    @router.put('/admin/adapters/{adapter_id}')
    def adapter_state(adapter_id: str, body: AdapterInput, request: Request):
        current(request)
        if adapter_id not in identity.adapters:
            raise HTTPException(404,'adapter not found')
        with identity._db() as db:
            enabled={a for a in identity.adapters if not (r:=db.execute('SELECT enabled FROM adapter_status WHERE adapter_id=?',(a,)).fetchone()) or r[0]}
            if not body.enabled and len(enabled-{adapter_id}) == 0:
                raise HTTPException(409,'cannot disable the last sign-in method')
            if body.enabled and adapter_id != 'local-password':
                row=db.execute('SELECT health FROM adapter_status WHERE adapter_id=?',(adapter_id,)).fetchone()
                if not row or row[0]!='healthy':
                    raise HTTPException(409,'test adapter health before activation')
            db.execute('INSERT INTO adapter_status (adapter_id,enabled,session_policy) VALUES (?,?,?) ON CONFLICT(adapter_id) DO UPDATE SET enabled=excluded.enabled,session_policy=excluded.session_policy,version=version+1',
                       (adapter_id,int(body.enabled),body.session_policy))
            if not body.enabled and body.session_policy=='revoke':
                # Sessions retain provider provenance, never trust merely a
                # strength label shared by independent configured providers.
                if 'adapter_id' in {r['name'] for r in db.execute('PRAGMA table_info(sessions)')}:
                    db.execute('UPDATE sessions SET revoked=1 WHERE adapter_id=?',(adapter_id,))
        log_event('auth_adapter_updated',adapter_id=adapter_id,enabled=body.enabled,session_policy=body.session_policy)
        return {'ok':True}

    @router.get('/admin/audit')
    def audit(request: Request, limit: int=100):
        current(request)
        return read_events(min(max(limit,1),1000))

    @router.get('/admin/audit/retention')
    def audit_retention(request:Request):
        current(request)
        from termx.audit import retention_policy
        return retention_policy()

    @router.put('/admin/audit/retention')
    def audit_retention_save(request:Request,body:AuditRetentionInput):
        from termx.audit import set_retention_policy,prune_events
        with identity._lock:
            actor=current(request)
            policy=set_retention_policy(body.model_dump())
            result=prune_events(policy=policy)
            log_event('audit_retention_changed',actor_id=actor.principal.id,**policy,removed_events=result['removed_events'])
        return result

    @router.post('/admin/audit/prune')
    def audit_prune(request:Request):
        from termx.audit import prune_events
        with identity._lock:
            actor=current(request)
            result=prune_events()
            log_event('audit_retention_pruned',actor_id=actor.principal.id,removed_events=result['removed_events'])
        return result

    app.include_router(router)
    app.state.authorization_mounted = True
