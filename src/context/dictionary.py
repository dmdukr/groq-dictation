"""Dictionary term management for the Context Engine.

Provides:
- CRUD operations for exact and context dictionary terms
- Post-LLM exact replacement with word-boundary matching
- Import/export with merge strategy
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

logger = logging.getLogger(__name__)


def get_exact_terms(db: sqlite3.Connection) -> dict[str, str]:
    """Return all exact dictionary terms as {source_text: target_text} dict."""
    rows = db.execute("SELECT source_text, target_text FROM dictionary WHERE term_type = 'exact'").fetchall()
    return {row["source_text"]: row["target_text"] for row in rows}


def get_context_terms(db: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return all context-type dictionary terms."""
    return db.execute("SELECT * FROM dictionary WHERE term_type = 'context'").fetchall()


def add_term(
    db: sqlite3.Connection,
    source: str,
    target: str,
    term_type: str = "exact",
    origin: str = "manual",
) -> int:
    """Add dictionary term. Returns id."""
    cursor = db.execute(
        """INSERT INTO dictionary (source_text, target_text, term_type, origin)
           VALUES (?, ?, ?, ?)""",
        [source, target, term_type, origin],
    )
    db.commit()
    row_id: int = cursor.lastrowid  # type: ignore[assignment]
    logger.debug("add_term: %s -> %s (type=%s, id=%d)", source, target, term_type, row_id)
    return row_id


def remove_term(db: sqlite3.Connection, term_id: int) -> None:
    """Remove dictionary term by id."""
    db.execute("DELETE FROM dictionary WHERE id = ?", [term_id])
    db.commit()
    logger.debug("remove_term: id=%d", term_id)


def apply_exact_replacements(
    text: str,
    exact_terms: dict[str, str],
    resolved_terms: set[str],
) -> str:
    """Post-LLM exact replacement.

    - For each exact term, do case-insensitive whole-word replacement
    - Skip terms that are in resolved_terms (already handled by context engine)
    - Use re.sub with word boundaries for matching
    - Returns modified text
    """
    result = text
    for source, target in exact_terms.items():
        if source in resolved_terms:
            continue
        # Word-boundary matching, case-insensitive
        pattern = re.compile(r"\b" + re.escape(source) + r"\b", re.IGNORECASE)
        result = pattern.sub(target, result)
    return result


def import_terms(db: sqlite3.Connection, terms: list[dict[str, str]]) -> int:
    """Import dictionary terms with merge strategy: imported values win on conflict.

    Each dict has: source_text, target_text, term_type, origin.
    Uses DELETE + INSERT to handle conflicts on source_text.
    Returns count of imported terms.
    """
    count = 0
    for term in terms:
        # Delete any existing term with the same source_text to implement
        # "imported values win on conflict" semantics.
        db.execute(
            "DELETE FROM dictionary WHERE source_text = ?",
            [term["source_text"]],
        )
        db.execute(
            """INSERT INTO dictionary (source_text, target_text, term_type, origin)
               VALUES (?, ?, ?, ?)""",
            [term["source_text"], term["target_text"], term["term_type"], term["origin"]],
        )
        count += 1
    db.commit()
    logger.info("Imported %d dictionary terms", count)
    return count


def export_terms(db: sqlite3.Connection) -> list[dict[str, str]]:
    """Export all dictionary terms as list of dicts."""
    rows = db.execute("SELECT source_text, target_text, term_type, origin FROM dictionary").fetchall()
    return [
        {
            "source_text": row["source_text"],
            "target_text": row["target_text"],
            "term_type": row["term_type"],
            "origin": row["origin"],
        }
        for row in rows
    ]
