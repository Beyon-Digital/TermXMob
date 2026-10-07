"""Opt-in real chat/microphone/local-HTTP audio proof; never calls a paid provider.

Run with the isolated managed Chromium: python tests/workspace_voice_e2e.py OUTPUT.
Synthetic Chromium microphone and generated MP3 replace physical devices/provider
inference; this does not qualify an entitled production speech model.
"""
from __future__ import annotations
import asyncio,json,os,socket,subprocess,sys,tempfile,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from time import monotonic,sleep
import imageio_ffmpeg,uvicorn
from playwright.sync_api import sync_playwright

def run(destination):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    report={'proof':'actual-chat-voice-with-local-HTTP-fixture','paid_provider_queries':0,'physical_microphone_access':False,'errors':[],'states':[],'provider_requests':[]}
    with tempfile.TemporaryDirectory(prefix='termx-voice-proof-') as temporary:
        root=Path(temporary);os.environ.update(TERMX_CONFIG_DIR=str(root/'config'),TERMX_AGENTS_DIR=str(root/'agents'),TERMX_ENGINE_STARTUP_REFRESH='0')
        audio=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.4','-f','mp3','pipe:1'],capture_output=True,check=True).stdout
        class AudioAPI(BaseHTTPRequestHandler):
            def do_POST(self):
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
                if self.path=='/v1/audio/transcriptions':
                    report['provider_requests'].append({'path':self.path,'bytes':len(body),'real_recorded_webm':b'\x1aE\xdf\xa3' in body,'model_selected':b'fixture-transcribe' in body})
                    response=json.dumps({'text':'Voice instruction from the local audio fixture'}).encode();mime='application/json'
                elif self.path=='/v1/audio/speech':
                    request=json.loads(body);report['provider_requests'].append({'path':self.path,'model':request['model'],'voice':request['voice'],'input':request['input']});response=audio;mime='audio/mpeg'
                else:self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(response)));self.end_headers();self.wfile.write(response)
            def log_message(self,*_):pass
        provider=ThreadingHTTPServer(('127.0.0.1',0),AudioAPI);threading.Thread(target=provider.serve_forever,daemon=True).start()
        from termx.app import AppState,create_app
        from termx.agent.providers import ProviderTurn
        from test_agent import FakeAdapter
        class ChatAdapter(FakeAdapter):
            async def turn(self,**kwargs):
                self.turns+=1;return ProviderTurn('fixture-reply','Voice assistant reply fixture',[],{},[])
        adapter=ChatAdapter();state=AppState(passcode=None,adapter_factory=lambda *_:adapter);owner=state.identity.setup_owner('voice-owner','voice-proof-password-123');project=state.projects.register(str(root),name='Voice proof')
        from workspace_ui_snapshot import snapshot_ui
        app=create_app(state,web_dir=snapshot_ui(root,Path(os.environ.get('TERMX_UI_PROOF_DIST',str(Path(__file__).resolve().parents[1]/'desktop/workspace/dist')))))
        report['UI_asset_snapshot']=json.loads((root/'fixture-ui-snapshot.json').read_text())
        session=state.workspace.create_session(owner,title='Voice chat proof',project_id=project['id'],cwd=str(root),mode='ask')
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        host=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='on'));thread=threading.Thread(target=lambda:asyncio.run(host.serve(sockets=[listener])),daemon=True);thread.start();deadline=monotonic()+25
        while not host.started:
            if monotonic()>deadline:raise RuntimeError('Voice fixture host did not start')
            sleep(.05)
        try:
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--use-fake-device-for-media-stream','--use-fake-ui-for-media-stream']);context=browser.new_context(viewport={'width':1440,'height':1000},permissions=['microphone'],reduced_motion='reduce');page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
                response=page.goto(f'http://127.0.0.1:{port}/?session={session["id"]}');assert response.headers['permissions-policy']=='microphone=(self), camera=(), display-capture=()'
                page.get_by_label('Username',exact=True).fill('voice-owner');page.get_by_label('Password',exact=True).fill('voice-proof-password-123');page.get_by_role('button',name='Continue with password').click();page.wait_for_function('!!document.querySelector("textarea[aria-label=Message]")&&!document.querySelector("textarea[aria-label=Message]").disabled',timeout=60000)
                page.get_by_role('button',name='Managers',exact=True).click();page.get_by_role('button',name='Models & engines',exact=True).click();page.get_by_text('Add provider account',exact=True).click();form=page.get_by_role('form',name='Add provider account',exact=True)
                form.get_by_label('Account ID',exact=True).fill('voice-fixture');form.get_by_label('Display name',exact=True).fill('Local audio fixture');form.get_by_label('Provider API address',exact=True).fill(f'http://127.0.0.1:{provider.server_port}/v1');form.get_by_label('API key',exact=True).fill('fixture-not-a-real-provider-key');form.get_by_label('Default for future sessions',exact=True).fill('fixture-chat');form.get_by_label('Audio transcription and speech',exact=True).check();form.get_by_role('button',name='Save account',exact=True).click();page.get_by_text('Provider account saved',exact=True).wait_for()
                assert 'audio' in state.agent_store.get_provider('voice-fixture')['capabilities'];assert not report['provider_requests'];report['capability_saved_through_managed_UI']=True
                page.get_by_role('button',name='Back to workspace',exact=True).click();voice=page.get_by_role('region',name='Voice controls',exact=True);voice.get_by_text('Voice mode · dictation and spoken replies',exact=True).click();voice.get_by_label('Voice account',exact=True).select_option('voice-fixture');voice.get_by_label('Transcription model',exact=True).fill('fixture-transcribe');voice.get_by_label('Speech model',exact=True).fill('fixture-speech');voice.get_by_label('Speech voice',exact=True).fill('fixture-voice')
                voice.get_by_role('button',name='Record dictation',exact=True).click();voice.get_by_role('button',name='Stop microphone',exact=True).wait_for();page.wait_for_timeout(1300);voice.get_by_role('button',name='Stop microphone',exact=True).click();transcribe=voice.get_by_role('button',name='Transcribe audio',exact=True);transcribe.wait_for();assert transcribe.is_disabled();assert not report['provider_requests'];voice.get_by_role('checkbox').check();transcribe.click();transcript=voice.get_by_label('Review voice transcript',exact=True);transcript.wait_for();assert transcript.input_value()=='Voice instruction from the local audio fixture';assert adapter.turns==0
                transcript.fill('Reviewed voice instruction');voice.get_by_role('button',name='Add transcript to composer',exact=True).click();message=page.get_by_role('textbox',name='Message',exact=True);assert message.input_value()=='Reviewed voice instruction';assert adapter.turns==0;report['transcript_requires_review_and_explicit_send']=True
                with page.expect_response(lambda response:response.request.method=='PATCH' and '/api/workspace/sessions/'+session['id'] in response.url):page.get_by_label('Session provider',exact=True).select_option('voice-fixture')
                page.get_by_role('button',name='Send message',exact=True).click();page.get_by_text('Voice assistant reply fixture',exact=True).wait_for(timeout=60000)
                speak=voice.get_by_role('button',name='Read latest reply aloud',exact=True);speak.wait_for();speak.click();player=voice.get_by_label('Spoken assistant reply',exact=True);player.wait_for();player.evaluate('e=>e.load()');page.wait_for_function('selector=>{const a=document.querySelector(selector);return a&&a.readyState>=2&&a.duration>0}',arg='audio[aria-label="Spoken assistant reply"]',timeout=15000);assert player.get_attribute('autoplay') is None;report['actual_generated_mp3_decoded_without_autoplay']=True
                axe=Path(__file__).resolve().parents[1]/'desktop/workspace/node_modules/axe-core/axe.min.js'
                for zoom in (100,200):
                    page.set_viewport_size({'width':1440*100//zoom,'height':1000*100//zoom})
                    for theme in ('dark','light'):
                        if page.locator('html').get_attribute('data-theme')!=theme:page.get_by_role('button',name='Appearance',exact=True).click();page.get_by_role('radio',name=theme.title(),exact=True).check();page.get_by_role('button',name='Close',exact=True).click()
                        page.add_script_tag(path=str(axe));result=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})");entry={'theme':theme,'zoom_percent':zoom,'overflow':page.evaluate('document.documentElement.scrollWidth>innerWidth'),'violations':[{'id':v['id'],'targets':[n['target'] for n in v['nodes']]} for v in result['violations']]};report['states'].append(entry);assert not entry['overflow'] and not entry['violations'],entry
                        voice.get_by_role('button',name='Record dictation',exact=True).focus();assert page.evaluate('document.activeElement.textContent')=='Record dictation';page.screenshot(path=str(destination/f'voice-{theme}-{zoom}.png'))
                assert len(report['provider_requests'])==2;assert report['provider_requests'][0]['real_recorded_webm'];assert report['provider_requests'][0]['model_selected'];assert report['provider_requests'][1]['input']=='Voice assistant reply fixture';assert adapter.turns==1;report['fixture_chat_turns']=adapter.turns
                report['production_bundle']=page.evaluate('Array.from(document.scripts).map(s=>s.src).find(s=>s.includes("/assets/index-"))?.split("/").pop()');assert not report['errors'];browser.close()
        finally:
            host.should_exit=True;thread.join(timeout=10);provider.shutdown();provider.server_close();(destination/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':print(json.dumps(run(sys.argv[1]),indent=2))
