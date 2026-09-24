"""News aggregator: fetch, deduplicate, and sort headlines."""

from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from models.news_model import NewsItem, NewsSnapshot
from providers.news_cache import news_cache
from providers.news_provider import NewsProvider, _title_hash

log = logging.getLogger(__name__)


def deduplicate_news(all_items: List[NewsItem]) -> List[NewsItem]:
    seen: dict[str, NewsItem] = {}
    deduped: List[NewsItem] = []
    for item in all_items:
        key = item.id or _title_hash(item.title, item.source)
        if key not in seen:
            seen[key] = item
            deduped.append(item)
    return deduped


class NewsAggregator:
    """Aggregate news from multiple providers with caching and dedup."""

    def __init__(self, providers: List[NewsProvider], cache_ttl_seconds: int = 900):
        self.providers = providers
        self.cache_ttl = cache_ttl_seconds

    def fetch_all_news(self, max_age_hours: int = 24) -> NewsSnapshot:
        all_items: List[NewsItem] = []
        source_status: dict[str, str] = {}
        now = datetime.now(timezone.utc)

        for provider in self.providers:
            name = provider.source_name()
            try:
                cached = news_cache.get(name)
                if cached:
                    all_items.extend(cached)
                    source_status[name] = "LIVE"
                else:
                    items = provider.fetch_news()
                    news_cache.set(name, items)
                    all_items.extend(items)
                    source_status[name] = "LIVE"
            except Exception as e:
                log.warning(f"News source {name} failed: {e}")
                source_status[name] = "DEGRADED"

        deduped = deduplicate_news(all_items)
        cutoff = now - timedelta(hours=max_age_hours)
        fresh = [item for item in deduped if item.published_at >= cutoff]
        for item in fresh:
            item.status = "LIVE" if (now - item.published_at) <= timedelta(hours=4) else "STALE"

        fresh.sort(key=lambda x: x.published_at, reverse=True)
        return NewsSnapshot(
            items=fresh[:15],
            fetch_timestamp=now,
            source_status=source_status,
        )


def fetch_news_snapshot(providers: List[NewsProvider], max_age_hours: int = 24) -> NewsSnapshot:
    aggregator = NewsAggregator(providers=providers)
    return aggregator.fetch_all_news(max_age_hours=max_age_hours)
