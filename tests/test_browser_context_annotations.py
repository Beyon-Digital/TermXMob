"""Real owned annotation snapshots, structural references and private consent."""
import asyncio,io,os
import pytest
from termx.browser.service import BrowserService

@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')
def test_real_element_region_snapshots_mask_frames_and_revoke_diagnostics(tmp_path):
    from test_managed_browser import website
    from PIL import Image
    async def run():
        server,site=await website();live=True
        service=BrowserService(tmp_path,development_origins=[site],session_valid=lambda *_:live)
        try:
            profile=service.create_profile('owner','project','Annotation fixture');tab=await service.create_tab('owner','sid',profile['id'],site);page=service._pages[tab['id']]
            await page.evaluate("html=>document.body.innerHTML=html", '<h1>Annotation target</h1><label for="draft">Visible label</label><input id="draft" value="PRIVATE_VALUE" style="position:absolute;left:20px;top:100px;width:180px;height:40px"><iframe style="position:absolute;left:400px;top:100px;width:300px;height:180px" srcdoc="&lt;input value=&quot;IFRAME_PRIVATE&quot; style=&quot;width:200px;height:50px&quot;&gt;"></iframe><button>Preview</button>')
            await page.frame_locator('iframe').locator('input').wait_for()
            observed=await service.observe(tab['id'],'owner',human=True)
            field=next(e for e in observed['elements'] if e['tag']=='input');assert field['name']=='Visible label' and field['selector']
            assert await page.locator(field['selector']).count()==1
            assert 'PRIVATE_VALUE' not in str(observed) and 'IFRAME_PRIVATE' not in str(observed)
            note=await service.annotate_context(tab['id'],'owner',revision=observed['document_revision'],comment='Exact field',selector=field['selector'],context_hash=field['context_hash'],include_screenshot=True)
            assert note['element']==field and note['frame']['kind']=='main' and note['screenshot']['redaction']
            image=Image.open(io.BytesIO((service.records.root/'annotation-frames'/note['screenshot']['id']).read_bytes())).convert('RGB')
            for point in ((60,120),(450,135)):
                r,g,b=image.getpixel(point);assert r>220 and b>220 and g<50,(point,(r,g,b))
            calls=0
            def expired():
                nonlocal calls
                calls+=1;return calls==1
            before=list((service.records.root/'annotation-frames').iterdir())
            with pytest.raises(PermissionError,match='authority expired'):
                await service.annotate_context(tab['id'],'owner',revision=observed['document_revision'],comment='Revoked during capture',region={'x':0,'y':0,'width':1,'height':1},include_screenshot=True,authority=expired)
            assert list((service.records.root/'annotation-frames').iterdir())==before and len(service.records.list('annotation'))==1
            region={'x':400,'y':100,'width':300,'height':180}
            regional=await service.annotate_context(tab['id'],'owner',revision=observed['document_revision'],comment='Iframe region',region=region)
            assert regional['region']==region and regional['screenshot'] is None
            await page.locator('#draft').evaluate("e=>e.setAttribute('aria-label','Changed target')")
            compared=await service.compare_annotation(tab['id'],'owner',note['id'],revision=observed['document_revision'],include_screenshot=True)
            assert compared['same_page'] and compared['target_available'] and compared['changed']
            assert compared['before']['element']==field and compared['after']['element']['name']=='Changed target'
            assert compared['after']['screenshot']['id']!=note['screenshot']['id']
            assert service.records.get('annotation',note['id'])['element']==field
            resolved=service.resolve_annotation(tab['id'],'owner',note['id'],resolved=True,revision=observed['document_revision'])
            assert resolved['resolved'] and resolved['element']==field
            assert service.resolve_annotation(tab['id'],'owner',note['id'],resolved=False,revision=observed['document_revision'])['resolved'] is False
            with pytest.raises(KeyError):service.resolve_annotation(tab['id'],'stranger',note['id'],resolved=True,revision=observed['document_revision'])
            with pytest.raises(PermissionError):await service.compare_annotation(tab['id'],'owner',note['id'],revision=observed['document_revision'],authority=lambda:False)
            with pytest.raises(ValueError,match='Element changed'):await service.annotate_context(tab['id'],'owner',revision=observed['document_revision'],comment='Wrong target',selector=field['selector'],context_hash=field['context_hash'])
            for bad in ({'x':-1,'y':0,'width':1,'height':1},{'x':0,'y':0,'width':float('nan'),'height':1},{'x':1440,'y':0,'width':1,'height':1}):
                with pytest.raises(ValueError):await service.annotate_context(tab['id'],'owner',revision=observed['document_revision'],comment='Outside viewport',region=bad)
            with pytest.raises(PermissionError):await service.human_action(tab['id'],'owner','diagnostics',{'view':'dom'})
            service.diagnostic_consent(tab['id'],'owner','sid',1,True)
            await page.evaluate("console.log('Visible diagnostic')")
            assert any('Visible diagnostic' in row['text'] for row in (await service.human_diagnostics(tab['id'],'owner','console'))['console'])
            assert 'PRIVATE_VALUE' not in str(await service.human_diagnostics(tab['id'],'owner','dom'))
            live=False
            with pytest.raises(PermissionError):await service.human_diagnostics(tab['id'],'owner','performance')
            live=True;service.takeover(tab['id'],'owner',private=True)
            with pytest.raises(PermissionError):service.annotations(tab['id'],'owner')
            with pytest.raises(PermissionError):await service.annotate_context(tab['id'],'owner',revision=observed['document_revision'],comment='Private')
            with pytest.raises(PermissionError):await service.frame(tab['id'],'owner',human=True,redacted=True)
            assert (await service.frame(tab['id'],'owner',human=True))[:2]==b'\xff\xd8' # human private viewer still works
            service.takeover(tab['id'],'owner')
            with pytest.raises(PermissionError):await service.human_diagnostics(tab['id'],'owner','console')
            await service.human_action(tab['id'],'owner','navigate',{'url':site+'/next'})
            assert all(row['stale'] for row in service.annotations(tab['id'],'owner'))
            latest=service.get(tab['id'],'owner')
            different=await service.compare_annotation(tab['id'],'owner',note['id'],revision=latest['document_revision'])
            assert not different['same_page'] and not different['target_available'] and different['after']['element'] is None
            notes=service.annotations(tab['id'],'owner')
            assert len(notes)==2 and len(next(row for row in notes if row['id']==note['id'])['comparisons'])==2
            history=service.records.list('history');assert all(service.records.get('history',row['id'])==row for row in history)
            await service.clear_profile(profile['id'],'owner')
            assert not list((service.records.root/'annotation-frames').iterdir())
        finally:await service.close();server.close();await server.wait_closed()
    asyncio.run(run())
