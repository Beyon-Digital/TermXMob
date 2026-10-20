/** Convert display references to a path within the current authorized checkout. */
export function workspaceFilePath(value:string,root:string):string|null{
 if(!root||!value||value.includes('\0'))return null;
 let path=value.trim().replaceAll('\\','/'),base=root.replaceAll('\\','/').replace(/\/$/,'');
 if(path.startsWith('file://')){try{const url=new URL(path);if(url.hostname&&url.hostname!=='localhost')return null;path=decodeURIComponent(url.pathname);if(/^\/[A-Za-z]:\//.test(path))path=path.slice(1)}catch{return null}}
 if(/^[a-z]+:\/\//i.test(path))return null;
 if(path.startsWith('/')||/^[A-Za-z]:\//.test(path)){if(!path.startsWith(base+'/'))return null;path=path.slice(base.length+1)}
 const parts:string[]=[];for(const part of path.split('/')){if(!part||part==='.')continue;if(part==='..'){if(!parts.length)return null;parts.pop()}else parts.push(part)}
 return parts.length?parts.join('/'):null;
}
