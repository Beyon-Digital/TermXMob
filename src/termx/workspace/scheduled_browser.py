"""Explicit browser consent for one bounded unattended execution delegation.

This authority is independent of interactive authentication sessions. It never
borrows a login session, private view, unrelated profile or agent-created tab.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from copy import deepcopy
from time import time
from math import isfinite
from urllib.parse import urlsplit

from fastapi import HTTPException

from termx.browser.network import origin

ACTIONS = {'observe', 'navigate', 'click', 'type', 'scroll', 'wait', 'find', 'zoom', 'history', 'capture'}


class ScheduledBrowserPermissions:
    def __init__(self, workspace):
        self.workspace = workspace
        self.browser = workspace.state.browser
        self._locks = defaultdict(asyncio.Lock)

    async def prepare(self, principal, conversation_id, selection):
        conversation = self.workspace.session(principal, conversation_id, turns=False)
        self.workspace.record(principal, 'conversation', conversation_id, scope='agent-run')
        if conversation.get('runner_id') or conversation.get('engine', 'internal') != 'internal' or conversation.get('mode') != 'agent':
            raise ValueError('Unattended browser permission requires a local Internal Agent conversation')
        if not isinstance(selection, dict) or set(selection) != {'tabs', 'expires_at', 'max_seconds'}:
            raise ValueError('Choose explicit tabs, expiry and a per-run browser duration')
        expires = selection['expires_at']
        seconds = selection['max_seconds']
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not isfinite(expires) or not time()+10 <= expires <= time()+90*86400:
            raise ValueError('Browser permission must expire within 90 days')
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 10 <= seconds <= 3600:
            raise ValueError('Browser duration must be 10 to 3600 seconds per run')
        tabs = selection['tabs']
        if not isinstance(tabs, list) or not 1 <= len(tabs) <= 8:
            raise ValueError('Choose one to eight browser tabs')
        captured = []
        seen = set()
        for item in tabs:
            if not isinstance(item, dict) or set(item) != {'tab_id', 'origins', 'actions'}:
                raise ValueError('Every tab needs its exact ID, origins and actions')
            if item['tab_id'] in seen:
                raise ValueError('Browser tabs must be unique')
            seen.add(item['tab_id'])
            tab = self.browser.get(item['tab_id'], principal.id)
            profile=self.browser.records.get('profile',tab['profile_id'])
            if not profile or profile.get('ephemeral') or profile.get('principal_id')!=principal.id:
                raise ValueError('Unattended browser permission requires an owned persistent profile')
            self.workspace.require(principal, 'desktop-control', tab.get('project_id'), resource_kind='browser-tab', resource_id=tab['id'])
            if (tab.get('project_id') or '') != (conversation.get('project_id') or ''):
                raise PermissionError('Browser tab must belong to the conversation project')
            if tab['state'] != 'human' or tab['id'] not in self.browser._pages:
                raise ValueError('Take human control of a live tab before authorizing unattended browser work')
            if not isinstance(item['origins'], list) or not 1 <= len(item['origins']) <= 30:
                raise ValueError('Choose one to thirty exact approved origins per tab')
            for site in item['origins']:
                if not isinstance(site,str):raise ValueError('Approved origins must be strings')
                parts=urlsplit(site)
                if parts.path not in {'','/'} or parts.query or parts.fragment or parts.username or parts.password:
                    raise ValueError('Approved origins must contain only scheme, host and port')
            sites = sorted(set(origin(site) for site in item['origins']))
            for site in sites:
                await self.browser.network.validate(site)
            actions = item['actions']
            if not isinstance(actions, list) or not actions or any(not isinstance(a, str) for a in actions) or set(actions)-ACTIONS or 'observe' not in actions:
                raise ValueError('Choose supported actions including observe; tab lifecycle and transfers need human control')
            page = self.browser._pages[tab['id']]
            url = page.url
            if origin(url) not in sites:
                raise ValueError('The tab current origin must be included in its permission')
            # Network validation yielded: recheck the exact human lease and page.
            current = self.browser.get(tab['id'], principal.id)
            if current['lease_revision'] != tab['lease_revision'] or current['state'] != 'human' or page.url != url:
                raise PermissionError('Browser control or page changed during authorization')
            captured.append({'tab_id': tab['id'], 'profile_id': tab['profile_id'], 'lease_revision': tab['lease_revision'],
                             'origins': sites, 'actions': sorted(set(actions)), 'start_url': url})
        for selected in captured:
            current=self.browser.get(selected['tab_id'],principal.id)
            page=self.browser._pages.get(selected['tab_id'])
            if current['state']!='human' or current['lease_revision']!=selected['lease_revision'] or not page or page.url!=selected['start_url']:
                raise PermissionError('A selected browser tab changed during authorization')
        return {'tabs': captured, 'expires_at': expires, 'max_seconds': seconds}

    async def authorize(self, principal, delegation_id, selection):
        grant = self.workspace.record(principal, 'delegation', delegation_id, scope='agent-control')
        goal = self.workspace.record(principal, 'goal', grant['goal_id'], scope='agent-control')
        permission = await self.prepare(principal, goal['conversation_id'], selection)
        if grant['revoked'] or permission['expires_at'] > grant['expires_at']:
            raise PermissionError('Browser permission must stay within the execution delegation expiry')
        with self.workspace.store.lock:
            current = self.workspace.store.get('delegation', delegation_id)
            if current['revision'] != grant['revision']:
                raise PermissionError('Execution delegation changed during browser authorization')
            return self.workspace.store.update('delegation', delegation_id, {**current, 'browser_permission': permission}, current['revision'])

    def _authority(self, binding):
        principal = self.workspace.state.identity.principal_by_id(binding['principal_id'])
        if not principal or principal.policy_version != binding['policy_version']:
            raise PermissionError('Scheduled browser principal or policy changed')
        task_id = binding.get('run_id') or binding.get('id')
        metadata = self.workspace.store.get('task', task_id)
        if not metadata or metadata['owner'] != principal.id or metadata.get('delegation_id') != binding['delegation_id']:
            raise PermissionError('Browser authority is outside this scheduled task')
        self.workspace.validate_task_execution(task_id)
        grant = self.workspace.store.get('delegation', binding['delegation_id'])
        permission = (grant or {}).get('browser_permission')
        if not permission or permission['expires_at'] <= time():
            raise PermissionError('Scheduled browser permission expired or was not granted')
        for selected in permission['tabs']:
            tab=self.browser.get(selected['tab_id'],principal.id)
            self.workspace.require(principal,'desktop-control',tab.get('project_id'),resource_kind='browser-tab',resource_id=tab['id'])
        return grant, permission

    def valid(self, binding):
        try:
            if binding.get('session_id') is not None:
                return False
            _, permission = self._authority(binding)
            if binding.get('tab_id'):
                selected = next((tab for tab in permission['tabs'] if tab['tab_id'] == binding['tab_id']), None)
                if not selected or selected['profile_id'] != binding['profile_id'] or selected['lease_revision'] != binding['lease_revision']:
                    return False
                if set(binding['origins']) != set(selected['origins']) or set(binding['actions']) != set(selected['actions']):
                    return False
            return True
        except (PermissionError, KeyError, ValueError, TypeError, HTTPException):
            return False

    async def activate(self, task_id):
        metadata = self.workspace.store.get('task', task_id)
        if not metadata or not metadata.get('delegation_id'):
            return []
        async with self._locks[metadata['delegation_id']]:
            return await self._activate_locked(task_id)

    async def _activate_locked(self, task_id):
        metadata = self.workspace.store.get('task', task_id)
        if not metadata or not metadata.get('delegation_id'):
            return []
        grant = self.workspace.store.get('delegation', metadata['delegation_id'])
        permission = deepcopy((grant or {}).get('browser_permission'))
        if not permission:
            return []
        principal = self.workspace.state.identity.principal_by_id(metadata['owner'])
        binding = {'principal_id':metadata['owner'], 'session_id':None, 'policy_version':grant['policy_version'],
                   'delegation_id':grant['id'], 'run_id':task_id}
        self._authority(binding)
        results = []
        try:
            for selected in permission['tabs']:
                tab = self.browser.get(selected['tab_id'], principal.id)
                existing=self.browser.records.get('grant',tab.get('grant_id')) if tab.get('grant_id') else None
                prior_task=self.workspace.agents.get_task(existing['run_id']) if existing else None
                if tab['state']=='agent' and existing and existing.get('delegation_id')==grant['id'] and prior_task and prior_task['status']=='completed':
                    tab=self.browser._revoke(tab,'human')
                    self.release(tab,existing)
                    refreshed=self.workspace.store.get('delegation',grant['id'])['browser_permission']
                    selected['lease_revision']=next(row for row in refreshed['tabs'] if row['tab_id']==tab['id'])['lease_revision']
                if tab['state']=='agent' and existing and existing['run_id']==task_id and existing.get('delegation_id')==grant['id'] and self.browser._grant_valid(tab,existing):
                    results.append({'tab':tab,'grant':existing})
                    continue
                if tab.get('restart_closed') and tab['state'] == 'closed':
                    if tab.get('restart_source_state')!='human' or tab.get('restart_source_lease')!=selected['lease_revision']:
                        raise PermissionError('Prior browser takeover or closure invalidated restart permission')
                    # Reopen only this explicitly captured profile and initial URL.
                    if selected['profile_id'] != tab['profile_id'] or origin(selected['start_url']) not in selected['origins']:
                        raise PermissionError('Scheduled browser restart scope changed')
                    replacement = await self.browser.create_tab(principal.id, None, selected['profile_id'], selected['start_url'],
                        scheduled_scope={'origins':selected['origins'],'authority':binding})
                    self.browser.claim_tab(principal.id,replacement)
                    selected['tab_id'] = replacement['id']
                    selected['lease_revision'] = replacement['lease_revision']
                    tab = replacement
                if tab['state'] != 'human' or selected['profile_id'] != tab['profile_id'] or selected['lease_revision'] != tab['lease_revision']:
                    raise PermissionError('Browser takeover, private login or tab closure invalidated unattended permission')
                remaining = min(permission['max_seconds'], int(permission['expires_at']-time()), int(grant['expires_at']-time()))
                if remaining < 10:
                    raise PermissionError('Scheduled browser permission has too little time remaining')
                # Reserve the exact lease produced by handoff before its first
                # observation checks consent. Interrupted activation fails closed.
                selected['lease_revision'] = tab['lease_revision']+1
                current = self.workspace.store.get('delegation', grant['id'])
                self.workspace.store.update('delegation', grant['id'], {**current, 'browser_permission':deepcopy(permission)}, current['revision'])
                handed = await self.browser.handoff(tab['id'], principal.id, None, run_id=task_id,
                    origins=selected['origins'], actions=selected['actions'], expires_in=remaining,
                    policy_version=grant['policy_version'], delegation_id=grant['id'])
                results.append(handed)
        except BaseException:
            for result in results:
                current = self.browser.get(result['tab']['id'], principal.id)
                if current.get('grant_id') == result['grant']['id']:
                    self.browser._revoke(current, 'human')
            raise
        return results

    def completed(self, browser_grant):
        task=self.workspace.agents.get_task(browser_grant['run_id'])
        if not task or task['status']!='completed':return False
        try:
            self._authority(browser_grant)
            return True
        except (PermissionError,KeyError,ValueError,HTTPException):
            return False

    def release(self, tab, browser_grant):
        """Only successful normal completion renews a selected human lease."""
        task = self.workspace.agents.get_task(browser_grant['run_id'])
        if not task or task['status'] != 'completed':
            return
        try:
            grant, permission = self._authority(browser_grant)
            selected = next((row for row in permission['tabs'] if row['tab_id'] == tab['id']), None)
            if not selected or selected['lease_revision'] != browser_grant['lease_revision'] or tab['lease_revision'] != browser_grant['lease_revision']+1:
                return
            updated = deepcopy(permission)
            next(row for row in updated['tabs'] if row['tab_id'] == tab['id'])['lease_revision'] = tab['lease_revision']
            self.workspace.store.update('delegation', grant['id'], {**grant, 'browser_permission':updated}, grant['revision'])
        except (PermissionError, KeyError, ValueError, HTTPException):
            return
