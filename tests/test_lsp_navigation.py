"""Provider-free navigation through the production JSON-RPC WebSocket bridge."""
import asyncio
import json
import os
from pathlib import Path
import shutil
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname
import pytest
from fastapi import WebSocketDisconnect
from termx import lsp

def local_file_identity(uri):
    """Compare actual files, including encoded/normalized Windows drive URIs."""
    parts = urlsplit(uri)
    assert parts.scheme == 'file' and parts.netloc in {'', 'localhost'}
    assert not parts.query and not parts.fragment
    # Windows url2pathname recognizes the drive before its own URL decoding;
    # LSP servers legitimately percent-encode the colon in file:///c%3A/....
    return Path(url2pathname(unquote(parts.path))).resolve()

class Socket:
    def __init__(self):self.input=asyncio.Queue();self.output=asyncio.Queue()
    async def accept(self):pass
    async def close(self,**kwargs):raise AssertionError(kwargs)
    async def receive_text(self):
        value=await self.input.get()
        if value is None:raise WebSocketDisconnect()
        return json.dumps(value)
    async def send_text(self,text):await self.output.put(json.loads(text))
    async def notify(self,method,params):await self.input.put({'jsonrpc':'2.0','method':method,'params':params})
    async def request(self,identifier,method,params):
        await self.input.put({'jsonrpc':'2.0','id':identifier,'method':method,'params':params})
        while True:
            message=await asyncio.wait_for(self.output.get(),20)
            if message.get('method') and 'id' in message:
                result=[{} for _ in message.get('params',{}).get('items',[])] if message['method']=='workspace/configuration' else None
                await self.input.put({'jsonrpc':'2.0','id':message['id'],'result':result})
            elif message.get('id')==identifier:
                assert 'error' not in message,message
                return message.get('result')

@pytest.mark.parametrize('language,filename,content,line,character',[('python','main.py','def answer() -> int:\n    return 42\n\nvalue = answer()\n',3,10),('typescript','main.ts','export function answer(): number { return 42; }\nconst value = answer();\nconst   badlySpaced=  1;\n',1,17)])
def test_real_language_definition_hover_and_completion(tmp_path,monkeypatch,language,filename,content,line,character):
    if language=='python':command=lsp._command('python')
    else:
        binary=os.environ.get('TERMX_TS_LANGUAGE_SERVER') or shutil.which('typescript-language-server')
        command=(binary,'--stdio') if binary else lsp._command(language)
    if not command:
        if os.environ.get('TERMX_RUNTIME_QUALIFICATION') == '1':
            pytest.fail('Required packaged language runtime is missing: '+language)
        pytest.skip('Install the pinned language runtime to run the actual server integration')
    monkeypatch.setattr(lsp,'_command',lambda requested:command)
    file=tmp_path/filename;file.write_text(content)
    if language=='typescript':(tmp_path/'tsconfig.json').write_text('{"compilerOptions":{"strict":true},"files":["main.ts"]}')
    async def run():
        socket=Socket();serve=asyncio.create_task(lsp.serve(socket,str(tmp_path),language))
        try:
            initialize=await socket.request(1,'initialize',{'processId':None,'rootUri':tmp_path.as_uri(),'capabilities':{'textDocument':{'hover':{'contentFormat':['plaintext']}}}})
            assert initialize['capabilities']['definitionProvider']
            await socket.notify('initialized',{})
            await socket.notify('textDocument/didOpen',{'textDocument':{'uri':file.as_uri(),'languageId':language,'version':1,'text':content}})
            params={'textDocument':{'uri':file.as_uri()},'position':{'line':line,'character':character}}
            definition=await socket.request(2,'textDocument/definition',params)
            locations=definition if isinstance(definition,list) else [definition]
            assert locations and local_file_identity(locations[0]['uri'])==file.resolve()
            assert locations[0]['range']['start']['line']==0
            hover=await socket.request(3,'textDocument/hover',params)
            assert 'answer' in json.dumps(hover)
            completion=await socket.request(4,'textDocument/completion',{'textDocument':{'uri':file.as_uri()},'position':{'line':line,'character':character-2}})
            assert completion and (completion.get('items') if isinstance(completion,dict) else completion)
            if initialize['capabilities'].get('documentFormattingProvider'):
                formatting=await socket.request(5,'textDocument/formatting',{'textDocument':{'uri':file.as_uri()},'options':{'tabSize':4,'insertSpaces':True}})
                assert formatting and all('range' in edit and 'newText' in edit for edit in formatting)
        finally:
            await socket.input.put(None)
            await asyncio.wait_for(serve,5)
    asyncio.run(run())


def test_language_messages_cannot_select_outside_files_or_symlinks(tmp_path):
    root=tmp_path/'root';root.mkdir();outside=tmp_path/'private.txt';outside.write_text('Private')
    (root/'outside.py').symlink_to(outside)
    def raw(uri):return json.dumps({'jsonrpc':'2.0','method':'textDocument/hover','params':{'textDocument':{'uri':uri}}})
    lsp._validate_client_message(raw((root/'new.py').as_uri()),str(root))
    for uri in (outside.as_uri(),(root/'outside.py').as_uri(),'file://another-host/root/main.py'):
        with pytest.raises((PermissionError,ValueError)):lsp._validate_client_message(raw(uri),str(root))
    with pytest.raises(ValueError):lsp._validate_client_message(json.dumps({'method':'workspace/executeCommand','params':{'command':'arbitrary'}}),str(root))
    with pytest.raises(PermissionError):lsp._validate_client_message(json.dumps({'method':'initialize','params':{'workspaceFolders':[{'uri':tmp_path.as_uri()}]}}),str(root))


def test_live_language_authority_revocation_closes_existing_transport(tmp_path,monkeypatch):
    import sys
    monkeypatch.setattr(lsp,'_command',lambda language:(sys.executable,'-u','-c','import time; time.sleep(30)'))
    async def run():
        class CheckedSocket(Socket):
            def __init__(self):super().__init__();self.closed=[]
            async def close(self,**kwargs):self.closed.append(kwargs)
        socket=CheckedSocket();calls=[];revoked=[False]
        def authorize():
            calls.append(True)
            if revoked[0]:raise PermissionError('Project grant revoked')
        worker=asyncio.create_task(lsp.serve(socket,str(tmp_path),'fixture',authorize=authorize))
        await socket.notify('initialized',{})
        for _ in range(100):
            if len(calls)>=2:break
            await asyncio.sleep(.01)
        assert len(calls)>=2
        revoked[0]=True
        await socket.notify('textDocument/didOpen',{'textDocument':{'uri':(tmp_path/'main.py').as_uri(),'text':'Authorized draft'}})
        await asyncio.wait_for(worker,5)
        assert socket.closed[0]['code']==4403 and len(calls)>=3
    asyncio.run(run())
