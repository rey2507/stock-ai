"""News aggregator with cross-provider deduplication and ranking."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import List, Dict, Optional, Tuple

from models.news_model import NewsItem, NewsSnapshot
from providers.news_provider import (
    NewsProvider, get_default_providers, _similarity, _utcnow,
)

log = logging.getLogger(__name__)


# ─── Deduplication Result ──────────────────────────────────────────

@dataclass
class DeduplicationResult:
    """Result of cross-provider deduplication."""
    items: List[NewsItem]
    duplicate_groups: Dict[str, List[str]] = field(default_factory=dict)
    source_counts: Dict[str, int] = field(default_factory=dict)

    @property
    def total_deduped(self) -> int:
        """Total items removed as duplicates."""
        return sum(len(group) - 1 for group in self.duplicate_groups.values())


# ─── Aggregator ────────────────────────────────────────────────────

class NewsAggregator:
    """Fetch, deduplicate, and rank news from multiple providers."""

    # Similarity threshold for considering two articles as duplicates
    SIMILARITY_THRESHOLD = 0.75

    # Time window (hours) for considering articles as same event
    TIME_WINDOW_HOURS = 12

    # Priority sources (lower = higher priority)
    SOURCE_PRIORITY = {
        "Reuters": 1,
        "Economic Times": 2,
        "Moneycontrol": 3,
        "Mint": 4,
        "NSE": 5,
        "RBI": 5,
        "Official": 5,
    }

    def __init__(
        self,
        providers: Optional[Dict[str, NewsProvider]] = None,
        max_age_hours: int = 24,
        limit_per_provider: int = 20,
    ):
        self.providers = providers or get_default_providers()
        self.max_age_hours = max_age_hours
        self.limit_per_provider = limit_per_provider

    def fetch_and_aggregate(
        self,
        query: str = "NIFTY India market",
        limit: int = 50,
    ) -> NewsSnapshot:
        """Fetch from all providers, deduplicate, and rank."""
        fetch_time = _utcnow()
        all_items: List[NewsItem] = []
        source_status: Dict[str, str] = {}
        source_counts: Dict[str, int] = {}

        # Fetch from all providers
        for name, provider in self.providers.items():
            try:
                items = provider.fetch_news(query=query, limit=self.limit_per_provider, max_age_hours=self.max_age_hours)
                all_items.extend(items)
                source_counts[name] = len(items)
                source_status[name] = "LIVE" if items else "EMPTY"
            except Exception as e:
                log.warning(f"Provider {name} failed: {e}")
                source_status[name] = f"ERROR: {str(e)[:50]}"

        # Cross-provider deduplication
        dedup_result = self._cross_provider_deduplicate(all_items)

        # Rank and limit
        ranked = self._rank_items(dedup_result.items, fetch_time)

        snapshot = NewsSnapshot(
            items=ranked[:limit],
            fetch_timestamp=fetch_time,
            source_status=source_status,
            total_fetched=len(all_items),
            total_deduped=dedup_result.total_deduped,
        )

        log.info(
            f"News aggregated: fetched={len(all_items)}, deduped={dedup_result.total_deduped}, "
            f"final={len(snapshot.items)}"
        )
        return snapshot

    def _cross_provider_deduplicate(self, items: List[NewsItem]) -> DeduplicationResult:
        """Remove duplicates across all providers using fuzzy matching."""
        if not items:
            return DeduplicationResult(items=[])

        # Group by similarity
        groups: Dict[str, List[NewsItem]] = {}
        used = set()

        for i, item in enumerate(items):
            if i in used:
                continue

            group_key = item.id
            groups[group_key] = [item]
            used.add(i)

            for j, other in enumerate(items[i+1:], start=i+1):
                if j in used:
                    continue

                # Check title similarity
                sim = _similarity(item.title, other.title)

                # Check time proximity
                time_diff = abs((item.published_at - other.published_at).total_seconds()) / 3600

                if sim >= self.SIMILARITY_THRESHOLD and time_diff <= self.TIME_WINDOW_HOURS:
                    groups[group_key].append(other)
                    used.add(j)

        # Select best item from each group
        deduped: List[NewsItem] = []
        duplicate_groups: Dict[str, List[str]] = {}

        for group_key, group_items in groups.items():
            if len(group_items) > 1:
                duplicate_groups[group_key] = [it.title[:50] for it in group_items]

            best = self._select_best(group_items)
            deduped.append(best)

        return DeduplicationResult(
            items=deduped,
            duplicate_groups=duplicate_groups,
            source_counts={item.source: sum(1 for d in deduped if d.source == item.source) for item in deduped},
        )

    def _select_best(self, items: List[NewsItem]) -> NewsItem:
        """Select best article from duplicate group based on source priority and freshness."""
        if len(items) == 1:
            return items[0]

        def score(item: NewsItem) -> Tuple[int, float]:
            # Priority: lower number = higher priority
            priority = self.SOURCE_PRIORITY.get(item.source, 999)
            # Freshness: more recent = higher score
            age_hours = (_utcnow() - item.published_at).total_seconds() / 3600
            freshness = 1.0 / (1.0 + age_hours)
            return (priority, -freshness)  # Negative for reverse sort

        return min(items, key=score)

    def _rank_items(self, items: List[NewsItem], fetch_time: datetime) -> List[NewsItem]:
        """Rank items by relevance and recency."""
        def rank_score(item: NewsItem) -> Tuple[int, int, float]:
            # 1. Priority by category (NIFTY_50 first)
            category_priority = {
                "NIFTY_50": 0,
                "BANK_NIFTY": 1,
                "RBI": 2,
                "SEBI": 3,
                "FII_DII": 4,
                "USD_INR": 5,
                "CRUDE": 6,
                "BONDS_YIELDS": 7,
                "FED": 8,
                "EARNINGS": 9,
                "GEOPOLITICAL": 10,
                "GOVERNMENT_POLICY": 11,
                "BANKING": 12,
                "IPO": 13,
                "INDIA_MARKET": 14,
            }
            cat_pri = category_priority.get(item.category or "", 99)

            # 2. Priority by source
            source_pri = self.SOURCE_PRIORITY.get(item.source, 999)

            # 3. Freshness (hours since publish, lower is better)
            age_hours = (fetch_time - item.published_at).total_seconds() / 3600

            # 4. Bonus for opening direction
            opening_bonus = 0 if item.is_opening_direction else 1

            return (cat_pri, source_pri, opening_bonus, age_hours)

        return sorted(items, key=rank_score)

    def get_provider_health(self) -> Dict[str, Dict[str, any]]:
        """Get health status of all providers."""
        health = {}
        for name, provider in self.providers.items():
            health[name] = {
                "source_name": provider.source_name(),
                "status": "unknown",
            }
        return health


# ─── Quick fetch function ──────────────────────────────────────────

def fetch_market_news(
    query: str = "NIFTY India market",
    limit: int = 50,
    providers: Optional[Dict[str, NewsProvider]] = None,
) -> NewsSnapshot:
    """Convenience function for one-shot news fetch."""
    aggregator = NewsAggregator(providers=providers)
    return aggregator.fetch_and_aggregate(query=query, limit=limit)