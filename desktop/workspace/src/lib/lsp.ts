/** JSON-RPC over the host's authenticated, project-scoped duplex endpoint. */
export type Position={line:number;character:number};
export type Diagnostic={range:{start:Position;end:Position};message:string;severity?:number};
export type Location={uri:string;range:{start:Position;end:Position}};
export class LanguageClient{
 private socket:WebSocket;private sequence=0;private pending=new Map<number,{resolve:(value:any)=>void;reject:(error:Error)=>void;timer:ReturnType<typeof setTimeout>}>();private ready:Promise<void>;private stopped=false;private capabilities:Record<string,unknown>={};
 constructor(projectId:string,language:string,root:string,private diagnostics:(uri:string,items:Diagnostic[])=>void,private report:(error:Error)=>void){
  this.socket=new WebSocket(`${location.protocol==='https:'?'wss:':'ws:'}//${location.host}/api/projects/${encodeURIComponent(projectId)}/lsp/${language}`);
  this.ready=new Promise((resolve,reject)=>{this.socket.onopen=()=>{this.call('initialize',{processId:null,rootUri:'file://'+root,capabilities:{textDocument:{publishDiagnostics:{},hover:{contentFormat:['plaintext']},completion:{completionItem:{snippetSupport:false}},definition:{linkSupport:false}},workspace:{configuration:true}},workspaceFolders:[{uri:'file://'+root,name:root.split('/').at(-1)}]}).then(result=>{this.capabilities=result.capabilities||{};this.notify('initialized',{});resolve()}).catch(reject)};this.socket.onerror=()=>reject(new Error('Language server connection failed'))});
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
export function fileUri(root:string,path:string){return 'file://'+root.replace(/\/$/,'')+'/'+path.split('/').map(encodeURIComponent).join('/')}
export function positionAt(text:string,offset:number):Position{const lines=text.slice(0,offset).split('\n');return {line:lines.length-1,character:lines.at(-1)!.length}}
