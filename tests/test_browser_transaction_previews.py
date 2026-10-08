"""Real localhost browser evidence; no external transactions or provider calls."""
import asyncio
import os

import pytest

from termx.auto_review import ActionBlocked, ReviewRequired
from termx.browser.service import BrowserService

pytestmark=pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'),reason='managed Chromium artifact required')


def test_ancestor_hidden_input_requires_manual_without_publishing_value(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        try:
            profile=service.create_profile('owner','project','Visible evidence fixture')
            tab=await service.create_tab('owner','sid',profile['id'],site);page=service._pages[tab['id']]
            await page.evaluate("""()=>{document.body.innerHTML='<form data-merchant="Fixture merchant" data-amount="1.50" data-currency="EUR"><div style="opacity:0"><input name=account value=NEVER_STORE_INVISIBLE_ANCESTOR></div><button id=buy>Buy order</button></form>';window.effects=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.effects++}}""")
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','click'],policy_version=1)
            current=service.get(tab['id'],'owner')
            with pytest.raises(ActionBlocked) as blocked:
                await service.action(tab['id'],'owner',session_id='sid',run_id='task',grant_id=grant['grant']['id'],policy_version=1,action_id='invisible-ancestor',action='click',args={'selector':'#buy'},document_revision=current['document_revision'],lease_revision=current['lease_revision'])
            preview=blocked.value.record['human_preview']
            assert preview['manual_required'] and preview['fields']==[] and preview['website_account'] is None
            assert 'NEVER_STORE_INVISIBLE_ANCESTOR' not in service.records.path.read_text(errors='ignore')
            assert await page.evaluate('window.effects')==0
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())


def test_exact_purchase_preview_redacts_and_binds_amount_before_one_effect(tmp_path):
    from test_managed_browser import website
    async def run():
        server,site=await website();service=BrowserService(tmp_path,development_origins=[site])
        try:
            profile=service.create_profile('owner','project','Fixture account profile')
            tab=await service.create_tab('owner','sid',profile['id'],site);page=service._pages[tab['id']]
            await page.evaluate("""()=>{document.body.innerHTML='<form data-merchant="Fixture merchant" data-amount="12.50" data-currency="EUR"><label>Item<input name=item value="Sample order"></label><input type=hidden name=csrf value="NEVER_STORE_HIDDEN_TOKEN"><button id=buy>Buy order</button></form>';window.effects=0;document.querySelector('form').onsubmit=e=>{e.preventDefault();window.effects++}}""")
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','click'],policy_version=1)
            async def act(identifier):
                current=service.get(tab['id'],'owner')
                return await service.action(tab['id'],'owner',session_id='sid',run_id='task',grant_id=grant['grant']['id'],policy_version=1,action_id=identifier,action='click',args={'selector':'#buy'},document_revision=current['document_revision'],lease_revision=current['lease_revision'])
            with pytest.raises(ReviewRequired) as pending:await act('purchase-old')
            preview=pending.value.record['human_preview']
            assert preview['merchant']=='Fixture merchant' and preview['amount']=='12.50' and preview['currency']=='EUR'
            assert preview['profile_name']=='Fixture account profile' and preview['fields']==[{'label':'Item item','type':'text','value':'Sample order'}]
            assert not preview['manual_required'] and preview['document_hash']==service.records.get('browser-action-document','purchase-old')['hash']
            assert 'NEVER_STORE_HIDDEN_TOKEN' not in service.records.path.read_text(errors='ignore')
            service.review.decide('purchase-old',principal_id='owner',approve=True)
            await page.locator('form').evaluate("e=>e.dataset.amount='13.50'")
            with pytest.raises(ValueError,match='document changed'):await act('purchase-old')
            assert await page.evaluate('window.effects')==0
            with pytest.raises(ReviewRequired) as fresh:await act('purchase-fresh')
            assert fresh.value.record['human_preview']['amount']=='13.50'
            service.review.decide('purchase-fresh',principal_id='owner',approve=True)
            await act('purchase-fresh')
            with pytest.raises(ReviewRequired):await act('purchase-fresh')
            assert await page.evaluate('window.effects')==1
            # CSS/semantic-hidden controls can still submit. Their values must
            # never be published as visible evidence or silently approved.
            for index,style in enumerate(('style="display:none"','style="visibility:hidden"','style="opacity:0"','hidden','aria-hidden="true"')):
                await page.locator('form').evaluate('(e,style)=>e.insertAdjacentHTML("afterbegin",`<input id=invisible name=account ${style} value=NEVER_STORE_INVISIBLE>`)',style)
                with pytest.raises(ActionBlocked) as invisible:await act(f'invisible-purchase-{index}')
                assert invisible.value.record['human_preview']['manual_required']
                assert 'NEVER_STORE_INVISIBLE' not in str(invisible.value.record['human_preview'])
                assert 'NEVER_STORE_INVISIBLE' not in service.records.path.read_text(errors='ignore')
                await page.locator('#invisible').evaluate('e=>e.remove()')
            # A hidden marker alone cannot supply merchant/charge evidence.
            await page.locator('form').evaluate("e=>{e.removeAttribute('data-merchant');e.insertAdjacentHTML('afterbegin','<span itemprop=seller style=display:none>NEVER_STORE_HIDDEN_MERCHANT</span>')}")
            with pytest.raises(ActionBlocked) as marker:await act('invisible-merchant')
            assert marker.value.record['human_preview']['merchant'] is None
            assert 'NEVER_STORE_HIDDEN_MERCHANT' not in service.records.path.read_text(errors='ignore')
            await page.locator('form').evaluate("e=>{e.dataset.merchant='Fixture merchant';e.querySelector('span').remove()}")
            # A visible payment credential or missing merchant/amount evidence
            # must not become an automated operation after a generic approval.
            await page.locator('form').evaluate("e=>e.insertAdjacentHTML('afterbegin','<input autocomplete=cc-number value=NEVER_STORE_CARD>')")
            with pytest.raises(ActionBlocked) as blocked:await act('sensitive-purchase')
            assert blocked.value.record['human_preview']['manual_required']
            assert '[redacted]' in str(blocked.value.record['human_preview'])
            assert 'NEVER_STORE_CARD' not in service.records.path.read_text(errors='ignore')
            await page.evaluate("()=>document.body.innerHTML='<button id=buy>Purchase unknown order</button>'")
            with pytest.raises(ActionBlocked) as unavailable:await act('missing-purchase')
            assert unavailable.value.record['human_preview']['manual_required'] and await page.evaluate('window.effects')==1
        finally:
            await service.close();server.close();await server.wait_closed()
    asyncio.run(run())


def test_scoped_data_requests_refuse_cross_origin_fetch_but_allow_passive_asset(tmp_path):
    from test_managed_browser import website
    async def run():
        requests=[];first,site=await website()
        async def passive_server(reader,writer):
            try:
                head=await reader.readuntil(b'\r\n\r\n');requests.append(head.split(b' ')[1].decode())
                image=b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8"><rect width="8" height="8" fill="green"/></svg>'
                writer.write(f'HTTP/1.1 200 OK\r\nContent-Type:image/svg+xml\r\nContent-Length:{len(image)}\r\nConnection:close\r\n\r\n'.encode()+image);await writer.drain()
            finally:writer.close()
        second=await asyncio.start_server(passive_server,'127.0.0.1',0);other=f'http://127.0.0.1:{second.sockets[0].getsockname()[1]}'
        service=BrowserService(tmp_path,development_origins=[site,other])
        try:
            profile=service.create_profile('owner','project','Scoped request fixture');tab=await service.create_tab('owner','sid',profile['id'],site);page=service._pages[tab['id']]
            await page.evaluate("()=>{document.body.innerHTML='<label><input id=flag type=checkbox>Send flag</label>';window.requests=0}")
            await page.locator('#flag').evaluate('(e,url)=>e.onclick=()=>fetch(url,{method:"POST",mode:"no-cors",body:"FIXTURE_DATA"}).catch(()=>{})',other+'/leak')
            grant=await service.handoff(tab['id'],'owner','sid',run_id='task',origins=[site],actions=['observe','click'],policy_version=1)
            assert (await service._effect(page,'click',{'selector':'#flag'}))[0]=='unknown'
            async with page.expect_response(lambda r:r.url==other+'/passive'):
                await page.evaluate('(url)=>{const img=document.createElement("img");img.src=url;document.body.append(img)}',other+'/passive')
            assert '/passive' in requests and service.get(tab['id'],'owner')['state']=='agent'
            current=service.get(tab['id'],'owner')
            options=dict(session_id='sid',run_id='task',grant_id=grant['grant']['id'],policy_version=1,action_id='custom-checkbox',action='click',args={'selector':'#flag'},document_revision=current['document_revision'],lease_revision=current['lease_revision'])
            with pytest.raises(ActionBlocked) as pending:await service.action(tab['id'],'owner',**options)
            assert pending.value.record['effect']=='unknown' and pending.value.record['human_preview']['target_label']
            assert pending.value.record['human_preview']['manual_required'] and not await page.locator('#flag').is_checked()
            # A page's own script can also attempt an ungranted data request;
            # the current agent-controlled lease still enforces its origins.
            async with page.expect_event('requestfailed',predicate=lambda request:request.url==other+'/leak'):
                await page.evaluate('(url)=>{void fetch(url,{method:"POST",mode:"no-cors",body:"FIXTURE_DATA"}).catch(()=>{})}',other+'/leak')
            assert '/leak' not in requests and service.records.get('grant',grant['grant']['id'])['revoked']
            assert service.get(tab['id'],'owner')['transport_restricted']
            # Revocation must not implicitly give a page script human network
            # authority: automatic retries remain refused after state=human.
            for _ in range(2):
                async with page.expect_event('requestfailed',predicate=lambda request:request.url==other+'/leak'):
                    await page.evaluate('(url)=>{void fetch(url,{method:"POST",mode:"no-cors",body:"FIXTURE_DATA"}).catch(()=>{})}',other+'/leak')
            assert '/leak' not in requests
            # Initial popup requests can precede frame attribution. Adoption
            # must retain the same takeover boundary, including later retries.
            async with page.expect_popup() as opening:
                await page.evaluate('(url)=>{window.open(url)}',other+'/popup')
            popup=await opening.value
            async with asyncio.timeout(10):
                while True:
                    popup_id=next((identifier for identifier,target in service._pages.items() if target==popup),None)
                    if popup_id and popup_id in service._capture_cdp:break
                    await asyncio.sleep(.025)
            assert popup_id and service.get(popup_id,'owner')['transport_restricted']
            async with popup.expect_event('requestfailed',predicate=lambda request:request.url==other+'/popup-retry'):
                await popup.evaluate('(url)=>{void fetch(url,{method:"POST",mode:"no-cors",body:"FIXTURE_DATA"}).catch(()=>{})}',other+'/popup-retry')
            assert '/popup' not in requests and '/popup-retry' not in requests
            assert any(e.get('reason')=='browser request destination outside live scoped origins' for e in service.records.events(100))
            service.takeover(tab['id'],'owner')
            assert not service.get(tab['id'],'owner').get('transport_restricted')
            async with page.expect_response(lambda response:response.url==other+'/human'):
                await page.evaluate('(url)=>{void fetch(url,{method:"POST",mode:"no-cors",body:"HUMAN_FIXTURE_DATA"}).catch(()=>{})}',other+'/human')
            assert '/human' in requests
        finally:
            await service.close();first.close();second.close();await first.wait_closed();await second.wait_closed()
    asyncio.run(run())
