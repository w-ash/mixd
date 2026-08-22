# Residue Tenant Purge — recheck on or after 2026-09-04

**Status**: Blocked on a waiting period — script written, tested, never run. Recheck **2026-09-04**.
Owner action is one dry-run command; everything else is written. Delete this file once all three
tenants are purged and `docs/backlog/v0.10.x.md` § *Open: residue tenants in production* is closed out.

**Why the date**: the purge refuses on its own liveness check today, because `default` was still
being written to on 2026-08-20 — one day before the v0.10.4.1 guard closed the door. Fourteen quiet
days is the evidence that the guard held. That is the whole reason to wait; it is not a
scheduling convenience.

---

## Do this first

```bash
uv run python scripts/purge_residue_tenants.py --tenant default
```

Dry run — opens a `READ ONLY` transaction, so Postgres itself refuses any write. Two outcomes:

**It reports the closure and no blockers** → the guard held. Proceed:

```bash
uv run python scripts/purge_residue_tenants.py --tenant default \
    --backup ~/mixd-backups/default-$(date +%F).json --apply
uv run python scripts/purge_residue_tenants.py --tenant repro_7d81986c \
    --backup ~/mixd-backups/repro_7d81986c-$(date +%F).json --apply
uv run python scripts/purge_residue_tenants.py --tenant repro_d46f7460 \
    --backup ~/mixd-backups/repro_d46f7460-$(date +%F).json --apply
```

**It still refuses on liveness** → something writes to `default` through a path the guard does not
cover. *Do not wait longer and retry.* Find the writer first: the timestamps the script prints name
the table, and `track_metrics` is the one that has moved every month. That would mean the session
guard has a hole, which matters far more than the cleanup does.

## What is being deleted

| tenant | rows | origin |
|---|---|---|
| `default` | 17,711 across 17 tables — incl. 424 tracks, 3 workflows, 2 playlists, 12,107 resolution_events, 1 Spotify OAuth token | pre-multi-user snapshot (2026-05-10) + a CLI invocation that picked up `.env.local`'s production URL (2026-07-27) |
| `repro_7d81986c` | 6 (1 track, 1 play, 2 connector_plays, 2 play_sources) | reproduction script run against prod (2026-07-17) |
| `repro_d46f7460` | 6, same shape | same |

**561 of `default`'s rows are reachable only by CASCADE** — 555 `playlist_tracks`, 5
`workflow_run_nodes`, 1 `workflow_run`. They belong to no tenant by column and are exactly what the
first, condemned draft of this script omitted from both its row count and its backup.

Everything under `default` is superseded: all 424 tracks share an ISRC with the real user's library,
and all three workflows and both playlists have namesakes under the real account with equal-or-more
content (👀 Likely Suspects: 568 tracks vs 430).

## The evidence trail

`default` was written to on a roughly monthly cadence — `track_metrics` bursts on 05-10, 05-11,
06-19, 06-25, 07-04, 07-26, and **08-20 (264 rows at 20:51)**, with `connector_plays` updated at
22:20 and a live Spotify token refresh at 21:08 the same evening. The v0.10.4.1 guard
(`set_rls_user_on_begin` refusing `DEFAULT_USER_ID` against a non-local database) deployed
2026-08-21. So the last known write predates the guard by about a day — consistent with the door
closing, unproven until a full cycle passes without a new burst.

## Things a future reader will otherwise get wrong

- **The cascade-blast refusal cannot be demonstrated against current production data.**
  `reown_cross_tenant_tracks.py` already repaired the v0.10.3 C3 population; today all 555
  `playlist_tracks` sit inside `default`'s *own* playlists and *own* tracks, and there are zero
  foreign references of any kind into `default`'s rows. The refusal is proven in
  `tests/integration/test_purge_residue_tenants.py`, not in prod. A clean dry run is therefore
  expected — it is not evidence the check is inert.
- **RLS is enabled and FORCEd on 24 production tables**, and the purge only sees the whole database
  because `neondb_owner` holds `BYPASSRLS`. A role without it sees zero rows and would
  "successfully" delete nothing. The script refuses when its own view is filtered — run it as the
  owner.
- **`RESTRICT` is evaluated at end-of-statement, not row-at-a-time.** A single DELETE over a whole
  `track_mappings` supersession chain succeeds; no pre-NULLing step is needed. An earlier draft had
  one on the opposite assumption.
- **The backup is the only undo.** It round-trips through `to_jsonb`, so jsonb columns and NULLs
  survive as real JSON rather than Python `repr`. Keep it until you have clicked around the app.

## Related

- Full context and the audit that condemned the first draft: [v0.10.x.md § Open: residue tenants
  in production](v0.10.x.md#open-residue-tenants-in-production)
- The guard that closed the door: `src/infrastructure/persistence/database/user_context.py`
  (`_refuse_default_user_on_remote`, `system_context`), shipped v0.10.4.1
- Still open, the DDL half of the same mechanism: **23 tables carry
  `DEFAULT 'default'::varchar` on `user_id`**, matched by `default="default"` on the 23 ORM
  columns. Any INSERT omitting `user_id` still lands in a phantom tenant — the session guard
  refuses a transaction that *declares* itself `default`, but a column default supplies the value
  without anyone declaring anything. **Not a one-line fix**: stripping both defaults from
  `db_models.py` fails **190 tests** (measured 2026-08-21). The dependency is test fixtures that
  build rows without a `user_id`, not production code — so it is a fixture-migration task, and it
  is the right follow-up once the rows are purged.
