"""Tests for src/context/cooccurrence.py — co-occurrence graph operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from src.context.cooccurrence import (
    emergency_prune,
    prune_cooccurrence,
    query_cooccurrence,
    should_update_cooccurrence,
    update_cooccurrence,
)

from tests.factories import create_cluster, create_cooccurrence

if TYPE_CHECKING:
    import sqlite3

    from tests.conftest import Timer

# =============================================================================
# Helpers
# =============================================================================


def _get_row(db: sqlite3.Connection, term_a: str, term_b: str, cluster_id: int) -> sqlite3.Row | None:
    """Fetch a single co-occurrence row by canonical key."""
    a, b = sorted([term_a, term_b])
    return db.execute(
        "SELECT * FROM term_cooccurrence WHERE term_a=? AND term_b=? AND cluster_id=?",
        [a, b, cluster_id],
    ).fetchone()


def _days_ago(days: int) -> str:
    """Return ISO timestamp for N days ago."""
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_ahead(days: int) -> str:
    """Return ISO timestamp for N days in the future."""
    return (datetime.now(UTC) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count_rows(db: sqlite3.Connection) -> int:
    """Count total rows in term_cooccurrence."""
    row = db.execute("SELECT COUNT(*) AS cnt FROM term_cooccurrence").fetchone()
    assert row is not None
    return row["cnt"]


# =============================================================================
# Canonical ordering
# =============================================================================


class TestCanonicalOrder:
    def test_canonical_order_a_before_b(self, db_with_schema: sqlite3.Connection):
        """update("zamok", "auth") stores term_a="auth", term_b="zamok"."""
        cid = create_cluster(db_with_schema)
        update_cooccurrence(db_with_schema, ["zamok", "auth"], cid)
        row = _get_row(db_with_schema, "auth", "zamok", cid)
        assert row is not None
        assert row["term_a"] == "auth"
        assert row["term_b"] == "zamok"

    def test_canonical_order_same_result(self, db_with_schema: sqlite3.Connection):
        """update("auth","zamok") and update("zamok","auth") produce same row."""
        cid = create_cluster(db_with_schema)
        update_cooccurrence(db_with_schema, ["auth", "zamok"], cid)
        update_cooccurrence(db_with_schema, ["zamok", "auth"], cid)
        row = _get_row(db_with_schema, "auth", "zamok", cid)
        assert row is not None
        assert row["weight"] == 2

    def test_canonical_order_cyrillic(self, db_with_schema: sqlite3.Connection):
        """Cyrillic sorts after Latin."""
        cid = create_cluster(db_with_schema)
        update_cooccurrence(db_with_schema, ["\u0437\u0430\u043c\u043e\u043a", "auth"], cid)
        row = _get_row(db_with_schema, "auth", "\u0437\u0430\u043c\u043e\u043a", cid)
        assert row is not None
        assert row["term_a"] == "auth"
        assert row["term_b"] == "\u0437\u0430\u043c\u043e\u043a"


# =============================================================================
# UPSERT
# =============================================================================


class TestUpsert:
    def test_upsert_new_pair(self, db_with_schema: sqlite3.Connection):
        """First insert creates weight=1."""
        cid = create_cluster(db_with_schema)
        update_cooccurrence(db_with_schema, ["deploy", "git"], cid)
        row = _get_row(db_with_schema, "deploy", "git", cid)
        assert row is not None
        assert row["weight"] == 1

    def test_upsert_increment_weight(self, db_with_schema: sqlite3.Connection):
        """Second update for same keywords increments weight."""
        cid = create_cluster(db_with_schema)
        update_cooccurrence(db_with_schema, ["deploy", "git"], cid)
        update_cooccurrence(db_with_schema, ["deploy", "git"], cid)
        row = _get_row(db_with_schema, "deploy", "git", cid)
        assert row is not None
        assert row["weight"] == 2

    def test_upsert_updates_last_used(self, db_with_schema: sqlite3.Connection):
        """last_used timestamp updates on increment."""
        cid = create_cluster(db_with_schema)
        # Insert with an old date via factory
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=1, last_used=_days_ago(30))
        old_row = _get_row(db_with_schema, "deploy", "git", cid)
        assert old_row is not None
        old_last_used = old_row["last_used"]

        # Update via the function under test
        update_cooccurrence(db_with_schema, ["deploy", "git"], cid)
        new_row = _get_row(db_with_schema, "deploy", "git", cid)
        assert new_row is not None
        assert new_row["last_used"] > old_last_used

    def test_upsert_different_clusters(self, db_with_schema: sqlite3.Connection):
        """Same pair, different clusters = separate rows."""
        cid1 = create_cluster(db_with_schema)
        cid2 = create_cluster(db_with_schema)
        update_cooccurrence(db_with_schema, ["deploy", "git"], cid1)
        update_cooccurrence(db_with_schema, ["deploy", "git"], cid2)
        row1 = _get_row(db_with_schema, "deploy", "git", cid1)
        row2 = _get_row(db_with_schema, "deploy", "git", cid2)
        assert row1 is not None
        assert row2 is not None
        assert row1["cluster_id"] != row2["cluster_id"]


# =============================================================================
# Temporal decay
# =============================================================================


class TestTemporalDecay:
    def test_decay_recent_full_weight(self, db_with_schema: sqlite3.Connection):
        """last_used=today -> decay factor ~1.0 (effective_weight close to weight)."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "auth", "zamok", cluster_id=cid, weight=10)
        rows = query_cooccurrence(db_with_schema, "auth", ["zamok"])
        assert len(rows) >= 1
        ew = rows[0]["effective_weight"]
        # Fresh entry: effective_weight should be close to weight (10.0)
        assert ew >= 9.0

    def test_decay_30_days_reduced(self, db_with_schema: sqlite3.Connection):
        """last_used=30d ago -> factor ~1/30."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "auth", "zamok", cluster_id=cid, weight=30, last_used=_days_ago(30))
        rows = query_cooccurrence(db_with_schema, "auth", ["zamok"])
        assert len(rows) >= 1
        ew = rows[0]["effective_weight"]
        # weight=30, days=30 -> effective_weight ~ 30/30 = 1.0
        assert 0.5 <= ew <= 2.0

    def test_decay_max_guard_clock_skew(self, db_with_schema: sqlite3.Connection):
        """last_used=tomorrow -> treated as fresh (max guard prevents negative days)."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "auth", "zamok", cluster_id=cid, weight=10, last_used=_days_ahead(1))
        rows = query_cooccurrence(db_with_schema, "auth", ["zamok"])
        assert len(rows) >= 1
        ew = rows[0]["effective_weight"]
        # Future date -> max(days, 0) then max(days, 1) -> divisor=1
        assert ew >= 9.0


# =============================================================================
# Batch insert
# =============================================================================


class TestBatchInsert:
    def test_batch_insert_all_pairs(self, db_with_schema: sqlite3.Connection):
        """4 keywords -> 6 pairs inserted."""
        cid = create_cluster(db_with_schema)
        update_cooccurrence(db_with_schema, ["a", "b", "c", "d"], cid)
        assert _count_rows(db_with_schema) == 6

    def test_batch_insert_performance(self, db_with_schema: sqlite3.Connection, timer: Timer):
        """8 keywords -> completes in <5ms."""
        cid = create_cluster(db_with_schema)
        keywords = [f"term_{i}" for i in range(8)]
        with timer("batch_insert"):
            update_cooccurrence(db_with_schema, keywords, cid)
        timer.assert_under_ms("batch_insert", 5.0)
        # 8 choose 2 = 28
        assert _count_rows(db_with_schema) == 28


# =============================================================================
# Both-direction query
# =============================================================================


class TestBothDirectionQuery:
    def test_query_finds_term_as_a(self, db_with_schema: sqlite3.Connection):
        """query("auth", ["zamok"]) finds rows where term_a="auth"."""
        cid = create_cluster(db_with_schema)
        # Canonical: auth < zamok, so term_a="auth"
        create_cooccurrence(db_with_schema, "auth", "zamok", cluster_id=cid, weight=5)
        rows = query_cooccurrence(db_with_schema, "auth", ["zamok"])
        assert len(rows) >= 1
        assert rows[0]["cluster_id"] == cid

    def test_query_finds_term_as_b(self, db_with_schema: sqlite3.Connection):
        """query("zamok", ["auth"]) finds rows where term_b="zamok"."""
        cid = create_cluster(db_with_schema)
        # Canonical: auth < zamok, so term_b="zamok"
        create_cooccurrence(db_with_schema, "auth", "zamok", cluster_id=cid, weight=5)
        rows = query_cooccurrence(db_with_schema, "zamok", ["auth"])
        assert len(rows) >= 1
        assert rows[0]["cluster_id"] == cid

    def test_query_with_cluster_grouping(self, db_with_schema: sqlite3.Connection):
        """Results ordered by effective_weight DESC."""
        cid1 = create_cluster(db_with_schema)
        cid2 = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "auth", "zamok", cluster_id=cid1, weight=2)
        create_cooccurrence(db_with_schema, "auth", "zamok", cluster_id=cid2, weight=10)
        rows = query_cooccurrence(db_with_schema, "auth", ["zamok"])
        assert len(rows) == 2
        assert rows[0]["effective_weight"] >= rows[1]["effective_weight"]


# =============================================================================
# Pruning
# =============================================================================


class TestPruning:
    def test_prune_removes_old_weak_edges(self, db_with_schema: sqlite3.Connection):
        """weight=1, 100d old -> deleted."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "old", "weak", cluster_id=cid, weight=1, last_used=_days_ago(100))
        deleted = prune_cooccurrence(db_with_schema)
        assert deleted == 1
        assert _count_rows(db_with_schema) == 0

    def test_prune_keeps_recent_weak_edges(self, db_with_schema: sqlite3.Connection):
        """weight=1, 30d old -> kept."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "recent", "weak", cluster_id=cid, weight=1, last_used=_days_ago(30))
        deleted = prune_cooccurrence(db_with_schema)
        assert deleted == 0
        assert _count_rows(db_with_schema) == 1

    def test_prune_keeps_old_strong_edges(self, db_with_schema: sqlite3.Connection):
        """weight=5, 100d old -> kept."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "old", "strong", cluster_id=cid, weight=5, last_used=_days_ago(100))
        deleted = prune_cooccurrence(db_with_schema)
        assert deleted == 0
        assert _count_rows(db_with_schema) == 1

    def test_prune_returns_deleted_count(self, db_with_schema: sqlite3.Connection):
        """Verify return value matches actual deletions."""
        cid = create_cluster(db_with_schema)
        for i in range(5):
            create_cooccurrence(
                db_with_schema,
                f"old_{i}",
                "weak",
                cluster_id=cid,
                weight=1,
                last_used=_days_ago(100),
            )
        deleted = prune_cooccurrence(db_with_schema)
        assert deleted == 5
        assert _count_rows(db_with_schema) == 0


# =============================================================================
# Emergency prune
# =============================================================================


class TestEmergencyPrune:
    def test_emergency_prune_over_threshold(self, db_with_schema: sqlite3.Connection):
        """Insert many edges -> prune works when over threshold."""
        cid = create_cluster(db_with_schema)
        # Insert 15 edges with low weight
        for i in range(15):
            create_cooccurrence(db_with_schema, f"term_a_{i}", f"term_b_{i}", cluster_id=cid, weight=1)
        # Insert 5 edges with high weight
        for i in range(5):
            create_cooccurrence(db_with_schema, f"strong_a_{i}", f"strong_b_{i}", cluster_id=cid, weight=5)
        # Use low threshold for testing
        deleted = emergency_prune(db_with_schema, max_edges=10, min_weight=3)
        assert deleted == 15
        assert _count_rows(db_with_schema) == 5

    def test_emergency_prune_under_threshold(self, db_with_schema: sqlite3.Connection):
        """Few edges -> no-op."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "a", "b", cluster_id=cid, weight=1)
        deleted = emergency_prune(db_with_schema, max_edges=200_000)
        assert deleted == 0
        assert _count_rows(db_with_schema) == 1


# =============================================================================
# Mixed-topic guard
# =============================================================================


class TestMixedTopicGuard:
    def test_mixed_topic_single_cluster(self, db_with_schema: sqlite3.Connection):
        """One cluster dominant -> (True, cluster_id)."""
        cid = create_cluster(db_with_schema)
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=10)
        create_cooccurrence(db_with_schema, "deploy", "merge", cluster_id=cid, weight=8)
        ok, best_id = should_update_cooccurrence(db_with_schema, ["deploy", "git", "merge"])
        assert ok is True
        assert best_id == cid

    def test_mixed_topic_two_close(self, db_with_schema: sqlite3.Connection):
        """score_2 > 0.7 * score_1 -> (False, best_id)."""
        cid1 = create_cluster(db_with_schema)
        cid2 = create_cluster(db_with_schema)
        # Cluster 1: total weight 10
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid1, weight=10)
        # Cluster 2: total weight 8 (> 0.7 * 10 = 7)
        create_cooccurrence(db_with_schema, "deploy", "remont", cluster_id=cid2, weight=8)
        ok, best_id = should_update_cooccurrence(db_with_schema, ["deploy", "git", "remont"])
        assert ok is False
        assert best_id == cid1

    def test_mixed_topic_empty_graph(self, db_with_schema: sqlite3.Connection):
        """No data -> (True, None)."""
        ok, best_id = should_update_cooccurrence(db_with_schema, ["deploy", "git"])
        assert ok is True
        assert best_id is None
