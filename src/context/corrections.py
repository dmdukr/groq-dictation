"""Correction learning and LLM confidence tracking for the Context Engine.

Provides:
- User correction ingestion (rate-limited)
- Token-level diff computation and error classification
- Auto-promotion of frequent corrections to dictionary
- Per-cluster LLM confidence scoring
"""

from __future__ import annotations

import logging
import time
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# Rate limiting state
_correction_timestamps: list[float] = []
MAX_CORRECTIONS_PER_MINUTE: int = 10

# Thresholds
AUTO_PROMOTE_THRESHOLD: int = 3
MIN_LLM_SAMPLES: int = 5
HIGH_ERROR_RATE: float = 0.2
DEGRADED_CONFIDENCE: float = 0.8


def mock_encrypt(text: str) -> bytes:
    """Placeholder for DPAPI. Just encode to bytes on non-Windows."""
    return text.encode("utf-8")


def mock_decrypt(data: bytes) -> str:
    """Placeholder for DPAPI. Just decode bytes on non-Windows."""
    return data.decode("utf-8")


def rate_limit_correction() -> bool:
    """Return True if correction is allowed (< MAX_CORRECTIONS_PER_MINUTE in last 60s).

    Cleans up old timestamps. Thread-safe not required (single-threaded UI).
    """
    now = time.monotonic()
    cutoff = now - 60.0

    # Remove timestamps older than 60 seconds
    while _correction_timestamps and _correction_timestamps[0] < cutoff:
        _correction_timestamps.pop(0)

    if len(_correction_timestamps) >= MAX_CORRECTIONS_PER_MINUTE:
        logger.warning("Correction rate limit reached (%d/min)", MAX_CORRECTIONS_PER_MINUTE)
        return False

    _correction_timestamps.append(now)
    return True


def compute_token_diffs(normalized: str, corrected: str) -> list[tuple[str, str]]:
    """Extract word-level diffs between normalized and corrected text.

    Use difflib.SequenceMatcher on word lists.
    Returns list of (old_token, new_token) pairs for changed words.
    """
    old_words = normalized.split()
    new_words = corrected.split()

    diffs: list[tuple[str, str]] = []
    matcher = SequenceMatcher(None, old_words, new_words)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "replace":
            # Pair up replaced words one-to-one where possible
            old_segment = old_words[i1:i2]
            new_segment = new_words[j1:j2]
            for k in range(max(len(old_segment), len(new_segment))):
                old_tok = old_segment[k] if k < len(old_segment) else ""
                new_tok = new_segment[k] if k < len(new_segment) else ""
                if old_tok and new_tok:
                    diffs.append((old_tok, new_tok))
        elif tag == "delete":
            diffs.extend((old_tok, "") for old_tok in old_words[i1:i2])
        elif tag == "insert":
            diffs.extend(("", new_tok) for new_tok in new_words[j1:j2])

    return diffs


def classify_error(old_token: str, raw: str, normalized: str) -> str:
    """Classify error source.

    - If old_token appears in raw (STT output) but was changed by LLM -> 'llm'
    - If old_token does NOT appear in raw (STT got it wrong) -> 'stt'
    - If old_token appears in both raw and normalized with same form -> 'both'
    """
    in_raw = old_token in raw.split()
    in_normalized = old_token in normalized.split()

    if in_raw and in_normalized:
        return "both"
    if in_raw and not in_normalized:
        return "llm"
    # old_token not in raw -> STT produced something different
    return "stt"


def auto_promote_check(db: sqlite3.Connection, old_token: str, new_token: str) -> bool:
    """Check correction_counts. If count >= 3, add to dictionary as exact term.

    Returns True if promoted.
    """
    row = db.execute(
        "SELECT count FROM correction_counts WHERE old_token = ? AND new_token = ?",
        [old_token, new_token],
    ).fetchone()

    if row is None or row["count"] < AUTO_PROMOTE_THRESHOLD:
        return False

    # Check if already in dictionary
    existing = db.execute(
        "SELECT id FROM dictionary WHERE source_text = ? AND target_text = ?",
        [old_token, new_token],
    ).fetchone()

    if existing is not None:
        return False

    db.execute(
        """INSERT INTO dictionary (source_text, target_text, term_type, origin)
           VALUES (?, ?, 'exact', 'auto_promoted')""",
        [old_token, new_token],
    )
    db.commit()
    logger.info("Auto-promoted correction: %s -> %s", old_token, new_token)
    return True


def get_llm_confidence(db: sqlite3.Connection, cluster_id: int | None) -> float:
    """LLM confidence adjusted by per-cluster error rate.

    - No stats or < 5 samples -> 1.0
    - error_rate > 20% -> 0.8
    - Otherwise -> 1.0
    """
    if cluster_id is None:
        return 1.0

    row = db.execute(
        "SELECT total_llm_resolutions, llm_errors FROM cluster_llm_stats WHERE cluster_id = ?",
        [cluster_id],
    ).fetchone()

    if row is None:
        return 1.0

    total: int = row["total_llm_resolutions"]
    errors: int = row["llm_errors"]

    if total < MIN_LLM_SAMPLES:
        return 1.0

    error_rate = errors / total
    if error_rate > HIGH_ERROR_RATE:
        return DEGRADED_CONFIDENCE

    return 1.0


def record_llm_outcome(db: sqlite3.Connection, cluster_id: int, was_corrected: bool) -> None:
    """Track LLM success/failure per cluster in cluster_llm_stats.

    INSERT OR UPDATE total_llm_resolutions (+1) and llm_errors (+1 if was_corrected).
    """
    error_inc = 1 if was_corrected else 0
    db.execute(
        """INSERT INTO cluster_llm_stats (cluster_id, total_llm_resolutions, llm_errors)
           VALUES (?, 1, ?)
           ON CONFLICT(cluster_id)
           DO UPDATE SET
               total_llm_resolutions = total_llm_resolutions + 1,
               llm_errors = llm_errors + ?""",
        [cluster_id, error_inc, error_inc],
    )
    db.commit()


def learn_from_correction(
    db: sqlite3.Connection,
    raw: str,
    normalized: str,
    corrected: str,
    app: str,
    thread_id: int | None,
    cluster_id: int | None,
    encrypt_fn: Callable[[str], bytes] = mock_encrypt,
) -> bool:
    """Full correction learning flow.

    1. Rate limit check -- if blocked, return False
    2. Store correction triad (encrypted)
    3. Compute token diffs
    4. For each diff: classify error, update correction_counts, check auto-promote
    5. Return True if stored
    """
    if not rate_limit_correction():
        return False

    # Store encrypted correction triad
    raw_enc = encrypt_fn(raw)
    norm_enc = encrypt_fn(normalized)
    corr_enc = encrypt_fn(corrected)

    db.execute(
        """INSERT INTO corrections
           (raw_text_enc, normalized_text_enc, corrected_text_enc, error_source, app, thread_id, cluster_id)
           VALUES (?, ?, ?, NULL, ?, ?, ?)""",
        [raw_enc, norm_enc, corr_enc, app, thread_id, cluster_id],
    )

    # Compute and process token diffs
    diffs = compute_token_diffs(normalized, corrected)
    for old_tok, new_tok in diffs:
        if not old_tok or not new_tok:
            continue

        error_source = classify_error(old_tok, raw, normalized)

        # Update correction_counts
        db.execute(
            """INSERT INTO correction_counts (old_token, new_token, count)
               VALUES (?, ?, 1)
               ON CONFLICT(old_token, new_token)
               DO UPDATE SET count = count + 1""",
            [old_tok, new_tok],
        )

        # Update the correction row's error_source with last classification
        db.execute(
            """UPDATE corrections SET error_source = ?
               WHERE id = (SELECT MAX(id) FROM corrections)""",
            [error_source],
        )

        db.commit()

        # Check auto-promote
        auto_promote_check(db, old_tok, new_tok)

    db.commit()
    logger.debug("Learned correction: %d diffs processed", len(diffs))
    return True
