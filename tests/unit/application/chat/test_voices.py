"""Tests for the chat voice registry."""

import pytest

from src.application.chat.voices import VOICE_NAMES, get_voice


def test_get_voice_unknown_name_raises() -> None:
    with pytest.raises(ValueError, match="Unknown voice"):
        get_voice("nope")


def test_all_voice_names_resolve() -> None:
    for name in VOICE_NAMES:
        voice = get_voice(name)
        assert voice["identity"]
