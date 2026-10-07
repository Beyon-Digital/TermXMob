# Shared Internal tree budget

The root and supervised Internal children share one durable allowance for reserved tool calls and summed active-worker seconds. Context preparation and provider planning are metered before tools; human approval and child-decision waits pause worker leases. These units are displayed explicitly and do not estimate token usage or cost.

SQLite reservations are atomic across independent Store connections. Exact parked retries preserve reservations; cancellation does not reset usage. Startup treats uncertain live leases conservatively. Approval, grant version and a recovery receipt commit in one transaction; restart asks for explicit continuation using the same approved grant. Planning resumed by a budget grant still requires separate plan approval.

[Actual coordinator regressions](../../../tests/test_tree_budget.py) cover concurrent children, summed planning/provider work, cancellation, parked retries, independent connections, rejected transaction rollback, sibling reuse and crash after committed grant. The final focused run passed 24 tests, with 99 unrelated cases deselected; [budget controls](../../../desktop/workspace/src/features/budget-approval.test.tsx) passed 4 tests. [Exact evidence](shared-tree-budget.json) preserves commands, units and limits. Final rendered/native qualification is independent.
