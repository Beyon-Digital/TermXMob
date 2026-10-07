import {csrf,refreshSession,ApiError} from './api';
import {nativeWorkspace,nativeBinary} from './native';

export async function transfer(path:string,init:RequestInit={},retry=true):Promise<Blob>{
 if(!path.startsWith('/')||path.startsWith('//'))throw new Error('Transfers must stay on this workspace');
 if(nativeWorkspace()){
  const result=await nativeBinary(path,init);
  const blob=new Blob([new Uint8Array(result.body)],{type:result.mime});
  if(result.status<200||result.status>=300){const error=await blob.text();throw new Error(error)}
  return blob;
 }
 const headers=new Headers(init.headers);if((init.method||'GET')!=='GET')headers.set('X-Termx-CSRF',csrf());
 const result=await fetch(path,{...init,headers,credentials:'same-origin'});
 if(result.status===401&&retry&&!path.startsWith('/auth/')){await refreshSession();return transfer(path,init,false)}
 if(!result.ok){const error=await result.json().catch(()=>({detail:result.statusText}));throw new ApiError(result.status,error.detail||result.statusText)}
 return result.blob();
}
export async function download(path:string,name:string){
 const blob=await transfer(path);const url=URL.createObjectURL(blob);const anchor=document.createElement('a');
 anchor.href=url;anchor.download=name;anchor.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
