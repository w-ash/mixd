---
paths:
  - "src/application/workflows/**"
  - "src/domain/entities/shared.py"
---
# Workflow Identifier Terminology

Three distinct identifiers — **one meaning each, everywhere** (Python vars, config keys, JSON field names, API request/response fields, log fields, error messages). Never overload one to mean another.

- **`playlist_id`** — the canonical mixd row `playlists.id` (UUID). Never means anything else.
- **`connector_playlist_id`** — the local cache row's primary key `connector_playlists.id` (UUID).
- **`connector_playlist_identifier`** — the external service's own ID (Spotify base62, Apple Music alphanumeric, Last.fm slug). Python side: the `ConnectorPlaylistIdentifier` NewType in `src/domain/entities/shared.py`.

**How to apply:**
- Use cases / services / API schemas: `connector_playlist_identifier: ConnectorPlaylistIdentifier` for external IDs, `playlist_id: UUID` for canonical, `connector_playlist_id: UUID` for the cache PK.
- Error messages and logs: name the field by its true meaning. Never call a Spotify ID a "playlist ID".
- Before coining a new identifier name, confirm none of the three is already the right word.

**Known exception (still live as of v0.8.3):** the `source.playlist` and `destination.update_playlist` workflow nodes use one polymorphic `playlist_id` config key for both meanings — canonical UUID when `connector` is empty, connector identifier when `connector` is set. Marked `KNOWN TERMINOLOGY EXCEPTION` in `src/application/workflows/nodes/config_fields.py` (~L216, ~L691) and `nodes/config_accessors.py` (~L68); `require_connector_playlist_identifier` reads it as-is so saved workflows keep running. The schema, every seed JSON under `definitions/`, and the web editor all emit this shape. Fix when next touching the editor / seed JSONs: split into `playlist_id` (canonical-only) + `connector_playlist_identifier` (connector-only), rewrite the seed JSONs, re-run `mixd workflow seed-personal`. No DB migration needed.
