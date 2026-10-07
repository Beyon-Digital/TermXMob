from termx.browser.service import BrowserService

def test_abrupt_restart_purges_ephemeral_cookie_and_owned_artifacts_but_preserves_persistent_profiles(tmp_path):
    service=BrowserService(tmp_path)
    temporary=service.create_profile('owner','project','Throwaway',ephemeral=True)
    persistent=service.create_profile('owner','project','Signed in')
    for profile in (temporary,persistent):
        (service.records.root/'profiles'/profile['id']/'Cookies').write_text('private fixture cookie')
        service.records.put('tab','tab-'+profile['id'],{'id':'tab-'+profile['id'],'principal_id':'owner','profile_id':profile['id'],'state':'human','lease_revision':1,'grant_id':None})
        service.records.put('history','history-'+profile['id'],{'id':'history-'+profile['id'],'profile_id':profile['id']})
    service.records.put('annotation-frame','frame',{'id':'frame','profile_id':temporary['id']})
    (service.records.root/'annotation-frames').mkdir(exist_ok=True)
    (service.records.root/'annotation-frames'/'frame').write_bytes(b'private fixture jpeg')
    restarted=BrowserService(tmp_path) # No graceful close: emulate persisted state after host failure.
    assert restarted.records.get('profile',temporary['id']) is None
    assert not (restarted.records.root/'profiles'/temporary['id']).exists()
    assert not (restarted.records.root/'annotation-frames'/'frame').exists()
    assert restarted.records.get('tab','tab-'+temporary['id']) is None
    assert restarted.records.get('history','history-'+temporary['id']) is None
    assert restarted.records.get('profile',persistent['id'])==persistent
    assert (restarted.records.root/'profiles'/persistent['id']/'Cookies').read_text()=='private fixture cookie'
    assert restarted.records.get('history','history-'+persistent['id'])
