"""Durable tool reservations and summed active-worker seconds for a task tree.

One host coordinator owns execution leases. On coordinator startup, uncertain
leases exhaust the time allowance rather than silently granting replay time.
Human approval waits have no lease. Reservations survive cancellation and renewal.

Provider tokens and cost are recorded per turn for the tree total. Cost is only
summed from turns with a known price: an unknown price stays unknown and never
counts as zero, so ``used_cost_usd`` is None until a priced turn arrives and
``unpriced_tokens`` shows how much usage it does not cover. Tokens and cost are
reported here, not enforced (Auto-mode ceilings enforce them).
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from time import time

from termx.agent.limits import resolve_limits


class TreeBudget:
    def __init__(self, store, *, clock=time):
        self.store = store
        self.clock = clock
        with store._lock:
            store._db.executescript('''
                CREATE TABLE IF NOT EXISTS agent_tree_budget(root TEXT PRIMARY KEY,max_steps INTEGER NOT NULL,max_seconds REAL NOT NULL,steps INTEGER NOT NULL DEFAULT 0,seconds REAL NOT NULL DEFAULT 0,version INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS agent_tree_member(task TEXT PRIMARY KEY,root TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agent_tree_step(task TEXT NOT NULL,position TEXT NOT NULL,PRIMARY KEY(task,position));
                CREATE TABLE IF NOT EXISTS agent_tree_lease(task TEXT PRIMARY KEY,root TEXT NOT NULL,touched REAL NOT NULL);
            ''')
            columns = {row['name'] for row in store._db.execute('PRAGMA table_info(agent_tree_budget)')}
            for column, ddl in (('tokens', 'ALTER TABLE agent_tree_budget ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0'),
                                ('unpriced_tokens', 'ALTER TABLE agent_tree_budget ADD COLUMN unpriced_tokens INTEGER NOT NULL DEFAULT 0'),
                                ('cost_usd', 'ALTER TABLE agent_tree_budget ADD COLUMN cost_usd REAL')):
                if column not in columns:
                    store._db.execute(ddl)
            store._db.commit()
            with self._transaction():
                store._db.execute('UPDATE agent_tree_budget SET seconds=max(seconds,max_seconds) WHERE root IN (SELECT root FROM agent_tree_lease)')
                store._db.execute('DELETE FROM agent_tree_lease')

    @contextmanager
    def _transaction(self):
        """Serialize read/modify/write across independent SQLite connections."""
        self.store._db.execute('BEGIN IMMEDIATE')
        try:
            yield
        except BaseException:
            self.store._db.rollback()
            raise
        else:
            self.store._db.commit()

    def bind(self, task_id):
        with self.store._lock:
            existing = self.store._db.execute('SELECT root FROM agent_tree_member WHERE task=?', (task_id,)).fetchone()
            if existing:
                return existing['root']
            chain = []
            current = task_id
            for _ in range(256):
                if current in chain:
                    raise ValueError('Task parent links contain a cycle')
                chain.append(current)
                task = self.store.get_task(current)
                if not task:
                    raise KeyError(current)
                parent = task.get('parent_id')
                if not parent:
                    root = current
                    break
                current = parent
            else:
                raise ValueError('Task ancestry exceeds the supported depth')
            with self._transaction():
                self.store._db.execute('INSERT OR IGNORE INTO agent_tree_budget(root,max_steps,max_seconds) VALUES(?,?,?)', (root, task['limits']['max_steps'], task['limits']['max_seconds']))
                self.store._db.executemany('INSERT OR IGNORE INTO agent_tree_member VALUES(?,?)', [(identifier, root) for identifier in chain])
            return root

    def _settle(self, root):
        now = self.clock()
        leases = self.store._db.execute('SELECT task,touched FROM agent_tree_lease WHERE root=?', (root,)).fetchall()
        delta = sum(max(0, now - lease['touched']) for lease in leases)
        if delta:
            self.store._db.execute('UPDATE agent_tree_budget SET seconds=seconds+? WHERE root=?', (delta, root))
        for lease in leases:
            self.store._db.execute('UPDATE agent_tree_lease SET touched=? WHERE task=?', (max(now, lease['touched']), lease['task']))
        return self.store._db.execute('SELECT * FROM agent_tree_budget WHERE root=?', (root,)).fetchone()

    @staticmethod
    def _public(row, active):
        reason = ('shared_max_seconds' if row['seconds'] >= row['max_seconds'] else
                  'shared_max_steps' if row['steps'] >= row['max_steps'] else None)
        return {'root_task_id': row['root'], 'max_steps': row['max_steps'], 'used_steps': row['steps'],
                'max_execution_seconds': int(row['max_seconds']), 'used_execution_seconds': round(row['seconds'], 3),
                'active_workers': active, 'version': row['version'], 'reason': reason,
                'used_tokens': row['tokens'], 'used_cost_usd': row['cost_usd'], 'unpriced_tokens': row['unpriced_tokens']}

    def snapshot(self, task_id):
        with self.store._lock:
            root = self.bind(task_id)
            with self._transaction():
                row = self._settle(root)
                active = self.store._db.execute('SELECT count(*) FROM agent_tree_lease WHERE root=?', (root,)).fetchone()[0]
            return self._public(row, active)

    def start(self, task_id):
        with self.store._lock:
            root = self.bind(task_id)
            with self._transaction():
                self._settle(root)
                self.store._db.execute('INSERT OR IGNORE INTO agent_tree_lease VALUES(?,?,?)', (task_id, root, self.clock()))
            return self.snapshot(task_id)

    def stop(self, task_id):
        with self.store._lock:
            root = self.bind(task_id)
            with self._transaction():
                self._settle(root)
                self.store._db.execute('DELETE FROM agent_tree_lease WHERE task=?', (task_id,))

    def record_usage(self, task_id, tokens, cost_usd=None):
        """Add one provider turn to the tree total. ``cost_usd=None`` means the model's price is unknown."""
        tokens = max(0, int(tokens or 0))
        with self.store._lock:
            root = self.bind(task_id)
            with self._transaction():
                if cost_usd is None:
                    self.store._db.execute('UPDATE agent_tree_budget SET tokens=tokens+?,unpriced_tokens=unpriced_tokens+? WHERE root=?', (tokens, tokens, root))
                else:
                    self.store._db.execute('UPDATE agent_tree_budget SET tokens=tokens+?,cost_usd=coalesce(cost_usd,0)+? WHERE root=?', (tokens, float(cost_usd), root))

    def reserve(self, task_id, positions):
        positions = list(positions)
        if not positions or len(positions) > 256 or any(not isinstance(position, str) or not position or len(position) > 256 for position in positions):
            raise ValueError('Provide bounded execution call positions')
        positions = list(dict.fromkeys(positions))
        with self.store._lock:
            root = self.bind(task_id)
            with self._transaction():
                row = self._settle(root)
                new = [position for position in positions if not self.store._db.execute('SELECT 1 FROM agent_tree_step WHERE task=? AND position=?', (task_id, position)).fetchone()]
                reason = ('shared_max_seconds' if row['seconds'] >= row['max_seconds'] else
                          'shared_max_steps' if row['steps'] + len(new) > row['max_steps'] else None)
                if not reason:
                    self.store._db.executemany('INSERT INTO agent_tree_step VALUES(?,?)', [(task_id, position) for position in new])
                    self.store._db.execute('UPDATE agent_tree_budget SET steps=steps+? WHERE root=?', (len(new), root))
            return reason

    def _renew(self, root, row, limits, version):
        if row['version'] != version:
            raise ValueError('The shared tree budget changed; inspect its current allowance')
        if limits['max_steps'] <= row['steps']:
            raise ValueError('The shared step ceiling must exceed the total reserved calls')
        self.store._db.execute('UPDATE agent_tree_budget SET max_steps=?,max_seconds=?,seconds=0,version=version+1 WHERE root=?', (limits['max_steps'], limits['max_seconds'], root))
        self.store._db.execute('UPDATE agent_tree_lease SET touched=? WHERE root=?', (self.clock(), root))

    def renew(self, task_id, limits, *, version):
        limits = resolve_limits(limits)
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError('Provide the inspected shared budget version')
        with self.store._lock:
            root = self.bind(task_id)
            with self._transaction():
                self._renew(root, self._settle(root), limits, version)
            return self.snapshot(task_id)

    def approve(self, task_id, approval_id, limits, *, version, renew=True):
        """Commit decision, grant and resumable intent together.

        If the host dies before launching the worker, the receipt requires an
        explicit restart confirmation within this grant, never a second reset.
        """
        limits = resolve_limits(limits)
        with self.store._lock:
            root = self.bind(task_id)
            with self._transaction():
                approval = self.store._db.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
                if not approval or approval['task_id'] != task_id or approval['kind'] != 'budget':
                    raise KeyError(approval_id)
                if approval['status'] != 'pending':
                    raise ValueError('approval is already resolved')
                row = self._settle(root)
                if renew:
                    self._renew(root, row, limits, version)
                elif row['version'] != version or self._public(row, 0)['reason']:
                    raise ValueError('The shared allowance changed; inspect its current budget request')
                task = self.store.get_task(task_id)
                runtime = {**(task.get('runtime') or {}), 'started_at': None,
                           'tree_budget_resume': {'approval_id': approval_id}}
                self.store._db.execute('UPDATE tasks SET limits=?,runtime=?,updated_at=? WHERE id=?', (json.dumps(limits), json.dumps(runtime), time(), task_id))
                self.store._db.execute("UPDATE approvals SET status='approved',resolved_at=? WHERE id=?", (time(), approval_id))
                result = self.store._db.execute('SELECT * FROM approvals WHERE id=?', (approval_id,)).fetchone()
            return self.store._approval(result)


class TreeTimeExceeded(RuntimeError):
    pass
