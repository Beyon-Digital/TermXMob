# Migration — custom agents DB → Markdown files

## Direction

`custom_agents` rows become canonical files at `~/.agents/agents/<slug>.agent.md`. The DB table remains as projection (search/list) + historical-reference integrity (`tasks.custom_agent_id`, `conversations.custom_agent_id` keep resolving).

## Steps (idempotent, reversible)

1. `agents/files.py` gains `import_db_agent(row) -> path` — writes `<slug>.agent.md` with `x-termx.id = agent.<row.id>` preserving the exact stored id in frontmatter `x-termx.legacy_id`.
2. On first host start with `settings.engine_extensions.agents_authority` unset: for each `custom_agents` row, write file if absent; set row `file_path`, `file_revision`, `sync_state='migrated'`, `migrated_at`. Rows are NOT deleted.
3. Set `agents_authority='files'`. From then: host API writes go file-first → re-project into DB.
4. Device-local agents (client SecureStore/localStorage) are pushed through `POST /api/custom-agents` as before; host assigns canonical ids, writes files; client adopts host id (existing `mappedAgentId` mechanism) and marks old local entries migrated, keeping them until host confirms (pending state, never silent).

## Rollback

- `POST /api/admin/agents/authority {mode:"db"}` flips authority back; files stay on disk untouched (reversible).
- Files keep `x-termx.legacy_id` → DB rows still present → zero data loss either direction.
- Delete = file delete + projection row tombstone (`enabled=0`,`sync_state='deleted'`) — historical sessions unaffected.

## Guarantees

- One-shot import, never re-imported (marker column `migrated_at`).
- Id conflicts: file slug exists → suffix `-2`, original DB id preserved in `legacy_id`.
- Corrupt file at read → entry `health.status='malformed'`; projection keeps last-good; never wipes DB.
- Expected-revision precondition on every write; external edits detected via mtime+hash → `conflict` sync state (UI shows conflict, no last-writer-wins).
