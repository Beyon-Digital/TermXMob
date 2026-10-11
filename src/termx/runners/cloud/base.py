from __future__ import annotations
import asyncio
import time
from urllib.parse import urlsplit
import httpx
import jwt

class CloudError(Exception):
    def __init__(self,message='Cloud operation failed; inspect permissions, quota and configuration.',*,definitive=False):
        super().__init__(message);self.definitive=definitive

async def until(operation,predicate=lambda value:bool(value),*,attempts=120,delay=5):
    for index in range(attempts):
        value=await operation()
        if predicate(value):return value
        if index+1<attempts:await asyncio.sleep(delay)
    raise CloudError('Cloud operation is still pending. Refresh or retry reconciliation; work is not replayed.')

def option(identifier,label=None,**extra):return {'id':identifier,'label':label or identifier,**extra}

def check_credentials(provider,value):
    fields={'aws':('access_key_id','secret_access_key'), 'azure':('tenant_id','client_id','client_secret','subscription_id'), 'gcp':('project_id','client_email','private_key')}
    if provider not in fields or not isinstance(value,dict):raise CloudError('Choose a supported provider and credential object.',definitive=True)
    if any(not isinstance(value.get(key),str) or not value[key].strip() or len(value[key])>20000 for key in fields[provider]):
        raise CloudError('Required provider authentication fields are missing or invalid.',definitive=True)
    import re
    if provider=='azure' and any(not re.fullmatch('[a-fA-F0-9-]{36}',value[key]) for key in ('tenant_id','client_id','subscription_id')):raise CloudError('Azure tenant, client and subscription IDs must be UUIDs.',definitive=True)
    if provider=='gcp' and (not re.fullmatch('[a-z][a-z0-9-]{4,61}[a-z0-9]',value['project_id']) or not re.fullmatch(r'[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com',value['client_email'])):raise CloudError('Provide a Google service account and project ID.',definitive=True)
    return {key:value[key] for key in (*fields[provider],*({'aws':['session_token']}.get(provider,[]))) if value.get(key)}

class RestProvider:
    base=''
    def __init__(self,credentials,client=None):
        self.credentials=credentials;self.client=client or httpx.AsyncClient(timeout=60,follow_redirects=False);self.own_client=client is None;self.token=None;self.expires=0
    async def close(self):
        if self.own_client:await self.client.aclose()
    async def access_token(self):
        if self.token and self.expires>time.time()+60:return self.token
        if self.kind=='azure':
            url='https://login.microsoftonline.com/'+self.credentials['tenant_id']+'/oauth2/v2.0/token'
            data={'grant_type':'client_credentials','client_id':self.credentials['client_id'],'client_secret':self.credentials['client_secret'],'scope':'https://management.azure.com/.default'}
        else:
            url='https://oauth2.googleapis.com/token';now=int(time.time())
            assertion=jwt.encode({'iss':self.credentials['client_email'],'scope':'https://www.googleapis.com/auth/compute','aud':url,'iat':now,'exp':now+3600},self.credentials['private_key'],algorithm='RS256')
            data={'grant_type':'urn:ietf:params:oauth:grant-type:jwt-bearer','assertion':assertion}
        try:response=await self.client.post(url,data=data)
        except httpx.HTTPError:raise CloudError('Provider authentication could not be reached.') from None
        if response.status_code!=200:raise CloudError('Provider rejected authentication. Check the account credentials.',definitive=True)
        payload=response.json();self.token=payload['access_token'];self.expires=time.time()+int(payload.get('expires_in',3600));return self.token
    async def api(self,method,path,body=None,*,missing=False):
        url=path if path.startswith('https://') else self.base+path
        if urlsplit(url).netloc!=urlsplit(self.base).netloc or not url.startswith(self.base):raise CloudError('Provider returned an unexpected API destination.',definitive=True)
        token=await self.access_token()
        try:response=await self.client.request(method,url,json=body,headers={'Authorization':'Bearer '+token})
        except httpx.HTTPError:raise CloudError('Cloud response was lost; reconcile resources before retrying.') from None
        if missing and response.status_code==404:return None
        if not response.is_success:raise CloudError(f'{self.kind.upper()} request failed (HTTP {response.status_code}). Check permissions, quota and selected resources.',definitive=400<=response.status_code<500 and response.status_code not in (408,409,429))
        return response.json() if response.content else {}

def require_owned(row,identifier,kind):
    tags=row.get('tags',{}) if kind=='azure' else row.get('labels',{})
    if tags.get('termx-deployment')!=identifier:raise CloudError('Resource ownership does not match this deployment; refusing to alter it.',definitive=True)
