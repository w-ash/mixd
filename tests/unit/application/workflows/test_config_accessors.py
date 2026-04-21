"""Tests for typed config value accessors.

Verifies that cfg_str, cfg_int, cfg_float, cfg_bool, cfg_str_list,
and cfg_str_or_none correctly narrow JsonValue to concrete types,
including the bool-as-int guard (isinstance(True, int) is True).
"""

import math

from src.application.workflows.config_accessors import (
    cfg_bool,
    cfg_float,
    cfg_int,
    cfg_str,
    cfg_str_list,
    cfg_str_or_none,
)


class TestCfgStr:
    def test_returns_string_value(self):
        assert cfg_str({"key": "hello"}, "key") == "hello"

    def test_returns_default_for_missing_key(self):
        assert cfg_str({}, "key") == ""

    def test_returns_custom_default(self):
        assert cfg_str({}, "key", "fallback") == "fallback"

    def test_coerces_non_string(self):
        assert cfg_str({"key": 42}, "key") == "42"

    def test_returns_default_for_none(self):
        assert cfg_str({"key": None}, "key", "fallback") == "fallback"


class TestCfgStrOrNone:
    def test_returns_string_value(self):
        assert cfg_str_or_none({"key": "hello"}, "key") == "hello"

    def test_returns_none_for_missing_key(self):
        assert cfg_str_or_none({}, "key") is None

    def test_coerces_non_string(self):
        assert cfg_str_or_none({"key": 42}, "key") == "42"


class TestCfgInt:
    def test_returns_int_value(self):
        assert cfg_int({"key": 42}, "key") == 42

    def test_returns_default_for_missing_key(self):
        assert cfg_int({}, "key") is None

    def test_returns_custom_default(self):
        assert cfg_int({}, "key", 10) == 10

    def test_rounds_float(self):
        assert cfg_int({"key": 3.7}, "key") == 4

    def test_returns_default_for_string(self):
        assert cfg_int({"key": "nope"}, "key", 5) == 5

    def test_bool_true_returns_default_not_1(self):
        """bool is a subclass of int — cfg_int must guard against it."""
        assert cfg_int({"count": True}, "count") is None

    def test_bool_true_returns_explicit_default_not_1(self):
        assert cfg_int({"count": True}, "count", 0) == 0

    def test_bool_false_returns_default_not_0(self):
        assert cfg_int({"count": False}, "count", 99) == 99


class TestCfgFloat:
    def test_returns_float_value(self):
        assert cfg_float({"key": math.pi}, "key") == math.pi

    def test_returns_int_as_float(self):
        assert cfg_float({"key": 5}, "key") == 5.0

    def test_returns_default_for_missing_key(self):
        assert cfg_float({}, "key") is None

    def test_bool_true_returns_default_not_1(self):
        """bool is a subclass of int — cfg_float must guard against it."""
        assert cfg_float({"rate": True}, "rate") is None

    def test_bool_false_returns_default_not_0(self):
        assert cfg_float({"rate": False}, "rate", 1.0) == 1.0


class TestCfgBool:
    def test_returns_true(self):
        assert cfg_bool({"key": True}, "key") is True

    def test_returns_false(self):
        assert cfg_bool({"key": False}, "key") is False

    def test_returns_default_for_missing_key(self):
        assert cfg_bool({}, "key") is False

    def test_returns_default_for_non_bool(self):
        assert cfg_bool({"key": 1}, "key") is False

    def test_returns_custom_default_for_non_bool(self):
        assert cfg_bool({"key": "yes"}, "key", True) is True


class TestCfgStrList:
    def test_returns_string_list(self):
        assert cfg_str_list({"key": ["a", "b", "c"]}, "key") == ["a", "b", "c"]

    def test_returns_empty_for_missing_key(self):
        assert cfg_str_list({}, "key") == []

    def test_coerces_non_string_items(self):
        assert cfg_str_list({"key": [1, 2, 3]}, "key") == ["1", "2", "3"]

    def test_splits_comma_separated_string(self):
        """UI writes comma-separated strings; raw JSON uses lists. Both supported."""
        assert cfg_str_list({"key": "a, b, c"}, "key") == ["a", "b", "c"]

    def test_returns_single_item_list_for_plain_string(self):
        assert cfg_str_list({"key": "not a list"}, "key") == ["not a list"]

    def test_strips_empty_pieces_from_csv(self):
        assert cfg_str_list({"key": "a,, b, ,c,"}, "key") == ["a", "b", "c"]

    def test_returns_empty_for_non_sequence(self):
        assert cfg_str_list({"key": 42}, "key") == []
