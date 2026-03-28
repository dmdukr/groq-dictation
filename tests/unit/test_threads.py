"""Tests for src/context/threads.py — thread lifecycle and weighted scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.context.threads import (
    assign_to_thread,
    create_thread,
    expire_threads,
    find_active_thread,
    save_fingerprint,
    update_thread,
)

from tests.factories import create_cluster
from tests.factories import create_thread as factory_create_thread


def _past(minutes: int) -> str:
    """Return ISO timestamp N minutes in the past."""
    return (datetime.now(UTC) - timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


# === find_active_thread weighted scoring ===


class TestFindActiveThreadScoring:
    """Weighted scoring: same_app=2.0, cross_app=1.0, threshold=2.0."""

    def test_same_app_1_keyword_score_2(self, db_with_schema):
        """1 overlap * 2.0 (same app) = 2.0 -> matches (exact threshold)."""
        factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"])
        result = find_active_thread(db_with_schema, ["git"], "telegram.exe")
        assert result is not None

    def test_same_app_2_keywords_score_4(self, db_with_schema):
        """2 overlap * 2.0 = 4.0 -> confident match."""
        factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git", "deploy"])
        result = find_active_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        assert result is not None

    def test_cross_app_1_keyword_score_1(self, db_with_schema):
        """1 overlap * 1.0 (cross app) = 1.0 -> too weak, no match."""
        factory_create_thread(db_with_schema, app="slack.exe", keywords=["git"])
        result = find_active_thread(db_with_schema, ["git"], "telegram.exe")
        assert result is None

    def test_cross_app_2_keywords_score_2(self, db_with_schema):
        """2 overlap * 1.0 = 2.0 -> borderline match."""
        factory_create_thread(db_with_schema, app="slack.exe", keywords=["git", "deploy"])
        result = find_active_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        assert result is not None

    def test_threshold_boundary_below(self, db_with_schema):
        """Score 1.0 (cross-app, 1 keyword) -> below threshold -> None."""
        factory_create_thread(db_with_schema, app="slack.exe", keywords=["git"])
        result = find_active_thread(db_with_schema, ["git"], "telegram.exe")
        assert result is None

    def test_threshold_boundary_exact(self, db_with_schema):
        """Score exactly 2.0 (same-app, 1 keyword) -> matches."""
        factory_create_thread(db_with_schema, app="telegram.exe", keywords=["deploy"])
        result = find_active_thread(db_with_schema, ["deploy"], "telegram.exe")
        assert result is not None


# === 0-keyword behavior ===


class TestZeroKeywords:
    """Empty keywords list -> no scoring, fallback to same-app recency."""

    def test_zero_keywords_uses_same_app(self, db_with_schema):
        """Empty keywords -> finds most recent active thread in same app."""
        tid = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"])
        result = assign_to_thread(db_with_schema, [], "telegram.exe")
        assert result == tid

    def test_zero_keywords_no_active_orphan(self, db_with_schema):
        """Empty keywords + no active thread in same app -> returns None."""
        factory_create_thread(db_with_schema, app="slack.exe", keywords=["git"])
        result = assign_to_thread(db_with_schema, [], "telegram.exe")
        assert result is None

    def test_zero_keywords_never_creates(self, db_with_schema):
        """0 keywords -> never creates a thread, just returns None if no match."""
        count_before = db_with_schema.execute("SELECT COUNT(*) AS c FROM conversation_threads").fetchone()["c"]
        result = assign_to_thread(db_with_schema, [], "telegram.exe")
        count_after = db_with_schema.execute("SELECT COUNT(*) AS c FROM conversation_threads").fetchone()["c"]
        assert result is None
        assert count_after == count_before


# === Lazy expiry ===


class TestLazyExpiry:
    """Threads older than THREAD_EXPIRY_MINUTES are not found."""

    def test_expired_thread_not_found(self, db_with_schema):
        """Thread with last_message > 15 min ago -> not found."""
        old_time = _past(20)
        factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git", "deploy"], last_message=old_time)
        result = find_active_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        assert result is None

    def test_active_thread_found(self, db_with_schema):
        """Thread with last_message < 15 min ago -> found."""
        recent_time = _past(5)
        factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git", "deploy"], last_message=recent_time)
        result = find_active_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        assert result is not None


# === assign_to_thread full flow ===


class TestAssignToThread:
    """Full assignment: find/update/create lifecycle."""

    def test_assign_existing_thread(self, db_with_schema):
        """Matching keywords -> returns existing thread and updates it."""
        tid = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git", "deploy"])
        result = assign_to_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        assert result == tid
        # Verify message_count was incremented
        row = db_with_schema.execute("SELECT message_count FROM conversation_threads WHERE id = ?", [tid]).fetchone()
        assert row["message_count"] == 2

    def test_assign_new_thread(self, db_with_schema):
        """No match -> creates new thread and returns its id."""
        result = assign_to_thread(db_with_schema, ["kubernetes", "helm"], "telegram.exe")
        assert result is not None
        row = db_with_schema.execute(
            "SELECT app, message_count FROM conversation_threads WHERE id = ?", [result]
        ).fetchone()
        assert row["app"] == "telegram.exe"
        assert row["message_count"] == 1

    def test_assign_orphan_no_keywords(self, db_with_schema):
        """Empty keywords, no active thread -> None (orphan)."""
        result = assign_to_thread(db_with_schema, [], "telegram.exe")
        assert result is None


# === Fingerprint save ===


class TestSaveFingerprint:
    """Fingerprints are saved only for threads with 3+ messages."""

    def test_fingerprint_saved_3plus_messages(self, db_with_schema):
        """Expired thread with 5 messages -> fingerprint saved."""
        tid = factory_create_thread(
            db_with_schema,
            app="telegram.exe",
            keywords=["git", "deploy", "ci"],
            message_count=5,
            cluster_id=None,
        )
        fp_id = save_fingerprint(db_with_schema, tid)
        assert fp_id is not None
        # Verify fingerprint data
        fp = db_with_schema.execute(
            "SELECT app, message_count FROM conversation_fingerprints WHERE id = ?", [fp_id]
        ).fetchone()
        assert fp["app"] == "telegram.exe"
        assert fp["message_count"] == 5
        # Verify keywords copied
        kws = db_with_schema.execute(
            "SELECT keyword FROM fingerprint_keywords WHERE fingerprint_id = ?", [fp_id]
        ).fetchall()
        assert {r["keyword"] for r in kws} == {"git", "deploy", "ci"}

    def test_fingerprint_skipped_under_3(self, db_with_schema):
        """Expired thread with 2 messages -> no fingerprint."""
        tid = factory_create_thread(
            db_with_schema,
            app="telegram.exe",
            keywords=["git"],
            message_count=2,
        )
        fp_id = save_fingerprint(db_with_schema, tid)
        assert fp_id is None


# === update_thread ===


class TestUpdateThread:
    """update_thread increments count, adds keywords, updates last_app."""

    def test_update_increments_count(self, db_with_schema):
        """message_count goes from 1 to 2."""
        tid = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"])
        update_thread(db_with_schema, tid, ["git"], "telegram.exe")
        row = db_with_schema.execute("SELECT message_count FROM conversation_threads WHERE id = ?", [tid]).fetchone()
        assert row["message_count"] == 2

    def test_update_adds_new_keywords(self, db_with_schema):
        """New keywords added, existing preserved."""
        tid = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"])
        update_thread(db_with_schema, tid, ["git", "deploy", "ci"], "telegram.exe")
        kws = db_with_schema.execute("SELECT keyword FROM thread_keywords WHERE thread_id = ?", [tid]).fetchall()
        assert {r["keyword"] for r in kws} == {"git", "deploy", "ci"}

    def test_update_changes_last_app(self, db_with_schema):
        """last_app updated to current app."""
        tid = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"])
        update_thread(db_with_schema, tid, ["git"], "slack.exe")
        row = db_with_schema.execute("SELECT last_app FROM conversation_threads WHERE id = ?", [tid]).fetchone()
        assert row["last_app"] == "slack.exe"


# === Tiebreaker ===


class TestTiebreaker:
    """Tiebreaker: highest score wins; same score -> most recent last_message."""

    def test_tiebreaker_highest_score_wins(self, db_with_schema):
        """Two threads, different overlap counts -> highest score wins."""
        # Thread 1: 1 keyword overlap -> score 2.0
        factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"])
        # Thread 2: 2 keyword overlap -> score 4.0
        tid2 = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git", "deploy"])
        result = find_active_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        assert result is not None
        assert result["id"] == tid2

    def test_tiebreaker_same_score_newest(self, db_with_schema):
        """Same score -> most recent last_message wins."""
        old_time = _past(10)
        recent_time = _past(2)
        factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"], last_message=old_time)
        tid2 = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"], last_message=recent_time)
        result = find_active_thread(db_with_schema, ["git"], "telegram.exe")
        assert result is not None
        assert result["id"] == tid2


# === expire_threads ===


class TestExpireThreads:
    """expire_threads deactivates old threads, keeps recent ones."""

    def test_expire_deactivates_old(self, db_with_schema):
        """Old threads set to is_active=0."""
        old_time = _past(20)
        tid = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"], last_message=old_time)
        expired = expire_threads(db_with_schema, "telegram.exe")
        assert tid in expired
        row = db_with_schema.execute("SELECT is_active FROM conversation_threads WHERE id = ?", [tid]).fetchone()
        assert row["is_active"] == 0

    def test_expire_keeps_recent(self, db_with_schema):
        """Recent threads stay active."""
        recent_time = _past(5)
        tid = factory_create_thread(db_with_schema, app="telegram.exe", keywords=["git"], last_message=recent_time)
        expired = expire_threads(db_with_schema, "telegram.exe")
        assert tid not in expired
        row = db_with_schema.execute("SELECT is_active FROM conversation_threads WHERE id = ?", [tid]).fetchone()
        assert row["is_active"] == 1


# === create_thread (module function, not factory) ===


class TestCreateThread:
    """create_thread inserts thread and keywords properly."""

    def test_create_returns_id(self, db_with_schema):
        """create_thread returns a valid thread_id."""
        tid = create_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        assert tid is not None
        assert tid > 0

    def test_create_inserts_keywords(self, db_with_schema):
        """Keywords are inserted into thread_keywords."""
        tid = create_thread(db_with_schema, ["git", "deploy"], "telegram.exe")
        kws = db_with_schema.execute("SELECT keyword FROM thread_keywords WHERE thread_id = ?", [tid]).fetchall()
        assert {r["keyword"] for r in kws} == {"git", "deploy"}

    def test_create_with_cluster_id(self, db_with_schema):
        """cluster_id is stored when provided."""
        cid = create_cluster(db_with_schema, display_name="devops")
        tid = create_thread(db_with_schema, ["git"], "telegram.exe", cluster_id=cid)
        row = db_with_schema.execute("SELECT cluster_id FROM conversation_threads WHERE id = ?", [tid]).fetchone()
        assert row["cluster_id"] == cid

    def test_create_empty_keywords(self, db_with_schema):
        """Empty keywords list -> thread created, no keywords inserted."""
        tid = create_thread(db_with_schema, [], "telegram.exe")
        assert tid > 0
        kws = db_with_schema.execute("SELECT keyword FROM thread_keywords WHERE thread_id = ?", [tid]).fetchall()
        assert len(kws) == 0
