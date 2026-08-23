#!/usr/bin/env python3
"""Refresh the vendored Tidal OpenAPI spec and print its SHA-256.

Fetches Tidal's published OpenAPI document, canonicalizes it (sorted keys,
2-space indent, trailing newline) so refresh diffs are readable, and writes
it to ``src/infrastructure/connectors/tidal/tidal-api-oas.json``. Tidal
revises the API in place, so the vendored copy plus the printed SHA-256 pin
(``TIDAL_OAS_SHA256`` in ``tidal/oas_models.py``, enforced by
``test_oas_pin.py``) turn silent contract changes into visible failures.

``--check`` fetches and compares without writing: exit 0 when the vendored
copy matches upstream, exit 1 on drift. The weekly ``tidal-oas-drift.yml``
workflow runs this mode.

Usage:
    uv run python scripts/refresh_tidal_oas.py [--check]
"""

import hashlib
import json
from pathlib import Path
import sys

import httpx2
from pydantic import TypeAdapter

TIDAL_OAS_URL = "https://tidal-music.github.io/tidal-api-reference/tidal-api-oas.json"
# Shape-only validation at the boundary: "a JSON object" — the vendored file is
# the contract artifact, not something to model field-by-field here.
_SPEC_ADAPTER: TypeAdapter[dict[str, object]] = TypeAdapter(dict[str, object])
VENDORED_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "infrastructure"
    / "connectors"
    / "tidal"
    / "tidal-api-oas.json"
)


def canonicalize(spec: dict[str, object]) -> str:
    """Deterministic serialization: sorted keys, 2-space indent, one trailing newline."""
    return json.dumps(spec, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def fetch_canonical_spec() -> str:
    """Fetch the upstream OpenAPI document and return its canonical form."""
    response = httpx2.get(TIDAL_OAS_URL, follow_redirects=True, timeout=60.0)
    response.raise_for_status()
    spec = _SPEC_ADAPTER.validate_json(response.content)
    if "openapi" not in spec:
        raise ValueError(f"Response from {TIDAL_OAS_URL} is not an OpenAPI document")
    return canonicalize(spec)


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    canonical = fetch_canonical_spec()
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    vendored = (
        VENDORED_PATH.read_text(encoding="utf-8") if VENDORED_PATH.exists() else None
    )
    in_sync = vendored == canonical

    if check_only:
        if in_sync:
            print(f"OK: vendored spec matches upstream (sha256 {digest})")
            return 0
        print(f"DRIFT: upstream spec differs from {VENDORED_PATH}")
        print(f"Upstream sha256: {digest}")
        print(
            "Run `uv run python scripts/refresh_tidal_oas.py`, review the diff "
            "against the consumed shapes in tidal/oas_models.py, then update "
            "TIDAL_OAS_SHA256."
        )
        return 1

    if in_sync:
        print(f"Already up to date: {VENDORED_PATH}")
    else:
        VENDORED_PATH.write_text(canonical, encoding="utf-8")
        print(f"Wrote {VENDORED_PATH} ({len(canonical.encode('utf-8'))} bytes)")
    print(f"sha256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
