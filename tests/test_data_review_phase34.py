"""Review fixes for the Copilot proposals and reports on views."""
import asyncio

from test_data_copilot import cleanup, configure, finish, response, setup
from test_data_copilot_proposals import _proposals, _results, _sales, _segment, _spec
from termx.data.query_spec import QuerySpec


def _report(view_id, **kw):
    return {"name": "Sales chart", "view_id": view_id,
            "visual": {"kind": "bar", "x": "region", "y": "amount", "aggregate": "sum"}, **kw}


def test_report_proposal_can_use_a_saved_view_and_pins_it(tmp_path):
    async def scenario():
        env = setup(tmp_path)
        configure(env)
        cid = env.service.runtime.create_connection(env.owner, env.wid, {
            "name": "Sales", "engine": "sqlite", "config": {"path": str(_sales(tmp_path))}})["id"]
        view = await env.service.views.create(env.owner, env.wid, "table", "All sales", "", QuerySpec.model_validate(_spec(cid)))
        queue = [("propose_report", _report(view["id"])), ("propose_report", _report("missing-view"))]
        env.host.handler = lambda _r, n: response(text="", calls=[queue[n - 1]]) if n <= len(queue) else response("Done")
        run = await finish(env, {"connection_id": cid, "include_schema": True})
        assert run["status"] == "completed", run
        assert [p["type"] for p in run["proposals"]] == ["report"], _results(run)
        assert run["proposals"][0]["definition"]["view_id"] == view["id"]
        assert "error" in _results(run)[1]
        await cleanup(env)
    asyncio.run(scenario())


def test_without_schema_consent_a_valid_and_an_invalid_proposal_look_the_same(tmp_path):
    async def scenario():
        env, cid, run = await _proposals(tmp_path, [
            ("propose_segment", lambda c: {"name": "Real", "spec": _segment(c)}),
            ("propose_segment", lambda c: {"name": "Guess", "spec": _spec(c, select=[], limit=200, **{"from": {"table": "nope", "alias": "s"}})})],
            include_schema=False)
        first, second = _results(run)
        assert first == second and "schema sharing" in first["error"], _results(run)
        assert run["proposals"] == []
        await cleanup(env)
    asyncio.run(scenario())
