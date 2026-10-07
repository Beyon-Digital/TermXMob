from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from termx.auth import Auth
from termx.authorization import AuthorizationService
from termx.workspace.project_pins import ProjectPins, mount_project_pins
from termx.workspace.store import Conflict, WorkspaceStore
from test_durable_workspace import workspace


def projects(service):
    available={'project-a':{'id':'project-a'},'project-b':{'id':'project-b'}}
    def project(identifier):
        return available[identifier]
    service.state.projects=SimpleNamespace(project=project)
    service.state.authorization=AuthorizationService(service.state.identity,Auth(identity=service.state.identity))
    return available


def test_pins_survive_reopen_use_exact_revision_and_never_edit_project_or_session(workspace):
    service,owner,path=workspace;available=projects(service);pins=ProjectPins(service)
    assert pins.read(owner)=={'revision':0,'projects':[]}
    first=pins.set(owner,'project-a',True,0)
    with pytest.raises(Conflict):pins.set(owner,'project-b',True,0)
    second=pins.set(owner,'project-b',True,first['revision'])
    reopened=WorkspaceStore(path/'workspace.sqlite3')
    try:
        assert reopened.get('project_pins',owner.id)['projects']==['project-a','project-b']
        assert reopened.list('conversation')==[] and reopened.list('task')==[]
    finally:reopened.close()
    assert pins.set(owner,'project-a',False,second['revision'])['projects']==['project-b']
    assert available=={'project-a':{'id':'project-a'},'project-b':{'id':'project-b'}}


def test_pins_are_owner_scoped_and_hide_or_refuse_revoked_project_access(workspace):
    service,owner,_=workspace;projects(service);pins=ProjectPins(service)
    viewer=service.state.identity.create_principal('Viewer',['files-read'])
    service.state.authorization.set_role(viewer.id,'viewer')
    service.state.authorization.grant_project(viewer.id,'project-a',['files-read'])
    pins.set(owner,'project-b',True,0)
    assert pins.read(viewer)=={'revision':0,'projects':[]}
    first=pins.set(viewer,'project-a',True,0)
    with pytest.raises(HTTPException) as denied:pins.set(viewer,'project-b',True,first['revision'])
    assert getattr(denied.value,'status_code',None)==403
    service.state.authorization.revoke_project(viewer.id,'project-a')
    assert pins.read(viewer)=={'revision':first['revision'],'projects':[]}
    assert pins.read(owner)['projects']==['project-b']


def test_managed_pin_routes_reject_foreign_input_and_stale_revision(workspace):
    import asyncio
    service,owner,_=workspace;projects(service)
    token=asyncio.run(service.state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='fixture')).access_token
    app=FastAPI();service.state.workspace=service;mount_project_pins(app,service.state)
    client=TestClient(app);headers={'Authorization':'Bearer '+token}
    assert client.get('/api/workspace/project-pins').status_code==401
    assert client.put('/api/workspace/project-pins',headers=headers,json={'project_id':'project-a','pinned':True,'revision':0,'owner':'someone-else'}).status_code==422
    first=client.put('/api/workspace/project-pins',headers=headers,json={'project_id':'project-a','pinned':True,'revision':0})
    assert first.status_code==200,first.text
    assert client.put('/api/workspace/project-pins',headers=headers,json={'project_id':'project-b','pinned':True,'revision':0}).status_code==409
    assert client.get('/api/workspace/project-pins',headers=headers).json()==first.json()
