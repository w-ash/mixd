"""Pure text normalization and phonetic matching for track identity resolution.

Preprocessing pipeline applied before fuzzy string comparison to handle
diacritics, transliterations, and common equivalences across music services.
"""

import functools
import re
from typing import Final
import unicodedata

import jellyfish

# Normalization is called once per title and per artist name on every
# comparison, and the same strings recur constantly across a library — a
# hit is ~60x cheaper than the seven-pass pipeline. Bounded because the API
# process is long-lived: 32k entries covers the distinct titles plus artist
# names of a large library with headroom, while an unbounded cache would be
# a slow leak.
_NORMALIZATION_CACHE_SIZE: Final = 32_768

# Common equivalences in music metadata
_EQUIVALENCES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfeat\.?\b", re.IGNORECASE), "featuring"),
    (re.compile(r"\bft\.?\b", re.IGNORECASE), "featuring"),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"\s*\+\s*"), " and "),
]

# The same equivalences as a public, hashable view: pattern source, flags, and
# replacement. They rewrite text before every comparison without appearing
# anywhere in ``MatchingConfig``, so ``src.domain.matching.version`` has to hash
# them — and reaching into a module private to do that would make the hash's
# inputs a matter of import etiquette. Derived from ``_EQUIVALENCES`` rather
# than restated so the two cannot drift: adding a rule above is what makes it
# hashed, not an edit in another file. Order is preserved, never sorted — the
# rules apply in sequence and reordering them is itself a behavior change.
EQUIVALENCE_RULES: Final[tuple[tuple[str, int, str], ...]] = tuple(
    (pattern.pattern, pattern.flags, replacement)
    for pattern, replacement in _EQUIVALENCES
)

# Leading article to strip for comparison (preserves original for display)
_LEADING_ARTICLE = re.compile(r"^the\s+", re.IGNORECASE)

# Non-alphanumeric characters to strip (keeps spaces)
_NON_ALNUM = re.compile(r"[^\w\s]", re.UNICODE)


@functools.lru_cache(maxsize=_NORMALIZATION_CACHE_SIZE)
def strip_diacritics(text: str) -> str:
    """Remove diacritical marks from text via Unicode NFD decomposition.

    Decomposes characters like 'é' into 'e' + combining accent, then strips
    the combining characters. Works for Latin, Cyrillic, and Greek scripts.

    >>> strip_diacritics("Björk")
    'Bjork'
    >>> strip_diacritics("Motörhead")
    'Motorhead'
    """
    nfd = unicodedata.normalize("NFD", text)
    # The ``Mn`` category test is deliberate, not a slow stand-in for a regex
    # character class: 753 of the 865 ``Mn`` codepoints below U+2000 sit
    # outside the Latin combining block (U+0300-U+036F), so a class over that
    # block silently keeps Arabic harakat, Hebrew niqqud, and Tibetan marks.
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


@functools.lru_cache(maxsize=_NORMALIZATION_CACHE_SIZE)
def normalize_for_comparison(text: str) -> str:
    """Full normalization pipeline for fuzzy string comparison.

    Steps: lowercase → strip diacritics → apply equivalences → strip
    non-alphanumeric → collapse whitespace.

    >>> normalize_for_comparison("The Beatles")
    'beatles'
    >>> normalize_for_comparison("AC/DC")
    'acdc'
    >>> normalize_for_comparison("feat. Kanye West")
    'featuring kanye west'
    """
    result = text.lower()
    result = strip_diacritics(result)

    for pattern, replacement in _EQUIVALENCES:
        result = pattern.sub(replacement, result)

    result = _LEADING_ARTICLE.sub("", result)
    result = _NON_ALNUM.sub("", result)
    return " ".join(result.split())


def phonetic_key(text: str) -> str:
    """Generate a Metaphone phonetic key for text.

    Normalizes text first (strip diacritics, lowercase) before computing
    the phonetic key to handle transliteration variants.

    >>> phonetic_key("Björk")
    'BJRK'
    >>> phonetic_key("Bjork")
    'BJRK'
    """
    normalized = strip_diacritics(text.lower())
    # Remove non-alpha characters before phonetic encoding
    alpha_only = re.sub(r"[^a-z\s]", "", normalized)
    return jellyfish.metaphone(alpha_only)


def strip_parentheticals(text: str) -> str:
    """Remove parenthetical/bracket suffixes and dash-separated qualifiers.

    Strips: (feat. X), (Remix), (Remastered 2024), (Live), [Deluxe],
    - Radio Edit, - Bonus Track, etc.
    """
    # Remove (...) and [...] blocks
    result = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", text)
    # Remove dash-separated qualifiers: " - Radio Edit", " - Remastered"
    result = re.sub(
        r"\s*-\s*(remix|remaster(?:ed)?|live|radio edit|extended|instrumental|bonus track|deluxe|single version|album version)\b.*",
        "",
        result,
        flags=re.IGNORECASE,
    )
    return result.strip()


def are_phonetic_matches(text_a: str, text_b: str) -> bool:
    """Check if two strings are phonetic matches via Metaphone.

    Returns True if both strings produce the same non-empty phonetic key.

    >>> are_phonetic_matches("Björk", "Bjork")
    True
    >>> are_phonetic_matches("Smith", "Smyth")
    True
    """
    key_a = phonetic_key(text_a)
    key_b = phonetic_key(text_b)
    return bool(key_a and key_b and key_a == key_b)
