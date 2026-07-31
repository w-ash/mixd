"""Tests for text normalization and phonetic matching.

Verifies the preprocessing pipeline that handles diacritics, transliterations,
and common equivalences before fuzzy string comparison.
"""

import unicodedata

import pytest

from src.domain.matching.text_normalization import (
    are_phonetic_matches,
    normalize_for_comparison,
    phonetic_key,
    strip_diacritics,
    strip_parentheticals,
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


class TestNormalizeNonLatinScripts:
    """Regression tests pinning current behavior on non-Latin inputs.

    These tests document what ``normalize_for_comparison`` does today for
    Japanese, Chinese, Arabic, Cyrillic, Greek, and Hebrew titles. They are
    *baseline tests, not correctness tests* — the goal is to catch any
    accidental change to non-Latin handling, not to assert the current
    behavior is semantically right for every script. Some pinned behaviors
    (e.g. dakuten stripping in Japanese kana) are pre-existing artifacts of
    NFD + category-Mn filtering and would benefit from a separate product
    review of search semantics for non-Latin scripts.
    """

    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            # Japanese: kana with dakuten/handakuten lose the marks via NFD+Mn-strip.
            # が (ga) → か (ka), ガ (Ga) → カ (Ka), ボ (bo) → ホ (ho).
            ("がガ", "かカ"),
            ("ロボット", "ロホット"),
            # Kanji and unmarked kana pass through unchanged.
            ("夜に駆ける", "夜に駆ける"),
            # Chinese: Simplified and Traditional CJK pass through unchanged
            # (no Mn-category combining marks).
            ("爱情转移", "爱情转移"),
            ("愛情轉移", "愛情轉移"),
            # Arabic and Hebrew: pass through unchanged in this corpus.
            ("البيتلز", "البيتلز"),
            ("שלום", "שלום"),
            # Cyrillic: lowercased; combining breve on й is stripped (й → и).
            ("Раммштайн", "раммштаин"),
            # Greek: lowercased and diacritic stripped.
            ("Ωραίο", "ωραιο"),
            # Pinyin with diacritics: lowercased and stripped to plain Latin.
            ("Wǒ Ài Nǐ", "wo ai ni"),
            # Mixed Latin + CJK preserves the CJK as-is, normalizes the Latin.
            ("YOASOBI - 夜に駆ける", "yoasobi 夜に駆ける"),
            ("Beyond - 海闊天空", "beyond 海闊天空"),
        ],
    )
    def test_pinned_behavior(self, input_text: str, expected: str):
        assert normalize_for_comparison(input_text) == expected

    def test_strip_parentheticals_preserves_cjk(self):
        """Parenthetical-stripping then normalizing preserves the CJK head."""
        assert (
            normalize_for_comparison(strip_parentheticals("夜に駆ける (Remix)"))
            == "夜に駆ける"
        )
        assert (
            normalize_for_comparison(strip_parentheticals("光辉岁月 (Glory Days)"))
            == "光辉岁月"
        )


class TestCombiningMarkSemantics:
    """Lock diacritic stripping to the Unicode ``Mn`` general category.

    The implementation filters on ``unicodedata.category(c) != "Mn"``, which
    covers combining marks in *every* script — Hebrew niqqud and Arabic
    harakat included, not just the Latin block. A regex character class is
    the tempting "faster" rewrite and is wrong: 753 of the 865 ``Mn``
    codepoints below U+2000 fall outside U+0300-U+036F. These tests fail if
    that substitution is ever made.
    """

    @pytest.mark.parametrize(
        ("input_text", "stripped", "normalized"),
        [
            # Latin
            ("Björk", "Bjork", "bjork"),
            ("Sigur Rós", "Sigur Ros", "sigur ros"),
            # Cyrillic — combining breve on й (U+0306)
            ("Мумий Тролль", "Мумии Тролль", "мумии тролль"),
            # Greek — tonos on ί and ά (U+0301)
            (
                "Ελευθερία Αρβανιτάκη",
                "Ελευθερια Αρβανιτακη",
                "ελευθερια αρβανιτακη",
            ),
            # Hebrew with niqqud (U+05B0-U+05C2) — outside any Latin range
            ("שָׁלוֹם", "שלום", "שלום"),
            ("אֱלֹהִים", "אלהים", "אלהים"),
            # Arabic with harakat (U+064B-U+0652) — likewise
            ("الْعَرَبِيَّة", "العربية", "العربية"),
            ("مُحَمَّد", "محمد", "محمد"),
        ],
    )
    def test_strips_combining_marks_across_scripts(
        self, input_text: str, stripped: str, normalized: str
    ):
        assert strip_diacritics(input_text) == stripped
        assert normalize_for_comparison(input_text) == normalized

    def test_every_mn_codepoint_is_removed(self):
        """No standalone ``Mn`` codepoint survives, across all scripts."""
        survivors = [
            f"U+{cp:04X}"
            for cp in range(0x2000)
            if unicodedata.category(chr(cp)) == "Mn" and strip_diacritics(chr(cp))
        ]
        assert survivors == []

    def test_non_mn_codepoints_are_preserved(self):
        """Only combining marks are dropped — nothing else is."""
        casualties = [
            f"U+{cp:04X}"
            for cp in range(0x2000)
            if unicodedata.category(chr(cp)) != "Mn"
            # NFD-stable codepoints only: ones that decompose (é → e + ́ )
            # legitimately lose their mark.
            and unicodedata.normalize("NFD", chr(cp)) == chr(cp)
            and strip_diacritics(chr(cp)) != chr(cp)
        ]
        assert casualties == []


class TestNormalizationCaching:
    """Both functions are memoized; results must be indistinguishable."""

    def test_repeat_calls_hit_the_cache(self):
        normalize_for_comparison.cache_clear()
        text = "The Motörhead ft. Björk & Friends"

        first = normalize_for_comparison(text)
        before = normalize_for_comparison.cache_info().hits
        second = normalize_for_comparison(text)

        assert second == first
        assert normalize_for_comparison.cache_info().hits == before + 1

    def test_strip_diacritics_repeat_calls_hit_the_cache(self):
        strip_diacritics.cache_clear()
        text = "Ελευθερία Αρβανιτάκη"

        first = strip_diacritics(text)
        before = strip_diacritics.cache_info().hits
        second = strip_diacritics(text)

        assert second == first
        assert strip_diacritics.cache_info().hits == before + 1

    def test_distinct_inputs_do_not_collide(self):
        normalize_for_comparison.cache_clear()
        assert normalize_for_comparison("The Beatles") == "beatles"
        assert normalize_for_comparison("The Beatles ") == "beatles"
        assert normalize_for_comparison("Beatles") == "beatles"
        assert normalize_for_comparison("AC/DC") == "acdc"
        assert normalize_for_comparison.cache_info().currsize == 4

    def test_cache_is_bounded(self):
        """A long-lived process must not accumulate entries without limit."""
        assert normalize_for_comparison.cache_info().maxsize is not None
        assert strip_diacritics.cache_info().maxsize is not None


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


class TestStripParentheticals:
    """Test removal of parenthetical/bracket suffixes and dash-separated qualifiers."""

    @pytest.mark.parametrize(
        ("input_text", "expected"),
        [
            ("Song (feat. Artist)", "Song"),
            ("Song (Remix)", "Song"),
            ("Song (Remastered 2024)", "Song"),
            ("Song (Live)", "Song"),
            ("Song [Deluxe]", "Song"),
            ("Song - Radio Edit", "Song"),
            ("Song - Remastered", "Song"),
            ("Song - Extended", "Song"),
            ("Song (feat. X) (Remix)", "Song"),
            ("Song With No Parens", "Song With No Parens"),
            ("", ""),
            ("Ultraviolet (feat. Neon Priest)", "Ultraviolet"),
        ],
    )
    def test_strips_parentheticals(self, input_text: str, expected: str):
        assert strip_parentheticals(input_text) == expected
