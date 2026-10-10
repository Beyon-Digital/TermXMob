import asyncio
from types import SimpleNamespace
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from termx.agent.store import AgentStore
from termx.agent.tree_budget import TreeBudget
from termx.graphql.context import TermxContext
from termx.graphql.schema import schema

@pytest.fixture
def dashboard(tmp_path):
    store = AgentStore(tmp_path / 'agents.db', tmp_path / 'artifacts')
    hidden = set()
    def require(secret, scope, **resource):
        if resource.get('resource_id') in hidden: raise HTTPException(403, 'Denied')
    state = SimpleNamespace(task_cursor_cipher=Fernet(Fernet.generate_key()), agent_store=store, auth=SimpleNamespace(scopes=lambda secret: ['agent-view']), authorization=SimpleNamespace(require=require, can=lambda secret, scope, **resource: resource.get('resource_id') not in hidden), agent=SimpleNamespace(tree_budget=TreeBudget(store)))
    def create(prompt, parent=None):
        return store.create_task(prompt=prompt, cwd='/project', provider_id='fixture', model='fixture', parent_id=parent, limits={'max_steps': 10, 'max_seconds': 30})
    yield store, hidden, TermxContext(state, 'test-only'), create
    store.close()

def query(context, text, variables=None):
    return asyncio.run(schema.execute(text, variable_values=variables, context_value=context))

def test_pagination_tie_breaks_and_searches_beyond_first_hundred(dashboard):
    store, hidden, context, create = dashboard
    for i in range(105):
        task = create(f'work {i}')
        store.update_task(task['id'], updated_at=100)
    first = query(context, '{ agent_task_page }')
    assert not first.errors
    page = first.data['agent_task_page']
    assert len(page['items']) == 100 and page['next_cursor']
    stolen = query(TermxContext(context.state, 'another-credential'), 'query($cursor:String){agent_task_page(cursor:$cursor)}', {'cursor': page['next_cursor']})
    assert stolen.errors[0].extensions['http_status'] == 400
    second = query(context, 'query($cursor:String){agent_task_page(cursor:$cursor)}', {'cursor': page['next_cursor']})
    assert not second.errors
    remainder = second.data['agent_task_page']
    assert len(remainder['items']) == 5 and remainder['next_cursor'] is None
    assert len({item['id'] for item in page['items'] + remainder['items']}) == 105
    result = query(context, '{agent_task_page(search:"work 104")}')
    assert len(result.data['agent_task_page']['items']) == 1

def test_page_filters_authority_and_rejects_bad_cursors(dashboard):
    store, hidden, context, create = dashboard
    denied = create('private')
    hidden.add(denied['id'])
    visible = create('public')
    store.update_task(visible['id'], status='recovery_confirmation_required')
    result = query(context, '{agent_task_page(status:"attention")}')
    assert [item['id'] for item in result.data['agent_task_page']['items']] == [visible['id']]
    bad = query(context, '{agent_task_page(cursor:"not-valid")}')
    assert bad.errors[0].extensions['http_status'] == 400

def test_supervision_never_exposes_unauthorized_children(dashboard):
    _, hidden, context, create = dashboard
    parent = create('parent')
    allowed = create('visible child', parent['id'])
    denied = create('hidden child', parent['id'])
    hidden.add(denied['id'])
    result = query(context, 'query($id:String!){agent_task_tree(task_id:$id)}', {'id': parent['id']})
    assert not result.errors
    assert [child['id'] for child in result.data['agent_task_tree']['children']] == [allowed['id']]
    assert 'tree_budget' in result.data['agent_task_tree']
