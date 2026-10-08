export const UI_CONTRACT=3;
export async function checkCompatibility(){
 const response=await fetch('/workspace-version.json',{credentials:'same-origin',cache:'no-store'});
 if(!response.ok)throw new Error('This host needs a workspace update. Update TermX on the host and reload.');
 const version=await response.json() as {host_contract:number;minimum_ui_contract:number;client:string};
 if(version.host_contract!==UI_CONTRACT||version.minimum_ui_contract>UI_CONTRACT||version.client!=='react-dom-workspace')throw new Error('This workspace bundle and host are incompatible. Update the desktop app or host bundle, then reload.');
}
