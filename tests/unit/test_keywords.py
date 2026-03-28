"""Unit tests for src.context.keywords -- keyword extraction module."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.context.keywords import (
    IMPORTANT_SHORT,
    STOP_WORDS_EN,
    STOP_WORDS_UK,
    extract_keywords,
    lemmatize,
)

if TYPE_CHECKING:
    from tests.conftest import Timer

# =============================================================================
# Lemmatization
# =============================================================================


class TestLemmatization:
    def test_lemmatize_ukrainian_noun(self) -> None:
        # "zamku" -> "zamok"
        assert lemmatize("\u0437\u0430\u043c\u043a\u0443") == "\u0437\u0430\u043c\u043e\u043a"

    def test_lemmatize_ukrainian_adjective(self) -> None:
        # "vkhidnykh" -> "vkhidnyy"
        result = lemmatize("\u0432\u0445\u0456\u0434\u043d\u0438\u0445")
        assert result == "\u0432\u0445\u0456\u0434\u043d\u0438\u0439"

    def test_lemmatize_english_passthrough(self) -> None:
        assert lemmatize("deploy") == "deploy"


# =============================================================================
# Stop words filtering
# =============================================================================


class TestStopWords:
    def test_stop_words_uk_filtered(self) -> None:
        # "ya v na shcho" -> empty
        result = extract_keywords("\u044f \u0432 \u043d\u0430 \u0449\u043e")
        assert result == []

    def test_stop_words_en_filtered(self) -> None:
        result = extract_keywords("the is and for")
        assert result == []

    def test_stop_words_greetings_filtered(self) -> None:
        # "pryvit davay nu" -> empty
        result = extract_keywords("\u043f\u0440\u0438\u0432\u0456\u0442 \u0434\u0430\u0432\u0430\u0439 \u043d\u0443")
        assert result == []

    def test_stop_words_sets_not_empty(self) -> None:
        assert len(STOP_WORDS_UK) > 0
        assert len(STOP_WORDS_EN) > 0


# =============================================================================
# 2-letter abbreviations
# =============================================================================


class TestImportantShort:
    def test_important_short_pr_preserved(self) -> None:
        result = extract_keywords("PR review")
        assert "pr" in result

    def test_important_short_db_preserved(self) -> None:
        result = extract_keywords("DB migration")
        assert "db" in result

    def test_important_short_ci_cd_preserved(self) -> None:
        result = extract_keywords("CI CD pipeline")
        assert "ci" in result
        assert "cd" in result

    def test_important_short_set_contents(self) -> None:
        assert "pr" in IMPORTANT_SHORT
        assert "db" in IMPORTANT_SHORT
        assert "ai" in IMPORTANT_SHORT
        # Ukrainian "tz" abbreviation
        assert "\u0442\u0437" in IMPORTANT_SHORT


# =============================================================================
# Bigram generation
# =============================================================================


class TestBigrams:
    def test_bigrams_generated(self) -> None:
        result = extract_keywords("pull request review")
        assert "pull request" in result

    def test_bigrams_with_lemmas(self) -> None:
        # "vkhidnykh zamkiv" -> lemmatized bigram "vkhidnyy zamok"
        text = "\u0432\u0445\u0456\u0434\u043d\u0438\u0445 \u0437\u0430\u043c\u043a\u0456\u0432"
        result = extract_keywords(text)
        expected_bigram = "\u0432\u0445\u0456\u0434\u043d\u0438\u0439 \u0437\u0430\u043c\u043e\u043a"
        assert expected_bigram in result


# =============================================================================
# Mixed text
# =============================================================================


class TestMixedText:
    def test_mixed_uk_en_text(self) -> None:
        # "zadeployity na prod server" -> "na" is stop word, rest extracted
        text = (
            "\u0437\u0430\u0434\u0435\u043f\u043b\u043e\u0457\u0442\u0438 "
            "\u043d\u0430 "
            "\u043f\u0440\u043e\u0434 "
            "\u0441\u0435\u0440\u0432\u0435\u0440"
        )
        result = extract_keywords(text)
        assert len(result) > 0
        # "server" in Ukrainian
        assert "\u0441\u0435\u0440\u0432\u0435\u0440" in result

    def test_extract_keywords_basic(self) -> None:
        result = extract_keywords("review pull request deploy")
        assert "review" in result
        assert "pull" in result
        assert "request" in result
        assert "deploy" in result


# =============================================================================
# Edge cases
# =============================================================================


class TestEdgeCases:
    def test_empty_text(self) -> None:
        assert extract_keywords("") == []

    def test_single_word(self) -> None:
        zamok = "\u0437\u0430\u043c\u043e\u043a"
        result = extract_keywords(zamok)
        assert result == [zamok]

    def test_all_stop_words(self) -> None:
        # "ya na tse nu" -> []
        assert extract_keywords("\u044f \u043d\u0430 \u0446\u0435 \u043d\u0443") == []

    def test_max_keywords_limit(self) -> None:
        # 20+ unique meaningful words
        text = (
            "python django flask fastapi celery redis postgres docker kubernetes "
            "nginx grafana prometheus terraform ansible jenkins github gitlab "
            "mongodb elasticsearch kibana logstash"
        )
        result = extract_keywords(text, max_keywords=5)
        assert len(result) == 5

    def test_duplicate_words(self) -> None:
        zamok = "\u0437\u0430\u043c\u043e\u043a"
        result = extract_keywords(f"{zamok} {zamok} {zamok}")
        assert result == [zamok]


# =============================================================================
# Performance
# =============================================================================


class TestPerformance:
    def test_performance_under_15ms(self, timer: Timer) -> None:
        # 15 Ukrainian words about deploying a service to production
        text = (
            "\u0437\u0430\u0434\u0435\u043f\u043b\u043e\u0457\u0442\u0438 "
            "\u043d\u043e\u0432\u0443 "
            "\u0432\u0435\u0440\u0441\u0456\u044e "
            "\u0441\u0435\u0440\u0432\u0456\u0441\u0443 "
            "\u043d\u0430 "
            "\u043f\u0440\u043e\u0434\u0430\u043a\u0448\u043d "
            "\u0441\u0435\u0440\u0432\u0435\u0440 "
            "\u0447\u0435\u0440\u0435\u0437 "
            "\u043f\u0430\u0439\u043f\u043b\u0430\u0439\u043d "
            "\u0431\u0435\u0437\u043f\u0435\u0440\u0435\u0440\u0432\u043d\u043e\u0457 "
            "\u0456\u043d\u0442\u0435\u0433\u0440\u0430\u0446\u0456\u0457 "
            "\u0442\u0430 "
            "\u0434\u043e\u0441\u0442\u0430\u0432\u043a\u0438 "
            "\u043a\u043e\u0434\u0443"
        )
        with timer("extract_keywords"):
            extract_keywords(text)
        timer.assert_under_ms("extract_keywords", 15.0)
