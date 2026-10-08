import asyncio
import os
import pytest
from termx.browser.service import BrowserService
from termx.browser.skills import BrowserSkills


@pytest.mark.skipif(not os.environ.get('PLAYWRIGHT_BROWSERS_PATH'), reason='managed Chromium artifact required')
def test_recorded_browser_skill_parameterization_actual_replay_then_edit_invalidates_activation(tmp_path):
    from test_managed_browser import website
    async def run():
        server, site = await website(); browser = BrowserService(tmp_path, development_origins=[site]); skills = BrowserSkills(browser)
        try:
            profile = browser.create_profile('owner', 'project', 'Test fixture')
            source = await browser.create_tab('owner', 'sid', profile['id'], site)
            target = await browser.create_tab('owner', 'sid', profile['id'], site)
            browser.recording(source['id'], 'owner', True)
            await browser.human_action(source['id'], 'owner', 'type', {'selector': '#draft', 'text': 'DONT_SAVE_SOURCE_VALUE', 'parameter': 'query'})
            await browser.human_action(source['id'], 'owner', 'click', {'selector': '#mystery'})
            browser.recording(source['id'], 'owner', False)
            draft = browser.skill_draft(source['id'], 'owner', 'Browser fixture')
            assert draft['parameters'] == ['query'] and len(draft['steps']) == 2
            with pytest.raises(PermissionError): skills.publish(draft['id'], 'owner', object(), object(), '1.0.0')
            tested = await skills.test(draft['id'], 'owner', target['id'], {'query': 'DONT_SAVE_TEST_VALUE'}, True, lambda: True)
            assert tested['test']['steps_executed'] == 2
            assert await browser._pages[target['id']].locator('h1').inner_text() == 'CHANGED'
            assert await browser._pages[target['id']].locator('#draft').input_value() == 'DONT_SAVE_TEST_VALUE'
            assert 'DONT_SAVE' not in browser.records.path.read_bytes().decode(errors='ignore')
            skills.edit(draft['id'], 'owner', tested['content'] + '\nEdited instructions\n')
            with pytest.raises(PermissionError): skills.publish(draft['id'], 'owner', object(), object(), '1.0.1')
        finally: await browser.close(); server.close(); await server.wait_closed()
    asyncio.run(run())
