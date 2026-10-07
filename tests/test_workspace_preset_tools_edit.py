"""Preset edits preserve revision checks and explicitly distinguish all/no tools."""
from fastapi.testclient import TestClient
from _gql import data, err_status
from termx.app import AppState, create_app

FIELDS = 'id tools tools_mode file_revision limits'
CREATE = 'mutation($input:CustomAgentInput!){create_custom_agent(input:$input){'+FIELDS+'}}'
PATCH = 'mutation($id:String!,$input:CustomAgentPatchInput!){patch_custom_agent(agent_id:$id,input:$input){'+FIELDS+'}}'

def test_explicit_inherited_mode_and_stale_revision(tmp_path, monkeypatch):
    monkeypatch.setenv('TERMX_CONFIG', str(tmp_path/'config.json'))
    with TestClient(create_app(AppState(passcode='secret'),web_dir=None)) as client:
        auth={'X-Termx-Passcode':'secret'}
        row=data(client,CREATE,'create_custom_agent',{'input':{'name':'Tools editor','tools':['read_file']}},auth)
        inherited=data(client,PATCH,'patch_custom_agent',{'id':row['id'],'input':{'revision':row['file_revision'],'tools_mode':'all'}},auth)
        assert inherited['tools_mode']=='all' and inherited['tools']==[]
        assert err_status(client,PATCH,{'id':row['id'],'input':{'revision':row['file_revision'],'tools_mode':'explicit','tools':[]}},auth)==409
        assert err_status(client,PATCH,{'id':row['id'],'input':{'revision':inherited['file_revision'],'tools_mode':'all','tools':['run_shell']}},auth)==400
        narrowed=data(client,PATCH,'patch_custom_agent',{'id':row['id'],'input':{'revision':inherited['file_revision'],'tools_mode':'explicit','tools':[]}},auth)
        assert narrowed['tools_mode']=='explicit' and narrowed['tools']==[]
