import {afterEach,expect,it,vi} from 'vitest';
import {LanguageClient} from './lsp';
class Socket {
 static OPEN=1;static instances:Socket[]=[];readyState=0;sent:string[]=[];
 onopen:(()=>void)|null=null;onerror:(()=>void)|null=null;onclose:((event:{code:number;reason?:string})=>void)|null=null;onmessage:((event:{data:string})=>void)|null=null;
 constructor(){Socket.instances.push(this)}
 send(value:string){this.sent.push(value)}
 close(){this.readyState=3;this.onclose?.({code:1000})}
 open(){this.readyState=1;this.onopen?.()}
}
afterEach(()=>{vi.unstubAllGlobals();Socket.instances=[]});
it('closes an initializing editor quietly and prevents late document effects',async()=>{
 vi.stubGlobal('WebSocket',Socket);const report=vi.fn(),diagnostics=vi.fn();const client=new LanguageClient('project','python','/tmp/project',diagnostics,report);
 const socket=Socket.instances[0];socket.open();const opening=client.open('file:///tmp/project/file.py','python','source');client.close();await opening;
 socket.onmessage?.({data:JSON.stringify({method:'textDocument/publishDiagnostics',params:{uri:'file:///tmp/project/file.py',diagnostics:[]}})});
 await Promise.resolve();expect(report).not.toHaveBeenCalled();expect(diagnostics).not.toHaveBeenCalled();expect(socket.sent).toHaveLength(1);
});
it('reports a genuine disconnect without swallowing its failed initialization',async()=>{
 vi.stubGlobal('WebSocket',Socket);const report=vi.fn();new LanguageClient('project','python','/tmp/project',vi.fn(),report);
 const socket=Socket.instances[0];socket.open();socket.onclose?.({code:4403});await Promise.resolve();await Promise.resolve();
 expect(report).toHaveBeenCalled();expect(report.mock.calls.some(([error])=>error.message.includes('disconnected'))).toBe(true);
});

it('keeps unavailable-runtime install guidance and bounds its actual close reason',()=>{
 vi.stubGlobal('WebSocket',Socket);const report=vi.fn();const client=new LanguageClient('project','python','/tmp/project',vi.fn(),report);
 Socket.instances[0].onclose?.({code:4404,reason:'Missing configured adapter\n'+ 'x'.repeat(1000)});
 const error=report.mock.calls[0][0] as Error;expect(error.message).toContain('Install the configured language runtime');expect(error.message).toContain('Missing configured adapter');expect(error.message).not.toContain('Reopen the project');expect((error.cause as {reason:string}).reason.length).toBeLessThanOrEqual(160);client.close();
});
