"""Source Authority Provider abstractions and neutral local implementations.

Provides pluggable publisher authority evaluation without external network calls
or magic domain string parsing.
"""

from __future__ import annotations

from typing import Protocol


class SourceAuthorityProvider(Protocol):
    """Protocol for evaluating publisher authority."""

    def get_authority_score(self, publisher: str, source_url: str | None = None) -> float:
        """Return deterministic authority score in [0.0, 100.0]."""
        ...


class NullAuthorityProvider:
    """Default neutral authority provider returning a baseline 50.0 score."""

    def get_authority_score(self, publisher: str, source_url: str | None = None) -> float:
        return 50.0


class ManualAuthorityProvider:
    """Explicitly seeded deterministic publisher authority provider."""

    def __init__(self, seeded_scores: dict[str, float] | None = None) -> None:
        self._scores = {k.lower(): v for k, v in (seeded_scores or {}).items()}

    def get_authority_score(self, publisher: str, source_url: str | None = None) -> float:
        pub_clean = publisher.strip().lower()
        if pub_clean in self._scores:
            return self._scores[pub_clean]
        return 50.0
