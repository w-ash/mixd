"""SHA pin for the vendored Tidal OpenAPI spec.

The vendored ``tidal-api-oas.json`` is the contract the hand-written
``oas_models.py`` shapes were derived from. Pinning its SHA-256 next to the
models means the two can only move together: a refresh
(``scripts/refresh_tidal_oas.py``) that changes the spec fails this test
until someone re-reads the diff and re-derives (or re-blesses) the models.
No network — the test reads only the vendored file.
"""

import hashlib
import json
from pathlib import Path

from src.infrastructure.connectors.tidal import oas_models
from src.infrastructure.connectors.tidal.oas_models import TIDAL_OAS_SHA256

VENDORED_SPEC = Path(oas_models.__file__).parent / "tidal-api-oas.json"


def test_vendored_spec_matches_pinned_sha() -> None:
    """The vendored spec's SHA-256 equals the pin in oas_models.py."""
    digest = hashlib.sha256(VENDORED_SPEC.read_bytes()).hexdigest()
    assert digest == TIDAL_OAS_SHA256, (
        "Vendored tidal-api-oas.json drifted from the pin in oas_models.py. "
        "Re-read the spec diff, update the models if the consumed shapes "
        "changed, then update TIDAL_OAS_SHA256 to the new digest "
        "(printed by scripts/refresh_tidal_oas.py)."
    )


def test_pin_is_a_sha256_hex_digest() -> None:
    """The pin constant is a lowercase 64-char hex digest."""
    assert len(TIDAL_OAS_SHA256) == 64
    assert set(TIDAL_OAS_SHA256) <= set("0123456789abcdef")


def test_vendored_spec_is_canonical_json() -> None:
    """The vendored file is valid JSON in the canonical serialization.

    Canonical form (sorted keys, 2-space indent, trailing newline) is what
    ``scripts/refresh_tidal_oas.py`` writes — asserting it here keeps hand
    edits from silently de-canonicalizing the file, which would make every
    future refresh diff unreadable.
    """
    raw = VENDORED_SPEC.read_text(encoding="utf-8")
    spec = json.loads(raw)
    assert raw == json.dumps(spec, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert spec["openapi"].startswith("3.")
