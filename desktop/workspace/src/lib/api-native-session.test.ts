import {beforeEach,afterEach,expect,it,vi} from 'vitest';
import {gql,request} from './api';
import {nativeRequest} from './native';

vi.mock('./native',()=>({nativeWorkspace:()=>true,nativeRequest:vi.fn()}));
beforeEach(()=>vi.mocked(nativeRequest).mockReset());
afterEach(()=>vi.unstubAllGlobals());

it.each([
 'Session cursor expired or changed scope; reload the list',
 'Socket proof expired or invalid',
 'OS secure credential storage unavailable; try again',
 'Session refresh unavailable (HTTP 503); try again',
 'Project access revoked',
])('does not sign out the account for a native operation error: %s',async(message)=>{
 const signedOut=vi.fn();window.addEventListener('termx-signed-out',signedOut);
 vi.mocked(nativeRequest).mockRejectedValueOnce(message);
 try{await expect(request('/api/fixture')).rejects.toThrow(message);expect(signedOut).not.toHaveBeenCalled();}
 finally{window.removeEventListener('termx-signed-out',signedOut);}
});

it('clears the workspace when the native credential coordinator confirms revocation',async()=>{
 const signedOut=vi.fn();window.addEventListener('termx-signed-out',signedOut);
 vi.mocked(nativeRequest).mockRejectedValueOnce('Session expired or revoked; sign in again');
 try{await expect(request('/api/fixture')).rejects.toMatchObject({status:401});expect(signedOut).toHaveBeenCalledOnce();}
 finally{window.removeEventListener('termx-signed-out',signedOut);}
});

it('recovers native GraphQL reads through the credential coordinator without browser refresh',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 vi.mocked(nativeRequest)
  .mockResolvedValueOnce({status:200,body:{errors:[{message:'Managed sign-in required',extensions:{code:'UNAUTHENTICATED',http_status:401}}]}})
  .mockResolvedValueOnce({status:200,body:{principal:{id:'owner'}}})
  .mockResolvedValueOnce({status:200,body:{data:{nodes:[]}}});
 expect(await gql('{nodes(ids:[]){id}}')).toEqual({nodes:[]});
 expect(vi.mocked(nativeRequest).mock.calls.map(([path])=>path)).toEqual(['/graphql','/auth/me','/graphql']);expect(fetch).not.toHaveBeenCalled();
});
