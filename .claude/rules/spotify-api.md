---
paths:
  - "src/infrastructure/connectors/spotify/**"
---
# Spotify Web API Rules

- **OpenAPI spec is source of truth**: https://developer.spotify.com/reference/web-api/open-api-schema.yaml — don't guess endpoints or field names.
- **No deprecated endpoints**: `/playlists/{id}/items` not `/playlists/{id}/tracks`; `/me/library` not type-specific library endpoints.
- **Minimum scopes only**: don't add OAuth scopes preemptively. Ref: https://developer.spotify.com/documentation/web-api/concepts/scopes
- **Paginate to completion**: user collections (playlists, liked tracks) require iterating all pages. Max `limit` varies by endpoint (commonly 50). Use `next` URL or `total` to detect more pages.
- **Refresh tokens expire 6 months after the original authorization** (policy 2026-06-18). A refresh does NOT extend the window — `extra_data["authorized_at"]` on the stored token anchors it. Expiry surfaces as HTTP 400 `invalid_grant` on the refresh POST, and only there: on detection, delete the dead token (it can never succeed again) and raise `SpotifyReauthRequiredError` → status `reauth_required` → one-click Reconnect. Never retry an `invalid_grant`.
