"""Tests for src/context/dictionary.py — dictionary term CRUD, replacements, and import/export."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.context.dictionary import (
    add_term,
    apply_exact_replacements,
    export_terms,
    get_context_terms,
    get_exact_terms,
    import_terms,
    remove_term,
)

from tests.factories import create_dictionary_term

if TYPE_CHECKING:
    import sqlite3


# =============================================================================
# CRUD
# =============================================================================


class TestCRUD:
    """Tests for add_term, get_exact_terms, get_context_terms, remove_term."""

    def test_add_exact_term(self, db_with_schema: sqlite3.Connection) -> None:
        """Add exact term and verify it exists in DB."""
        db = db_with_schema
        term_id = add_term(db, "пайтон", "Python")

        row = db.execute("SELECT * FROM dictionary WHERE id = ?", [term_id]).fetchone()
        assert row is not None
        assert row["source_text"] == "пайтон"
        assert row["target_text"] == "Python"
        assert row["term_type"] == "exact"
        assert row["origin"] == "manual"

    def test_add_context_term(self, db_with_schema: sqlite3.Connection) -> None:
        """Add context-type term and verify term_type."""
        db = db_with_schema
        term_id = add_term(db, "замок", "lock", term_type="context", origin="user")

        row = db.execute("SELECT * FROM dictionary WHERE id = ?", [term_id]).fetchone()
        assert row is not None
        assert row["term_type"] == "context"
        assert row["origin"] == "user"

    def test_get_exact_terms_dict(self, db_with_schema: sqlite3.Connection) -> None:
        """get_exact_terms returns {source: target} for exact terms only."""
        db = db_with_schema
        create_dictionary_term(db, "пайтон", "Python", term_type="exact")
        create_dictionary_term(db, "джаваскрипт", "JavaScript", term_type="exact")
        create_dictionary_term(db, "замок", "lock/castle", term_type="context")

        result = get_exact_terms(db)
        assert result == {"пайтон": "Python", "джаваскрипт": "JavaScript"}

    def test_get_context_terms_list(self, db_with_schema: sqlite3.Connection) -> None:
        """get_context_terms returns only context-type rows."""
        db = db_with_schema
        create_dictionary_term(db, "пайтон", "Python", term_type="exact")
        create_dictionary_term(db, "замок", "lock/castle", term_type="context")
        create_dictionary_term(db, "коса", "braid/scythe", term_type="context")

        result = get_context_terms(db)
        assert len(result) == 2
        sources = {row["source_text"] for row in result}
        assert sources == {"замок", "коса"}

    def test_remove_term(self, db_with_schema: sqlite3.Connection) -> None:
        """Remove term by id and verify it is gone."""
        db = db_with_schema
        term_id = add_term(db, "тест", "test")

        remove_term(db, term_id)

        row = db.execute("SELECT * FROM dictionary WHERE id = ?", [term_id]).fetchone()
        assert row is None


# =============================================================================
# apply_exact_replacements
# =============================================================================


class TestApplyExactReplacements:
    """Tests for apply_exact_replacements()."""

    def test_apply_exact_simple(self) -> None:
        """Simple replacement: 'пайтон' in text -> 'Python'."""
        text = "я використовую пайтон для роботи"
        exact_terms = {"пайтон": "Python"}
        resolved: set[str] = set()

        result = apply_exact_replacements(text, exact_terms, resolved)
        assert result == "я використовую Python для роботи"

    def test_apply_exact_skip_resolved(self) -> None:
        """Term in resolved_terms is not replaced."""
        text = "замок на дверях"
        exact_terms = {"замок": "lock"}
        resolved = {"замок"}

        result = apply_exact_replacements(text, exact_terms, resolved)
        assert result == "замок на дверях"

    def test_apply_exact_multiple(self) -> None:
        """Multiple terms are all replaced."""
        text = "пайтон та джаваскрипт для веб"
        exact_terms = {"пайтон": "Python", "джаваскрипт": "JavaScript"}
        resolved: set[str] = set()

        result = apply_exact_replacements(text, exact_terms, resolved)
        assert "Python" in result
        assert "JavaScript" in result
        assert "пайтон" not in result
        assert "джаваскрипт" not in result

    def test_apply_exact_case_insensitive(self) -> None:
        """Case-insensitive matching: 'Пайтон' matches source 'пайтон'."""
        text = "я вивчаю Пайтон зараз"
        exact_terms = {"пайтон": "Python"}
        resolved: set[str] = set()

        result = apply_exact_replacements(text, exact_terms, resolved)
        assert result == "я вивчаю Python зараз"


# =============================================================================
# Import/export
# =============================================================================


class TestImportExport:
    """Tests for import_terms() and export_terms()."""

    def test_export_all_terms(self, db_with_schema: sqlite3.Connection) -> None:
        """export_terms returns complete list of all terms."""
        db = db_with_schema
        create_dictionary_term(db, "пайтон", "Python", term_type="exact", origin="manual")
        create_dictionary_term(db, "замок", "lock", term_type="context", origin="user")

        result = export_terms(db)
        assert len(result) == 2
        sources = {d["source_text"] for d in result}
        assert sources == {"пайтон", "замок"}
        # Verify dict structure
        for item in result:
            assert "source_text" in item
            assert "target_text" in item
            assert "term_type" in item
            assert "origin" in item

    def test_import_new_terms_added(self, db_with_schema: sqlite3.Connection) -> None:
        """New terms are added to the dictionary."""
        db = db_with_schema
        terms = [
            {"source_text": "альфа", "target_text": "alpha", "term_type": "exact", "origin": "import"},
            {"source_text": "бета", "target_text": "beta", "term_type": "exact", "origin": "import"},
        ]

        count = import_terms(db, terms)
        assert count == 2

        all_terms = export_terms(db)
        assert len(all_terms) == 2
        sources = {d["source_text"] for d in all_terms}
        assert sources == {"альфа", "бета"}

    def test_import_merge_replace(self, db_with_schema: sqlite3.Connection) -> None:
        """Conflicting source_text: imported value wins."""
        db = db_with_schema
        # Pre-existing term
        create_dictionary_term(db, "пайтон", "OldValue", term_type="exact", origin="manual")

        # Import with same source_text but different target
        terms = [
            {"source_text": "пайтон", "target_text": "Python", "term_type": "exact", "origin": "import"},
        ]
        count = import_terms(db, terms)
        assert count == 1

        # Verify the imported value won
        exact = get_exact_terms(db)
        assert exact["пайтон"] == "Python"

        # Only one entry for this source_text
        rows = db.execute("SELECT COUNT(*) FROM dictionary WHERE source_text = 'пайтон'").fetchone()
        assert rows[0] == 1
