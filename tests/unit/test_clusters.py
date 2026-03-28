"""Tests for src/context/clusters.py — cluster detection, creation, and naming."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from src.context.clusters import detect_cluster, get_or_create_cluster, name_cluster

from tests.factories import create_cluster, create_cooccurrence

if TYPE_CHECKING:
    import sqlite3


def _date_ago(days: int) -> str:
    """Return ISO datetime string for N days ago."""
    dt = datetime.now(UTC) - timedelta(days=days)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# =============================================================================
# detect_cluster
# =============================================================================


class TestDetectCluster:
    """Tests for detect_cluster()."""

    def test_detect_strong_cluster(self, db_with_schema: sqlite3.Connection) -> None:
        """Keywords with co-occurrence score >= 5 returns the cluster_id."""
        db = db_with_schema
        cid = create_cluster(db, display_name="devops")
        # Each edge contributes weight=5, days_since=0 -> decay=max(0,1)=1 -> score=5 per edge
        create_cooccurrence(db, "deploy", "git", cluster_id=cid, weight=5)
        create_cooccurrence(db, "deploy", "merge", cluster_id=cid, weight=5)

        result = detect_cluster(db, ["deploy", "git", "merge"])
        assert result == cid

    def test_detect_weak_cluster(self, db_with_schema: sqlite3.Connection) -> None:
        """Keywords with co-occurrence score < 5 returns None."""
        db = db_with_schema
        cid = create_cluster(db, display_name="weak")
        create_cooccurrence(db, "alpha", "beta", cluster_id=cid, weight=1)

        result = detect_cluster(db, ["alpha", "beta"])
        assert result is None

    def test_detect_with_temporal_decay(self, db_with_schema: sqlite3.Connection) -> None:
        """Old edges contribute less due to temporal decay (weight / days)."""
        db = db_with_schema
        cid = create_cluster(db, display_name="old")
        # weight=10, but 100 days old -> score = 10/100 = 0.1 per edge
        old_date = _date_ago(100)
        create_cooccurrence(db, "old_a", "old_b", cluster_id=cid, weight=10, last_used=old_date)
        create_cooccurrence(db, "old_a", "old_c", cluster_id=cid, weight=10, last_used=old_date)

        result = detect_cluster(db, ["old_a", "old_b", "old_c"])
        assert result is None

    def test_detect_empty_graph(self, db_with_schema: sqlite3.Connection) -> None:
        """No co-occurrence data returns None."""
        db = db_with_schema
        result = detect_cluster(db, ["anything", "here"])
        assert result is None

    def test_detect_multiple_clusters(self, db_with_schema: sqlite3.Connection) -> None:
        """Keywords matching multiple clusters returns the highest-scoring one."""
        db = db_with_schema
        cid_weak = create_cluster(db, display_name="weak_cluster")
        cid_strong = create_cluster(db, display_name="strong_cluster")

        # Weak cluster: keyword "python" with low weight
        create_cooccurrence(db, "python", "code", cluster_id=cid_weak, weight=2)

        # Strong cluster: keyword "python" with high weight
        create_cooccurrence(db, "python", "deploy", cluster_id=cid_strong, weight=10)

        result = detect_cluster(db, ["python"])
        assert result == cid_strong

    def test_detect_empty_keywords(self, db_with_schema: sqlite3.Connection) -> None:
        """Empty keywords list returns None."""
        db = db_with_schema
        result = detect_cluster(db, [])
        assert result is None


# =============================================================================
# get_or_create_cluster
# =============================================================================


class TestGetOrCreateCluster:
    """Tests for get_or_create_cluster()."""

    def test_get_existing_cluster(self, db_with_schema: sqlite3.Connection) -> None:
        """Keywords matching existing cluster (score>=5) returns existing id."""
        db = db_with_schema
        cid = create_cluster(db, display_name="existing")
        create_cooccurrence(db, "react", "typescript", cluster_id=cid, weight=6)

        result = get_or_create_cluster(db, ["react", "typescript"])
        assert result == cid

    def test_create_new_cluster(self, db_with_schema: sqlite3.Connection) -> None:
        """No match creates a new cluster and returns new id."""
        db = db_with_schema
        # No data in graph at all
        result = get_or_create_cluster(db, ["brand_new_term"])

        # Should return a valid cluster_id
        assert result is not None
        assert result > 0

        # Verify the cluster row exists
        row = db.execute("SELECT id FROM clusters WHERE id = ?", [result]).fetchone()
        assert row is not None

    def test_create_cluster_auto_names(self, db_with_schema: sqlite3.Connection) -> None:
        """New cluster gets auto-generated display_name via name_cluster()."""
        db = db_with_schema
        result = get_or_create_cluster(db, ["novel_term"])

        row = db.execute("SELECT display_name FROM clusters WHERE id = ?", [result]).fetchone()
        assert row is not None
        # No edges exist for the new cluster, so fallback name
        assert row[0] == f"cluster_{result}"


# =============================================================================
# name_cluster
# =============================================================================


class TestNameCluster:
    """Tests for name_cluster()."""

    def test_name_cluster_top3_terms(self, db_with_schema: sqlite3.Connection) -> None:
        """Display_name is 'term1 / term2 / term3' (top 3 by weight)."""
        db = db_with_schema
        cid = create_cluster(db)
        # Create edges with different weights to establish ranking
        # "deploy" appears in edges with total weight = 10+8 = 18
        # "git" appears in edges with total weight = 10+5 = 15
        # "merge" appears in edges with total weight = 8+5 = 13
        # "branch" appears in edges with total weight = 3
        create_cooccurrence(db, "deploy", "git", cluster_id=cid, weight=10)
        create_cooccurrence(db, "deploy", "merge", cluster_id=cid, weight=8)
        create_cooccurrence(db, "git", "merge", cluster_id=cid, weight=5)
        create_cooccurrence(db, "branch", "deploy", cluster_id=cid, weight=3)

        result = name_cluster(db, cid)
        assert result == "deploy / git / merge"

    def test_name_cluster_union_query(self, db_with_schema: sqlite3.Connection) -> None:
        """Both term_a and term_b positions contribute to term total weights."""
        db = db_with_schema
        cid = create_cluster(db)
        # "alpha" is always term_a (canonical sort), "beta" always term_b
        # But both should accumulate weight correctly
        create_cooccurrence(db, "alpha", "beta", cluster_id=cid, weight=5)
        create_cooccurrence(db, "alpha", "gamma", cluster_id=cid, weight=3)

        result = name_cluster(db, cid)
        # alpha: 5+3=8, beta: 5, gamma: 3
        assert result == "alpha / beta / gamma"

    def test_name_cluster_cyrillic_not_missed(self, db_with_schema: sqlite3.Connection) -> None:
        """Ukrainian terms (often term_b in canonical order) still appear in name."""
        db = db_with_schema
        cid = create_cluster(db)
        # Cyrillic sorts after Latin, so Ukrainian terms end up as term_b
        create_cooccurrence(db, "code", "\u0434\u0435\u043f\u043b\u043e\u0439", cluster_id=cid, weight=7)
        create_cooccurrence(db, "code", "\u043c\u0435\u0440\u0434\u0436", cluster_id=cid, weight=5)
        create_cooccurrence(
            db, "\u0434\u0435\u043f\u043b\u043e\u0439", "\u043c\u0435\u0440\u0434\u0436", cluster_id=cid, weight=3
        )

        result = name_cluster(db, cid)
        # code: 7+5=12, деплой: 7+3=10, мердж: 5+3=8
        assert result == "code / \u0434\u0435\u043f\u043b\u043e\u0439 / \u043c\u0435\u0440\u0434\u0436"

    def test_name_cluster_updates_db(self, db_with_schema: sqlite3.Connection) -> None:
        """Display_name is written to the clusters table."""
        db = db_with_schema
        cid = create_cluster(db)
        # Give "api" higher weight so ordering is deterministic
        create_cooccurrence(db, "api", "rest", cluster_id=cid, weight=4)
        create_cooccurrence(db, "api", "http", cluster_id=cid, weight=3)

        name_cluster(db, cid)

        row = db.execute("SELECT display_name FROM clusters WHERE id = ?", [cid]).fetchone()
        assert row is not None
        # api: 4+3=7, rest: 4, http: 3
        assert row[0] == "api / rest / http"

    def test_name_cluster_no_terms_fallback(self, db_with_schema: sqlite3.Connection) -> None:
        """Empty cluster returns 'cluster_{id}' as display_name."""
        db = db_with_schema
        cid = create_cluster(db)

        result = name_cluster(db, cid)
        assert result == f"cluster_{cid}"

        # Also verify it's stored in DB
        row = db.execute("SELECT display_name FROM clusters WHERE id = ?", [cid]).fetchone()
        assert row is not None
        assert row[0] == f"cluster_{cid}"


# =============================================================================
# Lifecycle
# =============================================================================


class TestLifecycle:
    """Lifecycle and boundary tests."""

    def test_organic_growth_empty_to_first(self, db_with_schema: sqlite3.Connection) -> None:
        """0 clusters, first dictation -> returns new cluster."""
        db = db_with_schema

        # Verify no clusters exist
        count = db.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert count == 0

        cid = get_or_create_cluster(db, ["hello", "world"])

        # Should have created exactly one cluster
        assert cid > 0
        count = db.execute("SELECT COUNT(*) FROM clusters").fetchone()[0]
        assert count == 1

    def test_threshold_5_boundary(self, db_with_schema: sqlite3.Connection) -> None:
        """Score=4.99 -> None, score=5.01 -> cluster_id (boundary test)."""
        db = db_with_schema

        # Cluster with score just below threshold
        # Single keyword matching a single edge: score = weight / max(days, 1)
        # For a fresh edge (days<1 -> decay=1): score = weight
        # We need score < 5, so weight=4
        cid_below = create_cluster(db, display_name="below")
        create_cooccurrence(db, "below_a", "below_b", cluster_id=cid_below, weight=4)

        result_below = detect_cluster(db, ["below_a"])
        assert result_below is None

        # Cluster with score just above threshold
        # Two edges each contributing weight=3 for the same keyword: 3+3=6 > 5
        cid_above = create_cluster(db, display_name="above")
        create_cooccurrence(db, "above_a", "above_b", cluster_id=cid_above, weight=3)
        create_cooccurrence(db, "above_a", "above_c", cluster_id=cid_above, weight=3)

        result_above = detect_cluster(db, ["above_a"])
        assert result_above == cid_above
