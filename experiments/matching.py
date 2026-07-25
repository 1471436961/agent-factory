"""Deterministic, bounded text matching shared by fixtures and scoring."""

from __future__ import annotations

import regex

from experiments.contracts import MatcherKind, TextMatcher

MATCH_TIMEOUT_SECONDS = 0.1


class MatcherTimeoutError(RuntimeError):
    """A pre-registered regular expression exceeded its time budget."""


def matches_text(matcher: TextMatcher, text: str) -> bool:
    """Evaluate one reviewed matcher without exposing unbounded regex work."""

    if matcher.kind is MatcherKind.EXACT:
        if matcher.case_sensitive:
            return matcher.pattern in text
        return matcher.pattern.casefold() in text.casefold()
    flags = 0 if matcher.case_sensitive else regex.IGNORECASE
    try:
        return (
            regex.search(
                matcher.pattern,
                text,
                flags=flags,
                timeout=MATCH_TIMEOUT_SECONDS,
            )
            is not None
        )
    except TimeoutError as exc:
        raise MatcherTimeoutError("matcher evaluation exceeded timeout") from exc
