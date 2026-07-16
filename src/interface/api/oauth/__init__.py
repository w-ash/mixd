"""In-app OAuth 2.1 surface for the remote MCP server (v0.9.5).

mixd acts as both the OAuth **authorization server** (issuer
``settings.mcp_oauth.issuer_url``, e.g. ``https://mixd.me``) and the MCP
**resource server** (``settings.mcp_oauth.resource_uri``, e.g.
``https://mixd.me/mcp``). Neon Auth stays authentication-only — the consent
step rides the existing Neon Auth session; this package owns token issuance
and validation for external MCP clients.

Modules:
- ``keys``: Ed25519 signing-key loading + public JWK derivation.
- ``tokens``: access-token mint/verify (PyJWT EdDSA, audience-bound).
"""
