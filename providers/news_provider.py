"""News provider implementations.

Providers:
- NewsAPIProvider: NewsAPI.org
- RSSNewsProvider: RSS feeds (Economic Times, Moneycontrol, Business Standard)
- FinnhubNewsProvider: Finnhub news API
"""

from __future__ import annotations

import logging
import time
import hashlib
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from models.news_model import NewsItem, NewsSnapshot

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _title_hash(title: str, source: str) -> str:
    normalized = re.sub(r"\s+", " ", (title or "").strip().lower())
    return hashlib.md5(f"{normalized}|{source}".encode()).hexdigest()


class NewsProvider(ABC):
    """Abstract base for news providers."""

    @abstractmethod
    def source_name(self) -> str:
        """Return provider name."""
        pass

    @abstractmethod
    def fetch_news(self, query: str = "NIFTY India market") -> List[NewsItem]:
        """Fetch recent headlines. Returns empty list on failure."""
        pass

    def _make_item(
        self,
        title: str,
        source: str,
        url: str,
        published_at: Optional[datetime],
        content_snippet: Optional[str] = None,
        source_api: Optional[str] = None,
        raw_data: Optional[dict] = None,
    ) -> NewsItem:
        now = _utcnow()
        pub = _to_utc(published_at)
        title_hash = _title_hash(title, source)
        status = "LIVE" if (now - pub) <= timedelta(hours=4) else "STALE"
        return NewsItem(
            id=title_hash,
            title=title,
            source=source,
            url=url,
            published_at=pub,
            fetched_at=now,
            content_snippet=content_snippet,
            status=status,
            source_api=source_api or self.source_name(),
            raw_data=raw_data,
        )


class NewsAPIProvider(NewsProvider):
    """NewsAPI.org provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://newsapi.org/v2/everything"

    def source_name(self) -> str:
        return "NewsAPI"

    def fetch_news(self, query: str = "NIFTY India market") -> List[NewsItem]:
        items: List[NewsItem] = []
        try:
            import requests
            params = {
                "q": query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 10,
                "apiKey": self.api_key,
            }
            resp = requests.get(self.base_url, params=params, timeout=10)
            if resp.status_code != 200:
                log.warning(f"NewsAPI failed: {resp.status_code} {resp.text[:200]}")
                return items
            data = resp.json()
            articles = data.get("articles", [])
            for art in articles:
                title = art.get("title") or ""
                if not title:
                    continue
                published_at = art.get("publishedAt")
                pub_dt = None
                if published_at:
                    try:
                        pub_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                    except Exception:
                        pub_dt = None
                url = art.get("url") or ""
                snippet = art.get("description") or art.get("content") or ""
                source_name = (art.get("source") or {}).get("name") or "NewsAPI"
                items.append(self._make_item(
                    title=title,
                    source=source_name,
                    url=url,
                    published_at=pub_dt,
                    content_snippet=snippet[:300] if snippet else None,
                    raw_data=art,
                ))
        except Exception as e:
            log.warning(f"NewsAPI fetch failed: {e}")
        return items


class RSSNewsProvider(NewsProvider):
    """RSS feed provider."""

    def __init__(self, rss_urls: List[str]):
        self.rss_urls = rss_urls

    def source_name(self) -> str:
        return "RSS"

    def fetch_news(self, query: str = "NIFTY India market") -> List[NewsItem]:
        items: List[NewsItem] = []
        try:
            import requests
            try:
                import feedparser
            except ImportError:
                feedparser = None
            for url in self.rss_urls:
                try:
                    resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                    if resp.status_code != 200:
                        continue
                    source_label = self._source_label(url)
                    if feedparser:
                        parsed = feedparser.parse(resp.text)
                        for entry in parsed.entries:
                            title = getattr(entry, "title", "") or ""
                            link = getattr(entry, "link", "") or ""
                            published = getattr(entry, "published_parsed", None)
                            pub_dt = None
                            if published:
                                try:
                                    pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
                                except Exception:
                                    pub_dt = None
                            snippet = getattr(entry, "summary", "") or ""
                            items.append(self._make_item(
                                title=title,
                                source=source_label,
                                url=link,
                                published_at=pub_dt,
                                content_snippet=snippet[:300] if snippet else None,
                                raw_data={"link": link, "title": title},
                            ))
                    else:
                        import xml.etree.ElementTree as ET
                        root = ET.fromstring(resp.text)
                        for item in root.iter("item"):
                            title = item.findtext("title") or ""
                            link = item.findtext("link") or ""
                            pub_text = item.findtext("pubDate") or ""
                            pub_dt = None
                            if pub_text:
                                try:
                                    pub_dt = datetime.strptime(pub_text, "%a, %d %b %Y %H:%M:%S %z")
                                except Exception:
                                    pub_dt = None
                            desc = item.findtext("description") or ""
                            items.append(self._make_item(
                                title=title,
                                source=source_label,
                                url=link,
                                published_at=pub_dt,
                                content_snippet=desc[:300] if desc else None,
                                raw_data={"link": link, "title": title},
                            ))
                except Exception as e:
                    log.warning(f"RSS fetch failed for {url}: {e}")
        except Exception as e:
            log.warning(f"RSS provider failed: {e}")
        return items

    def _source_label(self, url: str) -> str:
        url = url.lower()
        if "economictimes" in url or "indiatimes" in url:
            return "Economic Times"
        if "moneycontrol" in url:
            return "Moneycontrol"
        if "business-standard" in url or "businessstandard" in url:
            return "Business Standard"
        if "livemint" in url:
            return "Livemint"
        return "RSS News"


class FinnhubNewsProvider(NewsProvider):
    """Finnhub news provider."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://finnhub.io/api/v1/news"

    def source_name(self) -> str:
        return "Finnhub"

    def fetch_news(self, query: str = "NIFTY India market") -> List[NewsItem]:
        items: List[NewsItem] = []
        try:
            import requests
            params = {
                "category": "general",
                "token": self.api_key,
            }
            resp = requests.get(self.base_url, params=params, timeout=10)
            if resp.status_code != 200:
                log.warning(f"Finnhub failed: {resp.status_code} {resp.text[:200]}")
                return items
            data = resp.json()
            if not isinstance(data, list):
                return items
            for art in data[:10]:
                title = art.get("headline") or art.get("title") or ""
                if not title:
                    continue
                pub_dt = None
                ts = art.get("datetime")
                if ts:
                    try:
                        pub_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    except Exception:
                        pub_dt = None
                url = art.get("url") or ""
                snippet = art.get("summary") or art.get("description") or ""
                source_name = art.get("source") or "Finnhub"
                items.append(self._make_item(
                    title=title,
                    source=source_name,
                    url=url,
                    published_at=pub_dt,
                    content_snippet=snippet[:300] if snippet else None,
                    raw_data=art,
                ))
        except Exception as e:
            log.warning(f"Finnhub fetch failed: {e}")
        return items
