from __future__ import annotations
import asyncio
import base64
import json
import os
import secrets
import shutil
from pathlib import Path
from time import time
from time import monotonic
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from typing import Callable
from termx.auto_review import ActionEnvelope,DecisionBroker,canonical_hash,ReviewRequired
from termx.browser.network import NetworkPolicy,EgressProxy,origin,TargetDenied
from termx.browser.storage import Records

class BrowserService:
    """A host-owned Chromium worker with persistent isolated browser profiles.

    Never attaches to existing desktop Chrome, exposes CDP or imports cookies.
    Browser and profile processes run as the host OS account; this is a trusted
    shared-host boundary until deployment supplies restricted OS identities.
    """
    def __init__(self,root:Path,*,review:DecisionBroker|None=None,development_origins=(),executable_path=None,session_valid:Callable[[str,str,int],bool]|None=None):
        self.records=Records(root);self.review=review or DecisionBroker(self.records)
        self.network=NetworkPolicy(development_origins);self.executable_path=executable_path
        self.session_valid=session_valid or (lambda principal,session,policy_version:True)
        self.task_summary=lambda task_id:''
        self.task_live=lambda task_id:True # App gateway replaces this with its canonical task ledger.
        self.claim_tab=lambda principal,tab:None # Router binds durable ownership for agent-created tabs.
        self._playwright=None;self._contexts={};self._pages={};self._proxies={};self._locks={};self._diagnostics={};self._start_lock=asyncio.Lock();self._monitor=None
        self._views={};self._permission_ports={};self._permission_origins=set();self._human_diagnostics={};self._capture_cdp={};self._capture_jobs={};self._frame_cache={};self._control_waiters={};self._closed_contexts=set();self._context_tabs={}
        for tab in self.records.list('tab'):
            self._clear_private_capture(tab['id'])
            tab.update(state='closed',grant_id=None,lease_revision=tab['lease_revision']+1);self.records.put('tab',tab['id'],tab)
        for grant in self.records.list('grant'):
            grant['revoked']=True;self.records.put('grant',grant['id'],grant)
        for profile in self.records.list('profile'):
            if profile.get('ephemeral'):self._clear_profile_artifacts(profile['id'],remove=True)
    def profiles(self,principal):return [r for r in self.records.list('profile') if r['principal_id']==principal]
    def tabs(self,principal):return [r for r in self.records.list('tab') if r['principal_id']==principal]
    def get(self,tab_id,principal):
        tab=self.records.get('tab',tab_id)
        if not tab or tab['principal_id']!=principal:raise KeyError('tab not found')
        return tab
    def viewer_start(self,id,principal,session_id,websocket):
        self.get(id,principal)
        key=secrets.token_urlsafe(16);self._views[key]={'id':key,'tab_id':id,'principal_id':principal,'session_id':session_id,'websocket':websocket}
        return key
    def viewer_finish(self,key):self._views.pop(key,None)
    def viewing_tabs(self,principal):return {row['tab_id'] for row in self._views.copy().values() if row['principal_id']==principal}
    async def stop_views(self,id,principal):
        self.get(id,principal)
        for row in list(self._views.values()):
            if row['tab_id']==id and row['principal_id']==principal:
                self._views.pop(row['id'],None)
                try:await row['websocket'].close(code=1000)
                except Exception:pass
        return {'ok':True}
    def create_profile(self,principal,project,name,*,ephemeral=False):
        if not name.strip() or len(name)>100:raise ValueError('profile name required, at most 100 characters')
        id=secrets.token_urlsafe(16)
        value={'id':id,'principal_id':principal,'project_id':project,'name':name,'ephemeral':ephemeral,'created_at':time()}
        path=self.records.root/'profiles'/id;path.mkdir(parents=True,mode=0o700);os.chmod(path,0o700)
        return self.records.put('profile',id,value)
    async def _context(self,profile):
        id=profile['id']
        async with self._start_lock:
            if id in self._closed_contexts:
                self._contexts.pop(id,None)
                previous=self._proxies.pop(id,None)
                if previous:await previous.close()
                self._closed_contexts.discard(id)
            if id in self._contexts:return self._contexts[id]
            from playwright.async_api import async_playwright
            if self._playwright is None:self._playwright=await async_playwright().start()
            proxy=EgressProxy(self.network);proxy_url=await proxy.start()
            options={'headless':True,'viewport':{'width':1440,'height':900},'accept_downloads':True,'service_workers':'block','permissions':[],
                     'proxy':{'server':proxy_url,'bypass':'<-loopback>'},
                     'args':['--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp','--disable-features=WebRtcHideLocalIpsWithMdns','--disable-background-networking','--disable-background-timer-throttling','--disable-renderer-backgrounding','--disable-backgrounding-occluded-windows']}
            if self.executable_path:options['executable_path']=self.executable_path
            try:
                context=await self._playwright.chromium.launch_persistent_context(str(self.records.root/'profiles'/id),**options)
                await context.clear_permissions()
                page=context.pages[0] if context.pages else await context.new_page()
                permission_port=await context.new_cdp_session(page)
                # Persistent Chromium profiles require explicit origin-specific
                # denials. The permission port stays host-owned and is never
                # exposed as CDP to the UI, agent or plugins.
                self._permission_ports[id]=permission_port
            except BaseException:
                if 'context' in locals():await context.close()
                await proxy.close();raise
            self._contexts[id]=context;self._proxies[id]=proxy
            self._context_tabs[id]=set()
            context.on('close',lambda:self._context_closed(id))
            if self._monitor is None:self._monitor=asyncio.create_task(self._monitor_grants())
            async def scoped_route(route):await self._route(route,profile_id=id)
            await context.route('**/*',scoped_route)
            # Popups stay in their originating profile and under parent ownership.
            context.on('page',lambda page:asyncio.create_task(self._adopt_popup(page,profile)))
            return context
    def _context_closed(self,profile_id):
        self._closed_contexts.add(profile_id)
        self._permission_ports.pop(profile_id,None)
        self._permission_origins={entry for entry in self._permission_origins if entry[0]!=profile_id}
        # Page-close events can precede context-close. Only tabs belonging to
        # this runtime context qualify; explicitly closed tabs were removed.
        for tab_id in self._context_tabs.pop(profile_id,set()):
            if self.records.get('tab',tab_id):self._closed(tab_id,'crashed')
    async def _monitor_grants(self):
        while True:
            for tab in self.records.list('tab'):
                if tab.get('grant_id') and not self._grant_valid(tab,self.records.get('grant',tab['grant_id'])):
                    self._revoke(tab,'human')
            await asyncio.sleep(.25)
    async def _route(self,route,*,profile_id=None):
        try:
            request=route.request
            if request.url.startswith(('data:','blob:','about:')):
                return await route.continue_()
            destination=await self.network.validate(request.url)
            try:page=request.frame.page
            except Exception:page=None
            if page:
                profile_id=profile_id or next((key for key,context in self._contexts.items() if context==page.context),None)
                if profile_id and (profile_id,destination) not in self._permission_origins:
                    port=self._permission_ports[profile_id]
                    for name in ('microphone','camera'):
                        await port.send('Browser.setPermission',{'permission':{'name':name},'setting':'denied','origin':destination})
                    self._permission_origins.add((profile_id,destination))
            tab_id=next((id for id,p in self._pages.items() if p==page),None)
            tab=self.records.get('tab',tab_id) if tab_id else None
            if not tab and page:
                # Popup adoption can race its first request. Do not let an
                # unadopted agent popup escape the originating tab's boundary.
                opener=await page.opener()
                parent_id=next((id for id,p in self._pages.items() if p==opener),None)
                tab=self.records.get('tab',parent_id) if parent_id else None
            # Passive assets may load their normal CDNs. Agent-controlled
            # documents, fetch/XHR and non-read requests cannot move data to an
            # ungranted origin, including a form or script-triggered request.
            moves_data=request.resource_type in {'document','fetch','xhr'} or request.method.upper() not in {'GET','HEAD','OPTIONS'}
            if not tab and moves_data and profile_id and any(row['profile_id']==profile_id and row['state'] not in {'closed','crashed'} and (row['state']=='agent' or row.get('transport_restricted')) for row in self.records.list('tab')):
                # Chromium can report a popup's initial request before its
                # frame/opener exists. Unattributed data movement in a scoped
                # profile is refused rather than inheriting human authority.
                return await route.abort('blockedbyclient')
            if tab and (tab['state']=='agent' or tab.get('transport_restricted')) and moves_data:
                grant=self.records.get('grant',tab['grant_id']) if tab.get('grant_id') else None
                if not grant or destination not in grant['origins'] or not self._grant_valid(tab,grant):
                    self.records.audit(principal_id=tab['principal_id'],session_id=(grant or {}).get('session_id'),tab_id=tab['id'],grant_id=tab.get('grant_id'),decision='BLOCK',reason='browser request destination outside live scoped origins',target=destination)
                    self._revoke(tab,'human');return await route.abort('blockedbyclient')
            await route.continue_()
        except Exception:await route.abort('blockedbyclient')
    async def _adopt_popup(self,page,profile):
        # new_page also triggers event; only genuine opener is adopted.
        opener=await page.opener()
        if opener is None:return
        parent_id=next((id for id,p in self._pages.items() if p==opener),None)
        parent=self.records.get('tab',parent_id) if parent_id else None
        if not parent:await page.close();return
        # An agent popup is human-owned until explicitly granted. No inherited control.
        tab=self._new_tab_record(profile,parent['session_id']);self._pages[tab['id']]=page
        if parent['state']=='agent' or parent.get('transport_restricted'):
            tab['transport_restricted']=True;self.records.put('tab',tab['id'],tab)
        await self._wire_page(page,tab['id']);await self._navigation(tab['id'])
    def _new_tab_record(self,profile,session_id):
        id=secrets.token_urlsafe(16)
        return self.records.put('tab',id,{'id':id,'principal_id':profile['principal_id'],'project_id':profile['project_id'],'profile_id':profile['id'],'session_id':session_id,'url':'about:blank','title':'New tab','state':'human','document_revision':0,'lease_revision':0,'grant_id':None,'recording':False,'created_at':time()})
    async def _wire_page(self,page,id):
        tab=self.records.get('tab',id)
        self._context_tabs.setdefault(tab['profile_id'],set()).add(id)
        page.on('framenavigated',lambda frame:asyncio.create_task(self._navigation(id)) if frame==page.main_frame else None)
        page.on('close',lambda: self._closed(id))
        page.on('crash',lambda:self._closed(id,'crashed'))
        page.on('download',lambda download:asyncio.create_task(self._download(id,download)))
        def diagnostic(kind,value):
            tab=self.records.get('tab',id)
            grant=self.records.get('grant',tab['grant_id']) if tab and tab.get('grant_id') else None
            if not tab or tab['state']=='private':return
            if not ((self._grant_valid(tab,grant) and 'diagnostics' in grant['actions']) or self._diagnostic_consent_valid(tab)):return
            from termx.agent.policy import redact
            cache=self._diagnostics.setdefault(id,{'console':[],'network':[]})
            if kind=='console':item={'type':value.type,'text':redact(value.text)[:1000]}
            else:item={'url':value.url.split('?')[0].split('#')[0],'status':value.status,'method':value.request.method}
            cache[kind].append(item);cache[kind]=cache[kind][-200:]
        page.on('console',lambda message:diagnostic('console',message))
        page.on('response',lambda response:diagnostic('network',response))
        # Browser-level Fetch interception includes redirect hops, which
        # Playwright route handlers intentionally omit. CDP remains private
        # to this worker; agents never receive a debugging endpoint.
        session=await page.context.new_cdp_session(page)
        self._capture_cdp[id]=session
        async def navigation(event):
            request_id=event['requestId']
            try:
                url=event['request']['url']
                if not url.startswith(('http://','https://')):raise TargetDenied('Privileged navigation')
                destination=await self.network.validate(url)
                tab=self.records.get('tab',id)
                grant=self.records.get('grant',tab['grant_id']) if tab and tab.get('grant_id') else None
                if tab and (tab['state']=='agent' or tab.get('transport_restricted')) and (not grant or not self._grant_valid(tab,grant) or destination not in grant['origins']):
                    self._revoke(tab,'human');raise TargetDenied('Redirect destination outside task grant')
                await session.send('Fetch.continueRequest',{'requestId':request_id})
            except (TargetDenied,ValueError,OSError):
                await session.send('Fetch.failRequest',{'requestId':request_id,'errorReason':'BlockedByClient'})
            except Exception:pass # page closed; no request can escape a closed target
        session.on('Fetch.requestPaused',lambda event:asyncio.create_task(navigation(event)))
        await session.send('Fetch.enable',{'patterns':[{'resourceType':'Document','requestStage':'Request'}]})
    async def create_tab(self,principal,session,profile_id,url='about:blank'):
        profile=self.records.get('profile',profile_id)
        if not profile or profile['principal_id']!=principal:raise KeyError('profile not found')
        if url!='about:blank':await self.network.validate(url)
        context=await self._context(profile);page=await context.new_page()
        tab=self._new_tab_record(profile,session);self._pages[tab['id']]=page;await self._wire_page(page,tab['id'])
        if url!='about:blank':
            try:
                await page.goto(url,wait_until='domcontentloaded',timeout=30000)
                await self.network.validate(page.url)
            except BaseException:
                await self.close_tab(tab['id'],principal)
                raise
        await self._navigation(tab['id'])
        return self.get(tab['id'],principal)
    async def _navigation(self,id):
        tab=self.records.get('tab',id);page=self._pages.get(id)
        if not tab or not page or page.is_closed():return
        if tab['state']=='private':
            # Private navigation produces no captured page URL/title/history.
            tab['document_revision']+=1
            self.records.put('tab',id,tab)
            return
        tab['url']=page.url
        tab['document_revision']+=1
        self.records.put('tab',id,tab)
        revision=tab['document_revision']
        # Invalidate the old document before awaiting renderer metadata. A
        # private takeover while title is in flight must never write it back.
        if tab['grant_id']:self.review.invalidate(grant_id=tab['grant_id'])
        try:title=await page.title()
        except Exception:title=tab['title']
        current=self.records.get('tab',id)
        if not current or current['state']=='private' or current['document_revision']!=revision or current['lease_revision']!=tab['lease_revision']:return
        current['title']=title;self.records.put('tab',id,current)
        history_id=secrets.token_urlsafe(12)
        self.records.put('history',history_id,{'id':history_id,'principal_id':tab['principal_id'],'profile_id':tab['profile_id'],'origin':origin(tab['url']) if tab['url'].startswith(('https://','http://')) else 'about:blank','title':title,'url':tab['url'].split('?')[0].split('#')[0],'visited_at':time()})
    def _closed(self,id,state='closed'):
        tab=self.records.get('tab',id)
        # A crashed renderer can subsequently emit close. Preserve recovery
        # information unless close_tab explicitly changed the state first.
        if tab and tab['state']=='crashed' and state=='closed':state='crashed'
        if tab:self._revoke(tab,state)
        self._pages.pop(id,None)
    async def close_tab(self,id,principal):
        tab=self.get(id,principal);self._revoke(tab,'closed')
        self._context_tabs.get(tab['profile_id'],set()).discard(id)
        page=self._pages.pop(id,None)
        if page and not page.is_closed():await page.close()
    async def recover_tab(self,id,principal,session_id):
        previous=self.get(id,principal)
        if previous['state'] not in {'closed','crashed'}:raise ValueError('Only a closed or crashed tab can be reopened')
        if previous.get('recovered_tab_id'):
            recovered=self.get(previous['recovered_tab_id'],principal)
            if recovered['state'] not in {'closed','crashed'}:return recovered
        # Preserve this user's profile, but never revive a renderer's task grant.
        recovered=await self.create_tab(principal,session_id,previous['profile_id'],previous['url'])
        previous['recovered_tab_id']=recovered['id'];self.records.put('tab',id,previous)
        return recovered
    def _revoke(self,tab,state):
        self._human_diagnostics.pop(tab['id'],None)
        self._diagnostics.pop(tab['id'],None)
        self._frame_cache.pop(tab['id'],None)
        if tab.get('grant_id'):
            grant=self.records.get('grant',tab['grant_id'])
            if grant:grant['revoked']=True;self.records.put('grant',grant['id'],grant)
            self.review.invalidate(grant_id=tab['grant_id'])
        tab.update(state=state,grant_id=None,lease_revision=tab['lease_revision']+1,recording=False)
        if state in {'closed','crashed'}:tab['closed_at']=time()
        self.records.put('tab',tab['id'],tab)
        self._clear_private_capture(tab['id'])
        if state=='private':
            from termx.desktop.recording import set_capture_private
            private_lease=tab['lease_revision']
            def current():
                row=self.records.get('tab',tab['id'])
                # Privacy is a host observation barrier, not a delegated tool
                # grant. A second valid human viewer may still show these
                # pixels after the initiating session expires. Only explicit
                # resume/close/restart releases the barrier.
                return bool(row and row['state']=='private' and row['lease_revision']==private_lease)
            set_capture_private(self._private_capture_id(tab['id']),expires_at=float('inf'),valid=current)
        return tab
    def _private_capture_id(self,id):return 'browser:'+canonical_hash(str(self.records.root.resolve()))+':'+id
    def _clear_private_capture(self,id):
        from termx.desktop.recording import clear_capture_private
        clear_capture_private(self._private_capture_id(id))
    def takeover(self,id,principal,*,private=False,session_id=None,policy_version=None):
        tab=self.get(id,principal)
        # Automatic revocation is not a human takeover. Only this explicit
        # authorized operation releases the retained page-network boundary.
        tab.pop('transport_restricted',None)
        if private:
            grant=self.records.get('grant',tab.get('grant_id')) if tab.get('grant_id') else None
            tab['private_session_id']=session_id or (grant or {}).get('session_id') or tab['session_id']
            tab['private_policy_version']=policy_version if policy_version is not None else (grant or {}).get('policy_version',0)
        return self._revoke(tab,'private' if private else 'human')
    def _grant_valid(self,tab,grant):
        try:
            return bool(grant and not grant['revoked'] and grant['expires_at']>time() and all(grant[key]==tab[key] for key in ('principal_id','project_id','profile_id')) and grant['tab_id']==tab['id'] and tab['grant_id']==grant['id'] and tab['lease_revision']==grant['lease_revision'] and tab['state']=='agent' and self.task_live(grant['run_id']) and self.session_valid(grant['principal_id'],grant['session_id'],grant['policy_version']))
        except Exception:
            return False
    async def handoff(self,id,principal,session,*,run_id,origins,actions,expires_in=600,policy_version=0):
        tab=self.get(id,principal)
        if tab['state'] in {'closed','crashed'}:raise ValueError('tab is closed')
        if not run_id or len(run_id)>128:raise ValueError('named task required')
        if not self.task_live(run_id):raise PermissionError('Start an active task before handing over this tab')
        sites=sorted(set(origin(o) for o in origins))
        if not sites or len(sites)>30:raise ValueError('one to thirty exact origins required')
        for site in sites:await self.network.validate(site)
        page=self._pages.get(id)
        if not page:raise ValueError('tab is unavailable')
        if origin(page.url) not in sites:raise ValueError('current page must be inside granted origins')
        allowed={'navigate','observe','capture','click','type','scroll','wait','find','zoom','history','upload','download','diagnostics','open_tab','close_tab'}
        if not actions or set(actions)-allowed:raise ValueError('unsupported action grant')
        if 'observe' not in actions:raise ValueError('handoff requires observation permission for its fresh context')
        if not 10<=expires_in<=3600:raise ValueError('grant duration must be 10 to 3600 seconds')
        current=self.get(id,principal)
        if current['lease_revision']!=tab['lease_revision']:raise PermissionError('Control changed during handoff; review the current tab')
        tab=self._revoke(current,'human');id_grant=secrets.token_urlsafe(16)
        current_url=page.url;title=await page.title()
        current=self.get(id,principal)
        if current['lease_revision']!=tab['lease_revision'] or current['document_revision']!=tab['document_revision'] or current['state']!='human' or page.url!=current_url or not self.task_live(run_id) or not self.session_valid(principal,session,policy_version):raise PermissionError('Task, authority or page changed during handoff')
        tab=current;tab['url']=current_url;tab['title']=title
        tab.update(state='agent',grant_id=id_grant,transport_restricted=True)
        grant={'id':id_grant,'tab_id':id,'principal_id':principal,'session_id':session,'project_id':tab['project_id'],'run_id':run_id,'profile_id':tab['profile_id'],'origins':sites,'actions':sorted(set(actions)),'expires_at':time()+expires_in,'lease_revision':tab['lease_revision'],'policy_version':policy_version,'revoked':False}
        self.records.put('grant',id_grant,grant);self.records.put('tab',id,tab)
        self.records.put('browser-task',run_id,{'id':run_id,'principal_id':principal,'session_id':session,'project_id':tab['project_id'],'policy_version':policy_version,'restricted':True})
        # Explicit Resume always obtains a fresh observation after grant creation.
        observation=await self.observe(id,principal,grant_id=id_grant,run_id=run_id)
        return {'tab':self.get(id,principal),'grant':grant,'observation':observation}
    def _agent(self,tab,grant_id,run_id,action,document_revision=None,lease_revision=None):
        grant=self.records.get('grant',grant_id)
        if not self._grant_valid(tab,grant) or grant['run_id']!=run_id or action not in grant['actions']:raise PermissionError('active task grant and control lease required')
        if document_revision is not None and document_revision!=tab['document_revision']:raise ValueError('document changed; observe before proposing another action')
        if lease_revision is not None and lease_revision!=tab['lease_revision']:raise ValueError('control lease changed')
        if origin(tab['url']) not in grant['origins']:raise PermissionError('page origin is outside grant')
        return grant
    async def observe(self,id,principal,*,grant_id=None,run_id=None,human=False):
        tab=self.get(id,principal);page=self._pages.get(id)
        if not page or page.is_closed():raise ValueError('tab closed')
        if tab['state']=='private':raise PermissionError('Private login suspends context observation')
        if not human:self._agent(tab,grant_id,run_id,'observe')
        # Values are intentionally omitted; password/hidden inputs entirely excluded.
        items=await page.evaluate("""() => {
          const selector=e=>{const path=[];while(e&&e.nodeType===1){const tag=e.tagName.toLowerCase();const peers=(e.parentElement?Array.from(e.parentElement.children):[e]).filter(n=>n.tagName===e.tagName);path.unshift(tag+':nth-of-type('+(peers.indexOf(e)+1)+')');e=e.parentElement;}return path.join(' > ')};
          return Array.from(document.querySelectorAll('a,button,input,textarea,select,[role],h1,h2,h3,p')).slice(0,700).filter(e => !e.closest('[data-private]') && !['password','hidden'].includes(e.type) && !/password|secret|token|api.?key|otp|credential/i.test([e.name,e.id,e.autocomplete].join(' '))).map((e,i) => ({index:i,tag:e.tagName.toLowerCase(),role:e.getAttribute('role'),selector:selector(e),frame:{kind:'main',path:[]},name:(e.getAttribute('aria-label') || e.labels?.[0]?.innerText || e.innerText || e.getAttribute('placeholder') || '').slice(0,240),type:e.type || null,href:e.tagName==='A' ? e.href.split('?')[0].split('#')[0] : null,disabled:!!e.disabled,box:(r=>({x:r.x,y:r.y,width:r.width,height:r.height}))(e.getBoundingClientRect())})).filter(e=>e.box.width>0&&e.box.height>0);
        }""")
        for item in items:item['context_hash']=canonical_hash(item)
        current=self.get(id,principal)
        if current['state']=='private':raise PermissionError('Private login suspends context observation')
        if current['document_revision']!=tab['document_revision'] or current['lease_revision']!=tab['lease_revision']:raise ValueError('Page changed during context observation')
        if not human:self._agent(current,grant_id,run_id,'observe',tab['document_revision'],tab['lease_revision'])
        return {'tab_id':id,'url':tab['url'].split('?')[0],'title':tab['title'],'document_revision':tab['document_revision'],'lease_revision':tab['lease_revision'],'frame':{'kind':'main','path':[],'viewport':page.viewport_size},'elements':items,'annotations':self.annotations(id,principal)}
    async def frame(self,id,principal,*,human=False,grant_id=None,run_id=None,redacted=False):
        tab=self.get(id,principal);page=self._pages.get(id)
        if not page:raise ValueError('tab closed')
        if not human:self._agent(tab,grant_id,run_id,'capture')
        if redacted and tab['state']=='private':raise PermissionError('Snapshot capture paused during private login')
        key=(id,human,redacted,tab['document_revision'],tab['lease_revision'])
        cached=self._frame_cache.get(id) if human and not redacted else None
        if cached and cached['key']==key and monotonic()-cached['at']<.125:
            return cached['frame']
        async def capture():
            # Only one human capture serves all viewers. Pending controls go
            # first; repeated viewers cannot fill the renderer command queue.
            while self._control_waiters.get(id,0):await asyncio.sleep(.005)
            async with self._locks.setdefault(id,asyncio.Lock()):
                current=self.get(id,principal)
                if current['lease_revision']!=tab['lease_revision']:raise PermissionError('Capture lease changed before rendering')
                if human and not redacted and id in self._capture_cdp:
                    # UI frames need no Playwright animation/font/RAF barriers
                    # or transient masking styles. CDP remains host-internal.
                    result=await asyncio.wait_for(self._capture_cdp[id].send('Page.captureScreenshot',{'format':'jpeg','quality':65,'fromSurface':True,'captureBeyondViewport':False,'optimizeForSpeed':True}),5)
                    data=base64.b64decode(result['data'])
                else:
                    masks=[] if human and not redacted else [f.locator('input[type="password"], input[autocomplete*="password"], input[autocomplete="one-time-code"], input[name*="token" i], input[id*="token" i], input[name*="secret" i], input[id*="secret" i], input[name*="api_key" i], input[id*="api_key" i], [data-private]') for f in getattr(page,'frames',[page])]
                    if redacted:masks=[f.locator('input,textarea,select,[contenteditable],[data-private]') for f in getattr(page,'frames',[page])]
                    data=await page.screenshot(type='jpeg',quality=75,mask=masks,timeout=5000)
                current=self.get(id,principal)
                if current['lease_revision']!=tab['lease_revision']:raise PermissionError('Capture lease changed while rendering')
                if human and not redacted:self._frame_cache[id]={'key':key,'frame':data,'at':monotonic()}
                return data
        job=self._capture_jobs.get(key)
        if job is None:
            job=asyncio.create_task(capture());self._capture_jobs[key]=job
            def finished(task):
                if self._capture_jobs.get(key) is task:self._capture_jobs.pop(key,None)
                if not task.cancelled():task.exception() # retrieve disconnected-viewer errors
            job.add_done_callback(finished)
        frame=await asyncio.shield(job)
        current=self.get(id,principal)
        if current['lease_revision']!=tab['lease_revision']:raise PermissionError('Capture lease changed before delivery')
        if redacted and (current['state']=='private' or current['document_revision']!=tab['document_revision']):raise PermissionError('Snapshot target changed during capture')
        if not human:self._agent(self.get(id,principal),grant_id,run_id,'capture',tab['document_revision'],tab['lease_revision'])
        return frame
    @asynccontextmanager
    async def _control(self,id):
        self._control_waiters[id]=self._control_waiters.get(id,0)+1
        try:
            async with self._locks.setdefault(id,asyncio.Lock()):yield
        finally:
            self._control_waiters[id]-=1
            self._frame_cache.pop(id,None)
    async def _effect(self,page,action,args):
        if action in {'click','type','upload'}:
            from termx.browser.challenges import requires_manual_challenge
            if await requires_manual_challenge(page,args.get('selector')):return 'challenge',('manual-only',)
        if action in {'observe','capture','scroll','wait','navigate','find','zoom','history'}:return action,()
        if action in {'upload','download'}:return ('upload' if action=='upload' else 'export'),()
        if action=='diagnostics':return 'export',()
        if action=='type':
            selector=args.get('selector')
            if not selector:return 'unknown',()
            metadata=await page.locator(selector).first.evaluate('(e)=>({type:e.type,autocomplete:e.autocomplete,name:e.name,id:e.id,private:!!e.closest(\'[data-private]\')})')
            import re
            if metadata.get('private') or metadata['type']=='password' or re.search(r'password|secret|token|api.?key|otp|credential',str(metadata.get('name',''))+' '+str(metadata.get('id',''))+' '+str(metadata.get('autocomplete','')),re.I) or metadata['autocomplete']=='one-time-code':return 'credential',('secret',)
            return 'edit',()
        if action=='click':
            if not args.get('selector'):return 'unknown',()
            metadata=await page.locator(args['selector']).first.evaluate('(e)=>({tag:e.tagName,type:e.type,custom:!!(e.onclick||e.onchange||e.oninput||e.onpointerdown),private:!!e.closest("[data-private]"),href:e.href,text:(e.innerText || e.getAttribute("aria-label") || "").slice(0,200)})')
            if metadata.get('private'):return 'credential',('secret',)
            label=metadata['text'].lower()
            for words,effect in [({'delete','remove','erase'},'delete'),({'buy','pay','purchase','checkout'},'purchase'),({'send','submit','publish','post'},'send'),({'login','sign in','password'},'credential'),({'share','permission','admin','invite'},'privilege')]:
                if any(w in label for w in words):return effect,()
            if metadata['tag']=='A' and metadata['href']:
                await self.network.validate(metadata['href']);return 'navigate',()
            if metadata['type'] in {'checkbox','radio'}:return ('unknown' if metadata['custom'] else 'edit'),()
            return 'unknown',()
        return 'unknown',()
    async def _document_hash(self,page,action,args):
        if action in {'click','type','upload'} and args.get('selector'):
            value=await page.locator(args['selector']).first.evaluate('''e=>{const root=e.form||e.closest('form,[data-transaction]');return {tag:e.tagName,attributes:Array.from(e.attributes).filter(a=>a.name!=='style').map(a=>[a.name,a.value]),text:e.innerText,href:e.href,disabled:!!e.disabled,form:e.form?Array.from(e.form.elements).map(c=>({name:c.name,type:c.type,value:c.value,checked:c.checked})):null,transaction:root?{attributes:Array.from(root.attributes).map(a=>[a.name,a.value]),text:root.innerText.slice(0,12000),markers:Array.from(root.querySelectorAll('[data-merchant],[data-amount],[data-total],[data-currency],[data-account],[itemprop]')).slice(0,100).map(x=>({attributes:Array.from(x.attributes).map(a=>[a.name,a.value]),text:x.innerText.slice(0,1000)}))}:null}}''')
        else:
            value=await page.evaluate('()=>({url:location.href,title:document.title})')
        # Values are hashed in trusted worker memory, never sent to reviewer,
        # audit or model. Ignore screenshot renderer's temporary CSS nodes.
        return canonical_hash(value)
    async def action(self,id,principal,*,session_id,run_id,grant_id,action_id,action,args,document_revision,lease_revision,policy_version=0,authority=None):
        if len(action_id)>128 or not action_id:raise ValueError('action id required')
        if len(json.dumps(args))>65536:raise ValueError('action arguments too large')
        tab=self.get(id,principal);grant=self._agent(tab,grant_id,run_id,action,document_revision,lease_revision);page=self._pages[id]
        effect,labels=await self._effect(page,action,args)
        # Hash rendered DOM authority before review. Never persist DOM/input
        # values in approval records. A changed target cannot inherit consent
        # merely because its selector still matches after a model round trip.
        document_hash=await self._document_hash(page,action,args)
        preview=None
        if effect in {'send','publish','purchase','delete','privilege','unknown'}:
            from termx.browser.approval_preview import approval_preview
            preview=await approval_preview(page,tab,self.records.get('profile',tab['profile_id']) or {},action,args,effect,document_hash)
            if await self._document_hash(page,action,args)!=document_hash:raise ValueError('Browser document changed during transaction preview; observe and propose a fresh action')
        previous_document=self.records.get('browser-action-document',action_id)
        previous_review=self.records.get('review',action_id)
        if previous_document and previous_review and previous_review['status'] in {'needs_user','approved_once','permitted'} and previous_document['hash']!=document_hash:
            self.review.invalidate(grant_id=grant_id)
            raise ValueError('Browser document changed during approval; observe and propose a fresh action')
        if not previous_document:
            self.records.put('browser-action-document',action_id,{'id':action_id,'hash':document_hash})
        target=origin(args['url']) if action=='navigate' else origin(tab['url'])
        if target not in grant['origins']:raise PermissionError('origin not granted')
        if session_id!=grant['session_id'] or policy_version!=grant['policy_version']:raise PermissionError('session or policy changed')
        envelope=ActionEnvelope(action_id,principal,session_id,tab['project_id'],run_id,'browser.'+action,canonical_hash(args),target,effect,grant_id,policy_version,document_revision,lease_revision,tab['profile_id'],data_labels=labels)
        def validate():
            current=self.records.get('tab',id);active=self.records.get('grant',grant_id)
            return bool(current and self._grant_valid(current,active) and current['document_revision']==document_revision and current['lease_revision']==lease_revision and (authority is None or authority()))
        denied='use private human login; credentials cannot enter agent tools' if effect=='credential' else preview['reason'] if preview and preview['manual_required'] else None
        permit=await self.review.authorize(envelope,validate=validate,hard_deny=denied,context={'effect_summary':effect,'task_summary':getattr(self,'task_summary',lambda _:'')(run_id),'human_preview':preview})
        async def execute():
            result=await self._perform(page,tab,action,args,human=False,expected_hash=document_hash)
            current=self.records.get('tab',id);active=self.records.get('grant',grant_id)
            if not current or not self._grant_valid(current,active) or current['lease_revision']!=lease_revision or (authority and not authority()):
                raise PermissionError('Browser authority changed during execution; verify the outcome before proposing another action')
            if tab['recording']:
                self._record_step(tab, action, args, identifier=action_id)
            return result
        result=await self.review.execute(envelope,permit['permit'],validate=validate,operation=execute)
        return {'action_id':action_id,'result':result,'tab':self.get(id,principal)}
    async def tab_lifecycle(self,id,principal,*,session_id,run_id,grant_id,action_id,action,args,document_revision,lease_revision,policy_version=0,authority=None):
        """Explicit opt-in lifecycle scope; no ambient profile/session authority."""
        if action not in {'open_tab','close_tab'}:raise ValueError('Unsupported tab lifecycle')
        if not action_id or len(action_id)>128:raise ValueError('Action id required')
        tab=self.get(id,principal);grant=self._agent(tab,grant_id,run_id,action,document_revision,lease_revision)
        if grant['session_id']!=session_id or grant['policy_version']!=policy_version:raise PermissionError('Session or policy changed')
        target=origin(args['url']) if action=='open_tab' else origin(tab['url'])
        if target not in grant['origins']:raise PermissionError('Tab origin was not granted')
        if action=='open_tab':await self.network.validate(args['url'])
        document_hash=await self._document_hash(self._pages[id],'close_tab',{}) if action=='close_tab' else None
        if document_hash:
            previous=self.records.get('browser-action-document',action_id)
            if previous and previous['hash']!=document_hash:
                self.review.invalidate(grant_id=grant_id)
                raise ValueError('Page changed during close approval; observe and propose a fresh action')
            if not previous:self.records.put('browser-action-document',action_id,{'id':action_id,'hash':document_hash})
        # Closing may discard a site's unsaved state: always exact human review.
        effect='navigate' if action=='open_tab' else 'unknown'
        envelope=ActionEnvelope(action_id,principal,session_id,tab['project_id'],run_id,'browser.'+action,canonical_hash(args),target,effect,grant_id,policy_version,document_revision,lease_revision,tab['profile_id'])
        def validate():
            current=self.records.get('tab',id)
            return bool(current and self._grant_valid(current,self.records.get('grant',grant_id)) and current['document_revision']==document_revision and current['lease_revision']==lease_revision and (authority is None or authority()))
        permit=await self.review.authorize(envelope,validate=validate,context={'effect_summary':'Close tab may discard unsaved page changes' if action=='close_tab' else 'Open a tab within the exact approved origins','task_summary':self.task_summary(run_id)})
        async def execute():
            if action=='close_tab':
                async with self._control(id):
                    if not validate() or await self._document_hash(self._pages[id],'close_tab',{})!=document_hash:raise ValueError('Page changed during close approval; propose a fresh action')
                    await self.close_tab(id,principal)
                if not self.task_live(run_id) or not self.session_valid(principal,session_id,policy_version) or (authority and not authority()):raise PermissionError('Task authority changed during close; verify the tab state')
                return {'closed':id}
            child=await self.create_tab(principal,session_id,tab['profile_id'],args['url'])
            try:
                if not validate():raise PermissionError('Source handoff changed while opening the tab')
                self.claim_tab(principal,child)
                remaining=int(grant['expires_at']-time())
                if remaining<10:raise PermissionError('Handoff expires before new tab can receive control')
                result=await self.handoff(child['id'],principal,session_id,run_id=run_id,origins=grant['origins'],actions=grant['actions'],expires_in=min(remaining,3600),policy_version=policy_version)
                if not validate():raise PermissionError('Source handoff changed during tab creation')
                return {'tab':result['tab'],'observation':result['observation']}
            except BaseException:
                await self.close_tab(child['id'],principal)
                raise
        return await self.review.execute(envelope,permit['permit'],validate=validate,operation=execute)
    async def human_action(self,id,principal,action,args):
        tab=self.get(id,principal);page=self._pages.get(id)
        if action=='diagnostics':return await self.human_diagnostics(id,principal,args.get('view','performance'))
        if tab.pop('transport_restricted',None):self.records.put('tab',id,tab)
        if tab['state']=='agent':self._revoke(tab,'human')
        if not page:raise ValueError('tab closed')
        # Classify before the effect: a solved challenge may disappear on click.
        # Manual solutions must never become replayable recorded skill steps.
        effect, _ = await self._effect(page, action, args) if tab['recording'] and tab['state']=='human' else (None,())
        result = await self._perform(page,tab,action,args,human=True)
        current = self.get(id, principal)
        if current['recording'] and current['state'] == 'human':
            if effect not in {'credential','challenge'}: self._record_step(current, action, args)
        return result
    def _record_step(self,tab,action,args,identifier=None):
        import re
        if action not in {'navigate','click','scroll','wait','zoom','observe','capture','type'}: return
        safe = {k:v for k,v in args.items() if k in {'selector','x','y','button','seconds','zoom'} and isinstance(v,(str,int,float))}
        parameter = None
        if action == 'navigate': safe = {'url':'${selected_test_tab_url}'}
        if action == 'type':
            parameter = args.get('parameter')
            if not parameter or not re.fullmatch(r'[a-z][a-z0-9_]{0,39}',parameter) or not args.get('selector'): return
            safe = {'selector':args['selector'],'text':'${'+parameter+'}'}
        identifier = identifier or secrets.token_urlsafe(16)
        self.records.put('recording-step',identifier,{'id':identifier,'principal_id':tab['principal_id'],'tab_id':tab['id'],'profile_id':tab['profile_id'],'origin':origin(tab['url']) if tab['url'].startswith(('http://','https://')) else 'about:blank','action':action,'selector':args.get('selector'),'args':safe,'parameter':parameter,'created_at':time()})
    async def _perform(self,page,tab,action,args,human,expected_hash=None):
        if action=='capture':return await self._perform_unlocked(page,tab,action,args,human)
        async with self._control(tab['id']):
            current=self.get(tab['id'],tab['principal_id'])
            if not human:
                grant=self.records.get('grant',tab['grant_id'])
                if not self._grant_valid(current,grant) or current['lease_revision']!=tab['lease_revision']:raise PermissionError('Control authority changed while queued')
            if expected_hash is not None and await self._document_hash(page,action,args)!=expected_hash:raise ValueError('Browser document changed during approval; observe and propose a fresh action')
            if not human and action in {'click','type','upload'}:
                from termx.browser.challenges import requires_manual_challenge
                if await requires_manual_challenge(page,args.get('selector')):raise PermissionError('Recognized authentication challenge requires human takeover/private login')
            return await self._perform_unlocked(page,tab,action,args,human)
    async def _perform_unlocked(self,page,tab,action,args,human):
        if action=='navigate':
            await self.network.validate(args['url']);await page.goto(args['url'],wait_until='domcontentloaded',timeout=30000)
            await self.network.validate(page.url)
        elif action=='click':
            if args.get('selector'):await page.locator(args['selector']).first.click(timeout=5000)
            elif human:await page.mouse.click(float(args['x']),float(args['y']),button=args.get('button','left'))
            else:raise ValueError('agent click requires a semantic selector')
        elif action=='type':
            text=str(args.get('text',''))
            if len(text)>16000:raise ValueError('text too long')
            if args.get('selector'):await page.locator(args['selector']).first.fill(text,timeout=5000)
            elif human:await page.keyboard.insert_text(text)
            else:raise ValueError('agent typing requires a selector')
        elif action=='key' and human:
            key=str(args['key'])
            if len(key)>80:raise ValueError('invalid key')
            await page.keyboard.press(key)
        elif action=='scroll':await page.mouse.wheel(max(-2000,min(2000,float(args.get('x',0)))),max(-2000,min(2000,float(args.get('y',0)))))
        elif action=='wait':await asyncio.sleep(max(0,min(3,float(args.get('seconds',0)))))
        elif action=='history':
            direction=args.get('direction')
            if direction=='back':await page.go_back(wait_until='domcontentloaded')
            elif direction=='forward':await page.go_forward(wait_until='domcontentloaded')
            elif direction=='reload':await page.reload(wait_until='domcontentloaded')
            else:raise ValueError('unsupported history action')
        elif action=='zoom':
            zoom=float(args.get('zoom',1))
            if not .5<=zoom<=3:raise ValueError('zoom must be 0.5 to 3')
            await page.evaluate('(zoom)=>{document.documentElement.style.zoom=zoom}',zoom)
        elif action=='find':
            text=str(args.get('text',''))[:500]
            return {'found':await page.evaluate('(text)=>window.find(text)',text)}
        elif action=='observe':return await self.observe(tab['id'],tab['principal_id'],human=human,grant_id=tab['grant_id'],run_id=self.records.get('grant',tab['grant_id'])['run_id'] if tab['grant_id'] else None)
        elif action=='capture':
            frame=await self.frame(tab['id'],tab['principal_id'],human=human,grant_id=tab['grant_id'],run_id=self.records.get('grant',tab['grant_id'])['run_id'] if tab['grant_id'] else None)
            return {'jpeg_base64':base64.b64encode(frame).decode()}
        elif action=='upload':
            ref=self.records.get('upload',str(args.get('file_id','')))
            if not ref or ref['principal_id']!=tab['principal_id'] or ref['tab_id']!=tab['id'] or ref['origin']!=origin(tab['url']) or ref['expires_at']<time():raise PermissionError('explicit current approved file reference required')
            path=self.records.root/'uploads'/ref['id']
            if not path.is_file() or canonical_hash(base64.b64encode(path.read_bytes()).decode())!=ref['content_hash']:raise ValueError('approved upload changed')
            await page.locator(args['selector']).first.set_input_files({'name':ref['filename'],'mimeType':ref['mime_type'],'buffer':path.read_bytes()},timeout=5000)
        elif action=='download':
            # Downloads initiated by approved click/navigation are held in quarantine.
            ref=self.records.get('download',str(args.get('file_id','')))
            if not ref or ref['tab_id']!=tab['id'] or ref['principal_id']!=tab['principal_id']:raise KeyError('download not found')
            ref['approved']=True;self.records.put('download',ref['id'],ref);return ref
        elif action=='diagnostics':
            if tab['state']=='private':raise PermissionError('Developer capture paused during private login')
            view=args.get('view','performance')
            if view in {'console','network'}:return {view:list(self._diagnostics.get(tab['id'],{}).get(view,[]))}
            if view=='dom':
                dom=await page.evaluate('''()=>{const clone=document.documentElement.cloneNode(true);clone.querySelectorAll('script,style,input,textarea,select,[contenteditable],[data-private]').forEach(e=>e.remove());clone.querySelectorAll('*').forEach(e=>Array.from(e.attributes).forEach(a=>{if(a.name.startsWith('on')||/token|password|secret|value|srcdoc/i.test(a.name))e.removeAttribute(a.name)}));return clone.outerHTML.slice(0,20000)}''')
                from termx.agent.policy import redact
                return {'dom':redact(dom),'truncated':len(dom)>=20000}
            if view!='performance':raise ValueError('Unknown developer diagnostic view')
            return {'performance':await page.evaluate('()=>({navigation:performance.getEntriesByType("navigation").map(n=>({duration:n.duration,domContentLoaded:n.domContentLoadedEventEnd})),memory:performance.memory ? {used:performance.memory.usedJSHeapSize}:null})')}
        else:raise ValueError('unsupported browser action')
        return {'ok':True}
    def approve_upload(self,id,principal,filename,mime_type,data):
        tab=self.get(id,principal)
        if len(data)>10*1024*1024 or not data:raise ValueError('upload must be 1 byte to 10 MiB')
        filename=Path(filename).name
        if not filename or len(filename)>200:raise ValueError('invalid filename')
        ref=secrets.token_urlsafe(16);path=self.records.root/'uploads';path.mkdir(exist_ok=True,mode=0o700)
        file=path/ref;file.write_bytes(data);os.chmod(file,0o600)
        return self.records.put('upload',ref,{'id':ref,'principal_id':principal,'tab_id':id,'profile_id':tab['profile_id'],'origin':origin(tab['url']),'filename':filename,'mime_type':mime_type[:150],'size':len(data),'content_hash':canonical_hash(base64.b64encode(data).decode()),'expires_at':time()+600})
    async def _download(self,id,download):
        tab=self.records.get('tab',id)
        if not tab or tab['state']=='private':await download.cancel();return
        ref=secrets.token_urlsafe(16);folder=self.records.root/'downloads';folder.mkdir(exist_ok=True,mode=0o700)
        try:
            # Streaming cap: cancel once temporary artifact exceeds cap.
            job=asyncio.create_task(download.save_as(str(folder/ref)))
            while not job.done():
                await asyncio.sleep(.1)
                try:
                    artifact=await asyncio.wait_for(download.path(),.01)
                    if artifact and Path(artifact).stat().st_size>20*1024*1024:await download.cancel();job.cancel();break
                except (TimeoutError,OSError):pass
            await job
            if (folder/ref).stat().st_size>20*1024*1024:raise ValueError('download limit exceeded')
            os.chmod(folder/ref,0o600)
            self.records.put('download',ref,{'id':ref,'principal_id':tab['principal_id'],'tab_id':id,'profile_id':tab['profile_id'],'filename':Path(download.suggested_filename).name[:200],'size':(folder/ref).stat().st_size,'approved':tab['state']=='human','created_at':time()})
        except Exception:
            await download.cancel();(folder/ref).unlink(missing_ok=True)
    def _diagnostic_consent_valid(self,tab):
        consent=self._human_diagnostics.get(tab['id'])
        return bool(consent and tab['state']!='private' and consent['expires_at']>time() and consent['lease_revision']==tab['lease_revision'] and self.session_valid(tab['principal_id'],consent['session_id'],consent['policy_version']))
    def diagnostic_consent(self,id,principal,session_id,policy_version,enabled):
        tab=self.get(id,principal)
        if tab['state']=='private':raise PermissionError('Developer observation paused during private login')
        self._diagnostics.pop(id,None)
        if enabled:self._human_diagnostics[id]={'session_id':session_id,'policy_version':policy_version,'lease_revision':tab['lease_revision'],'expires_at':time()+600}
        else:self._human_diagnostics.pop(id,None)
        return {'enabled':bool(enabled),'expires_at':time()+600 if enabled else None}
    async def human_diagnostics(self,id,principal,view):
        tab=self.get(id,principal)
        if not self._diagnostic_consent_valid(tab):raise PermissionError('Enable developer observation for this tab first')
        value=await self._perform(self._pages[id],tab,'diagnostics',{'view':view},human=True)
        if not self._diagnostic_consent_valid(self.get(id,principal)):raise PermissionError('Developer observation expired during capture')
        return value
    def annotations(self,id,principal):
        tab=self.get(id,principal)
        if tab['state']=='private':raise PermissionError('Annotations paused during private login')
        comparisons=self.records.list('annotation-comparison')
        return [{**a,'stale':a['document_revision']!=tab['document_revision'],'comparisons':sorted([c for c in comparisons if c['annotation_id']==a['id'] and c['principal_id']==principal],key=lambda c:c['created_at'])[-20:]} for a in self.records.list('annotation') if a['tab_id']==id and a['principal_id']==principal]
    def resolve_annotation(self,id,principal,ref,*,resolved,revision,authority=lambda:True):
        tab=self.get(id,principal);note=self.records.get('annotation',ref)
        if not note or note['principal_id']!=principal or note['tab_id']!=id:raise KeyError('annotation')
        if tab['state']=='private' or not authority():raise PermissionError('Annotation authority is unavailable')
        if tab['document_revision']!=revision:raise ValueError('Page changed; refresh annotations before resolving')
        # Resolution updates the discussion state, never its frozen reference.
        note.update(resolved=bool(resolved),resolved_at=time() if resolved else None)
        return self.records.put('annotation',ref,note)
    async def compare_annotation(self,id,principal,ref,*,revision,include_screenshot=False,authority=lambda:True):
        note=self.records.get('annotation',ref)
        if not note or note['principal_id']!=principal or note['tab_id']!=id:raise KeyError('annotation')
        if not authority():raise PermissionError('Annotation authority expired')
        snapshot=await self.observe(id,principal,human=True)
        if snapshot['document_revision']!=revision:raise ValueError('Page changed; refresh annotations before comparing')
        same_page=snapshot['url'].split('#')[0]==note['url']
        element=next((item for item in snapshot['elements'] if item['selector']==note.get('selector')),None) if same_page else None
        # A selector on a different page is never silently treated as the old
        # target. A fresh, explicit screenshot can still show the current page.
        after=await self.annotate_context(id,principal,revision=revision,comment='Comparison snapshot',selector=element['selector'] if element else None,context_hash=element['context_hash'] if element else None,region=note.get('region') if same_page else None,include_screenshot=include_screenshot,authority=authority)
        self.records.delete('annotation',after['id'])
        value={'id':secrets.token_urlsafe(16),'annotation_id':ref,'principal_id':principal,'tab_id':id,'profile_id':note['profile_id'],'created_at':time(),'same_page':same_page,'target_available':bool(element or (same_page and note.get('region'))),'changed':not same_page or (bool(note.get('element')) and (not element or element['context_hash']!=note['element']['context_hash'])),'before':{key:note.get(key) for key in ('url','document_revision','lease_revision','frame','element','region','screenshot')},'after':{key:after.get(key) for key in ('url','document_revision','lease_revision','frame','element','region','screenshot')}}
        return self.records.put('annotation-comparison',value['id'],value)
    async def annotate_context(self,id,principal,*,revision,comment,selector=None,region=None,context_hash=None,include_screenshot=False,authority=lambda:True):
        if not authority():raise PermissionError('Annotation authority expired')
        if not comment.strip() or len(comment)>2000:raise ValueError('Annotation requires at most 2000 characters')
        snapshot=await self.observe(id,principal,human=True)
        if snapshot['document_revision']!=revision:raise ValueError('Annotation target is stale; refresh Page controls')
        if selector and region:raise ValueError('Choose one element or region')
        element=None
        if selector:
            element=next((item for item in snapshot['elements'] if item['selector']==selector),None)
            if not element or not context_hash or element['context_hash']!=context_hash:raise ValueError('Element changed; select it again from Page controls')
        if region:
            viewport=snapshot['frame']['viewport']
            if set(region)!={'x','y','width','height'} or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not __import__('math').isfinite(v) for v in region.values()):raise ValueError('A region requires finite viewport coordinates')
            if region['x']<0 or region['y']<0 or region['width']<=0 or region['height']<=0 or region['x']+region['width']>viewport['width'] or region['y']+region['height']>viewport['height']:raise ValueError('Region must be inside the current viewport')
        screenshot=None
        if include_screenshot:
            data=await self.frame(id,principal,human=True,redacted=True)
            ref=secrets.token_urlsafe(16);folder=self.records.root/'annotation-frames';folder.mkdir(exist_ok=True,mode=0o700)
            file=folder/ref;file.write_bytes(data);os.chmod(file,0o600)
            screenshot={'id':ref,'mime_type':'image/jpeg','url':f'/api/browser/tabs/{id}/annotation-frames/{ref}','redaction':'all form fields, editable content and marked private regions across frames','document_revision':revision,'lease_revision':snapshot['lease_revision']}
            self.records.put('annotation-frame',ref,{'id':ref,'principal_id':principal,'tab_id':id,'profile_id':self.get(id,principal)['profile_id'],'document_revision':revision})
        current=self.get(id,principal)
        try:permitted=authority()
        except PermissionError:permitted=False
        if not permitted or current['state']=='private' or current['document_revision']!=revision or current['lease_revision']!=snapshot['lease_revision']:
            if screenshot:
                (self.records.root/'annotation-frames'/screenshot['id']).unlink(missing_ok=True);self.records.delete('annotation-frame',screenshot['id'])
            if not permitted:raise PermissionError('Annotation authority expired during capture')
            raise ValueError('Annotation target changed during capture')
        value=self.annotate(id,principal,revision=revision,comment=comment,selector=selector,region=region)
        value.update(frame=snapshot['frame'],lease_revision=snapshot['lease_revision'],element=element,screenshot=screenshot)
        return self.records.put('annotation',value['id'],value)
    def annotate(self,id,principal,*,revision,comment,selector=None,region=None):
        tab=self.get(id,principal)
        if tab['state']=='private':raise PermissionError('annotations and capture paused in private login')
        if revision!=tab['document_revision']:raise ValueError('annotation target is stale')
        if not comment.strip() or len(comment)>2000:raise ValueError('annotation requires at most 2000 characters')
        ref=secrets.token_urlsafe(16)
        return self.records.put('annotation',ref,{'id':ref,'principal_id':principal,'tab_id':id,'profile_id':tab['profile_id'],'document_revision':revision,'url':tab['url'].split('?')[0].split('#')[0],'comment':comment,'selector':selector,'region':region,'created_at':time()})
    def recording(self,id,principal,enabled):
        tab=self.get(id,principal)
        if tab['state']=='private':raise PermissionError('recording disabled during private login')
        tab['recording']=bool(enabled);return self.records.put('tab',id,tab)
    def skill_draft(self,id,principal,name):
        self.get(id,principal)
        if not name.strip() or len(name)>100:raise ValueError('skill name required')
        steps=sorted([s for s in self.records.list('recording-step') if s['tab_id']==id and s['principal_id']==principal and s.get('args') is not None],key=lambda s:s['created_at'])[-200:]
        if not steps:raise ValueError('no consented safe recording steps')
        instructions='\n'.join(f"{i+1}. {s['action']} on {s['origin']}" + (f" using `{s['selector']}`" if s['selector'] else '') for i,s in enumerate(steps))
        body=f'---\nname: {json.dumps(name)}\ndescription: "Draft from consented TermX browser recording; review before use"\n---\n\n# {name}\n\n{instructions}\n\nRequires a fresh explicit browser grant. Recorded actions convey no current authority.\n'
        draft=secrets.token_urlsafe(16)
        return self.records.put('skill-draft',draft,{'id':draft,'principal_id':principal,'tab_id':id,'name':name,'source':'consented-browser-recording','status':'draft','content':body+'\n```json\n'+json.dumps([{'action':s['action'],'args':s['args'],'parameter':s.get('parameter')} for s in steps],indent=2)+'\n```\n','steps':[{'action':s['action'],'args':s['args'],'parameter':s.get('parameter')} for s in steps],'parameters':sorted({s['parameter'] for s in steps if s.get('parameter')}),'revision':1,'test':None,'created_at':time()})
    async def clear_profile(self,id,principal,*,remove=False):
        profile=self.records.get('profile',id)
        if not profile or profile['principal_id']!=principal:raise KeyError('profile not found')
        for tab in self.tabs(principal):
            if tab['profile_id']==id:await self.close_tab(tab['id'],principal)
        context=self._contexts.pop(id,None)
        if context:await context.close()
        proxy=self._proxies.pop(id,None)
        if proxy:await proxy.close()
        self._permission_ports.pop(id,None)
        self._permission_origins={entry for entry in self._permission_origins if entry[0]!=id}
        self._clear_profile_artifacts(id,remove=remove)
        return {'ok':True}
    def _clear_profile_artifacts(self,id,*,remove=False):
            shutil.rmtree(self.records.root/'profiles'/id,ignore_errors=True)
            for kind in ('history','download','upload','annotation','annotation-frame','annotation-comparison','recording-step'):
                for row in self.records.list(kind):
                    if row.get('profile_id')==id:
                        self.records.delete(kind,row['id'])
                        if kind=='annotation-frame':(self.records.root/'annotation-frames'/row['id']).unlink(missing_ok=True)
                        if kind in {'download','upload'}:
                            (self.records.root/('downloads' if kind=='download' else 'uploads')/row['id']).unlink(missing_ok=True)
            if remove:
                self.records.delete('profile',id)
                for row in self.records.list('tab'):
                    if row.get('profile_id')==id:self.records.delete('tab',row['id'])
    async def close(self):
        if self._monitor:
            self._monitor.cancel();await asyncio.gather(self._monitor,return_exceptions=True);self._monitor=None
        for tab in self.records.list('tab'):
            if tab['state'] not in {'closed','crashed'}:self._revoke(tab,'closed')
        self._context_tabs.clear() # Intentional host shutdown is not a renderer failure.
        captures=list(self._capture_jobs.values())
        for capture in captures:capture.cancel()
        await asyncio.gather(*captures,return_exceptions=True)
        self._frame_cache.clear();self._capture_cdp.clear()
        self._permission_ports.clear();self._permission_origins.clear()
        await asyncio.gather(*(c.close() for c in self._contexts.values()),return_exceptions=True)
        await asyncio.gather(*(p.close() for p in self._proxies.values()),return_exceptions=True)
        self._contexts.clear();self._pages.clear();self._proxies.clear()
        if self._playwright:await self._playwright.stop();self._playwright=None
        for profile in self.records.list('profile'):
            if profile['ephemeral']:await self.clear_profile(profile['id'],profile['principal_id'],remove=True)
