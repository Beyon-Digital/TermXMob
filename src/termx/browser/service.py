from __future__ import annotations
import asyncio
import base64
import json
import os
import secrets
import shutil
from pathlib import Path
from time import time
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
        self._playwright=None;self._contexts={};self._pages={};self._proxies={};self._locks={};self._diagnostics={};self._start_lock=asyncio.Lock();self._monitor=None
        for tab in self.records.list('tab'):
            tab.update(state='closed',grant_id=None,lease_revision=tab['lease_revision']+1);self.records.put('tab',tab['id'],tab)
        for grant in self.records.list('grant'):
            grant['revoked']=True;self.records.put('grant',grant['id'],grant)
    def profiles(self,principal):return [r for r in self.records.list('profile') if r['principal_id']==principal]
    def tabs(self,principal):return [r for r in self.records.list('tab') if r['principal_id']==principal]
    def get(self,tab_id,principal):
        tab=self.records.get('tab',tab_id)
        if not tab or tab['principal_id']!=principal:raise KeyError('tab not found')
        return tab
    def create_profile(self,principal,project,name,*,ephemeral=False):
        if not name.strip() or len(name)>100:raise ValueError('profile name required, at most 100 characters')
        id=secrets.token_urlsafe(16)
        value={'id':id,'principal_id':principal,'project_id':project,'name':name,'ephemeral':ephemeral,'created_at':time()}
        path=self.records.root/'profiles'/id;path.mkdir(parents=True,mode=0o700);os.chmod(path,0o700)
        return self.records.put('profile',id,value)
    async def _context(self,profile):
        id=profile['id']
        async with self._start_lock:
            if id in self._contexts:return self._contexts[id]
            from playwright.async_api import async_playwright
            if self._playwright is None:self._playwright=await async_playwright().start()
            proxy=EgressProxy(self.network);proxy_url=await proxy.start()
            options={'headless':True,'viewport':{'width':1440,'height':900},'accept_downloads':True,'service_workers':'block',
                     'proxy':{'server':proxy_url,'bypass':'<-loopback>'},
                     'args':['--disable-quic','--force-webrtc-ip-handling-policy=disable_non_proxied_udp','--disable-features=WebRtcHideLocalIpsWithMdns','--disable-background-networking']}
            if self.executable_path:options['executable_path']=self.executable_path
            try:
                context=await self._playwright.chromium.launch_persistent_context(str(self.records.root/'profiles'/id),**options)
            except BaseException:
                await proxy.close();raise
            self._contexts[id]=context;self._proxies[id]=proxy
            if self._monitor is None:self._monitor=asyncio.create_task(self._monitor_grants())
            await context.route('**/*',self._route)
            # Popups stay in their originating profile and under parent ownership.
            context.on('page',lambda page:asyncio.create_task(self._adopt_popup(page,profile)))
            return context
    async def _monitor_grants(self):
        while True:
            for tab in self.records.list('tab'):
                if tab.get('grant_id') and not self._grant_valid(tab,self.records.get('grant',tab['grant_id'])):
                    self._revoke(tab,'human')
            await asyncio.sleep(.25)
    async def _route(self,route):
        try:
            request=route.request
            if request.url.startswith(('data:','blob:','about:')):
                return await route.continue_()
            destination=await self.network.validate(request.url)
            try:page=request.frame.page
            except Exception:page=None
            tab_id=next((id for id,p in self._pages.items() if p==page),None)
            tab=self.records.get('tab',tab_id) if tab_id else None
            if tab and tab['state']=='agent' and request.is_navigation_request() and request.frame==page.main_frame:
                grant=self.records.get('grant',tab['grant_id'])
                if not grant or destination not in grant['origins'] or not self._grant_valid(tab,grant):
                    self._revoke(tab,'human');return await route.abort('blockedbyclient')
            await route.continue_()
        except (TargetDenied,ValueError,OSError):await route.abort('blockedbyclient')
    async def _adopt_popup(self,page,profile):
        # new_page also triggers event; only genuine opener is adopted.
        opener=await page.opener()
        if opener is None:return
        parent_id=next((id for id,p in self._pages.items() if p==opener),None)
        parent=self.records.get('tab',parent_id) if parent_id else None
        if not parent:await page.close();return
        # An agent popup is human-owned until explicitly granted. No inherited control.
        tab=self._new_tab_record(profile,parent['session_id']);self._pages[tab['id']]=page
        await self._wire_page(page,tab['id']);await self._navigation(tab['id'])
    def _new_tab_record(self,profile,session_id):
        id=secrets.token_urlsafe(16)
        return self.records.put('tab',id,{'id':id,'principal_id':profile['principal_id'],'project_id':profile['project_id'],'profile_id':profile['id'],'session_id':session_id,'url':'about:blank','title':'New tab','state':'human','document_revision':0,'lease_revision':0,'grant_id':None,'recording':False,'created_at':time()})
    async def _wire_page(self,page,id):
        page.on('framenavigated',lambda frame:asyncio.create_task(self._navigation(id)) if frame==page.main_frame else None)
        page.on('close',lambda: self._closed(id))
        page.on('crash',lambda:self._closed(id,'crashed'))
        page.on('download',lambda download:asyncio.create_task(self._download(id,download)))
        def diagnostic(kind,value):
            tab=self.records.get('tab',id)
            grant=self.records.get('grant',tab['grant_id']) if tab and tab.get('grant_id') else None
            if not tab or not self._grant_valid(tab,grant) or 'diagnostics' not in grant['actions']:return
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
        async def navigation(event):
            request_id=event['requestId']
            try:
                url=event['request']['url']
                if not url.startswith(('http://','https://')):raise TargetDenied('Privileged navigation')
                destination=await self.network.validate(url)
                tab=self.records.get('tab',id)
                grant=self.records.get('grant',tab['grant_id']) if tab and tab.get('grant_id') else None
                if tab and tab['state']=='agent' and (not grant or not self._grant_valid(tab,grant) or destination not in grant['origins']):
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
        try:tab['title']=await page.title()
        except Exception:pass
        tab['document_revision']+=1
        self.records.put('tab',id,tab)
        # Revisions invalidate previously observed actions, not the active grant.
        if tab['grant_id']:self.review.invalidate(grant_id=tab['grant_id'])
        if tab['state']!='private':
            self.records.put('history',secrets.token_urlsafe(12),{'id':secrets.token_urlsafe(12),'principal_id':tab['principal_id'],'profile_id':tab['profile_id'],'origin':origin(page.url) if page.url.startswith(('https://','http://')) else 'about:blank','title':tab['title'],'url':page.url.split('?')[0].split('#')[0],'visited_at':time()})
    def _closed(self,id,state='closed'):
        tab=self.records.get('tab',id)
        if tab:self._revoke(tab,state)
        self._pages.pop(id,None)
    async def close_tab(self,id,principal):
        tab=self.get(id,principal);self._revoke(tab,'closed')
        page=self._pages.pop(id,None)
        if page and not page.is_closed():await page.close()
    def _revoke(self,tab,state):
        self._diagnostics.pop(tab['id'],None)
        if tab.get('grant_id'):
            grant=self.records.get('grant',tab['grant_id'])
            if grant:grant['revoked']=True;self.records.put('grant',grant['id'],grant)
            self.review.invalidate(grant_id=tab['grant_id'])
        tab.update(state=state,grant_id=None,lease_revision=tab['lease_revision']+1,recording=False)
        self.records.put('tab',tab['id'],tab);return tab
    def takeover(self,id,principal,*,private=False):
        return self._revoke(self.get(id,principal),'private' if private else 'human')
    def _grant_valid(self,tab,grant):
        try:
            return bool(grant and not grant['revoked'] and grant['expires_at']>time() and tab['grant_id']==grant['id'] and tab['lease_revision']==grant['lease_revision'] and tab['state']=='agent' and self.session_valid(grant['principal_id'],grant['session_id'],grant['policy_version']))
        except Exception:
            return False
    async def handoff(self,id,principal,session,*,run_id,origins,actions,expires_in=600,policy_version=0):
        tab=self.get(id,principal)
        if tab['state'] in {'closed','crashed'}:raise ValueError('tab is closed')
        if not run_id or len(run_id)>128:raise ValueError('named task required')
        sites=sorted(set(origin(o) for o in origins))
        if not sites or len(sites)>30:raise ValueError('one to thirty exact origins required')
        for site in sites:await self.network.validate(site)
        page=self._pages.get(id)
        if not page:raise ValueError('tab is unavailable')
        if origin(page.url) not in sites:raise ValueError('current page must be inside granted origins')
        allowed={'navigate','observe','capture','click','type','scroll','wait','find','zoom','history','upload','download','diagnostics'}
        if not actions or set(actions)-allowed:raise ValueError('unsupported action grant')
        if not 10<=expires_in<=3600:raise ValueError('grant duration must be 10 to 3600 seconds')
        self._revoke(tab,'human');id_grant=secrets.token_urlsafe(16)
        tab['url']=page.url
        tab['title']=await page.title()
        tab.update(state='agent',grant_id=id_grant)
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
        items=await page.evaluate('''() => Array.from(document.querySelectorAll('a,button,input,textarea,select,[role],h1,h2,h3,p')).slice(0,700).filter(e => !e.closest('[data-private]') && !['password','hidden'].includes(e.type) && !/password|secret|token|api.?key|otp|credential/i.test([e.name,e.id,e.autocomplete].join(' '))).map((e,i) => ({index:i,tag:e.tagName.toLowerCase(),role:e.getAttribute('role'),name:(e.getAttribute('aria-label') || e.innerText || e.getAttribute('placeholder') || '').slice(0,240),type:e.type || null,href:e.tagName==='A' ? e.href : null,disabled:!!e.disabled,box:(r=>({x:r.x,y:r.y,width:r.width,height:r.height}))(e.getBoundingClientRect())}))''')
        current=self.get(id,principal)
        if current['state']=='private':raise PermissionError('Private login suspends context observation')
        if not human:self._agent(current,grant_id,run_id,'observe',tab['document_revision'],tab['lease_revision'])
        return {'tab_id':id,'url':tab['url'].split('?')[0],'title':tab['title'],'document_revision':tab['document_revision'],'lease_revision':tab['lease_revision'],'elements':items}
    async def frame(self,id,principal,*,human=False,grant_id=None,run_id=None):
        tab=self.get(id,principal);page=self._pages.get(id)
        if not page:raise ValueError('tab closed')
        if not human:self._agent(tab,grant_id,run_id,'capture')
        masks=[] if human else [page.locator('input[type="password"], input[autocomplete*="password"], input[autocomplete="one-time-code"], input[name*="token" i], input[id*="token" i], input[name*="secret" i], input[id*="secret" i], input[name*="api_key" i], input[id*="api_key" i], [data-private]')]
        frame=await page.screenshot(type='jpeg',quality=75,mask=masks,timeout=5000)
        if not human:self._agent(self.get(id,principal),grant_id,run_id,'capture',tab['document_revision'],tab['lease_revision'])
        return frame
    async def _effect(self,page,action,args):
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
            metadata=await page.locator(args['selector']).first.evaluate('(e)=>({tag:e.tagName,type:e.type,private:!!e.closest("[data-private]"),href:e.href,text:(e.innerText || e.getAttribute("aria-label") || "").slice(0,200)})')
            if metadata.get('private'):return 'credential',('secret',)
            label=metadata['text'].lower()
            for words,effect in [({'delete','remove','erase'},'delete'),({'buy','pay','purchase','checkout'},'purchase'),({'send','submit','publish','post'},'send'),({'login','sign in','password'},'credential'),({'share','permission','admin','invite'},'privilege')]:
                if any(w in label for w in words):return effect,()
            if metadata['tag']=='A' and metadata['href']:
                await self.network.validate(metadata['href']);return 'navigate',()
            if metadata['type'] in {'checkbox','radio'}:return 'edit',()
            return 'unknown',()
        return 'unknown',()
    async def _document_hash(self,page,action,args):
        if action in {'click','type','upload'} and args.get('selector'):
            value=await page.locator(args['selector']).first.evaluate('''e=>({tag:e.tagName,attributes:Array.from(e.attributes).filter(a=>a.name!=='style').map(a=>[a.name,a.value]),text:e.innerText,href:e.href,disabled:!!e.disabled,form:e.form?Array.from(e.form.elements).map(c=>({name:c.name,type:c.type,value:c.value,checked:c.checked})):null})''')
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
        permit=await self.review.authorize(envelope,validate=validate,hard_deny='use private human login; credentials cannot enter agent tools' if effect=='credential' else None,context={'effect_summary':effect,'task_summary':getattr(self,'task_summary',lambda _:'')(run_id)})
        async def execute():
            if await self._document_hash(page,action,args)!=document_hash:
                raise ValueError('Browser document changed during approval; observe and propose a fresh action')
            result=await self._perform(page,tab,action,args,human=False)
            current=self.records.get('tab',id);active=self.records.get('grant',grant_id)
            if not current or not self._grant_valid(current,active) or current['lease_revision']!=lease_revision or (authority and not authority()):
                raise PermissionError('Browser authority changed during execution; verify the outcome before proposing another action')
            if tab['recording']:
                self._record_step(tab, action, args, identifier=action_id)
            return result
        result=await self.review.execute(envelope,permit['permit'],validate=validate,operation=execute)
        return {'action_id':action_id,'result':result,'tab':self.get(id,principal)}
    async def human_action(self,id,principal,action,args):
        tab=self.get(id,principal);page=self._pages.get(id)
        if tab['state']=='agent':self._revoke(tab,'human')
        if not page:raise ValueError('tab closed')
        result = await self._perform(page,tab,action,args,human=True)
        current = self.get(id, principal)
        if current['recording'] and current['state'] == 'human':
            effect, _ = await self._effect(page, action, args)
            if effect != 'credential': self._record_step(current, action, args)
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
    async def _perform(self,page,tab,action,args,human):
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
                dom=await page.evaluate('''()=>{const clone=document.documentElement.cloneNode(true);clone.querySelectorAll('script,style,input,textarea,[data-private]').forEach(e=>e.remove());clone.querySelectorAll('*').forEach(e=>Array.from(e.attributes).forEach(a=>{if(a.name.startsWith('on')||/token|password|secret|value|srcdoc/i.test(a.name))e.removeAttribute(a.name)}));return clone.outerHTML.slice(0,20000)}''')
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
    def annotate(self,id,principal,*,revision,comment,selector=None,region=None):
        tab=self.get(id,principal)
        if tab['state']=='private':raise PermissionError('annotations and capture paused in private login')
        if revision!=tab['document_revision']:raise ValueError('annotation target is stale')
        if not comment.strip() or len(comment)>2000:raise ValueError('annotation requires at most 2000 characters')
        ref=secrets.token_urlsafe(16)
        return self.records.put('annotation',ref,{'id':ref,'principal_id':principal,'tab_id':id,'profile_id':tab['profile_id'],'document_revision':revision,'url':tab['url'].split('?')[0],'comment':comment,'selector':selector,'region':region,'created_at':time()})
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
        shutil.rmtree(self.records.root/'profiles'/id,ignore_errors=True)
        for kind in ('history','download','upload','annotation','recording-step'):
            for row in self.records.list(kind):
                if row.get('profile_id')==id:
                    self.records.delete(kind,row['id'])
                    if kind in {'download','upload'}:
                        (self.records.root/('downloads' if kind=='download' else 'uploads')/row['id']).unlink(missing_ok=True)
        if remove:self.records.delete('profile',id)
        return {'ok':True}
    async def close(self):
        if self._monitor:
            self._monitor.cancel();await asyncio.gather(self._monitor,return_exceptions=True);self._monitor=None
        for tab in self.records.list('tab'):
            if tab['state'] not in {'closed','crashed'}:self._revoke(tab,'closed')
        await asyncio.gather(*(c.close() for c in self._contexts.values()),return_exceptions=True)
        await asyncio.gather(*(p.close() for p in self._proxies.values()),return_exceptions=True)
        self._contexts.clear();self._pages.clear();self._proxies.clear()
        if self._playwright:await self._playwright.stop();self._playwright=None
        for profile in self.records.list('profile'):
            if profile['ephemeral']:await self.clear_profile(profile['id'],profile['principal_id'],remove=True)
