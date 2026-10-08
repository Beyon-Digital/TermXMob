"""Live-authority, immutable-scope editor for coding-tool remembered consent.

Legacy rules are administrator-authored shared-host policy. Member consent is
namespaced by principal/current policy; conversation consent additionally binds
the canonical conversation and originating managed device session.
"""
from time import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from termx.agent.policies.models import rule_public
from termx.auth import extract_passcode
from termx.identity_http import ACCESS_COOKIE


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)
    effect: str = Field(pattern='^(allow|deny)$')
    expires_in: int = Field(ge=1, le=30*86400)


class Revoke(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(ge=1)


def mount_coding_policies(app, state):
    router = APIRouter(prefix='/api/workspace/coding-policies')

    def actor(request):
        raw = extract_passcode(authorization=request.headers.get('authorization')) or request.cookies.get(ACCESS_COOKIE)
        session = state.identity.resolve(raw)
        if not session: raise HTTPException(401, 'Managed sign-in required')
        return session

    def authority(session, rule, *, write=False):
        binding = rule.get('consent_binding') or {}
        live = state.identity.session_by_id(session.session_id)
        if not live or live.principal.id != session.principal.id: raise PermissionError('Device session changed')
        principal = live.principal
        if write and (rule["revoked_at"] is not None or rule["expires_at"] is not None and rule["expires_at"] <= time()):
            raise PermissionError("Consent expired or was revoked; request fresh approval")
        if binding:
            if binding.get('principal_id') != principal.id: raise KeyError(rule['id'])
            state.workspace.require(principal, 'agent-control' if write else 'agent-view', rule.get('project_id'))
            if write:
                if binding.get('policy_version') != principal.policy_version: raise PermissionError('Policy changed; request fresh consent')
                if rule['revoked_at'] is not None or rule['expires_at'] is not None and rule['expires_at'] <= time():
                    raise PermissionError('Consent expired or was revoked; request fresh approval')
                if rule['scope_type'] == 'conversation':
                    if binding.get('session_id') != session.session_id: raise PermissionError('Conversation consent belongs to another device session')
                    state.workspace.record(principal, 'conversation', rule['scope_id'], scope='agent-control')
                if rule['scope_type'] == 'task':
                    state.workspace.record(principal, 'task', rule['scope_id'], scope='agent-control')
                    task=state.agent_store.get_task(rule['scope_id'])
                    if not task or task['status'] not in {'planning','running','awaiting_approval','paused','recovering'}:
                        raise PermissionError('Task ended; request fresh approval')
        else:
            if 'host-admin' not in principal.scopes: raise KeyError(rule['id'])
            state.workspace.require(principal, 'agent-control' if write else 'agent-view')
        return principal

    def public(session, rule):
        authority(session, rule)
        editable=True
        try: authority(session, rule, write=True)
        except PermissionError: editable=False
        return {**rule_public(rule), 'binding': rule.get('consent_binding') or {}, 'editable': editable,
                'policy_source': 'Your bounded remembered consent' if rule.get('consent_binding') else 'Administrator shared-host policy'}

    def failure(error):
        if isinstance(error, KeyError): return HTTPException(404, 'Coding policy not found')
        if isinstance(error, PermissionError): return HTTPException(403, str(error))
        if isinstance(error, ValueError): return HTTPException(409, str(error))
        raise error

    @router.get('')
    def list_rules(request:Request):
        session=actor(request); rows=[]
        for rule in state.agent_store.list_policy_rules(include_revoked=True,limit=1000):
            try: rows.append(public(session,rule))
            except (KeyError,PermissionError): continue
        return {'rules':rows,'precedence':['host restrictions and sandbox','matching deny','bounded remembered allow','engine/default policy','human decision']}

    @router.patch('/{identifier}')
    def edit(request:Request,identifier:str,body:Edit):
        session=actor(request)
        try:
            rule=state.agent_store.edit_policy_consent(identifier,version=body.version,effect=body.effect,
                expires_at=time()+body.expires_in,validate=lambda row:authority(session,row,write=True))
            return public(session,rule)
        except (KeyError,PermissionError,ValueError) as error: raise failure(error) from None

    @router.delete('/{identifier}')
    def revoke(request:Request,identifier:str,body:Revoke):
        session=actor(request)
        try:
            rule=state.agent_store.edit_policy_consent(identifier,version=body.version,revoke=True,
                validate=lambda row:authority(session,row,write=True))
            return public(session,rule)
        except (KeyError,PermissionError,ValueError) as error: raise failure(error) from None

    app.include_router(router)
