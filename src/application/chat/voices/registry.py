"""Voice lookup and validation."""

from src.application.chat.voices._types import VoiceDict
from src.application.chat.voices.default import VOICE as _DEFAULT

VOICE_NAMES: frozenset[str] = frozenset({"default"})

_VOICES: dict[str, VoiceDict] = {
    "default": _DEFAULT,
}


def get_voice(name: str) -> VoiceDict:
    """Return the voice dict for *name*, or raise ValueError."""
    try:
        return _VOICES[name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown voice: {name!r}. Valid: {sorted(VOICE_NAMES)}"
        ) from exc
