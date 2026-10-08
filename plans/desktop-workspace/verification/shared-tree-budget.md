# Shared Internal tree budget

The root and supervised Internal children share one durable allowance for reserved tool calls and summed active-worker seconds. Context preparation and provider planning are metered before tools; human approval and child-decision waits pause worker leases. These displayed units do not estimate token usage or cost.

SQLite reservations are atomic across independent Store connections. Exact parked retries preserve reservations; cancellation does not reset usage. Startup treats uncertain live leases conservatively. Approval, grant version and recovery receipt commit in one transaction; restart asks for explicit continuation within the same approved grant. Resumed planning still requires separate plan approval.

Actual spawned children now escalate both tool and budget decisions to the parent's conversation. Invalid child budget grants preserve both pending approvals. Durable verified wrapper metadata reconstructs a missing relay map; sibling continuation reuses the approved grant. Human waits pause the independent child watchdog as well as the shared active-seconds meter.

[Coordinator regressions](../../../tests/test_tree_budget.py) cover concurrent children, planning/provider work, cancellation, retries, independent connections, transaction rollback, sibling reuse, restart receipts and actual watcher escalation. The final focused backend slice passed 16 tests (108 deselected), plus two existing fanout/async-handle regressions. The parent separately reported 16 modal regressions in three files. The previous 24 backend and four original UI checks remain explicitly historical in the [evidence](shared-tree-budget.json).

The [actual rendered host proof](shared-tree-budget-rendered.md) passed 13 loaded states and 31 keyboard/ring checks with four explicit dialog focus returns. It verifies one HTTP extension, real spawned child completion, unchanged human-wait accounting, host restart and explicit same-grant continuation. The final immutable bundle is recorded with every asset hash; no UI/server errors occurred. Installed-native and live-provider qualification remain independent.
