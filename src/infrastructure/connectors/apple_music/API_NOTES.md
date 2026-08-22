# Apple Music API Notes

Facts verified against the live API. Probe date: 2026-08-22. Storefront: `us`.

## Authentication errors: 401 vs 403

- A bad developer token causes HTTP 401.
- A bad or dead Music User Token (MUT) causes HTTP 403.
- The 403 body does not contain a `TOKEN_EXPIRED` string.
- Rule: 401 = instance problem (developer token). 403 on a MUT-bearing endpoint = user problem (reauthorize).
- The client treats any 403 as a MUT rejection and sets `reauth_required`.
- A 401 never sets `reauth_required`.

Verbatim 403 body for a dead MUT:

```json
{"errors": [{"code": "40300", "detail": "Invalid authentication",
  "id": "REDACTED", "status": "403", "title": "Forbidden"}]}
```

## GET /v1/me/storefront

- Requires the MUT header.
- Returns one storefront resource. Only `id` (for example `"us"`) is consumed.

## GET /v1/catalog/{storefront}/songs?filter[isrc]=

- A hit returns 200 with matching songs in `data`.
- One ISRC can return many songs. Observed: 43 songs for one ISRC (one recording, many releases).
- Correlate results by `attributes.isrc`, not by position.
- A miss returns 200 with empty `data`. A miss is not an error.
- `meta.filters.isrc` maps each requested code to its matching song references.
- Catalog songs carry `isrc` and `durationInMillis` reliably.
- Catalog `playParams` is `{id, kind}` only. No `catalogId`.
- All observed song ids are numeric strings.

Redacted miss sample:

```json
{"data": [], "meta": {"filters": {"isrc": {"QQQAA0000001": []}}}}
```

## GET /v1/catalog/{storefront}/songs?ids=

- Returns full song resources for known ids.
- Same song shape as the ISRC lookup. Same `playParams` rule.

## GET /v1/catalog/{storefront}/songs?filter[equivalents]=

- Returns the storefront-local equivalent for each requested id.
- `meta.filters.equivalents` maps each requested id to its equivalent references.
- A same-storefront id maps to itself.

## GET /v1/me/recent/played/tracks

- Requires the MUT header.
- Maximum `limit` is 30. `limit=50` returns 400 with code `"40005"`.
- Paging is offset-based. `next` looks like `/v1/me/recent/played/tracks?offset=30&types=songs`.
- The window is at least 60 items deep (two full pages observed, `next` still present).
- The window contains unique songs only. Apple collapses repeats.
- A re-listen MOVES an item to the head. It does not prepend a duplicate.
- Consequence: the importer's conflict-skip turnover path is the designed handling. The honest floor is a ~30-song fingerprint that ages out.
- Items report no timestamps and no play durations.
- `playParams` is `{id, kind}` only, as in the catalog. `playParams.catalogId` stays dormant until v0.13 library objects.

Verbatim over-limit error body:

```json
{"errors": [{"code": "40005", "detail": "Value must be an integer less than or
  equal to 30, but was: 50", "id": "REDACTED",
  "source": {"parameter": "limit"}, "status": "400",
  "title": "Invalid Parameter Value"}]}
```

## Error envelope

- Errors arrive as JSON:API: `{"errors": [{id, title, detail, status, code}]}`.
- `status` and `code` are strings.
- `code` observed values: `"40300"` (bad MUT), `"40005"` (invalid parameter value).
