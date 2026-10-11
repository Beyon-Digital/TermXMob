import asyncio
import time
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from termx.runners.cloud.azure import AzureProvider
from termx.runners.cloud.gcp import GcpProvider
from termx.runners.cloud.base import CloudError, check_credentials
from termx.runners.cloud.bootstrap import identities
from cloud_fixtures import CREDENTIALS


@pytest.mark.parametrize('kind',['azure','gcp'])
def test_authentication_uses_fixed_oauth_destinations_and_refreshes_tokens(kind):
    async def run():
        credentials={**CREDENTIALS[kind]};tokens=[];calls=[]
        if kind=='gcp':
            from cryptography.hazmat.primitives import serialization
            key=serialization.load_ssh_private_key(identities()['private'].encode(),password=None)
            credentials['private_key']=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
        def respond(request):
            calls.append(request)
            if request.method=='POST':
                values=parse_qs(request.content.decode());tokens.append(values)
                if kind=='azure':
                    assert request.url.host=='login.microsoftonline.com'
                    assert values['scope']==['https://management.azure.com/.default']
                    assert values['client_secret']==[credentials['client_secret']]
                else:
                    assert str(request.url)=='https://oauth2.googleapis.com/token'
                    claims=jwt.decode(values['assertion'][0],options={'verify_signature':False})
                    assert claims['iss']==credentials['client_email']
                    assert claims['aud']=='https://oauth2.googleapis.com/token'
                    assert claims['exp']-claims['iat']==3600
                return httpx.Response(200,json={'access_token':'token-'+str(len(tokens)),'expires_in':3600})
            assert request.headers['authorization']=='Bearer token-'+str(len(tokens))
            return httpx.Response(200,json={'subscriptionId':credentials.get('subscription_id'),'name':'fixture-project'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            adapter=(AzureProvider if kind=='azure' else GcpProvider)(credentials,client=client)
            await adapter.identity();await adapter.identity();assert len(tokens)==1
            adapter.expires=time.time()-1
            await adapter.identity();assert len(tokens)==2
            count=len(calls)
            with pytest.raises(CloudError):await adapter.api('GET','https://attacker.invalid/pagination')
            assert len(calls)==count
    asyncio.run(run())


def test_credential_input_ignores_untrusted_token_uris_and_rejects_bad_accounts():
    value=check_credentials('gcp',{**CREDENTIALS['gcp'],'token_uri':'https://attacker.invalid'})
    assert 'token_uri' not in value
    with pytest.raises(CloudError):check_credentials('azure',{**CREDENTIALS['azure'],'tenant_id':'../other-tenant'})
    with pytest.raises(CloudError):check_credentials('aws',{'access_key_id':'only-id'})
