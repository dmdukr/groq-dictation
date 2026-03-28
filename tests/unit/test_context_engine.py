"""Tests for src/context/engine.py — 4-level cascade context resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from src.context.engine import (
    CONFIDENCE_THRESHOLDS,
    ContextEngine,
)

from tests.factories import (
    create_cluster,
    create_cooccurrence,
    create_fingerprint,
    create_thread,
)

if TYPE_CHECKING:
    import sqlite3


# =============================================================================
# Helpers
# =============================================================================


def _make_engine(db: sqlite3.Connection) -> ContextEngine:
    """Create a ContextEngine bound to the test database."""
    return ContextEngine(db)


def _count_cooccurrence_rows(db: sqlite3.Connection) -> int:
    """Count total rows in term_cooccurrence table."""
    row = db.execute("SELECT COUNT(*) AS cnt FROM term_cooccurrence").fetchone()
    assert row is not None
    return int(row["cnt"])


# =============================================================================
# 4-level cascade
# =============================================================================


class TestLevel1ConfidentStops:
    """Level 1: Strong co-occurrence stops the cascade."""

    def test_level1_confident_stops(self, db_with_schema: sqlite3.Connection) -> None:
        """Create cluster + strong co-occurrence (weight>=5).
        Verify resolution at level=1 with confidence >= 0.8."""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        # Strong co-occurrence edges for "deploy" with context terms
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=6)
        create_cooccurrence(db_with_schema, "deploy", "staging", cluster_id=cid, weight=5)
        create_cooccurrence(db_with_schema, "git", "staging", cluster_id=cid, weight=5)

        engine = _make_engine(db_with_schema)
        # Mock extract_keywords to return controlled keywords
        with patch(
            "src.context.engine.extract_keywords",
            return_value=["deploy", "git", "staging"],
        ):
            result = engine.resolve("deploy to git staging", "terminal.exe")

        # Find resolution for "deploy"
        deploy_res = [r for r in result.resolutions if r.term == "deploy"]
        assert len(deploy_res) >= 1
        res = deploy_res[0]
        assert res.level == 1
        assert res.confidence >= CONFIDENCE_THRESHOLDS[1]
        assert res.cluster_id == cid
        assert res.resolved_meaning == "DevOps"


class TestLevel1LowEscalatesToLevel2:
    """Level 1 weak -> escalate to level 2 via active thread."""

    def test_level1_low_escalates_to_level2(self, db_with_schema: sqlite3.Connection) -> None:
        """Weak co-occurrence (weight=2, conf=0.4). Active thread with cluster_id
        and message_count=5 -> resolution at level=2."""
        cid = create_cluster(db_with_schema, display_name="HomeRepair")
        # Weak co-occurrence: weight=2 -> confidence=2/5=0.4 < 0.8
        create_cooccurrence(db_with_schema, "zamok", "dveri", cluster_id=cid, weight=2)

        # Create active thread with high message_count in same cluster
        thread_id = create_thread(
            db_with_schema,
            app="telegram.exe",
            cluster_id=cid,
            message_count=5,
            keywords=["zamok", "dveri"],
        )

        engine = _make_engine(db_with_schema)
        with (
            patch(
                "src.context.engine.extract_keywords",
                return_value=["zamok", "dveri"],
            ),
            patch(
                "src.context.engine.assign_to_thread",
                return_value=thread_id,
            ),
        ):
            result = engine.resolve("zamok dveri", "telegram.exe")

        zamok_res = [r for r in result.resolutions if r.term == "zamok"]
        assert len(zamok_res) >= 1
        res = zamok_res[0]
        assert res.level == 2
        assert res.confidence >= CONFIDENCE_THRESHOLDS[2]
        assert res.cluster_id == cid


class TestLevel2ConfidentStops:
    """Level 2: Thread with sufficient message_count stops cascade."""

    def test_level2_confident_stops(self, db_with_schema: sqlite3.Connection) -> None:
        """Thread with message_count >= 3 (confidence=1.0).
        Verify resolved at level=2."""
        cid = create_cluster(db_with_schema, display_name="Medical")
        # Need co-occurrence so the term is considered ambiguous, but weak
        create_cooccurrence(db_with_schema, "analiz", "recept", cluster_id=cid, weight=1)

        thread_id = create_thread(
            db_with_schema,
            app="telegram.exe",
            cluster_id=cid,
            message_count=3,
            keywords=["analiz", "recept"],
        )

        engine = _make_engine(db_with_schema)
        with (
            patch(
                "src.context.engine.extract_keywords",
                return_value=["analiz", "recept"],
            ),
            patch(
                "src.context.engine.assign_to_thread",
                return_value=thread_id,
            ),
        ):
            result = engine.resolve("analiz recept", "telegram.exe")

        analiz_res = [r for r in result.resolutions if r.term == "analiz"]
        assert len(analiz_res) >= 1
        res = analiz_res[0]
        assert res.level == 2
        assert res.confidence == 1.0
        assert res.resolved_meaning == "Medical"


class TestLevel2LowEscalatesToLevel3:
    """Level 2 low confidence -> fingerprint (level 3)."""

    def test_level2_low_escalates_to_level3(self, db_with_schema: sqlite3.Connection) -> None:
        """Thread with message_count=1 (conf=0.33 < 0.75).
        Fingerprints provide level=3 resolution."""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        # Weak co-occurrence (ambiguous)
        create_cooccurrence(db_with_schema, "deploy", "server", cluster_id=cid, weight=1)

        # Thread with low message_count -> low confidence
        thread_id = create_thread(
            db_with_schema,
            app="terminal.exe",
            cluster_id=cid,
            message_count=1,
            keywords=["deploy", "server"],
        )

        # Create dominant fingerprints for this cluster
        for _ in range(5):
            create_fingerprint(
                db_with_schema,
                cluster_id=cid,
                keywords=["deploy", "server", "prod"],
            )

        engine = _make_engine(db_with_schema)
        with (
            patch(
                "src.context.engine.extract_keywords",
                return_value=["deploy", "server"],
            ),
            patch(
                "src.context.engine.assign_to_thread",
                return_value=thread_id,
            ),
        ):
            result = engine.resolve("deploy server", "terminal.exe")

        deploy_res = [r for r in result.resolutions if r.term == "deploy"]
        assert len(deploy_res) >= 1
        res = deploy_res[0]
        assert res.level == 3
        assert res.confidence >= CONFIDENCE_THRESHOLDS[3]
        assert res.cluster_id == cid


class TestLevel3ConfidentStops:
    """Level 3: Dominant fingerprints resolve the term."""

    def test_level3_confident_stops(self, db_with_schema: sqlite3.Connection) -> None:
        """Many fingerprints for one cluster (dominance > 0.7).
        Verify resolved at level=3."""
        cid1 = create_cluster(db_with_schema, display_name="DevOps")
        cid2 = create_cluster(db_with_schema, display_name="Other")

        # Need co-occurrence so term is considered ambiguous
        create_cooccurrence(db_with_schema, "deploy", "code", cluster_id=cid1, weight=1)

        # Create dominant fingerprints for cid1 (8 out of 9 total)
        for _ in range(8):
            create_fingerprint(
                db_with_schema,
                cluster_id=cid1,
                keywords=["deploy", "code", "ci"],
            )
        # One fingerprint for cid2
        create_fingerprint(
            db_with_schema,
            cluster_id=cid2,
            keywords=["deploy"],
        )

        engine = _make_engine(db_with_schema)
        # No thread -> skip level 2
        with (
            patch(
                "src.context.engine.extract_keywords",
                return_value=["deploy", "code"],
            ),
            patch(
                "src.context.engine.assign_to_thread",
                return_value=None,
            ),
        ):
            result = engine.resolve("deploy code", "terminal.exe")

        deploy_res = [r for r in result.resolutions if r.term == "deploy"]
        assert len(deploy_res) >= 1
        res = deploy_res[0]
        assert res.level == 3
        assert res.confidence >= CONFIDENCE_THRESHOLDS[3]


class TestLevel3LowGoesToUnresolved:
    """Level 3: Mixed fingerprints -> term goes to unresolved."""

    def test_level3_low_goes_to_unresolved(self, db_with_schema: sqlite3.Connection) -> None:
        """Mixed fingerprints (no dominance).
        Verify term appears in unresolved_terms."""
        cid1 = create_cluster(db_with_schema, display_name="DevOps")
        cid2 = create_cluster(db_with_schema, display_name="HomeRepair")

        # Weak co-occurrence (term is ambiguous)
        create_cooccurrence(db_with_schema, "zamok", "test", cluster_id=cid1, weight=1)

        # Even split of fingerprints -> no dominance
        for _ in range(3):
            create_fingerprint(
                db_with_schema,
                cluster_id=cid1,
                keywords=["zamok", "test"],
            )
        for _ in range(3):
            create_fingerprint(
                db_with_schema,
                cluster_id=cid2,
                keywords=["zamok", "test"],
            )

        engine = _make_engine(db_with_schema)
        with (
            patch(
                "src.context.engine.extract_keywords",
                return_value=["zamok", "test"],
            ),
            patch(
                "src.context.engine.assign_to_thread",
                return_value=None,
            ),
        ):
            result = engine.resolve("zamok test", "telegram.exe")

        # zamok should be unresolved (50/50 split = 0.5 < 0.7 threshold)
        assert "zamok" in result.unresolved_terms


# =============================================================================
# Confidence calculations
# =============================================================================


class TestConfidenceFormulas:
    """Verify exact confidence calculations for each level."""

    def test_level1_confidence_formula(self, db_with_schema: sqlite3.Connection) -> None:
        """weight=5 -> min(5/5.0, 1.0) = 1.0"""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=5)

        engine = _make_engine(db_with_schema)
        res = engine._level1_self_context("deploy", ["deploy", "git"], cid)  # noqa: SLF001
        assert res is not None
        # effective_weight = weight / max(days_since_last_used, 1) = 5/1 = 5.0
        # confidence = min(5.0 / 5.0, 1.0) = 1.0
        assert res.confidence == 1.0
        assert res.level == 1

    def test_level1_confidence_low_weight(self, db_with_schema: sqlite3.Connection) -> None:
        """weight=2 -> min(2/5.0, 1.0) = 0.4 < 0.8 threshold."""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=2)

        engine = _make_engine(db_with_schema)
        res = engine._level1_self_context("deploy", ["deploy", "git"], cid)  # noqa: SLF001
        assert res is not None
        # effective_weight = 2/1 = 2.0, confidence = 2.0/5.0 = 0.4
        assert res.confidence < CONFIDENCE_THRESHOLDS[1]
        assert abs(res.confidence - 0.4) < 0.01

    def test_level2_confidence_formula(self, db_with_schema: sqlite3.Connection) -> None:
        """message_count=3 -> min(3/3.0, 1.0) = 1.0"""
        cid = create_cluster(db_with_schema, display_name="Medical")
        thread_id = create_thread(
            db_with_schema,
            app="telegram.exe",
            cluster_id=cid,
            message_count=3,
            keywords=["analiz"],
        )
        thread_row = db_with_schema.execute("SELECT * FROM conversation_threads WHERE id = ?", [thread_id]).fetchone()
        assert thread_row is not None

        engine = _make_engine(db_with_schema)
        res = engine._level2_active_thread("analiz", thread_row)  # noqa: SLF001
        assert res is not None
        assert res.confidence == 1.0
        assert res.level == 2

    def test_level3_confidence_dominance(self, db_with_schema: sqlite3.Connection) -> None:
        """hits=[5,1] -> 5/6 = 0.83 >= 0.7 threshold."""
        cid1 = create_cluster(db_with_schema, display_name="DevOps")
        cid2 = create_cluster(db_with_schema, display_name="Other")

        # 5 fingerprints for cid1
        for _ in range(5):
            create_fingerprint(
                db_with_schema,
                cluster_id=cid1,
                keywords=["deploy", "server"],
            )
        # 1 fingerprint for cid2
        create_fingerprint(
            db_with_schema,
            cluster_id=cid2,
            keywords=["deploy"],
        )

        engine = _make_engine(db_with_schema)
        res = engine._level3_fingerprint("deploy", ["deploy", "server"])  # noqa: SLF001
        assert res is not None
        # cid1 has 10 keyword hits (5 fps * 2 keywords each), cid2 has 1 hit
        # confidence = 10/11 = 0.909...
        assert res.confidence >= CONFIDENCE_THRESHOLDS[3]
        assert res.level == 3
        assert res.cluster_id == cid1


# =============================================================================
# Full resolve()
# =============================================================================


class TestResolveColdStart:
    """Cold start: empty DB, nothing to resolve."""

    def test_resolve_cold_start_empty_graph(self, db_with_schema: sqlite3.Connection) -> None:
        """Empty DB -> keywords extracted but all continue to unresolved
        (no co-occurrence data so nothing to resolve)."""
        engine = _make_engine(db_with_schema)
        with patch(
            "src.context.engine.extract_keywords",
            return_value=["deploy", "git", "staging"],
        ):
            result = engine.resolve("deploy to git staging", "terminal.exe")

        assert result.keywords == ["deploy", "git", "staging"]
        # No co-occurrence data -> no resolutions and no unresolved
        # (terms without co-occurrence data are skipped entirely)
        assert result.resolutions == []
        assert result.unresolved_terms == []


class TestResolveReturnsKeywords:
    """Verify result.keywords is populated."""

    def test_resolve_returns_keywords(self, db_with_schema: sqlite3.Connection) -> None:
        """Verify result.keywords is populated."""
        engine = _make_engine(db_with_schema)
        with patch(
            "src.context.engine.extract_keywords",
            return_value=["python", "code", "refactor"],
        ):
            result = engine.resolve("python code refactor", "vscode.exe")

        assert result.keywords == ["python", "code", "refactor"]


class TestResolveReturnsResolvedSet:
    """Verify resolved_terms contains resolved keywords."""

    def test_resolve_returns_resolved_set(self, db_with_schema: sqlite3.Connection) -> None:
        """Resolved terms appear in result.resolved_terms."""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        # Strong co-occurrence
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=6)
        create_cooccurrence(db_with_schema, "deploy", "staging", cluster_id=cid, weight=6)
        create_cooccurrence(db_with_schema, "git", "staging", cluster_id=cid, weight=6)

        engine = _make_engine(db_with_schema)
        with patch(
            "src.context.engine.extract_keywords",
            return_value=["deploy", "git", "staging"],
        ):
            result = engine.resolve("deploy git staging", "terminal.exe")

        # All terms should be resolved at level 1 with high confidence
        assert "deploy" in result.resolved_terms
        assert "git" in result.resolved_terms
        assert "staging" in result.resolved_terms


class TestResolveUpdatesCooccurrence:
    """Verify resolve() updates co-occurrence graph."""

    def test_resolve_updates_cooccurrence(self, db_with_schema: sqlite3.Connection) -> None:
        """After resolve(), new co-occurrence edges exist in DB."""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        # Set up enough co-occurrence for cluster detection (score >= 5.0)
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=6)
        create_cooccurrence(db_with_schema, "deploy", "staging", cluster_id=cid, weight=6)
        create_cooccurrence(db_with_schema, "git", "staging", cluster_id=cid, weight=6)

        engine = _make_engine(db_with_schema)
        with patch(
            "src.context.engine.extract_keywords",
            return_value=["deploy", "git", "staging"],
        ):
            engine.resolve("deploy git staging", "terminal.exe")

        # Co-occurrence should have been updated (weights incremented)
        # Since edges already exist, weight should increase
        row = db_with_schema.execute(
            "SELECT weight FROM term_cooccurrence WHERE term_a=? AND term_b=? AND cluster_id=?",
            ["deploy", "git", cid],
        ).fetchone()
        assert row is not None
        # Was 6, updated by resolve -> now 7
        assert row["weight"] == 7


# =============================================================================
# Edge cases
# =============================================================================


class TestResolveEmptyText:
    """Empty text produces empty result."""

    def test_resolve_empty_text(self, db_with_schema: sqlite3.Connection) -> None:
        """\"\" -> empty result."""
        engine = _make_engine(db_with_schema)
        result = engine.resolve("", "telegram.exe")

        assert result.keywords == []
        assert result.resolutions == []
        assert result.unresolved_terms == []
        assert result.resolved_terms == set()
        assert result.thread_id is None
        assert result.cluster_id is None


class TestResolveNoAmbiguousTerms:
    """Text with words that have no co-occurrence data."""

    def test_resolve_no_ambiguous_terms(self, db_with_schema: sqlite3.Connection) -> None:
        """Text with words that have no co-occurrence -> empty resolutions."""
        engine = _make_engine(db_with_schema)
        with patch(
            "src.context.engine.extract_keywords",
            return_value=["unicorn", "rainbow"],
        ):
            result = engine.resolve("unicorn rainbow", "notepad.exe")

        assert result.keywords == ["unicorn", "rainbow"]
        assert result.resolutions == []
        assert result.unresolved_terms == []


class TestResolveThreadAssigned:
    """Thread assignment produces thread_id in result."""

    def test_resolve_thread_assigned(self, db_with_schema: sqlite3.Connection) -> None:
        """result.thread_id is populated when thread matches."""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        thread_id = create_thread(
            db_with_schema,
            app="terminal.exe",
            cluster_id=cid,
            message_count=3,
            keywords=["deploy", "git"],
        )

        engine = _make_engine(db_with_schema)
        with (
            patch(
                "src.context.engine.extract_keywords",
                return_value=["deploy", "git"],
            ),
            patch(
                "src.context.engine.assign_to_thread",
                return_value=thread_id,
            ),
        ):
            result = engine.resolve("deploy git", "terminal.exe")

        assert result.thread_id == thread_id


class TestResolveClusterDetected:
    """Cluster detection populates result.cluster_id."""

    def test_resolve_cluster_detected(self, db_with_schema: sqlite3.Connection) -> None:
        """result.cluster_id populated when graph has data."""
        cid = create_cluster(db_with_schema, display_name="DevOps")
        # Strong co-occurrence edges so detect_cluster finds score >= 5.0
        create_cooccurrence(db_with_schema, "deploy", "git", cluster_id=cid, weight=6)
        create_cooccurrence(db_with_schema, "deploy", "staging", cluster_id=cid, weight=6)
        create_cooccurrence(db_with_schema, "git", "staging", cluster_id=cid, weight=6)

        engine = _make_engine(db_with_schema)
        with patch(
            "src.context.engine.extract_keywords",
            return_value=["deploy", "git", "staging"],
        ):
            result = engine.resolve("deploy git staging", "terminal.exe")

        assert result.cluster_id == cid
