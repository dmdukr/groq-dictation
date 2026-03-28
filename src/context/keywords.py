"""Keyword extraction for Context Engine.

Provides bilingual (Ukrainian + English) keyword extraction with
lemmatization, stop-word filtering, and bigram generation.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymorphy3

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy singleton for pymorphy3.MorphAnalyzer (thread-safe)
# ---------------------------------------------------------------------------

_morph: pymorphy3.MorphAnalyzer | None = None
_morph_lock = threading.Lock()


def get_morph() -> pymorphy3.MorphAnalyzer:
    """Return a lazily-initialized, thread-safe pymorphy3 MorphAnalyzer for Ukrainian."""
    global _morph  # noqa: PLW0603
    if _morph is None:
        with _morph_lock:
            if _morph is None:
                import pymorphy3 as _pymorphy3  # noqa: PLC0415

                _morph = _pymorphy3.MorphAnalyzer(lang="uk")
                logger.info("[keywords] pymorphy3 MorphAnalyzer initialized (first use)")
    return _morph


# ---------------------------------------------------------------------------
# Stop words
# ---------------------------------------------------------------------------

# Ukrainian stop words (pronouns, conjunctions, particles, prepositions, fillers)
STOP_WORDS_UK: frozenset[str] = frozenset(
    {
        # pronouns: ya, ty, vin, vona, my, vy, vony
        "\u044f",
        "\u0442\u0438",
        "\u0432\u0456\u043d",
        "\u0432\u043e\u043d\u0430",
        "\u043c\u0438",
        "\u0432\u0438",
        "\u0432\u043e\u043d\u0438",
        # demonstratives: tse, toy, ta
        "\u0446\u0435",
        "\u0442\u043e\u0439",
        "\u0442\u0430",
        # conjunctions/particles: i, y, a, ale, abo, chy, shcho, yak, de, koly, bo, khocha
        "\u0456",
        "\u0439",
        "\u0430",
        "\u0430\u043b\u0435",
        "\u0430\u0431\u043e",
        "\u0447\u0438",
        "\u0449\u043e",
        "\u044f\u043a",
        "\u0434\u0435",
        "\u043a\u043e\u043b\u0438",
        "\u0431\u043e",
        "\u0445\u043e\u0447\u0430",
        # discourse: ni, tak, nu, ok, ladno, davay
        "\u043d\u0456",
        "\u0442\u0430\u043a",
        "\u043d\u0443",
        "\u043e\u043a",
        "\u043b\u0430\u0434\u043d\u043e",
        "\u0434\u0430\u0432\u0430\u0439",
        # greetings/polite: pryvit, dyakuyu, bud, laska
        "\u043f\u0440\u0438\u0432\u0456\u0442",
        "\u0434\u044f\u043a\u0443\u044e",
        "\u0431\u0443\u0434\u044c",
        "\u043b\u0430\u0441\u043a\u0430",
        # filler: prosto, mozhe, treba, mozhna, potribno, dobre, harazd
        "\u043f\u0440\u043e\u0441\u0442\u043e",
        "\u043c\u043e\u0436\u0435",
        "\u0442\u0440\u0435\u0431\u0430",
        "\u043c\u043e\u0436\u043d\u0430",
        "\u043f\u043e\u0442\u0440\u0456\u0431\u043d\u043e",
        "\u0434\u043e\u0431\u0440\u0435",
        "\u0433\u0430\u0440\u0430\u0437\u0434",
        # prepositions: v, na, z, za, do, vid, po, dlya, pid, nad, cherez, mizh,
        # bilya, kolo, pislya, pered, bez, pro, u, iz, zi
        "\u0432",
        "\u043d\u0430",
        "\u0437",
        "\u0437\u0430",
        "\u0434\u043e",
        "\u0432\u0456\u0434",
        "\u043f\u043e",
        "\u0434\u043b\u044f",
        "\u043f\u0456\u0434",
        "\u043d\u0430\u0434",
        "\u0447\u0435\u0440\u0435\u0437",
        "\u043c\u0456\u0436",
        "\u0431\u0456\u043b\u044f",
        "\u043a\u043e\u043b\u043e",
        "\u043f\u0456\u0441\u043b\u044f",
        "\u043f\u0435\u0440\u0435\u0434",
        "\u0431\u0435\u0437",
        "\u043f\u0440\u043e",
        "\u0443",
        "\u0456\u0437",
        "\u0437\u0456",
    }
)

STOP_WORDS_EN: frozenset[str] = frozenset(
    {
        # articles / be / auxiliaries
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        # modals
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "must",
        # prepositions
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "out",
        "off",
        "over",
        "under",
        # adverbs / conjunctions
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        # determiners / quantifiers
        "all",
        "both",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        # conjunctions
        "and",
        "but",
        "or",
        "if",
        # pronouns
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "i",
        "me",
        "my",
        "we",
        "us",
        "our",
        "you",
        "your",
        "he",
        "him",
        "his",
        "she",
        "her",
        "they",
        "them",
        "their",
        "who",
        "which",
        "what",
        # fillers
        "just",
        "about",
        "up",
        "also",
        "well",
        "really",
        "quite",
        "pretty",
        "much",
        "even",
        "still",
        "already",
        "yet",
    }
)

_ALL_STOP_WORDS: frozenset[str] = STOP_WORDS_UK | STOP_WORDS_EN

# ---------------------------------------------------------------------------
# Important 2-letter abbreviations to preserve
# ---------------------------------------------------------------------------

IMPORTANT_SHORT: frozenset[str] = frozenset(
    {
        "pr",
        "db",
        "ci",
        "cd",
        "ui",
        "ux",
        "ai",
        "ml",
        "qa",
        "hr",
        "os",
        "ip",
        "id",
        "js",
        "ts",
        "go",
        # Ukrainian: tz, bd, zp
        "\u0442\u0437",
        "\u0431\u0434",
        "\u0437\u043f",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Cyrillic range boundaries for Ukrainian detection
_CYRILLIC_LOWER_A = "\u0430"
_CYRILLIC_LOWER_YA = "\u044f"
_CYRILLIC_EXTRA = "\u0456\u0457\u0454\u0491"

# Regex: Latin lowercase + Cyrillic Ukrainian lowercase, 2+ chars
_TOKEN_RE = re.compile(r"[a-z\u0430-\u044f\u0456\u0457\u0454\u0491]{2,}")


def _is_cyrillic(word: str) -> bool:
    """Return True if *word* contains Ukrainian Cyrillic characters."""
    return any(_CYRILLIC_LOWER_A <= c <= _CYRILLIC_LOWER_YA or c in _CYRILLIC_EXTRA for c in word)


def lemmatize(word: str) -> str:
    """Lemmatize a single word.

    Ukrainian (Cyrillic) words are lemmatized via pymorphy3; Latin words
    are returned unchanged.
    """
    if _is_cyrillic(word):
        parsed = get_morph().parse(word)
        if parsed:
            return str(parsed[0].normal_form)
    return word


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------


def extract_keywords(text: str, max_keywords: int = 12) -> list[str]:
    """Extract up to *max_keywords* keywords (unigrams + bigrams) from *text*.

    Algorithm:
    1. Tokenize into lowercase words (>= 2 chars, Latin + Ukrainian).
    2. Keep IMPORTANT_SHORT items and words >= 3 chars not in stop words.
    3. Lemmatize Ukrainian tokens.
    4. Deduplicate (preserve order).
    5. Build bigrams from consecutive filtered tokens.
    6. Combine unigrams + bigrams, cap at *max_keywords*.
    """
    if not text:
        return []

    logger.debug("[keywords] extract_keywords: input text=%d chars", len(text))
    tokens: list[str] = _TOKEN_RE.findall(text.lower())

    # Step 2+3: filter and lemmatize
    filtered: list[str] = []
    for tok in tokens:
        if tok in IMPORTANT_SHORT:
            filtered.append(tok)
        elif len(tok) >= 3 and tok not in _ALL_STOP_WORDS:  # noqa: PLR2004
            filtered.append(lemmatize(tok))
        # else: skip (short non-important or stop word)

    # Step 4: deduplicate preserving order (for unigrams)
    seen: set[str] = set()
    unigrams: list[str] = []
    for w in filtered:
        if w not in seen:
            seen.add(w)
            unigrams.append(w)

    # Step 5: bigrams from consecutive *filtered* tokens (skip same-word pairs)
    bigrams: list[str] = []
    bigram_seen: set[str] = set()
    for i in range(len(filtered) - 1):
        if filtered[i] == filtered[i + 1]:
            continue
        bg = f"{filtered[i]} {filtered[i + 1]}"
        if bg not in bigram_seen:
            bigram_seen.add(bg)
            bigrams.append(bg)

    # Step 6: combine and cap
    combined = unigrams + bigrams
    result = combined[:max_keywords]
    logger.debug(
        "[keywords] extract_keywords: tokens_after_filter=%d, final_keywords=%d",
        len(filtered),
        len(result),
    )
    return result
