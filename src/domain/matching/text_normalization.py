"""Pure text normalization and phonetic matching for track identity resolution.

Preprocessing pipeline applied before fuzzy string comparison to handle
diacritics, transliterations, and common equivalences across music services.
"""

import re
import unicodedata

import jellyfish

# Common equivalences in music metadata
_EQUIVALENCES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bfeat\.?\b", re.IGNORECASE), "featuring"),
    (re.compile(r"\bft\.?\b", re.IGNORECASE), "featuring"),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"\s*\+\s*"), " and "),
]

# Leading article to strip for comparison (preserves original for display)
_LEADING_ARTICLE = re.compile(r"^the\s+", re.IGNORECASE)

# Non-alphanumeric characters to strip (keeps spaces)
_NON_ALNUM = re.compile(r"[^\w\s]", re.UNICODE)


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
    return "".join(c for c in nfd if unicodedata.category(c) != "Mn")


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


def normalize_artist_name(name: str) -> str:
    """Normalize an artist name for comparison.

    Same pipeline as normalize_for_comparison but preserves "the" prefix
    stripping and applies music-specific artist equivalences.
    """
    return normalize_for_comparison(name)


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
