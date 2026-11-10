"""Opt-in browser proof of dictation and spoken replies against the shipped workspace; never calls a paid provider.

Run it either way (both need the built workspace in desktop/workspace/dist, so run `pnpm build` there first):

    TERMX_RUN_BROWSER_E2E=1 uv run pytest tests/workspace_voice_e2e.py -q
    uv run python tests/workspace_voice_e2e.py OUTPUT_DIR        # also writes report.json and screenshots

It is skipped unless TERMX_RUN_BROWSER_E2E is set. Playwright is a project dependency, but its own downloaded browsers are not
used: the page runs in the system Google Chrome (channel "chrome", or TERMX_E2E_CHROME for another executable) with Chrome's fake
microphone (--use-fake-device-for-media-stream --use-fake-ui-for-media-stream). A local HTTP server stands in for the audio
provider and a generated MP3 for its speech, so this does not qualify a real speech model.
"""
from __future__ import annotations
import asyncio,json,os,socket,subprocess,sys,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from time import monotonic,sleep
import pytest

ENABLE='TERMX_RUN_BROWSER_E2E'
CHROME_PATH=os.environ.get('TERMX_E2E_CHROME','')
FAKE_MIC=['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']
TRANSCRIPT='Voice instruction from the local audio fixture'
REPLY='Voice assistant reply fixture'

def launch_chrome(playwright):
    """System Chrome, headless, with a synthetic microphone; no Playwright-managed browser download is needed."""
    options=dict(headless=True,args=FAKE_MIC)
    if CHROME_PATH:return playwright.chromium.launch(executable_path=CHROME_PATH,**options)
    return playwright.chromium.launch(channel='chrome',**options)

def run(destination):
    import imageio_ffmpeg,uvicorn
    from playwright.sync_api import sync_playwright
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'chat-voice-with-local-HTTP-fixture','paid_provider_queries':0,'physical_microphone_access':False,'errors':[],'states':[],'provider_requests':[]}
    with tempfile.TemporaryDirectory(prefix='termx-voice-proof-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        audio=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.4','-f','mp3','pipe:1'],capture_output=True,check=True).stdout
        class AudioAPI(BaseHTTPRequestHandler):
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                if self.path=='/v1/audio/transcriptions':
                    report['provider_requests'].append({'path':self.path,'bytes':len(body),'real_recorded_webm':b'\x1aE\xdf\xa3' in body,'model_selected':b'fixture-transcribe' in body})
                    response=json.dumps({'text':TRANSCRIPT}).encode();mime='application/json'
                elif self.path=='/v1/audio/speech':
                    request=json.loads(body);report['provider_requests'].append({'path':self.path,'model':request['model'],'voice':request.get('voice'),'input':request['input']});response=audio;mime='audio/mpeg'
                else:self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
            def log_message(self,*_):pass
        provider=ThreadingHTTPServer(('127.0.0.1',0),AudioAPI);threading.Thread(target=provider.serve_forever,daemon=True).start()
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderTurn
        from test_agent import FakeAdapter
        class ChatAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                self.turns+=1;return ProviderTurn('fixture-reply',REPLY,[],{},[])
        adapter=ChatAdapter();state=AppState(passcode=None,adapter_factory=lambda *_:adapter);owner=state.identity.setup_owner('voice-owner','voice-proof-password-123');project=state.projects.register(str(root),name='Voice proof')
        # The audio account is saved through the same manager call the Models & engines screen uses; its key is a dummy that only the local fixture sees.
        state.agent.save_provider(provider_id='voice-fixture',kind='openai-compatible',name='Local audio fixture',base_url=f'http://127.0.0.1:{provider.server_port}/v1',model='fixture-chat',capabilities=['audio'],api_key='fixture-not-a-real-provider-key')
        assert 'audio' in state.agent_store.get_provider('voice-fixture')['capabilities'];assert not report['provider_requests']
        from workspace_ui_snapshot import snapshot_ui
        app=create_app(state,web_dir=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist')))))
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        session=state.workspace.create_session(owner,title='Voice chat proof',project_id=project['id'],cwd=str(root),mode='ask',provider_id='voice-fixture',model='fixture-chat')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));thread=threading.Thread(target=lambda:asyncio.run(host.serve(sockets=[listener])),daemon=True);thread.start();deadline=monotonic()+25
        while not host.started:
            if monotonic()>deadline:raise RuntimeError('Voice fixture host did not start')
            sleep(.05)
        base=f'http://127.0.0.1:{port}'
        try:
            with sync_playwright() as playwright:
                browser=launch_chrome(playwright);context=browser.new_context(viewport={'width':1440,'height':1000},permissions=['microphone'],reduced_motion='reduce');page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                response=page.goto(f'{base}/?session={session["id"]}');assert response.headers['permissions-policy']=='microphone=(self), camera=(), display-capture=()'
                page.get_by_label('Username',exact=True).fill('voice-owner');page.get_by_label('Password',exact=True).fill('voice-proof-password-123');page.get_by_role('button',name='Continue with password').click();page.wait_for_function('!!document.querySelector("textarea[aria-label=Message]")&&!document.querySelector("textarea[aria-label=Message]").disabled',timeout=60000)
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                def check(label):
                    page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
                    entry={'screen':label,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['violations']]};report['states'].append(entry);page.screenshot(path=str(destination/f'{label}.png'));assert not entry['overflow'] and not entry['violations'],entry
                # Settings, Voice: pick the audio account, agree to send recordings to it (a local server), name the models, choose the API voice.
                page.get_by_role('button',name='Settings',exact=True).first.click();page.get_by_role('link',name='Voice',exact=True).click()
                form=page.get_by_role('form',name='Voice settings',exact=True);form.wait_for()
                form.get_by_role('combobox',name='Audio account',exact=True).select_option('voice-fixture')
                form.get_by_label(f'Send text and recordings to Local audio fixture, which runs on this device (127.0.0.1:{provider.server_port}).',exact=True).check()
                form.get_by_role('textbox',name='Transcription model',exact=True).fill('fixture-transcribe');form.get_by_role('textbox',name='Speech model',exact=True).fill('fixture-speech')
                form.get_by_role('radio',name='Cloud or server API').check()
                assert not report['provider_requests'];check('voice-settings')
                page.get_by_role('button',name='Save',exact=True).click();page.get_by_text('Voice settings saved',exact=True).wait_for()
                saved=state.workspace.voice_settings(owner) if hasattr(state.workspace,'voice_settings') else None
                if saved is not None:assert saved['provider']=='voice-fixture'
                page.keyboard.press('Escape');page.get_by_role('dialog').wait_for(state='hidden')
                # Composer: the microphone button records, then the transcript lands in the message box and is not sent.
                message=page.get_by_role('textbox',name='Message',exact=True)
                mic=page.get_by_role('button',name='Start dictation',exact=True);mic.wait_for();page.wait_for_function('!document.querySelector("button[aria-label=\\"Start dictation\\"]").disabled')
                mic.click();page.get_by_role('button',name='Stop dictation',exact=True).wait_for();page.wait_for_timeout(1300);page.get_by_role('button',name='Stop dictation',exact=True).click()
                page.wait_for_function('selector=>document.querySelector(selector).value!==""',arg='textarea[aria-label=Message]',timeout=30000)
                assert message.input_value().strip()==TRANSCRIPT;assert adapter.turns==0
                assert len(report['provider_requests'])==1 and report['provider_requests'][0]['real_recorded_webm'] and report['provider_requests'][0]['model_selected'],report['provider_requests']
                report['transcript_requires_review_and_explicit_send']=True
                # Review it, ask for replies to be read aloud, send, and hear the reply come back through the speech endpoint.
                message.fill('Reviewed voice instruction');assert adapter.turns==0
                page.get_by_label('Read replies aloud',exact=True).check();page.wait_for_timeout(300)
                page.get_by_role('button',name='Send message',exact=True).click()
                try:page.get_by_text(REPLY,exact=True).first.wait_for(timeout=60000)
                except Exception:
                    report['send_diagnostics']={'alerts':page.get_by_role('alert').all_text_contents(),'turns':adapter.turns,'tasks':[{'status':task['status'],'error':task.get('error'),'result':task.get('result')} for task in state.agent_store.list_tasks()]}
                    page.screenshot(path=str(destination/'send-failure.png'));raise
                deadline=monotonic()+30
                while len(report['provider_requests'])<2 and monotonic()<deadline:sleep(.1)
                assert len(report['provider_requests'])==2,report['provider_requests']
                speech=report['provider_requests'][1];assert speech['path']=='/v1/audio/speech' and speech['input'].rstrip('.')==REPLY and speech['model']=='fixture-speech',speech
                assert adapter.turns==1;report['fixture_chat_turns']=adapter.turns;report['spoken_reply_requested_from_fixture']=True
                check('composer')
                report['production_bundle']=page.evaluate('Array.from(document.scripts).map(s=>s.src).find(s=>s.includes("/assets/index-"))?.split("/").pop()');assert not report['errors'],report['errors'];browser.close()
        finally:
            host.should_exit=True;thread.join(timeout=10);provider.shutdown();provider.server_close();(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

@pytest.mark.skipif(not os.environ.get(ENABLE),reason=f'opt-in real-browser proof; set {ENABLE}=1 (needs system Google Chrome and a built workspace)')
def test_workspace_voice_e2e(tmp_path):
    report=run(tmp_path/'proof');assert report['transcript_requires_review_and_explicit_send'] and report['spoken_reply_requested_from_fixture']

if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
