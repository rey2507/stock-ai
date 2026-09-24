"""Normalized news data model for Market Intelligence dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class NewsItem:
    """Normalized news article - Phase 1 + Phase 1A fields."""

    # Core (Phase 1)
    id: str
    title: str
    source: str
    url: str
    published_at: datetime
    fetched_at: datetime
    content_snippet: Optional[str] = None
    category: Optional[str] = None
    status: str = "LIVE"
    source_api: str = "unknown"
    raw_data: Optional[dict] = None

    # Phase 1A additions
    summary: Optional[str] = None
    market_relevance: str = "INDIA_MARKET"
    entity: Optional[str] = None
    sector: Optional[str] = None
    is_opening_direction: bool = False
    reported_expectation: Optional[str] = None
    time_horizon: str = "INTRADAY"
    source_type: str = "NEWS"


@dataclass
class NewsSnapshot:
    """Aggregated news snapshot."""
    items: list[NewsItem] = field(default_factory=list)
    fetch_timestamp: Optional[datetime] = None
    source_status: dict[str, str] = field(default_factory=dict)
    total_fetched: int = 0
    total_deduped: int = 0