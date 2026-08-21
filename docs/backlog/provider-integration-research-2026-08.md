# Provider Integration Research — 2026-08

**Status**: Active — captured 2026-08-20 from the August 2026 provider research pass (live probes
of the Spotify, Apple Music, Discogs, and Tidal APIs). Drains into: the v0.11.0/.1/.2/.3 and
v0.13.0/.1/.4 epics, PDR-002/PDR-003 ledger rows, and the
[identity-resolution-design-space.md](identity-resolution-design-space.md) §6 cell refresh.
Delete when v0.11.3 and v0.13.4 ship and no unshipped epic cites a section here.

Scope: engineering facts the connector epics cite — capabilities, auth, rate behavior, endpoint
constraints, open probes. Per-epic design lives in the version files. ⚠︎ marks a low-confidence
or community-sourced fact.

## 1. Capability matrix

Use cases: **U1** saved/liked tracks · **U2** playlists read+write · **U3** listening history ·
**U4** ISRC/UPC + text matching · **U5** metadata enrichment · **U6** self-hosted multi-user auth.
Cells: **S** supported · **P** partial · **N** no.

| | Spotify (dev mode) | Apple Music | Discogs | Tidal |
|---|---|---|---|---|
| **U1** | **S** — `GET /me/tracks` (scope `user-library-read`, ≤50/page, `added_at` cursor); writes `PUT/DELETE /me/library?uris=` ≤40 | **P** — no liked-songs collection; library songs list is alphabetical, no ISRC; `recently-added` is the only dateAdded order; favorites write-only | **N** — collection folders + wantlist are release-granular, never track-granular | **S** — `GET/POST/DELETE /userCollectionTracks/me/relationships/items`, scopes `collection.read/write`, `sort=-addedAt`, mutations ≤50 |
| **U2** | **S** — own/collaborated playlists R/W; add ≤100 URIs; `snapshot_id`; third-party playlist items return 403 | **P** — read + create + append-to-end only; no remove, reorder, rename, delete | **N** — Lists are read-only | **S** — full CRUD: add ≤50/req with `positionBefore`, reorder ≤20/req, delete items ([staff, 2026-07-29](https://github.com/orgs/tidal-music/discussions/350)) |
| **U3** | **P** — `GET /me/player/recently-played`, ~50-item rolling window, `before/after` cursors, no backfill | **N** — no endpoint returns `playedAt`; `recent/played/tracks` window ≈30 ⚠︎, no timestamps | **N** — no plays endpoint | **N** — no history endpoint ([staff, 2024-09-17](https://github.com/orgs/tidal-music/discussions/96)) |
| **U4** | **P** — `search` `isrc:`/`upc:` filters work; `external_ids` retained ([2026-03 changelog](https://developer.spotify.com/documentation/web-api/references/changes/march-2026)); search limit max 10 / default 5 ⚠︎; batch `GET /tracks?ids=` works for this app (PDR-003) | **S** — `filter[isrc]` ≤25/req, `filter[upc]` on albums, `filter[equivalents]` cross-storefront; storefront mandatory in every catalog path; no EAN | **P** — `barcode=` search (UPC/EAN); **no ISRC parameter**; text search by artist/title/label/catno; durations are strings, often blank | **S** — `GET /tracks?filter[isrc]=`, `GET /albums?filter[barcodeId]=` (EAN-13/UPC-A); free-text `searchResults` has no fielded filters — match client-side |
| **U5** | **P** — ISRC/UPC + artwork + release date only; audio-features/recommendations return 403 (2024-11-27), no replacement; no credits endpoint | **P** — genres, artwork `{w}x{h}`, `composerName`, `isrc`, `upc`; no credits endpoint | **S** — genres+styles taxonomy, `extraartists[]` credit roles, labels/catno, pressing data, images | **P** — isrc, barcode, releaseDate, duration, bpm/key, popularity, artwork; genres/credits/lyrics not returned to this app class ⚠︎ |
| **U6** | **P** — auth-code (+PKCE) server-side; 5-user cap; owner needs Premium; refresh grants expire 6 months from original authorization; HTTPS redirects (loopback IP for CLI) | **N** server-side — Music User Token (MUT) only via MusicKit JS in a browser; 6-month fixed lifetime; no refresh; no CLI path | **P** — OAuth 1.0a or personal access token (PAT); tokens do not expire; PAT fits the CLI | **S** — OAuth 2.1 + PKCE, refresh tokens, device-code flow, no user cap |

## 2. Auth comparison

| | Spotify | Apple Music | Discogs | Tidal |
|---|---|---|---|---|
| **Flows** | Auth code, auth code+PKCE, client credentials; token `POST accounts.spotify.com/api/token` | Developer token = self-signed ES256 JWT (Team ID, `kid`, `exp` ≤6 months). User = MUT via MusicKit JS `authorize()` popup only — no redirect flow, no client secret | OAuth 1.0a (request → authorize → access; PLAINTEXT signature over HTTPS) or PAT | OAuth 2.1: auth code + PKCE S256 (mandatory for all clients); client_credentials for catalog; device-code flow works, undocumented. Authorize `login.tidal.com/authorize`; token `auth.tidal.com/v1/oauth2/token` |
| **Access-token TTL** | 3600 s | Dev token: chosen at mint, ≤6 months. MUT: 6 months fixed | No expiry | Documented 86 400 s; one 2026-07 report measured 4 h ⚠︎ — trust the live `expires_in` |
| **Refresh** | Refresh token expires 6 months after the original authorization; refreshing does not extend it; expiry = 400 `invalid_grant` ([policy, 2026-06-18](https://developer.spotify.com/blog/2026-06-18-refresh-token-expiration); enforced for existing apps 2026-07-20). Rotation: a new refresh token "might not be included" — keep the old one | None. MUT is non-renewable ([Apple staff](https://developer.apple.com/forums/thread/654814)); also invalidates on password/subscription change. MusicKit's browser storage is per-origin — a hostname change silently de-authorizes ([music-assistant #3897](https://github.com/orgs/music-assistant/discussions/3897)) | None needed; 401 is the only revocation signal. Request token/verifier expire in 15 min | `refresh_token` grant works ([fixed 2026-07-16](https://github.com/orgs/tidal-music/discussions/333)); tokens rotate — treat a concurrent refresh as replay. Refresh-token lifetime undocumented ⚠︎ |
| **Registration** | Free dashboard; owner needs active Premium (2026-03-09); 25 client IDs/account with one pooled quota (2026-07-23); extended quota = orgs ≥250k MAU ([2025-04-15](https://developer.spotify.com/blog/2025-04-15-updating-the-criteria-for-web-api-extended-access)) | US$99/yr Apple Developer Program per deployment; Media ID + ≤2 Media Services keys; no domain/origin/redirect-URI field exists — the `origin` JWT claim is optional and developer-authored | Free; per-deployment app; PAT needs no app at all | Free dashboard; ≤10 apps/account, ≤3 redirect URIs/app, name ≤30 chars; each deployment registers its own app |
| **Server implements** | `invalid_grant` handling, `authorized_at` persistence, reconnect UX; loopback-IP redirect for CLI | ES256 minting (rotate ~90 d); MusicKit JS bridge page with `Referrer-Policy: strict-origin-when-cross-origin` (stricter policies are the documented cause of `authorize()` 403s); MUT persisted server-side with explicit `expires_at`; 403 `TOKEN_EXPIRED` → reconnect | PAT entry field; unique User-Agent; 401-as-revocation | Server-held PKCE verifier; single-flight refresh; BYO client ID per deployment; device-code or localhost redirect for CLI; vendored OpenAPI spec diffed in CI |

## 3. Rate limits and quotas

| Provider | Published | Signals | Bucket scope | Confidence |
|---|---|---|---|---|
| Spotify | None; rolling 30 s window | 429 + `Retry-After`; quota exhaustion = 429 body `reason: "QUOTA_EXCEEDED"` (does not clear on `Retry-After` timescales) | Per developer account — all client IDs pooled (2026-07-23) | Window/headers official; throughput figures folklore ⚠︎ |
| Apple Music | None; no rate headers; no tiers | 429, self-resolving; no documented `Retry-After` | Per developer token → all users of an instance share it | Numbers unverifiable ⚠︎ |
| Discogs | 60 req/min authenticated / 25 unauthenticated; 60 s moving average; `X-Discogs-Ratelimit[-Used/-Remaining]` headers; 10,000-item pagination ceiling | 429; headers are the only true view (counter can be non-zero on the first call — shared egress) | **Per source IP** — the whole instance shares one counter (docs; [staff, 2021-05-03](https://www.discogs.com/forum/thread/867991); probed 2026-08-20). `i.discogs.com` images = separate undocumented bucket | High. Pace ~40–45 req/min: bursts trip the moving average at 30–50 ⚠︎ |
| Tidal | None anywhere ([staff, 2025-07-14](https://github.com/orgs/tidal-music/discussions/135): `Retry-After` added, other headers removed) | 429 JSON:API body + `Retry-After` | Unknown (client ID vs user token vs IP) ⚠︎ — BYO client ID gives each deployment its own budget either way | Design to `Retry-After` only |
| MusicBrainz (context) | 1 req/s per IP; mandatory User-Agent | 503 | Per IP → instance-wide | Official |

## 4. Per-provider endpoint facts

### Spotify
- Playlist items endpoint is `/playlists/{id}/items` (renamed from `/tracks`); saved-tracks writes are `/me/library` ([reference](https://developer.spotify.com/documentation/web-api/reference/get-playlists-items)). The client is already migrated (`.claude/rules/spotify-api.md`).
- Search `limit` max 10 / default 5; deeper pools paginate `offset` in steps of 10.
- Dev-mode `GET /me` omits `email`, `country`, `product`; `account_id` (added 2026-05) is the designated external-linkage key — verify the live field name ⚠︎.
- Batch `GET /tracks?ids=` returns 200 for this app (probed 2026-08-08); it stays on the postponed removal list — PDR-003 holds the watch and fallback.

### Apple Music
- Catalog paths embed a storefront segment; read the user's storefront via `GET /v1/me/storefront` (needs a MUT). `filter[equivalents]` (≤300) maps IDs across storefronts.
- `GET /v1/me/library/songs`: alphabetical, no dateAdded sort, no ISRC — join to the catalog via `playParams.catalogId` ([reference](https://developer.apple.com/documentation/applemusicapi/Get-All-Library-Songs)). `/v1/me/library/recently-added` is the only dateAdded ordering.
- Favorites: `POST /v1/me/favorites` → 202; no GET.
- Playlists: read; `POST /v1/me/library/playlists` (create); append-to-end. No remove, reorder, rename, delete.
- `GET /v1/me/recent/played/tracks` (`types=songs`): window ≈30 items ⚠︎ (community-measured 2021–23), offset paging, **no timestamps**.
- Catalog search: no structured field search; offset caps at 75 (~100 results).
- MusicKit JS v3 loads only from Apple's CDN; `authorize()` sends the page host as the app name and the full URL as referrer.

### Discogs
- Endpoints: `/users/{username}/collection/folders/0/releases`, `/releases/{id}`, `/masters/{id}`, `/database/search`.
- `barcode=` search accepts UPC/EAN. No ISRC parameter; ISRC appears only as sparse free text in `identifiers[]`. Durations are strings and often blank — matching is artist+title+catno+format.
- Generic User-Agents get silently lower limits; send `Mixd/<version> +<repo-url>`.
- OAuth 1.0a and PAT share the same per-IP bucket — auth choice does not change throughput.
- Monthly CC0 XML dumps exist at [data.discogs.com](https://data.discogs.com/) (releases ~5 GB gz). Out of scope by decision (2026-08-20): the connector is API-only.

### Tidal
- JSON:API responses; cursor pagination with server-controlled page size; `Idempotency-Key` header on writes; include-tree depth is limited.
- Collection: `GET/POST/DELETE /userCollectionTracks/me/relationships/items`; `sort=-addedAt` default; mutations ≤50 items/request.
- Playlists: `POST/PATCH/DELETE /playlists`; add ≤50 items/request with `positionBefore`; reorder ≤20/request; delete items. Custom cover art and collaborators are not available to this app class.
- Matching: `GET /tracks?filter[isrc]=` (1:N, paginated; multi-ISRC batching returns one match per code — send one code per request); `GET /albums?filter[barcodeId]=`.
- Free-text search: `GET /searchResults?filter[query]=` — no fielded filters. Contract changed 2026-08-12 and was fixed same-day ([#366](https://github.com/orgs/tidal-music/discussions/366)) — the reason to vendor and diff the OpenAPI spec.
- The `replacement` relationship is the official successor pointer for dead IDs (mass ID substitution has precedent: the 2024 MQA catalog swap).
- Legacy v1 API and the `tidalapi` PyPI library are deprecated — build only on the documented v2 API.

## 5. Open technical probes

Each probe is cheap; run it before the fact it guards is load-bearing.

1. **Discogs bucket sharing (authenticated)** — alternate two PATs from one IP; a monotonic `X-Discogs-Ratelimit-Used` sequence (0,1,2,3…) confirms one shared counter.
2. **Tidal bucket keying** — two user tokens under one client ID; interleave calls and watch 429 onset.
3. **Tidal allowed scopes** — register the app; record the dashboard's allowed-scope list (v0.11.3 epic completion note).
4. **Tidal access-token TTL** — log `expires_in` from live token responses (86,400 s documented vs 4 h measured ⚠︎).
5. **Apple `authorize()` origin shapes** — probe `http://localhost:5173`, an HTTPS host, and `http://192.168.x.x:8080`; no source documents the LAN-IP case.
6. **Apple recently-played window** — measure the real item depth (≈30 is community-measured, 2021–23).
7. **Spotify search cap** — confirm whether the 10-item limit binds this app (mitigation is identical either way: offset pagination). Fold into PDR-003's re-verify cadence.
8. **Spotify `account_id`** — verify the field name on a live `GET /me` before the v0.11.2 linkage work.
9. **Last.fm scrobble limits** — the "tightened 2026 limits" claim is uncited ⚠︎; probe before relying on it for import throughput planning.
