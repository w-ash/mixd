"""Per-user Anthropic credential management (v0.9.0.1 BYO-key).

Connect / test / remove the acting user's Anthropic API key, plus a per-user
capability signal (``GET /assistant/status``) that the frontend uses to gate the
entire chat surface. The key is a write-only secret — validated live with a
minimal completion (so a key with no billing is caught here, not on first use)
before it is stored encrypted, and never returned in any response.

These handlers read/write the token store directly rather than through
``execute_use_case`` — the sanctioned v0.6.5 exception for connector/OAuth
credential management, of which this is the same shape.
"""

from fastapi import APIRouter, Depends

from src.domain.exceptions import ChatUnavailableError, InvalidApiKeyError
from src.infrastructure.chat.anthropic_adapter import (
    evict_adapter_cache,
    validate_anthropic_key,
)
from src.infrastructure.chat.credentials import (
    delete_user_anthropic_key,
    load_user_anthropic_key,
    looks_like_anthropic_key,
    save_user_anthropic_key,
)
from src.interface.api.deps import get_current_user_id, resolve_chat_source
from src.interface.api.rate_limit import InMemoryRateLimiter
from src.interface.api.schemas.assistant import (
    AssistantStatusResponse,
    ConnectKeyRequest,
    ConnectKeyResponse,
    TestKeyRequest,
    TestKeyResponse,
)

router = APIRouter(tags=["assistant"])

# Both key-write paths run a live Anthropic completion, which makes them a
# validation oracle (submit-and-observe to brute-force keys) and a cost lever.
# A tight per-user budget throttles that without impeding legitimate use — a
# user connects/tests their key a handful of times, not dozens per minute.
_key_probe_limiter = InMemoryRateLimiter(max_requests=5, window_seconds=60)


@router.get("/assistant/status")
async def get_assistant_status(
    user_id: str = Depends(get_current_user_id),
) -> AssistantStatusResponse:
    source = await resolve_chat_source(user_id)
    return AssistantStatusResponse(connected=source is not None, source=source)


@router.put("/assistant/key")
async def put_assistant_key(
    body: ConnectKeyRequest,
    user_id: str = Depends(get_current_user_id),
) -> ConnectKeyResponse:
    _key_probe_limiter.check(user_id)
    key = body.api_key.strip()
    if not looks_like_anthropic_key(key):
        raise InvalidApiKeyError(
            "That doesn't look like an Anthropic API key (expected 'sk-ant-…')."
        )
    # A transport/5xx failure raises ChatUnavailableError (→ 503) rather than a
    # false "rejected" — the middleware maps it to an actionable envelope.
    if not await validate_anthropic_key(key):
        raise InvalidApiKeyError(
            "Anthropic rejected that key. Check it was copied in full and that "
            "your account has billing set up."
        )
    await save_user_anthropic_key(user_id, key)
    evict_adapter_cache()
    return ConnectKeyResponse()


@router.post("/assistant/key/test")
async def probe_assistant_key(
    body: TestKeyRequest,
    user_id: str = Depends(get_current_user_id),
) -> TestKeyResponse:
    _key_probe_limiter.check(user_id)
    key = (body.api_key or "").strip() or await load_user_anthropic_key(user_id)
    if not key:
        return TestKeyResponse(ok=False, detail="No API key stored to test.")
    try:
        ok = await validate_anthropic_key(key)
    except ChatUnavailableError as exc:
        # Couldn't reach Anthropic — report as a soft failure the user can retry,
        # not a hard "rejected" verdict on the key.
        return TestKeyResponse(ok=False, detail=str(exc))
    return TestKeyResponse(ok=ok, detail=None if ok else "Anthropic rejected the key.")


@router.delete("/assistant/key", status_code=204)
async def delete_assistant_key(
    user_id: str = Depends(get_current_user_id),
) -> None:
    await delete_user_anthropic_key(user_id)
    evict_adapter_cache()
