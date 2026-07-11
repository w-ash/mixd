"""Shared type for voice definitions."""

from typing import TypedDict


class VoiceDict(TypedDict):
    identity: str
    voice_examples: list[str]
    rules: list[str]
