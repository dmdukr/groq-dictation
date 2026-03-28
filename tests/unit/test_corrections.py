"""Tests for src/context/corrections.py — correction learning, diffs, rate limiting, LLM confidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from src.context.corrections import (
    MAX_CORRECTIONS_PER_MINUTE,
    _correction_timestamps,
    auto_promote_check,
    classify_error,
    compute_token_diffs,
    get_llm_confidence,
    learn_from_correction,
    mock_decrypt,
    mock_encrypt,
    rate_limit_correction,
    record_llm_outcome,
)

from tests.factories import create_cluster, create_correction_count

if TYPE_CHECKING:
    import sqlite3


@pytest.fixture(autouse=True)
def _clear_rate_limit() -> None:
    """Reset rate limit state before each test to avoid pollution."""
    _correction_timestamps.clear()


# =============================================================================
# Token diffs
# =============================================================================


class TestComputeTokenDiffs:
    """Tests for compute_token_diffs()."""

    def test_compute_diffs_single_change(self) -> None:
        """Single word changed returns one diff pair."""
        diffs = compute_token_diffs("замок двері", "lock двері")
        assert ("замок", "lock") in diffs
        assert len(diffs) == 1

    def test_compute_diffs_no_change(self) -> None:
        """Identical text returns empty list."""
        diffs = compute_token_diffs("hello world", "hello world")
        assert diffs == []

    def test_compute_diffs_multiple(self) -> None:
        """Multiple word changes returns multiple diff pairs."""
        diffs = compute_token_diffs("замок двері вікно", "lock door вікно")
        assert len(diffs) == 2
        old_tokens = {d[0] for d in diffs}
        new_tokens = {d[1] for d in diffs}
        assert "замок" in old_tokens
        assert "двері" in old_tokens
        assert "lock" in new_tokens
        assert "door" in new_tokens


# =============================================================================
# Error classification
# =============================================================================


class TestClassifyError:
    """Tests for classify_error()."""

    def test_classify_stt_error(self) -> None:
        """old_token NOT in raw -> 'stt'."""
        result = classify_error("замок", "замак двері", "замок двері")
        assert result == "stt"

    def test_classify_llm_error(self) -> None:
        """old_token in raw but changed by LLM -> 'llm'."""
        # STT produced "замок", LLM changed it to something else (not in normalized)
        result = classify_error("замок", "замок двері", "lock двері")
        assert result == "llm"

    def test_classify_both_error(self) -> None:
        """old_token in both raw and normalized with same form -> 'both'."""
        result = classify_error("замок", "замок двері", "замок двері")
        assert result == "both"


# =============================================================================
# Auto-promote
# =============================================================================


class TestAutoPromote:
    """Tests for auto_promote_check()."""

    def test_auto_promote_at_3(self, db_with_schema: sqlite3.Connection) -> None:
        """Count reaches 3 -> promoted to dictionary."""
        db = db_with_schema
        create_correction_count(db, "замок", "lock", count=3)

        result = auto_promote_check(db, "замок", "lock")
        assert result is True

        # Verify in dictionary
        row = db.execute("SELECT * FROM dictionary WHERE source_text = 'замок' AND target_text = 'lock'").fetchone()
        assert row is not None
        assert row["term_type"] == "exact"
        assert row["origin"] == "auto_promoted"

    def test_auto_promote_under_3(self, db_with_schema: sqlite3.Connection) -> None:
        """Count=2 -> not promoted."""
        db = db_with_schema
        create_correction_count(db, "замок", "lock", count=2)

        result = auto_promote_check(db, "замок", "lock")
        assert result is False

        row = db.execute("SELECT * FROM dictionary WHERE source_text = 'замок'").fetchone()
        assert row is None

    def test_auto_promote_no_duplicate(self, db_with_schema: sqlite3.Connection) -> None:
        """Already promoted term is not re-added."""
        db = db_with_schema
        create_correction_count(db, "замок", "lock", count=5)

        # First promote
        assert auto_promote_check(db, "замок", "lock") is True
        # Second attempt should return False (already exists)
        assert auto_promote_check(db, "замок", "lock") is False

        count = db.execute(
            "SELECT COUNT(*) FROM dictionary WHERE source_text = 'замок' AND target_text = 'lock'"
        ).fetchone()[0]
        assert count == 1


# =============================================================================
# Rate limiting
# =============================================================================


class TestRateLimiting:
    """Tests for rate_limit_correction()."""

    def test_rate_limit_allows_10(self) -> None:
        """10 corrections within a minute are all allowed."""
        results = [rate_limit_correction() for _ in range(10)]
        assert all(results)

    def test_rate_limit_blocks_11th(self) -> None:
        """11th correction is blocked."""
        for _ in range(MAX_CORRECTIONS_PER_MINUTE):
            assert rate_limit_correction() is True

        assert rate_limit_correction() is False

    def test_rate_limit_resets(self) -> None:
        """After clearing old timestamps, allows corrections again."""
        # Fill up the limit
        for _ in range(MAX_CORRECTIONS_PER_MINUTE):
            rate_limit_correction()

        # Simulate time passing: clear timestamps
        _correction_timestamps.clear()

        assert rate_limit_correction() is True


# =============================================================================
# LLM confidence
# =============================================================================


class TestLLMConfidence:
    """Tests for get_llm_confidence() and record_llm_outcome()."""

    def test_llm_confidence_default(self, db_with_schema: sqlite3.Connection) -> None:
        """No stats -> 1.0."""
        db = db_with_schema
        cid = create_cluster(db)
        assert get_llm_confidence(db, cid) == 1.0

    def test_llm_confidence_none_cluster(self, db_with_schema: sqlite3.Connection) -> None:
        """None cluster_id -> 1.0."""
        db = db_with_schema
        assert get_llm_confidence(db, None) == 1.0

    def test_llm_confidence_below_5(self, db_with_schema: sqlite3.Connection) -> None:
        """3 samples -> 1.0 (below threshold)."""
        db = db_with_schema
        cid = create_cluster(db)
        # Record 3 outcomes, all errors
        for _ in range(3):
            record_llm_outcome(db, cid, was_corrected=True)

        assert get_llm_confidence(db, cid) == 1.0

    def test_llm_confidence_high_error(self, db_with_schema: sqlite3.Connection) -> None:
        """>20% errors with 5+ samples -> 0.8."""
        db = db_with_schema
        cid = create_cluster(db)
        # 5 total, 2 errors = 40% error rate
        for _ in range(3):
            record_llm_outcome(db, cid, was_corrected=False)
        for _ in range(2):
            record_llm_outcome(db, cid, was_corrected=True)

        assert get_llm_confidence(db, cid) == 0.8

    def test_llm_confidence_low_error(self, db_with_schema: sqlite3.Connection) -> None:
        """<20% errors -> 1.0."""
        db = db_with_schema
        cid = create_cluster(db)
        # 10 total, 1 error = 10% error rate
        for _ in range(9):
            record_llm_outcome(db, cid, was_corrected=False)
        record_llm_outcome(db, cid, was_corrected=True)

        assert get_llm_confidence(db, cid) == 1.0


# =============================================================================
# Full learn_from_correction
# =============================================================================


class TestLearnFromCorrection:
    """Tests for learn_from_correction()."""

    def test_learn_stores_triad(self, db_with_schema: sqlite3.Connection) -> None:
        """Verify correction triad is stored in DB."""
        db = db_with_schema
        result = learn_from_correction(
            db,
            raw="замок двері",
            normalized="замок двері",
            corrected="lock двері",
            app="telegram.exe",
            thread_id=None,
            cluster_id=None,
        )

        assert result is True

        row = db.execute("SELECT * FROM corrections").fetchone()
        assert row is not None
        assert mock_decrypt(row["raw_text_enc"]) == "замок двері"
        assert mock_decrypt(row["normalized_text_enc"]) == "замок двері"
        assert mock_decrypt(row["corrected_text_enc"]) == "lock двері"

    def test_learn_rate_limited(self, db_with_schema: sqlite3.Connection) -> None:
        """When rate limited -> returns False, nothing saved."""
        db = db_with_schema

        # Exhaust the rate limit
        for _ in range(MAX_CORRECTIONS_PER_MINUTE):
            rate_limit_correction()

        result = learn_from_correction(
            db,
            raw="тест",
            normalized="тест",
            corrected="test",
            app="telegram.exe",
            thread_id=None,
            cluster_id=None,
        )

        assert result is False

        row = db.execute("SELECT COUNT(*) FROM corrections").fetchone()
        assert row[0] == 0

    def test_learn_updates_correction_counts(self, db_with_schema: sqlite3.Connection) -> None:
        """learn_from_correction updates correction_counts table."""
        db = db_with_schema
        learn_from_correction(
            db,
            raw="замок двері",
            normalized="замок двері",
            corrected="lock двері",
            app="telegram.exe",
            thread_id=None,
            cluster_id=None,
        )

        row = db.execute(
            "SELECT count FROM correction_counts WHERE old_token = 'замок' AND new_token = 'lock'"
        ).fetchone()
        assert row is not None
        assert row["count"] == 1


# =============================================================================
# Mock encrypt/decrypt
# =============================================================================


class TestMockCrypto:
    """Tests for mock_encrypt and mock_decrypt."""

    def test_roundtrip(self) -> None:
        """Encrypt then decrypt returns original text."""
        original = "привіт світ"
        encrypted = mock_encrypt(original)
        assert isinstance(encrypted, bytes)
        decrypted = mock_decrypt(encrypted)
        assert decrypted == original
