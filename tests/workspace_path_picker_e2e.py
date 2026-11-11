"""Actual folder/file picker proof on scratch authenticated hosts; no inference."""
from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright,expect
import workspace_studio_projects_e2e as fixture
from workspace_fork_context_e2e import TransferFixture

REPO=Path(__file__).resolve().parents[1]

def audit(page,out,report,state,theme,width):
    dialog=page.get_by_role('dialog',name='Choose file' if state=='files' else 'Choose folder',exact=True) if state in ('folders','nested','files') else page.get_by_role('dialog',name='Add project' if state=='selection' else 'New chat with options',exact=True)
    page.wait_for_function("()=>[...document.querySelectorAll('[role=dialog]')].every(e=>e.getAnimations().every(a=>a.playState!=='running'||a.effect?.getTiming().iterations===Infinity))")
    page.wait_for_function('(e)=>e.contains(document.activeElement)',arg=dialog.element_handle())
    for key in ('Tab','Tab','Shift+Tab','Tab'):
        page.keyboard.press(key)
        assert dialog.evaluate('e=>e.contains(document.activeElement)'), 'Focus escaped '+state
    data=page.evaluate('''()=>{const dialogs=[...document.querySelectorAll('[role=dialog]')].filter(e=>e.getBoundingClientRect().width&&getComputedStyle(e).visibility!=='hidden'&&!e.closest('[inert]'));return{overflow:document.documentElement.scrollWidth>innerWidth,dialogs:dialogs.map(e=>({rect:e.getBoundingClientRect().toJSON(),border:getComputedStyle(e).borderWidth})),focus:{outline:getComputedStyle(document.activeElement).outlineStyle,width:parseFloat(getComputedStyle(document.activeElement).outlineWidth)},controls:dialogs.at(-1)?[...dialogs.at(-1).querySelectorAll('button,input,summary')].filter(e=>e.getBoundingClientRect().width&&e.getBoundingClientRect().height).map(e=>({tag:e.tagName,height:e.getBoundingClientRect().height,font:getComputedStyle(e).fontSize})):[]}}''')
    assert not data['overflow'],data
    for item in data['dialogs']:
        rect=item['rect'];assert rect['x']>=0 and rect['right']<=width+1 and rect['y']>=0 and rect['bottom']<=page.viewport_size['height']+1,rect
    assert data['focus']['outline']!='none' and data['focus']['width']>=2,data['focus']
    if width==375:
        for control in data['controls']:
            if control['tag']=='BUTTON':assert control['height']>=40,control
    axe=REPO/'desktop/workspace/node_modules/axe-core/axe.min.js'
    if not page.evaluate("typeof axe!=='undefined'"):page.add_script_tag(path=str(axe))
    a11y=page.evaluate("async()=>await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}})")
    data['violations']=[{'id':v['id'],'targets':[n['target']for n in v['nodes']]}for v in a11y['violations']]
    data['incomplete']=[v['id']for v in a11y['incomplete']]
    assert not data['violations'],data['violations']
    name=f'{state}-{theme}-{width}.png';page.screenshot(path=str(out/name),full_page=True)
    report['states'].append({'state':state,'theme':theme,'width':width,'screenshot':name,**data})
    (out/'partial-report.json').write_text(json.dumps(report,indent=2)+'\n')


def run(output,dist):
    out=Path(output).resolve();out.mkdir(parents=True,exist_ok=False)
    report={'result':'running','output':str(out),'mode':'picker','states':[],'checks':[],'errors':[],'external_requests':[],'backend_requests':[],'forbidden_mutations':[],'limitations':['Scratch local stores and synthetic project contents only. No real user folders were listed.','No inference, provider calls, native window, or paid actions.','Native API bridge compatibility is source-checked; these captures use web Chrome.'],'runner_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True,executable_path='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome');report['browser']=browser.version
            for width,height,theme in ((375,812,'dark'),(375,812,'light'),(1280,900,'dark'),(1280,900,'light')):
                print(json.dumps({'case':theme,'width':width}),flush=True)
                with fixture.isolated_host(dist,report) as (state,owner,project,base,scratch),patch('termx.workspace.path_http.Path.home',return_value=scratch):
                    chosen=scratch/'projects'/'selected-project';chosen.mkdir(parents=True);chosen=chosen.resolve()
                    (chosen/'unchanged.txt').write_text('Synthetic project remains unchanged.\n')
                    source=Path(project['path']);(source/'src').mkdir();(source/'src'/'main.ts').write_text('export const selected = true;\n');(source/'.hidden').mkdir()
                    adapter=TransferFixture();state.engines.register(adapter);descriptor=adapter.descriptor().as_dict();descriptor['capabilities']=adapter.capabilities().as_dict();state.engines.catalogue.entries['fixture-transfer']={'descriptor':descriptor,'configuration':{'models':['fixture-target']},'refreshed_at':1,'stale':False,'refresh_error':None}
                    state.agent_store.put_provider('picker-fixture',kind='openai-compatible',name='Offline picker fixture',base_url='http://127.0.0.1:1/v1',model='fixture',capabilities=['chat'],secret_configured=False)
                    session=state.workspace.create_session(owner,title='Synthetic file context',project_id=project['id'],cwd=str(source),provider_id='picker-fixture',model='fixture',mode='ask')
                    state.agent_store.add_conversation_turn(session['id'],prompt='Synthetic context already recorded; no inference occurred.',mode='ask')
                    context=browser.new_context(viewport={'width':width,'height':height},has_touch=width<640,reduced_motion='reduce')
                    context.add_init_script('localStorage.setItem("termx-shell-theme",'+json.dumps(theme)+')')
                    origin=urlsplit(base)
                    def guard(route):
                        r=route.request;url=urlsplit(r.url)
                        if url.scheme in ('http','https') and (url.scheme,url.netloc)!=(origin.scheme,origin.netloc):report['external_requests'].append(url.path);route.abort();return
                        if r.method!='GET'and ('/turns'in url.path or '/api/media/'in url.path):report['forbidden_mutations'].append(url.path);route.abort();return
                        route.continue_()
                    context.route('**/*',guard);page=context.new_page();page.set_default_timeout(15000);page.on('pageerror',lambda error:report['errors'].append(str(error)))
                    try:
                        page.goto(base+'/manage/projects?workspace=agent');page.get_by_label('Username',exact=True).fill(fixture.USERNAME);page.get_by_label('Password',exact=True).fill(fixture.PASSWORD);page.get_by_role('button',name='Continue with password',exact=True).click()
                        expect(page.get_by_role('heading',name='Projects',exact=True)).to_be_visible(timeout=60000)
                        hide=page.get_by_role('navigation',name='Workspace navigation',exact=True).get_by_role('button',name='Hide sidebar',exact=True)
                        if width<640 and hide.is_visible():hide.click()
                        page.get_by_role('region',name='Projects workspace',exact=True).get_by_role('button',name='Add project',exact=True).click();add=page.get_by_role('dialog',name='Add project',exact=True)
                        expect(add.get_by_label('Folder path',exact=True)).not_to_be_visible();add.get_by_role('button',name='Browse folders',exact=True).click();picker=page.get_by_role('dialog',name='Choose folder',exact=True)
                        expect(picker.get_by_role('button',name='Open folder projects',exact=True)).to_be_visible();audit(page,out,report,'folders',theme,width)
                        picker.get_by_role('textbox',name='Filter folders',exact=True).fill('projects');expect(picker.get_by_role('button',name='Open folder agent-project',exact=True)).to_have_count(0);picker.get_by_role('button',name='Open folder projects',exact=True).click();picker.get_by_role('button',name='Open folder selected-project',exact=True).click();expect(picker.get_by_text('No folders here.',exact=True)).to_be_visible();audit(page,out,report,'nested',theme,width)
                        picker.get_by_role('button',name='Parent folder',exact=True).click();picker.get_by_role('button',name='Open folder selected-project',exact=True).click();expect(picker.get_by_text('No folders here.',exact=True)).to_be_visible();picker.get_by_role('button',name='Choose folder',exact=True).click();expect(picker).not_to_be_visible();expect(add.get_by_text(str(chosen),exact=True)).to_be_visible();audit(page,out,report,'selection',theme,width)
                        add.get_by_role('button',name='Add project',exact=True).click();expect(add).not_to_be_visible();fixture.wait(lambda:any(p['path']==str(chosen)for p in state.projects.projects()),page)
                        assert (chosen/'unchanged.txt').read_text()=='Synthetic project remains unchanged.\n'
                        page.goto(base+'/session/'+session['id']);expect(page.get_by_label('Message',exact=True)).to_be_enabled(timeout=60000)
                        back=page.get_by_role('button',name='Back to chat',exact=True)
                        if back.is_visible():back.click()
                        expect(page.get_by_label('Message',exact=True)).to_be_visible()
                        page.get_by_role('button',name='Workspace actions',exact=True).click();page.get_by_role('button',name='New chat with options',exact=True).click();new=page.get_by_role('dialog',name='New chat with options',exact=True);new.get_by_role('button',name='Browse folders',exact=True).click();picker=page.get_by_role('dialog',name='Choose folder',exact=True);picker.get_by_role('button',name='Open folder agent-project',exact=True).click();expect(picker.get_by_role('button',name='Open folder src',exact=True)).to_be_visible();picker.get_by_role('button',name='Choose folder',exact=True).click();expect(new.get_by_text(str(source),exact=True)).to_be_visible();audit(page,out,report,'new-chat',theme,width);page.keyboard.press('Escape');expect(new).not_to_be_visible()
                        page.get_by_role('button',name='Session options',exact=True).click();page.get_by_label('Session engine',exact=True).select_option('fixture-transfer');fork=page.get_by_role('dialog',name='Continue with fixture-transfer',exact=True);fork.get_by_role('button',name='Browse files',exact=True).click();files=page.get_by_role('dialog',name='Choose file',exact=True);files.get_by_role('button',name='Open folder src',exact=True).click();files.get_by_role('button',name='Select file main.ts',exact=True).click();expect(files.get_by_role('button',name='Choose file',exact=True)).to_be_enabled();expect(files.get_by_role('button',name='Home folder',exact=True)).to_have_count(0);audit(page,out,report,'files',theme,width)
                        files.get_by_role('button',name='Choose file',exact=True).click();expect(fork.get_by_role('listitem').get_by_text('src/main.ts',exact=True)).to_be_visible();fork.get_by_role('button',name='Remove file src/main.ts',exact=True).click();expect(fork.get_by_text('No files selected.',exact=True)).to_be_visible();fork.get_by_role('button',name='Browse files',exact=True).click();files=page.get_by_role('dialog',name='Choose file',exact=True);files.get_by_role('button',name='Open folder src',exact=True).click();files.get_by_role('button',name='Select file main.ts',exact=True).click();files.get_by_role('button',name='Choose file',exact=True).click();fork.get_by_role('button',name='Preview transferred context',exact=True).click();preview=fork.get_by_role('region',name='Context transfer preview',exact=True);expect(preview).to_be_visible();transfer=json.loads(preview.inner_text());assert transfer['files'][0]['path']=='src/main.ts'and transfer['files'][0]['content']=='export const selected = true;\n'
                        report['checks'].append({'theme':theme,'width':width,'folder_browse_registration':True,'new_chat_folder_browse':True,'scoped_file_browse_remove_and_preview':True,'keyboard_focus_trapped':True,'source_files_preserved':True,'provider_requests':0})
                    except BaseException:
                        page.screenshot(path=str(out/'failure.png'),full_page=True);raise
                    finally:context.close()
            browser.close()
        assert not report['errors']and not report['external_requests']and not report['forbidden_mutations'],report
        report['result']='passed'
    except BaseException as error:report.update(result='failed',failure=f'{type(error).__name__}: {error}');raise
    finally:(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':
    report=run(sys.argv[1],sys.argv[2]);print(json.dumps({'result':report['result'],'states':len(report['states']),'checks':len(report['checks'])}))
