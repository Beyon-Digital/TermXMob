"""Trusted native/TermX/MCP tool adapter for browser task grants.

Engine requests contain selectors and action arguments, never user identities,
credentials, profile paths, grants, policy revisions or raw CDP commands.
"""
from __future__ import annotations
from termx.browser.service import BrowserService

BROWSER_TOOL_SCHEMAS=[
 {'type':'function','name':'browser_tabs','description':'List built-in tabs explicitly handed to this task; no external browser tabs.','parameters':{'type':'object','properties':{},'additionalProperties':False}},
 {'type':'function','name':'browser_observe','description':'Read fresh rendered semantic context for one granted built-in tab. Private login and revoked control cannot be observed.','parameters':{'type':'object','properties':{'tab_id':{'type':'string'}},'required':['tab_id'],'additionalProperties':False}},
 {'type':'function','name':'browser_action','description':'Propose a task-scoped browser action against the most recent observation. Sensitive effects pause for an exact human decision.','parameters':{'type':'object','properties':{'tab_id':{'type':'string'},'action':{'type':'string','enum':['navigate','click','type','scroll','wait','find','zoom','history','capture','upload','download','diagnostics']},'args':{'type':'object'},'document_revision':{'type':'integer'},'lease_revision':{'type':'integer'}},'required':['tab_id','action','args','document_revision','lease_revision'],'additionalProperties':False}},
]

class BrowserToolAdapter:
    def __init__(self,service:BrowserService):self.service=service
    def _grant(self,tab_id,principal,task,session):
        tab=self.service.get(tab_id,principal)
        grant=self.service.records.get('grant',tab['grant_id']) if tab.get('grant_id') else None
        if not grant or grant['run_id']!=task or grant['session_id']!=session or not self.service._grant_valid(tab,grant):
            raise PermissionError('no current browser handoff for this task')
        return tab,grant
    async def execute(self,name,args,*,task_id,principal_id,session_id,call_id,authority=None):
        if name=='browser_tabs':
            return [{k:tab[k] for k in ('id','title','url','state','document_revision','lease_revision')} for tab in self.service.tabs(principal_id) if (grant:=self.service.records.get('grant',tab['grant_id']) if tab.get('grant_id') else None) and grant['run_id']==task_id and grant['session_id']==session_id and self.service._grant_valid(tab,grant)]
        tab,grant=self._grant(args['tab_id'],principal_id,task_id,session_id)
        if name=='browser_observe':
            if authority and not authority():raise PermissionError('task authority was revoked')
            return await self.service.observe(tab['id'],principal_id,grant_id=grant['id'],run_id=task_id)
        if name!='browser_action':raise ValueError('unknown browser tool')
        return await self.service.action(tab['id'],principal_id,session_id=session_id,run_id=task_id,grant_id=grant['id'],action_id=call_id,policy_version=grant['policy_version'],authority=authority,action=args['action'],args=args['args'],document_revision=args['document_revision'],lease_revision=args['lease_revision'])
