"""Tests for text normalization and phonetic matching.

Verifies the preprocessing pipeline that handles diacritics, transliterations,
and common equivalences before fuzzy string comparison.
"""

import pytest

from src.domain.matching.text_normalization import (
    are_phonetic_matches,
    normalize_artist_name,
    normalize_for_comparison,
    phonetic_key,
    strip_diacritics,
)


class TestStripDiacritics:
    """Test Unicode NFD diacritic removal."""

    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("Björk", "Bjork"),
            ("Motörhead", "Motorhead"),
            ("Beyoncé", "Beyonce"),
            ("Sigur Rós", "Sigur Ros"),
            ("Alizée", "Alizee"),
            ("Zoë Kravitz", "Zoe Kravitz"),
            ("naïve", "naive"),
            ("café", "cafe"),
        ],
    )
    def test_strips_common_diacritics(self, input_text: str, expected: str):
        assert strip_diacritics(input_text) == expected

    def test_preserves_plain_ascii(self):
        assert strip_diacritics("Radiohead") == "Radiohead"

    def test_empty_string(self):
        assert strip_diacritics("") == ""

    def test_idempotent(self):
        text = "Björk"
        assert strip_diacritics(strip_diacritics(text)) == strip_diacritics(text)


class TestNormalizeForComparison:
    """Test the full normalization pipeline."""

    def test_strips_leading_the(self):
        assert normalize_for_comparison("The Beatles") == "beatles"

    def test_preserves_internal_the(self):
        result = normalize_for_comparison("Cage the Elephant")
        assert "the" in result

    def test_lowercases(self):
        assert normalize_for_comparison("RADIOHEAD") == "radiohead"

    def test_strips_diacritics(self):
        assert normalize_for_comparison("Björk") == "bjork"

    def test_normalizes_ampersand(self):
        assert normalize_for_comparison("Simon & Garfunkel") == "simon and garfunkel"

    def test_normalizes_plus(self):
        assert normalize_for_comparison("Simon + Garfunkel") == "simon and garfunkel"

    def test_normalizes_feat_dot(self):
        assert normalize_for_comparison("feat. Kanye") == "featuring kanye"

    def test_normalizes_feat_no_dot(self):
        assert normalize_for_comparison("feat Kanye") == "featuring kanye"

    def test_normalizes_ft_dot(self):
        assert normalize_for_comparison("ft. Kanye") == "featuring kanye"

    def test_strips_punctuation(self):
        assert normalize_for_comparison("AC/DC") == "acdc"

    def test_collapses_whitespace(self):
        assert normalize_for_comparison("  Foo   Bar  ") == "foo bar"

    def test_empty_string(self):
        assert normalize_for_comparison("") == ""

    def test_idempotent(self):
        text = "The Björk feat. Someone & Others"
        result = normalize_for_comparison(text)
        assert normalize_for_comparison(result) == result

    def test_combined_normalizations(self):
        """Multiple normalizations applied together."""
        result = normalize_for_comparison("The Motörhead ft. Björk & Friends")
        assert result == "motorhead featuring bjork and friends"


class TestNormalizeArtistName:
    """Test artist-specific normalization."""

    def test_delegates_to_normalize_for_comparison(self):
        assert normalize_artist_name("The Beatles") == normalize_for_comparison(
            "The Beatles"
        )

    def test_handles_common_artist_patterns(self):
        assert normalize_artist_name("Beyoncé") == "beyonce"


class TestPhoneticKey:
    """Test Metaphone phonetic key generation."""

    def test_same_key_for_diacritic_variants(self):
        assert phonetic_key("Björk") == phonetic_key("Bjork")

    def test_same_key_for_spelling_variants(self):
        assert phonetic_key("Smith") == phonetic_key("Smyth")

    def test_different_keys_for_different_names(self):
        assert phonetic_key("Beatles") != phonetic_key("Radiohead")

    def test_empty_string_returns_empty(self):
        assert phonetic_key("") == ""

    def test_strips_non_alpha_before_encoding(self):
        """Punctuation and numbers should not affect the phonetic key."""
        assert phonetic_key("AC/DC") == phonetic_key("ACDC")


class TestArePhoneticMatches:
    """Test phonetic matching between two strings."""

    @pytest.mark.parametrize(
        ("text_a", "text_b"),
        [
            ("Björk", "Bjork"),
            ("Smith", "Smyth"),
            ("Beyoncé", "Beyonce"),
        ],
    )
    def test_matching_pairs(self, text_a: str, text_b: str):
        assert are_phonetic_matches(text_a, text_b) is True

    @pytest.mark.parametrize(
        ("text_a", "text_b"),
        [
            ("Beatles", "Radiohead"),
            ("Mozart", "Beethoven"),
        ],
    )
    def test_non_matching_pairs(self, text_a: str, text_b: str):
        assert are_phonetic_matches(text_a, text_b) is False

    def test_empty_strings_do_not_match(self):
        assert are_phonetic_matches("", "") is False

    def test_symmetric(self):
        assert are_phonetic_matches("Björk", "Bjork") == are_phonetic_matches(
            "Bjork", "Björk"
        )
