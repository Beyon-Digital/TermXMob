"""Recorded browser drafts are editable and tested before bundle activation."""
import json
import re
import secrets
from time import time

from termx.agent.policy import redact
from termx.desktop.recording import digest


class BrowserSkills:
    def __init__(self, browser): self.browser = browser; self.records = browser.records

    def get(self, identifier, principal):
        row = self.records.get('skill-draft', identifier)
        if not row or row['principal_id'] != principal: raise KeyError(identifier)
        return row

    def edit(self, identifier, principal, content):
        row = self.get(identifier, principal)
        if not content.strip() or len(content) > 100_000 or redact(content) != content: raise ValueError('Draft must contain no embedded credentials')
        blocks = re.findall(r'```json\s*\n(.*?)\n```', content, re.S)
        if len(blocks) != 1: raise ValueError('Keep exactly one editable JSON step list')
        steps = json.loads(blocks[0])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 200: raise ValueError('Draft must contain one to 200 steps')
        for step in steps:
            if not isinstance(step, dict) or set(step) - {'action', 'args', 'parameter'} or step.get('action') not in {'navigate','click','scroll','wait','zoom','observe','capture','type'} or not isinstance(step.get('args'),dict): raise ValueError('Invalid browser step')
            if set(step['args']) - {'selector','x','y','button','seconds','zoom','url','text'}: raise ValueError('Unsupported recorded action arguments')
            if step['action'] == 'type' and (not re.fullmatch(r'[a-z][a-z0-9_]{0,39}', str(step.get('parameter') or '')) or step['args'].get('text') != '${' + step['parameter'] + '}' or not step['args'].get('selector')): raise ValueError('Recorded typing needs a semantic selector and named parameter')
            if step['action'] == 'navigate' and step['args'].get('url') != '${selected_test_tab_url}': raise ValueError('Recorded navigation must use the explicitly selected safe target')
        row.update(content=content, steps=steps, parameters=sorted({step['parameter'] for step in steps if step.get('parameter')}), revision=row.get('revision', 1) + 1, test=None)
        return self.records.put('skill-draft', identifier, row)

    async def test(self, identifier, principal, target_tab_id, parameters, safe_target, authority):
        row = self.get(identifier, principal); target = self.browser.get(target_tab_id, principal)
        if not safe_target or target_tab_id == row['tab_id']: raise ValueError('Authorize replay against a separate safe test tab')
        if target['state'] != 'human' or target['recording']: raise PermissionError('Safe target must be under human control and not recording')
        if set(parameters) != set(row.get('parameters', [])) or any(not isinstance(v, str) or len(v) > 2000 for v in parameters.values()): raise ValueError('Provide exactly the recorded parameters')
        revision = row.get('revision', 1); lease = target['lease_revision']
        def validate():
            current = self.browser.get(target_tab_id, principal)
            return authority() and current['state'] == 'human' and current['lease_revision'] == lease and self.get(identifier, principal).get('revision', 1) == revision
        for step in row['steps']:
            if not validate(): raise PermissionError('Draft or capture consent changed during replay')
            args = dict(step['args'])
            if step.get('parameter'): args['text'] = parameters[step['parameter']]
            if step['action'] == 'navigate': args['url'] = target['url']  # chosen safe target, never the recorded destination
            effect, _ = await self.browser._effect(self.browser._pages[target_tab_id], step['action'], args)
            if effect == 'credential': raise PermissionError('Credentials require private human login and cannot be replayed from a recording')
            await self.browser.human_action(target_tab_id, principal, step['action'], args)
        if not validate(): raise PermissionError('Replay scope changed during execution')
        await self.browser.observe(target_tab_id, principal, human=True)
        row = self.get(identifier, principal)
        row['test'] = {'digest': digest({'content': row['content'], 'steps': row['steps']}), 'expires_at': time() + 600, 'passed_at': time(), 'steps_executed': len(row['steps']), 'target_tab_id': target_tab_id}
        return self.records.put('skill-draft', identifier, row)

    def publish(self, identifier, principal_id, principal, extensions, version):
        row = self.get(identifier, principal_id); test = row.get('test')
        if not test or test['expires_at'] <= time() or test['digest'] != digest({'content': row['content'], 'steps': row['steps']}): raise PermissionError('Test this exact edited draft before publishing')
        target = self.browser.get(test['target_tab_id'], principal_id)
        if target['state'] != 'human': raise PermissionError('Test target is no longer under human control')
        slug = re.sub(r'[^a-z0-9-]+', '-', row['name'].lower()).strip('-')[:60]
        preview = extensions.preview(principal, {'id': slug, 'version': version, 'markdown': row['content'], 'permissions': ['agent-run']}, 'skill-markdown/v1')
        installed = extensions.install(principal, preview['id'], preview['digest'])
        row.update(status='published', published_version=version, published_digest=installed['active_digest'])
        self.records.put('skill-draft', identifier, row)
        return installed
