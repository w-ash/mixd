---
paths:
  - "alembic/**"
  - "src/infrastructure/persistence/database/**"
---
# Database Migration Rules

- **Run `uv run alembic heads` before setting `down_revision`** — the graph must have exactly one head; point the new migration at it. `alembic history` grep misleads when hashed-name migrations sit on orphan branches.
- **Keep identifiers ≤ 63 chars** (Postgres `NAMEDATALEN`). SQLAlchemy's `op.f()` silently hash-truncates longer names on CREATE (`..._fef9`), so later `RENAME CONSTRAINT` against the declared name fails. If an identifier must stay long, rename via `pg_constraint` lookup in a `DO $$ ... $$` block rather than hardcoding the hashed form.
- **Verify migrations end-to-end before tagging a version**: `docker run -d postgres:17-alpine` → `DATABASE_URL=... uv run alembic upgrade head` → `alembic downgrade <prior>` → `upgrade head`. Integration tests use `metadata.create_all` and bypass the migration chain entirely — broken DDL ships through them.
- **Number-prefix the revision id** (`016_playlist_metadata_mappings`), not the auto-generated hash. Mixed-style ids make `alembic history` harder to scan.
- **Renaming an enum string value requires `UPDATE table SET col = new WHERE col = old`** in the migration — changing the Python `Literal` does not migrate existing rows.
- **Each migration file is one DDL transaction** — on failure, the whole file rolls back and the DB stays at the previous revision. No partial-state recovery logic.
