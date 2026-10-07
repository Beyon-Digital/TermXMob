"""Session-bound host presets; selection never grants tool or execution authority."""
from __future__ import annotations
import hashlib,json
from fastapi import HTTPException


def resolve_preset(workspace,principal,identifier,*,engine='internal',project_id=None,runner_id=None,workflow=None):
    if identifier is None:return None
    if not isinstance(identifier,str) or not identifier or len(identifier)>200:
        raise ValueError('Choose a valid agent preset ID')
    profile=workspace.agents.get_custom_agent(identifier)
    if not profile:raise ValueError('Agent preset is unavailable')
    file=profile.get('file') or {}
    extra=file.get('x_termx_extra') or {}
    metadata=[profile,file,extra]
    declared_owner=next((item.get('owner_id') or item.get('owner') or item.get('principal_id') for item in metadata if isinstance(item,dict) and (item.get('owner_id') or item.get('owner') or item.get('principal_id'))),None)
    declared_project=next((item.get('project_id') for item in metadata if isinstance(item,dict) and item.get('project_id')),None)
    if declared_owner and declared_owner!=principal.id:raise PermissionError('Agent preset belongs to another principal')
    if declared_project and declared_project!=project_id:raise PermissionError('Agent preset belongs to another project')
    authz=getattr(workspace.state,'authorization',None)
    if authz:
        owner=authz.resource_owner('custom_agent',identifier)
        if owner:
            if owner.get('principal_id')!=principal.id:raise PermissionError('Agent preset belongs to another principal')
            if owner.get('project_id') and owner['project_id']!=project_id:raise PermissionError('Agent preset belongs to another project')
            authz.require_principal(principal,'agent-view',resource_kind='custom_agent',resource_id=identifier)
        else:
            # Pre-existing enabled host-installed definitions are shared, not
            # assigned a fabricated owner. New UI-created profiles are claimed.
            authz.require_creation_principal(principal,'agent-view')
            if profile.get('source') not in {'device','bundled'}:raise PermissionError('Agent preset is not an approved shared host definition')
    if not profile.get('enabled',True) or profile.get('sync_state') in {'missing','error','invalid','deleted'}:
        raise ValueError('Agent preset is disabled or unavailable')
    profile_engine=profile.get('engine') or file.get('engine') or 'inherit'
    if profile_engine not in {'inherit',engine}:
        raise ValueError('Agent preset requires a different engine; create a conversation using its engine')
    if engine!='internal' and engine not in workspace.state.engines.engines():raise ValueError('Agent preset engine is not installed')
    if runner_id:
        from termx.runners.worker import TOOLS
        if engine!='internal' or file.get('skills') or file.get('config_options'):
            raise ValueError('This preset uses native skills or settings unavailable in the isolated internal runner')
        if not set(profile.get('tools') or []).issubset(TOOLS):
            raise ValueError('This preset requests tools outside the qualified runner registry')
    if workflow=='browser' and (profile.get('tools') or file.get('tools') or file.get('skills')):
        raise ValueError('This preset requests tools unavailable in the broker-only browser workflow')
    config={key:profile.get(key) for key in ('id','name','instructions','provider_id','model','engine','enabled','tools','limits','approval_mode','sandbox_profile','file_revision','file')}
    profile=dict(profile);profile['workspace_revision']=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
    return profile


def available_presets(workspace,principal,*,project_id=None,engine=None,runner_id=None):
    authz=getattr(workspace.state,'authorization',None)
    if project_id:workspace.require(principal,'agent-view',project_id)
    elif authz:authz.require_creation_principal(principal,'agent-view')
    else:workspace.require(principal,'agent-view')
    rows=[]
    for candidate in workspace.agents.list_custom_agents():
        selected=engine or (candidate.get('engine') if candidate.get('engine')!='inherit' else None) or 'internal'
        try:profile=resolve_preset(workspace,principal,candidate['id'],engine=selected,project_id=project_id,runner_id=runner_id)
        except (ValueError,PermissionError,HTTPException):continue
        rows.append({key:profile.get(key) for key in ('id','name','description','engine','provider_id','model','workspace_revision')})
    return rows
