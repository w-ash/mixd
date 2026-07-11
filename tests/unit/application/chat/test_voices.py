"""Tests for the chat voice registry."""

import pytest

from src.application.chat.voices import VOICE_NAMES, get_voice


def test_get_voice_returns_default_voice() -> None:
    voice = get_voice("default")

    assert set(voice.keys()) == {"identity", "voice_examples", "rules"}
    assert voice["identity"]
    assert isinstance(voice["voice_examples"], list)
    assert isinstance(voice["rules"], list)


def test_get_voice_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="Unknown voice"):
        get_voice("nope")


def test_all_voice_names_resolve() -> None:
    for name in VOICE_NAMES:
        voice = get_voice(name)
        assert voice["identity"]
