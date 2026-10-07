import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from fastapi import HTTPException
from termx.browser.network import TargetDenied
from termx.development.preview import PreviewService,ExactPreviewNetwork

def test_exact_preview_origin_rejects_cross_origin_metadata():
    policy=ExactPreviewNetwork('http://127.0.0.1:5173')
    async def run():
        with pytest.raises(TargetDenied):await policy.resolve('127.0.0.1',8000,'http')
        with pytest.raises(TargetDenied):await policy.resolve('169.254.169.254',80,'http')
        with pytest.raises(TargetDenied):await policy.resolve('example.com',443,'https')
    asyncio.run(run())

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='Requires explicit managed Chromium installation')
def test_real_host_preview_render_input_origin_isolation_and_revoke(tmp_path):
    class Site(BaseHTTPRequestHandler):
        def do_GET(self):
            raw=b'<html><title>Real host preview</title><h1>Actual project server</h1><button id="change" onclick="document.querySelector(\'h1\').innerText=\'Changed\'">Change title</button></html>'
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(raw)
        def log_message(self,*_):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Site);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    valid=True;service=PreviewService(tmp_path,lambda *_:valid)
    async def run():
        nonlocal valid
        try:
            created=await service.create('alice','device',1,'project',f'http://127.0.0.1:{server.server_port}')
            item=service.get('alice',created['id'])
            with pytest.raises(HTTPException):service.get('bob',created['id'])
            context=await item['service'].observe(item['tab'],'alice',human=True)
            assert context['title']=='Real host preview'
            assert any(e['name']=='Actual project server' for e in context['elements'])
            await item['service'].human_action(item['tab'],'alice','click',{'selector':'#change'})
            changed=await item['service'].observe(item['tab'],'alice',human=True)
            assert any(e['name']=='Changed' for e in changed['elements'])
            frame=await item['service'].frame(item['tab'],'alice',human=True)
            assert frame.startswith(b'\xff\xd8')
            with pytest.raises(TargetDenied):await item['service'].network.validate('http://127.0.0.1:1')
            valid=False
            with pytest.raises(HTTPException,match='revoked'):service.get('alice',created['id'])
            service.start()
            for _ in range(100):
                if not service.items:break
                await asyncio.sleep(.1)
            await asyncio.gather(*list(service.cleanup_tasks))
            assert service.items=={}
            assert not (tmp_path/created['id']).exists()
        finally:await service.close()
    try:asyncio.run(run())
    finally:server.shutdown();server.server_close()
