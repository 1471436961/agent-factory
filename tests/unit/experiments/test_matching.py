"""Shared bounded matcher semantics for fixture validation and scoring."""

from __future__ import annotations

import pytest

from experiments.contracts import MatcherKind, TextMatcher
from experiments.matching import MatcherTimeoutError, matches_text


def test_exact_matching_honors_case_sensitivity() -> None:
    insensitive = TextMatcher(kind=MatcherKind.EXACT, pattern="risk:")
    sensitive = TextMatcher(
        kind=MatcherKind.EXACT,
        pattern="Risk:",
        case_sensitive=True,
    )

    assert matches_text(insensitive, "Current RISK: monitor retries")
    assert matches_text(sensitive, "Risk: monitor retries")
    assert not matches_text(sensitive, "RISK: monitor retries")


def test_regex_matching_honors_case_sensitivity() -> None:
    insensitive = TextMatcher(kind=MatcherKind.REGEX, pattern=r"at most \d+ events")
    sensitive = TextMatcher(
        kind=MatcherKind.REGEX,
        pattern=r"^In practice",
        case_sensitive=True,
    )

    assert matches_text(insensitive, "AT MOST 80 EVENTS")
    assert matches_text(sensitive, "In practice, send a batch.")
    assert not matches_text(sensitive, "in practice, send a batch.")


def test_regex_timeout_is_not_silently_treated_as_no_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise TimeoutError

    monkeypatch.setattr("experiments.matching.regex.search", raise_timeout)
    matcher = TextMatcher(kind=MatcherKind.REGEX, pattern="current")

    with pytest.raises(MatcherTimeoutError, match="exceeded timeout"):
        matches_text(matcher, "current")
