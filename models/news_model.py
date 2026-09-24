"""Normalized news data model for Market Intelligence dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class NewsItem:
    """Normalized news article."""
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


@dataclass
class NewsSnapshot:
    """Aggregated news snapshot."""
    items: list[NewsItem] = field(default_factory=list)
    fetch_timestamp: Optional[datetime] = None
    source_status: dict[str, str] = field(default_factory=dict)
