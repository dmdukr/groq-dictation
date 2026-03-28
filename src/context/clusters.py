"""Cluster detection and management for the Context Engine co-occurrence graph.

Provides:
- detect_cluster(): find best-matching cluster from keywords
- get_or_create_cluster(): find or create cluster for keywords
- name_cluster(): auto-generate display_name from top terms
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)

CLUSTER_SCORE_THRESHOLD: float = 5.0


def detect_cluster(db: sqlite3.Connection, keywords: list[str]) -> int | None:
    """Determine cluster_id from keywords via co-occurrence graph.

    Algorithm:
    1. For each keyword, query term_cooccurrence for all cluster_ids it appears in
       (both as term_a and term_b)
    2. Sum weights per cluster_id (with temporal decay: weight / max(days_since_last_used, 1))
    3. If best score >= 5.0: return that cluster_id
    4. If best score < 5.0: return None
    """
    if not keywords:
        return None

    placeholders = ",".join("?" for _ in keywords)

    # Query both term_a and term_b positions with temporal decay.
    # Placeholders are safe: only "?" chars generated from len(keywords).
    template = """
        SELECT cluster_id,
               SUM(weight * 1.0 / MAX(
                   CAST(julianday('now') - julianday(last_used) AS REAL),
                   1.0
               )) AS score
        FROM term_cooccurrence
        WHERE term_a IN ({ph}) OR term_b IN ({ph})
        GROUP BY cluster_id
        ORDER BY score DESC
        LIMIT 1
    """
    query = template.format(ph=placeholders)
    params = [*keywords, *keywords]
    row = db.execute(query, params).fetchone()
    if row is None:
        logger.debug("[clusters] detect_cluster: no co-occurrence data for keywords=%s", keywords)
        return None

    cluster_id: int = row[0]
    score: float = row[1]
    logger.debug(
        "[clusters] detect_cluster: top_cluster=%d, score=%.2f, threshold=%.1f, accepted=%s",
        cluster_id,
        score,
        CLUSTER_SCORE_THRESHOLD,
        score >= CLUSTER_SCORE_THRESHOLD,
    )

    if score >= CLUSTER_SCORE_THRESHOLD:
        return cluster_id
    return None


def name_cluster(db: sqlite3.Connection, cluster_id: int) -> str:
    """Generate display_name from top-3 terms by total weight in this cluster.

    1. UNION query: find all terms (both term_a and term_b) in term_cooccurrence
    2. Group by term, sum weights
    3. Top 3 by total weight
    4. Join with " / " separator
    5. UPDATE clusters SET display_name = result
    6. Return the display_name

    If no terms found: return "cluster_{id}"
    """
    query = """
        SELECT term, SUM(w) AS total_weight
        FROM (
            SELECT term_a AS term, weight AS w
            FROM term_cooccurrence
            WHERE cluster_id = ?
            UNION ALL
            SELECT term_b AS term, weight AS w
            FROM term_cooccurrence
            WHERE cluster_id = ?
        )
        GROUP BY term
        ORDER BY total_weight DESC
        LIMIT 3
    """
    rows = db.execute(query, [cluster_id, cluster_id]).fetchall()

    display_name = f"cluster_{cluster_id}" if not rows else " / ".join(row[0] for row in rows)

    db.execute(
        "UPDATE clusters SET display_name = ? WHERE id = ?",
        [display_name, cluster_id],
    )
    db.commit()
    logger.debug("[clusters] name_cluster: cluster_id=%d, display_name=%s", cluster_id, display_name)
    return display_name


def get_or_create_cluster(db: sqlite3.Connection, keywords: list[str]) -> int:
    """Find existing cluster or create new one.

    1. detect_cluster(keywords)
    2. If found: return existing cluster_id
    3. If not found: INSERT INTO clusters, call name_cluster(), return new id
    """
    existing = detect_cluster(db, keywords)
    if existing is not None:
        logger.debug("[clusters] get_or_create_cluster: found existing cluster=%d", existing)
        return existing

    cursor = db.execute("INSERT INTO clusters (display_name) VALUES (NULL)")
    db.commit()
    new_id: int = cursor.lastrowid  # type: ignore[assignment]
    logger.info("[clusters] get_or_create_cluster: created new cluster=%d", new_id)

    name_cluster(db, new_id)
    return new_id
