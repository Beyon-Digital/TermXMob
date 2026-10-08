from __future__ import annotations

import asyncio
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_durable_workspace import workspace
from termx.authorization import AuthorizationService
from termx.development.delivery import DeliveryService
from termx.project_files import ProjectFiles
from termx.workspace.http import mount_workspace


def setup(workspace):
    service,owner,path=workspace
    root=path/'project';root.mkdir()
    def git(*args):
        return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True,text=True).stdout
    git('init','-b','main');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (root/'base.txt').write_text('Needle exists in content, not filename\n')
    git('add','.');git('commit','-m','Fixture')
    project={'id':'project-a','name':'Fixture','path':str(root)}
    service.state.projects=SimpleNamespace(project=lambda identifier:project if identifier=='project-a' else (_ for _ in ()).throw(KeyError(identifier)),projects=lambda:[project],lock=threading.RLock())
    service.state.authorization=AuthorizationService(service.state.identity)
    service.state.delivery=DeliveryService(path/'delivery')
    tree=service.state.delivery.effect('project-a',str(root),'worktree-create',{'branch':'codex/quick-file','base':'main'})
    (root/'main-only.txt').write_text('Main source')
    (Path(tree['path'])/'isolated-only.txt').write_text('Isolated source')
    row=service.create_session(owner,project_id='project-a',worktree_id=tree['id'],provider_id='fixture',model='fixture')
    service.state.workspace=service
    app=FastAPI();mount_workspace(app,service.state)
    auth=asyncio.run(service.state.identity.login('local-password',{'username':'owner','password':'strong fixture password'},peer='fixture'))
    client=TestClient(app,headers={'Authorization':'Bearer '+auth.access_token})
    url='/api/workspace/sessions/'+row['id']+'/files'
    return service,owner,root,tree,row,client,url


def test_filename_search_is_checkout_bound_and_never_searches_content(workspace):
    service,owner,root,tree,row,client,url=setup(workspace)
    args={'operation':'quick-open','worktree_id':tree['id']}
    result=client.get(url,params={**args,'query':'only'});assert result.status_code==200
    assert [item['path'] for item in result.json()['results']]==['isolated-only.txt']
    assert client.get(url,params={**args,'query':'Needle'}).json()['results']==[]
    assert client.get(url,params={**args,'query':'x'*201}).status_code==400
    assert client.get(url,params={'operation':'quick-open','query':'main-only'}).status_code==409
    selected=client.get(url,params={'operation':'read','path':'isolated-only.txt','worktree_id':tree['id']})
    assert selected.status_code==200 and selected.json()['content']=='Isolated source'
    assert (root/'main-only.txt').read_text()=='Main source'


def test_filename_search_excludes_outside_symlink_and_revalidates_authority_before_publish(workspace,monkeypatch):
    service,owner,root,tree,row,client,url=setup(workspace)
    checkout=Path(tree['path']);outside=root.parent/'outside-secret.txt';outside.write_text('secret')
    (checkout/'outside-link.txt').symlink_to(outside)
    args={'operation':'quick-open','worktree_id':tree['id']}
    response=client.get(url,params={**args,'query':'outside'});assert response.status_code==200
    assert response.json()['results']==[]
    original=ProjectFiles.search
    def search_then_revoke(*args,**kwargs):
        result=original(*args,**kwargs)
        service.state.identity.set_scopes(owner.id,['machine-view'])
        return result
    monkeypatch.setattr(ProjectFiles,'search',search_then_revoke)
    response=client.get(url,params={**args,'query':'only'})
    assert response.status_code==403 and 'results' not in response.json()
