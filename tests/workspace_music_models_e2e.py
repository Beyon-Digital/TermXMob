"""Opt-in Music model proof using immutable UI and real isolated authenticated routes.

Run: .venv/bin/python -B tests/workspace_music_models_e2e.py OUTPUT UI_DIST
All provider HTTP uses MockTransport. No real profiles, keys, paid calls or deployment.
"""
from __future__ import annotations
import asyncio, base64, hashlib, json, os, sys, threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from playwright.sync_api import expect, sync_playwright

REPO = Path(os.environ.get('TERMX_MUSIC_MODELS_REPO') or (Path(__file__).resolve().parents[1] if Path(__file__).parent.name == 'tests' else Path.cwd()))
sys.path.insert(0, str(REPO/'tests'))
import workspace_studio_projects_e2e as p
from workspace_studio_composer_refinement_e2e import focus_sample

PRO='google/lyria-3-pro-preview'
CLIP='google/lyria-3-clip-preview'
ELEVEN='elevenlabs/music_v2_5'
MUSIC_PATH='/api/media/studio/music/jobs'
MP3=(REPO/'tests/fixtures/music-silence.mp3').read_bytes()
# This fixture deliberately exceeds ElevenLabs section and line limits. It must
# remain exact text in Lyria's prompt-directed custom lyric operation.
LYRICS='[Verse]\n'+'A'*220+'\n'+''.join(f'Exact synthetic line {n}  \n' for n in range(35))+'\n[Chorus]\nOriginal fixture only.\n'

class Fragmented(httpx.AsyncByteStream):
    def __init__(self, raw): self.raw=raw
    async def __aiter__(self):
        for start in range(0,len(self.raw),37): yield self.raw[start:start+37]

def event(value): return b'data: '+json.dumps(value).encode()+b'\n\n'
def stream():
    return b': fixture keepalive\n\n'+b''.join(event({'choices':[{'index':0,'delta':{'audio':{'data':base64.b64encode(chunk).decode()}}}]}) for chunk in (MP3[:17],MP3[17:101],MP3[101:]))+event({'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})+b'data: [DONE]\n\n'

def seed(state, identifier, name, origin, capabilities):
    return state.agent.save_provider(provider_id=identifier,kind='openai-compatible',name=name,base_url=origin,model='synthetic-preserved-chat-model',capabilities=capabilities,api_key='synthetic-memory-only-key',wire='chat')

def no_secrets(value):
    raw=json.dumps(value)
    assert 'synthetic-memory-only-key' not in raw
    assert 'access_token' not in raw and 'refresh_token' not in raw

def capture(page,out,report,name,theme,width):
    return p.audit(page,out,report,name,theme,width,REPO/'desktop/workspace/node_modules/axe-core/axe.min.js')

def section(page,name):
    p.studio_section(page,name)
    p.close_studio_sidebar(page)

def assert_options(select, expected):
    actual=select.locator('option').evaluate_all('rows=>rows.map(e=>e.value)')
    assert set(actual)==set(expected),(actual,expected)
    return actual

def submit(page,api,state,owner,form,expected_model,title,report):
    form.get_by_role('textbox',name='Song title',exact=True).fill(title)
    before=len(report['provider_posts'])
    with page.expect_response(lambda response: urlsplit(response.url).path==MUSIC_PATH and response.request.method=='POST') as sent:
        form.get_by_role('button',name='Create',exact=True).click()
    response=sent.value
    assert response.ok,response.text()
    body=response.request.post_data_json
    receipt=response.json()
    assert body['model']==expected_model and receipt['kind']=='audio'
    p.wait(lambda:state.studio.get(owner.id,receipt['id'])['status']!='submitting',page=page)
    final=state.studio.get(owner.id,receipt['id'])
    assert final['status']=='completed',final
    assert len(final['outputs'])==1
    replay=api.post(MUSIC_PATH,json=body)
    assert replay.status_code==200 and replay.json()['id']==final['id'],replay.text
    refresh=api.post('/api/media/studio/jobs/'+final['id']+'/refresh')
    assert refresh.status_code==200 and refresh.json()['id']==final['id']
    assert len(report['provider_posts'])==before+1,'Duplicate provider POST after retry/refresh'
    blob=api.get(final['outputs'][0])
    assert blob.status_code==200 and blob.content==MP3 and blob.headers['content-type'].startswith('audio/mpeg')
    assert httpx.get(str(api.base_url).rstrip('/')+final['outputs'][0],trust_env=False).status_code==401
    artifact_id=final['outputs'][0].split('/')[4]
    try: state.artifacts.get('foreign-synthetic-user',artifact_id)
    except Exception as error: assert getattr(error,'status_code',None)==404,error
    else: raise AssertionError('A foreign owner could read generated audio')
    no_secrets(final)
    report['checks'].append({'check':'real-host-mock-transport-owned-binary-idempotent-retry','model':expected_model,'job':final['id'],'request_id':final['request_id'],'provider_posts':1,'blob_bytes':len(blob.content),'blob_sha256':hashlib.sha256(blob.content).hexdigest(),'unauthenticated_export':401,'foreign_owner_export':404})
    return final

def exercise(browser,out,report,ui_dist,width,theme):
    with p.isolated_host(ui_dist,report) as (state,owner,agent_project,base,scratch):
        seed(state,'synthetic-openrouter','Synthetic OpenRouter','https://openrouter.ai/api/v1',['shell','image','video'])
        catalog_fixture=REPO/'tests/fixtures/openrouter_music_catalog.json'
        live=json.loads(catalog_fixture.read_text())['models']
        assert {row['id'] for row in live}=={PRO,CLIP}
        inventory=live+[{'id':'synthetic/tts-only','architecture':{'input_modalities':['text'],'output_modalities':['audio']}}]
        def provider(request):
            row={'method':request.method,'host':request.url.host,'path':request.url.path,'query':str(request.url.query,'ascii')}
            report['backend_requests'].append(row)
            if request.method=='GET':
                assert 'authorization' not in request.headers,'Public catalog request included a key'
                if request.url.path=='/api/v1/models':
                    return httpx.Response(200,json={'data':inventory if request.url.params.get('output_modalities')=='audio' else []})
                if request.url.path in('/api/v1/images/models','/api/v1/videos/models'):
                    return httpx.Response(503,json={'error':'Synthetic visual catalog unavailable'})
            if request.method=='POST' and request.url.host=='openrouter.ai' and request.url.path=='/api/v1/chat/completions':
                body=json.loads(request.content)
                assert request.headers.get('authorization')=='Bearer synthetic-memory-only-key'
                assert body['model'] in(PRO,CLIP) and body['stream'] is True and body['modalities']==['text','audio'] and body['audio']=={'format':'mp3'}
                row.update(model=body['model'],stream=True)
                report['provider_posts'].append(row)
                if body['model']==PRO: assert LYRICS in body['messages'][0]['content'],'Lyria lyrics were rewritten or section-limited'
                return httpx.Response(200,stream=Fragmented(stream()),headers={'content-type':'text/event-stream'})
            if request.method=='POST' and request.url.host=='api.elevenlabs.io' and request.url.path=='/v1/music':
                assert request.headers.get('xi-api-key')=='synthetic-memory-only-key'
                row['model']=ELEVEN;report['provider_posts'].append(row)
                return httpx.Response(200,content=MP3,headers={'content-type':'audio/mpeg'})
            raise AssertionError('Unexpected outbound provider request: '+json.dumps(row))
        client=httpx.AsyncClient(transport=httpx.MockTransport(provider))
        state.studio.client=client;state.studio.openrouter.client=client
        context=browser.new_context(viewport={'width':width,'height':812 if width<640 else 900},has_touch=width<640,reduced_motion='reduce',accept_downloads=True)
        context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
        origin=urlsplit(base)
        def route(item):
            url=urlsplit(item.request.url)
            if url.scheme in('http','https') and (url.scheme,url.netloc)!=(origin.scheme,origin.netloc):
                report['external_browser_requests'].append(item.request.url);item.abort('blockedbyclient')
            elif item.request.method=='POST' and (url.path.startswith('/api/agent/tasks') or '/turns' in url.path):
                raise AssertionError('Unexpected Agent inference request')
            else: item.continue_()
        context.route('**/*',route)
        page=context.new_page();page.on('pageerror',lambda error:report['errors'].append(str(error)))
        api=None
        try:
            p.authenticate(page,base);section(page,'Music')
            music=page.get_by_role('region',name='Music studio',exact=True)
            form=music.get_by_role('form',name='Music generation',exact=True)
            expect(form.get_by_role('button',name='Create',exact=True)).to_be_disabled()
            expect(form).to_contain_text('Enable Music for your provider in Studio settings.')
            assert 'ElevenLabs' not in form.inner_text()
            capture(page,out,report,'music-needs-audio-opt-in',theme,width)
            seed(state,'synthetic-eleven','Synthetic ElevenLabs','https://api.elevenlabs.io',['audio'])
            form.get_by_role('button',name='Configure music provider',exact=True).click()
            settings=page.get_by_role('region',name='Studio settings',exact=True);expect(settings).to_be_visible()
            settings.get_by_role('button',name='Edit Synthetic OpenRouter',exact=True).click()
            editor=settings.get_by_role('form',name='Edit Studio provider',exact=True)
            audio=editor.get_by_role('checkbox',name='Allow music generation',exact=True)
            expect(audio).not_to_be_checked()
            audio.check()
            capture(page,out,report,'openrouter-audio-explicit-opt-in',theme,width)
            editor.get_by_role('button',name='Save provider changes',exact=True).click();expect(editor).not_to_be_visible()
            stored=state.agent_store.get_provider('synthetic-openrouter')
            assert set(stored['capabilities'])=={'shell','image','video','audio'} and stored['model']=='synthetic-preserved-chat-model' and stored['wire']=='chat',stored
            no_secrets(context.request.get(base+'/api/media/studio/catalog').json())
            settings.get_by_role('tab',name='Models & defaults',exact=True).click()
            search=settings.get_by_role('searchbox',name='Search Studio models',exact=True);search.fill('Lyria')
            expect(settings.get_by_role('checkbox',name='Enable Lyria 3 Pro',exact=True)).to_be_checked()
            expect(settings.get_by_role('checkbox',name='Enable Lyria 3 Clip',exact=True)).to_be_checked()
            dp=settings.get_by_role('combobox',name='Music default provider',exact=True)
            assert {'','synthetic-openrouter','synthetic-eleven'}==set(dp.locator('option').evaluate_all('rows=>rows.map(e=>e.value)'))
            dp.select_option('synthetic-openrouter')
            dm=settings.get_by_role('combobox',name='Music default model',exact=True);assert_options(dm,[PRO,CLIP]);dm.select_option(PRO)
            settings.get_by_role('spinbutton',name='Music duration target',exact=True).fill('184')
            capture(page,out,report,'music-model-defaults',theme,width)
            settings.get_by_role('button',name='Save Studio settings',exact=True).click()
            expect(settings.get_by_text('Studio settings saved.',exact=True)).to_be_visible()
            section(page,'Create')
            studio=page.get_by_role('region',name='Media studio',exact=True)
            for tab in('Image','Video'):
                studio.get_by_role('tab',name=tab,exact=True).click()
                assert not studio.locator('option').filter(has_text='Lyria').count(),'Music leaked into visual model choice'
            section(page,'Music')
            # A new default must initialize a fresh view, while an existing Music draft stays unchanged.
            page.goto(base+'/?workspace=studio&studio=music')
            expect(music).to_be_visible();p.close_studio_sidebar(page)
            expect(form.get_by_role('button',name='Create',exact=True)).to_be_enabled()
            model=form.get_by_role('combobox',name='Music model',exact=True);assert_options(model,[PRO,CLIP]);expect(model).to_have_value(PRO)
            focus_sample(page,model,'Music model',report,True)
            model.focus();page.keyboard.press('Alt+ArrowDown');page.keyboard.press('Escape');expect(model).to_have_value(PRO)
            form.get_by_role('textbox',name='Song description',exact=True).fill('Synthetic calm acoustic song fixture, never sent as a live provider request.')
            music.locator('.music-compose-scroll').evaluate('e=>e.scrollTop=0')
            capture(page,out,report,'lyria-pro-simple',theme,width)
            form.get_by_role('tab',name='Advanced',exact=True).click()
            lyrics=form.get_by_role('textbox',name='Lyrics',exact=True);lyrics.fill(LYRICS)
            form.get_by_role('textbox',name='Music styles',exact=True).fill('Synthetic calm acoustic piano fixture')
            assert 'Each section:' not in form.inner_text()
            expect(form.get_by_role('button',name='+ Verse',exact=True)).to_have_count(0)
            more=form.get_by_role('button',name='More options',exact=True);more.click()
            duration=form.get_by_role('spinbutton',name='Music duration target',exact=True)
            expect(duration).to_have_attribute('min','3');expect(duration).to_have_attribute('max','184');expect(duration).to_have_value('184')
            duration.fill('184');form.get_by_role('textbox',name='Exclude music styles',exact=True).fill('Synthetic heavy distortion exclusion')
            providers=form.get_by_role('combobox',name='Music provider account',exact=True);assert_options(providers,['synthetic-openrouter','synthetic-eleven'])
            focus_sample(page,duration,'Lyria duration target',report,True)
            reduced=music.locator('*').evaluate_all("rows=>rows.filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>({animation:getComputedStyle(e).animationName,transition:getComputedStyle(e).transitionDuration})).filter(s=>s.animation!=='none'||s.transition.split(',').some(v=>parseFloat(v)>0))")
            assert not reduced,('Reduced motion did not suppress Music animations/transitions',reduced)
            report.setdefault('motion',[]).append({'theme':theme,'width':width,'visible_music_motion_under_reduce':reduced})
            capture(page,out,report,'lyria-pro-custom-no-section-limit',theme,width)
            form.get_by_role('button',name='Expand lyrics editor',exact=True).click()
            dialog=page.get_by_role('dialog',name='Lyrics editor',exact=True);p.keyboard_dialog(page,dialog)
            expect(dialog.get_by_role('textbox',name='Full-screen lyrics',exact=True)).to_have_value(LYRICS)
            capture(page,out,report,'lyria-lyrics-keyboard-editor',theme,width)
            page.keyboard.press('Escape');expect(dialog).not_to_be_visible()
            api,_=p.owner_api(state,base)
            final=submit(page,api,state,owner,form,PRO,'Synthetic Lyria Pro fixture',report)
            music.get_by_role('button',name='Refresh My songs',exact=True).click()
            row=music.get_by_role('article',name='Synthetic Lyria Pro fixture · Completed',exact=True);expect(row).to_be_visible()
            row.get_by_role('button',name='Play Synthetic Lyria Pro fixture',exact=True).click()
            player=music.get_by_role('region',name='Music player',exact=True);audio_el=player.locator('audio')
            p.wait(lambda:audio_el.evaluate('e=>Number.isFinite(e.duration)&&e.duration>0'),page=page)
            p.wait(lambda:audio_el.evaluate('e=>e.currentTime>0.02'),page=page)
            player.get_by_role('button',name='Pause music',exact=True).click();assert audio_el.evaluate('e=>e.paused')
            seek=player.get_by_role('slider',name='Playback position',exact=True);seek.focus();page.keyboard.press('Home');page.keyboard.press('ArrowRight')
            metadata=audio_el.evaluate('e=>({duration:e.duration,currentTime:e.currentTime,readyState:e.readyState,type:e.getAttribute("type"),source:e.src})')
            metadata['source']='blob:owned-authenticated-audio';report['playback'].append({'theme':theme,'width':width,**metadata})
            with page.expect_download() as downloading:row.get_by_role('button',name='Download Synthetic Lyria Pro fixture',exact=True).click()
            download=downloading.value;assert Path(download.path()).read_bytes()==MP3
            capture(page,out,report,'lyria-owned-player',theme,width)
            # Test model and provider normalization while reusing the same draft.
            model.select_option(CLIP)
            expect(form.get_by_label('Fixed music duration 30 seconds',exact=True)).to_be_visible()
            expect(form.get_by_role('spinbutton',name='Music duration target',exact=True)).to_have_count(0)
            capture(page,out,report,'lyria-clip-fixed-thirty',theme,width)
            form.get_by_role('tab',name='Simple',exact=True).click()
            form.get_by_role('textbox',name='Song description',exact=True).fill('Synthetic 30-second instrumental fixture')
            # Title is available only in Advanced; preserve it before switching.
            form.get_by_role('tab',name='Advanced',exact=True).click();form.get_by_role('textbox',name='Lyrics',exact=True).fill('Synthetic short lyrics')
            submit(page,api,state,owner,form,CLIP,'Synthetic Lyria Clip fixture',report)
            providers.select_option('synthetic-eleven');assert_options(model,[ELEVEN])
            expect(form).to_contain_text('Each section:')
            expect(form.get_by_role('spinbutton',name='Music duration',exact=True)).to_have_attribute('max','120')
            form.get_by_role('spinbutton',name='Music duration',exact=True).fill('30')
            capture(page,out,report,'elevenlabs-kept-separate',theme,width)
            if width==1280 and theme=='dark':submit(page,api,state,owner,form,ELEVEN,'Synthetic ElevenLabs fixture',report)
            report['checks'].append({'check':'music-ui-multimodel-config-and-contracts','width':width,'theme':theme,'audio_capability_saved_explicitly':True,'preserved_agent_capabilities_model_and_wire':True,'models_only_music':True,'clip_fixed_seconds':30,'pro_target_min':3,'pro_target_max':184,'exact_long_line_and_35_line_custom_lyrics':True,'elevenlabs_section_help_and_custom_max_retained':True,'keyboard_lyrics_dialog_and_native_model_menu_escape':True,'reduced_motion':page.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"),'touch_context':width<640})
        except BaseException:
            page.screenshot(path=str(out/f'failure-{theme}-{width}.png'),full_page=True)
            page.locator('input[type=password]').evaluate_all('rows=>rows.forEach(e=>e.value="")')
            (out/f'failure-{theme}-{width}.html').write_text(page.content())
            raise
        finally:
            if api:api.close()
            context.close()
            cleanup=threading.Thread(target=lambda:asyncio.run(client.aclose()))
            cleanup.start();cleanup.join(timeout=5)
            assert not cleanup.is_alive(),"Fixture HTTP client did not close"

def run(destination,ui_dist):
    out=Path(destination).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'result':'running','mode':'after','output':str(out),'states':[],'checks':[],'focus':[],'playback':[],'backend_requests':[],'provider_posts':[],'errors':[],'external_browser_requests':[],'started_at_utc':datetime.now(timezone.utc).isoformat(),'ui_dist':str(Path(ui_dist).resolve()),'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'limitations':['Provider responses are explicit mock HTTP fixtures; no live or paid audio generation occurred.','Actual browser rendered an immutable production UI with managed password authentication and scratch SQLite storage.','Browser touch emulation does not establish physical mobile/device/native package behavior.','Full-document axe incomplete items remain in each capture; this is not a screen-reader audit.']}
    try:
        with sync_playwright() as playwright:
            executable=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE') or next((str(pth) for pth in (Path(playwright.chromium.executable_path),Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')) if pth.is_file()),None)
            browser=playwright.chromium.launch(headless=True,executable_path=executable)
            report['browser']={'version':browser.version,'executable':executable}
            for width,theme in((375,'light'),(1280,'dark'),(1280,'light'),(375,'dark')):
                p.phase(report,'starting-case',width=width,theme=theme)
                exercise(browser,out,report,ui_dist,width,theme)
            browser.close()
        assert not report['errors'] and not report['external_browser_requests'],report
        assert all(not item.get('violations') for item in report['states']),[(s['state'],s.get('violations')) for s in report['states'] if s.get('violations')]
        report['result']='passed'
    except BaseException as error:
        report.update(result='failed',failure=f'{type(error).__name__}: {error}');raise
    finally:
        report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
        no_secrets(report)
        (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    if len(sys.argv)!=3:raise SystemExit('Usage: workspace_music_models_e2e.py OUTPUT UI_DIST')
    result=run(sys.argv[1],sys.argv[2]);print(json.dumps({'result':result['result'],'states':len(result['states']),'checks':len(result['checks']),'provider_mock_posts':len(result['provider_posts']),'ui_manifest_digest':result.get('ui_manifest_digest')},indent=2))
