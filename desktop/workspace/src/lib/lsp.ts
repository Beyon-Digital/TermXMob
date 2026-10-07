/** JSON-RPC over the host's authenticated, project-scoped duplex endpoint. */
export type Position={line:number;character:number};
export type Diagnostic={range:{start:Position;end:Position};message:string;severity?:number};
export type Location={uri:string;range:{start:Position;end:Position}};
export class LanguageClient{
 private socket:WebSocket;private sequence=0;private pending=new Map<number,{resolve:(value:any)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();private ready:Promise<void>;private stopped=false;private capabilities:Record<string,unknown>={};
 constructor(projectId:string,language:string,root:string,private diagnostics:(uri:string,items:Diagnostic[])=>void,private report:(error:Error)=>void,workspaceSession?:string){
  this.socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/projects/${encodeURIComponent(projectId)}/lsp/${language}${workspaceSession?'?workspace_session='+encodeURIComponent(workspaceSession):''}`);
  this.ready=new Promise((resolve,reject)=>{this.socket.onopen=()=>{this.call('initialize',{processId:null,rootUri:fileUri(root,''),capabilities:{textDocument:{publishDiagnostics:{},hover:{contentFormat:['plaintext']},completion:{completionItem:{snippetSupport:false}},definition:{linkSupport:false}},workspace:{configuration:true}},workspaceFolders:[{uri:fileUri(root,''),name:root.replaceAll('\\','/').split('/').at(-1)}]}).then(result=>{this.capabilities=result.capabilities||{};this.notify('initialized',{});resolve()}).catch(reject)};this.socket.onerror=()=>reject(new Error('Language server connection failed'))});
  // Consume early errors even when no editor file has opened yet.
  void this.ready.catch(report);
  this.socket.onmessage=event=>{try{const message=JSON.parse(event.data);if(message.method==='textDocument/publishDiagnostics')this.diagnostics(message.params.uri,message.params.diagnostics||[]);else if(message.method&&message.id!==undefined){let result:unknown=null;if(message.method==='workspace/configuration')result=(message.params.items||[]).map(()=>({}));this.socket.send(JSON.stringify({jsonrpc:'2.0',id:message.id,result}))}else if(message.id!==undefined){const pending=this.pending.get(message.id);if(pending){clearTimeout(pending.timer);this.pending.delete(message.id);message.error?pending.reject(new Error(message.error.message)):pending.resolve(message.result)}}}catch(error){report(error instanceof Error?error:new Error(String(error)))}};
  this.socket.onclose=event=>{for(const pending of this.pending.values()){clearTimeout(pending.timer);pending.reject(new Error('Language server disconnected'))}this.pending.clear();if(!this.stopped)this.report(new Error(event.code===4404?'Language server unavailable. Install the configured language runtime on the host.':'Language server disconnected. Reopen the project to reconnect.'))};
 }
 private call(method:string,params:unknown):Promise<any>{return new Promise((resolve,reject)=>{const id=++this.sequence;const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('Language server timed out: '+method))},15000);this.pending.set(id,{resolve,reject,timer});this.socket.send(JSON.stringify({jsonrpc:'2.0',id,method,params}))})}
 private notify(method:string,params:unknown){if(this.socket.readyState===WebSocket.OPEN)this.socket.send(JSON.stringify({jsonrpc:'2.0',method,params}))}
 async open(uri:string,language:string,text:string){await this.ready;this.notify('textDocument/didOpen',{textDocument:{uri,languageId:language,version:1,text}})}
 async change(uri:string,text:string,version:number){await this.ready;this.notify('textDocument/didChange',{textDocument:{uri,version},contentChanges:[{text}]})}
 async closeDocument(uri:string){await this.ready;this.notify('textDocument/didClose',{textDocument:{uri}})}
 async format(uri:string){await this.ready;if(!this.capabilities.documentFormattingProvider)throw new Error('This language server does not advertise formatting. Configure a formatting-capable server on the host.');return this.call('textDocument/formatting',{textDocument:{uri},options:{tabSize:4,insertSpaces:true}})}
 async request(method:string,uri:string,position:Position){await this.ready;return this.call('textDocument/'+method,{textDocument:{uri},position})}
 close(){this.stopped=true;this.socket.close()}
}
export function fileUri(root:string,path:string){
 const normalized=root.replaceAll('\\','/').replace(/\/$/,'');
 const absolute=/^[A-Za-z]:\//.test(normalized)?'/'+normalized:normalized;
 const encoded=absolute.split('/').map((part,index)=>/^[A-Za-z]:$/.test(part)&&index===1?part:encodeURIComponent(part)).join('/');
 const prefix=absolute.startsWith('//')?'file:'+encoded:'file://'+encoded;
 return prefix+'/'+path.replaceAll('\\','/').split('/').map(encodeURIComponent).join('/');
}
/** Preserve native path casing while comparing drive roots without trusting an outside URI. */
export function relativeFileUri(root:string,uri:string):string|null{
 try{
  const candidate=new URL(uri),base=new URL(fileUri(root,''));
  if(candidate.protocol!=='file:'||candidate.hostname!==base.hostname)return null;
  const path=decodeURIComponent(candidate.pathname),prefix=decodeURIComponent(base.pathname);
  const windows=/^\/[A-Za-z]:\//.test(prefix)||!!base.hostname;
  const comparable=windows?path.toLowerCase():path,expected=windows?prefix.toLowerCase():prefix;
  if(!comparable.startsWith(expected))return null;
  const relative=path.slice(prefix.length);
  return relative.split('/').some(part=>part==='..'||part==='.')?null:relative;
 }catch{return null}
}
export function positionAt(text:string,offset:number):Position{const lines=text.slice(0,offset).split('\n');return {line:lines.length-1,character:lines.at(-1)!.length}}
