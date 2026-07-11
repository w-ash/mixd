"""Composable chat voice registry.

Each voice is a static, developer-authored dict with three keys:
- identity: str — who the assistant is
- voice_examples: list[str] — example responses calibrating tone
- rules: list[str] — behavioral constraints

Voice content lands in the system prompt's authoritative instruction space.
No runtime user data, no DB-stored text — this is the security invariant.
"""

from src.application.chat.voices.registry import VOICE_NAMES, VoiceDict, get_voice

__all__ = ["VOICE_NAMES", "VoiceDict", "get_voice"]
