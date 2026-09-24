"""News provider implementations for Indian-market pre-market sources.

Providers:
- ReutersNewsProvider: Reuters India market news
- MoneycontrolNewsProvider: Moneycontrol market news
- EconomicTimesNewsProvider: Economic Times market news
- MintNewsProvider: Mint market commentary
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from typing import List, Optional

import requests

from models.news_model import NewsItem, NewsSnapshot

log = logging.getLogger(__name__)


# ─── Utilities ──────────────────────────────────────────────────────

NIFTY_50_COMPANIES = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC",
    "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "ASIANPAINT", "AXISBANK",
    "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "BAJFINANCE", "HCLTECH",
    "WIPRO", "NESTLEIND", "POWERGRID", "NTPC", "TATAMOTORS", "TECHM",
    "BAJAJFINSV", "ADANIENT", "JSWSTEEL", "ONGC", "COALINDIA", "GRASIM",
    "HINDALCO", "CIPLA", "DRREDDY", "BRITANNIA", "EICHERMOT", "DIVISLAB",
    "APOLLOHOSP", "HEROMOTOCO", "TATASTEEL", "SBILIFE", "HDFCLIFE",
    "INDUSINDBK", "BPCL", "UPL", "TATACONSUM", "M&M", "SHREECEM",
]


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


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


class ProviderError(Exception):
    pass


# ─── Base Provider ──────────────────────────────────────────────────

class NewsProvider(ABC):
    """Abstract base for news sources."""

    @abstractmethod
    def source_name(self) -> str:
        """Return provider name."""
        pass

    @abstractmethod
    def fetch_news(
        self, query: str = "NIFTY India market", limit: int = 20, max_age_hours: int = 24
    ) -> List[NewsItem]:
        """Fetch recent headlines. Returns empty list on failure."""
        pass

    def deduplicate(self, items: List[NewsItem]) -> List[NewsItem]:
        """Remove duplicate/syndicated articles within this provider."""
        seen = set()
        deduped = []
        for item in items:
            key = item.id or _title_hash(item.title, item.source)
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        return deduped

    def _make_item(
        self,
        title: str,
        source: str,
        url: str,
        published_at: Optional[datetime],
        content_snippet: Optional[str] = None,
        source_api: Optional[str] = None,
        raw_data: Optional[dict] = None,
    ) -> Optional[NewsItem]:
        now = _utcnow()
        pub = _to_utc(published_at)
        title_hash = _title_hash(title, source)
        age_hours = (now - pub).total_seconds() / 3600 if pub else 0
        status = "LIVE" if age_hours <= 4 else ("STALE" if age_hours <= 24 else "UNAVAILABLE")

        reported_exp = self._extract_opening_expectation(title)
        category = self._categorize(title)
        entity = self._extract_entity(title)
        sector = self._extract_sector(title)

        return NewsItem(
            id=title_hash,
            title=title,
            source=source,
            url=url,
            published_at=pub,
            fetched_at=now,
            content_snippet=content_snippet[:300] if content_snippet else None,
            summary=content_snippet[:300] if content_snippet else None,
            category=category,
            market_relevance="NIFTY_50" if "nifty" in title.lower() or "sensex" in title.lower() else "INDIA_MARKET",
            entity=entity,
            sector=sector,
            status=status,
            source_api=source_api or self.source_name(),
            raw_data=raw_data,
            is_opening_direction=reported_exp is not None,
            reported_expectation=reported_exp,
            time_horizon="OVERNIGHT" if reported_exp else "INTRADAY",
            source_type="NEWS",
        )

    def _extract_opening_expectation(self, title: str) -> Optional[str]:
        """Detect opening-direction statements."""
        lower_title = title.lower()

        if any(p in lower_title for p in ["open lower", "open down", "open negative", "seen opening lower"]):
            return "LOWER_OPEN"
        elif any(p in lower_title for p in ["open higher", "open up", "open positive", "seen opening higher"]):
            return "HIGHER_OPEN"
        elif any(p in lower_title for p in ["open flat", "open stable", "open sideways", "flat open"]):
            return "STABLE_OPEN"
        elif any(p in lower_title for p in ["mixed open", "cautious open", "uncertain open"]):
            return "MIXED_OPEN"

        return None

    def _categorize(self, title: str) -> str:
        """Assign category based on headline."""
        lower = title.lower()

        if any(w in lower for w in ["nifty", "sensex", "nifty 50", "nifty50"]):
            return "NIFTY_50"
        elif any(w in lower for w in ["bank nifty", "banknifty"]):
            return "BANK_NIFTY"
        elif any(w in lower for w in ["rbi", "reserve bank", "rate", "repo", "policy"]):
            return "RBI"
        elif "sebi" in lower or "regulator" in lower:
            return "SEBI"
        elif any(w in lower for w in ["fii", "dii", "foreign investor", "domestic investor", "fpi"]):
            return "FII_DII"
        elif any(w in lower for w in ["crude", "oil", "brent", "wti", "energy"]):
            return "CRUDE"
        elif any(w in lower for w in ["dollar", "usd", "inr", "rupee", "currency", "forex"]):
            return "USD_INR"
        elif any(w in lower for w in ["fed", "federal reserve", "fomc", "us rate"]):
            return "FED"
        elif any(w in lower for w in ["yield", "bond", "treasury", "gsec", "government bond"]):
            return "BONDS_YIELDS"
        elif any(w in lower for w in ["geo", "war", "tension", "conflict", "border"]):
            return "GEOPOLITICAL"
        elif any(w in lower for w in ["earnings", "results", "q1", "q2", "q3", "q4", "quarter"]):
            return "EARNINGS"
        elif any(w in lower for w in ["ipo", "listing", "public issue"]):
            return "IPO"
        elif any(w in lower for w in ["government", "policy", "budget", "fiscal", "minister"]):
            return "GOVERNMENT_POLICY"
        elif any(w in lower for w in ["bank", "banking", "lender", "nbfc"]):
            return "BANKING"

        return "INDIA_MARKET"

    def _extract_entity(self, title: str) -> Optional[str]:
        """Extract company/entity name if mentioned."""
        for company in NIFTY_50_COMPANIES:
            if company in title.upper():
                return company
        return None

    def _extract_sector(self, title: str) -> Optional[str]:
        """Extract sector if identifiable."""
        lower = title.lower()
        sector_map = {
            "bank": "BANKING",
            "it": "IT",
            "tech": "IT",
            "pharma": "PHARMA",
            "auto": "AUTO",
            "auto": "AUTO",
            "cement": "CEMENT",
            "steel": "METALS",
            "metal": "METALS",
            "oil": "OIL_GAS",
            "gas": "OIL_GAS",
            "power": "POWER",
            "energy": "POWER",
            "telecom": "TELECOM",
            "fmcg": "FMCG",
            "consumer": "FMCG",
            "realty": "REAL_ESTATE",
            "real estate": "REAL_ESTATE",
            "infra": "INFRASTRUCTURE",
            "chemical": "CHEMICALS",
        }
        for keyword, sector in sector_map.items():
            if keyword in lower:
                return sector
        return None


# ─── RSS Provider Base ──────────────────────────────────────────────

class RSSNewsProvider(NewsProvider):
    """Base class for RSS-based news providers."""

    def __init__(self, rss_urls: List[str], source_name: str):
        self.rss_urls = rss_urls
        self._source_name = source_name

    def source_name(self) -> str:
        return self._source_name

    def fetch_news(
        self, query: str = "NIFTY India market", limit: int = 20, max_age_hours: int = 24
    ) -> List[NewsItem]:
        items: List[NewsItem] = []
        cutoff = _utcnow() - timedelta(hours=max_age_hours)

        for url in self.rss_urls:
            try:
                resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    log.warning(f"RSS fetch failed for {url}: {resp.status_code}")
                    continue

                items.extend(self._parse_rss(resp.text, cutoff))
            except Exception as e:
                log.warning(f"RSS provider {self._source_name} failed for {url}: {e}")

        return self.deduplicate(items[:limit])

    def _parse_rss(self, xml_text: str, cutoff: datetime) -> List[NewsItem]:
        """Parse RSS XML into NewsItem list."""
        items: List[NewsItem] = []

        # Try feedparser first (if available), fallback to xml.etree
        try:
            import feedparser
            parsed = feedparser.parse(xml_text)
            for entry in parsed.entries:
                item = self._parse_entry(entry, cutoff)
                if item:
                    items.append(item)
        except ImportError:
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_text)
                for item_elem in root.iter("item"):
                    item = self._parse_item_element(item_elem, cutoff)
                    if item:
                        items.append(item)
            except Exception as e:
                log.warning(f"XML parse failed: {e}")

        return items

    def _parse_entry(self, entry, cutoff: datetime) -> Optional[NewsItem]:
        """Parse feedparser entry."""
        title = getattr(entry, "title", "") or ""
        if not title:
            return None

        link = getattr(entry, "link", "") or ""
        published = getattr(entry, "published_parsed", None)
        pub_dt = None
        if published:
            try:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
            except Exception:
                pub_dt = None

        if pub_dt and pub_dt < cutoff:
            return None

        snippet = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""

        return self._make_item(
            title=title,
            source=self._source_name,
            url=link,
            published_at=pub_dt,
            content_snippet=snippet[:300] if snippet else None,
            raw_data={"title": title, "link": link},
        )

    def _parse_item_element(self, elem, cutoff: datetime) -> Optional[NewsItem]:
        """Parse xml.etree Element."""
        title = elem.findtext("title") or ""
        if not title:
            return None

        link = elem.findtext("link") or ""
        pub_text = elem.findtext("pubDate") or ""
        pub_dt = None
        if pub_text:
            try:
                pub_dt = datetime.strptime(pub_text, "%a, %d %b %Y %H:%M:%S %z")
            except Exception:
                pub_dt = None

        if pub_dt and pub_dt < cutoff:
            return None

        desc = elem.findtext("description") or ""

        return self._make_item(
            title=title,
            source=self._source_name,
            url=link,
            published_at=pub_dt,
            content_snippet=desc[:300] if desc else None,
            raw_data={"title": title, "link": link},
        )


# ─── Reuters Provider ──────────────────────────────────────────────

class ReutersNewsProvider(NewsProvider):
    """Reuters news for India market."""

    def __init__(self):
        self._source_name = "Reuters"
        self.rss_urls = [
            "https://www.reuters.com/world/india/rss",
            "https://www.reuters.com/markets/rss",
            "https://www.reuters.com/business/finance/rss",
        ]

    def source_name(self) -> str:
        return self._source_name

    def fetch_news(
        self, query: str = "NIFTY India market", limit: int = 20, max_age_hours: int = 24
    ) -> List[NewsItem]:
        items: List[NewsItem] = []
        cutoff = _utcnow() - timedelta(hours=max_age_hours)

        for url in self.rss_urls:
            try:
                resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    continue
                items.extend(self._parse_reuters_rss(resp.text, cutoff))
            except Exception as e:
                log.warning(f"Reuters RSS fetch failed for {url}: {e}")

        return self.deduplicate(items[:limit])

    def _parse_reuters_rss(self, xml_text: str, cutoff: datetime) -> List[NewsItem]:
        items: List[NewsItem] = []

        try:
            import feedparser
            parsed = feedparser.parse(xml_text)
            for entry in parsed.entries:
                item = self._parse_entry(entry, cutoff)
                if item:
                    items.append(item)
        except ImportError:
            import xml.etree.ElementTree as ET
            try:
                root = ET.fromstring(xml_text)
                for item_elem in root.iter("item"):
                    item = self._parse_item_element(item_elem, cutoff)
                    if item:
                        items.append(item)
            except Exception as e:
                log.warning(f"Reuters XML parse failed: {e}")

        return items

    def _parse_entry(self, entry, cutoff: datetime) -> Optional[NewsItem]:
        title = getattr(entry, "title", "") or ""
        if not title:
            return None

        link = getattr(entry, "link", "") or ""
        published = getattr(entry, "published_parsed", None)
        pub_dt = None
        if published:
            try:
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc)
            except Exception:
                pub_dt = None

        if pub_dt and pub_dt < cutoff:
            return None

        snippet = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""

        # Override source to always be "Reuters" even if RSS feed name differs
        return self._make_item(
            title=title,
            source="Reuters",
            url=link,
            published_at=pub_dt,
            content_snippet=snippet[:300] if snippet else None,
            raw_data={"title": title, "link": link},
        )

    def _parse_item_element(self, elem, cutoff: datetime) -> Optional[NewsItem]:
        title = elem.findtext("title") or ""
        if not title:
            return None

        link = elem.findtext("link") or ""
        pub_text = elem.findtext("pubDate") or ""
        pub_dt = None
        if pub_text:
            try:
                pub_dt = datetime.strptime(pub_text, "%a, %d %b %Y %H:%M:%S %z")
            except Exception:
                pub_dt = None

        if pub_dt and pub_dt < cutoff:
            return None

        desc = elem.findtext("description") or ""

        return self._make_item(
            title=title,
            source="Reuters",
            url=link,
            published_at=pub_dt,
            content_snippet=desc[:300] if desc else None,
            raw_data={"title": title, "link": link},
        )


# ─── Moneycontrol Provider ──────────────────────────────────────────

class MoneycontrolNewsProvider(RSSNewsProvider):
    """Moneycontrol market news via RSS."""

    def __init__(self):
        super().__init__(
            rss_urls=[
                "https://www.moneycontrol.com/rss/marketnews.xml",
                "https://www.moneycontrol.com/rss/business.xml",
                "https://www.moneycontrol.com/rss/stockmarket.xml",
            ],
            source_name="Moneycontrol",
        )


# ─── Economic Times Provider ────────────────────────────────────────

class EconomicTimesNewsProvider(RSSNewsProvider):
    """Economic Times market news via RSS."""

    def __init__(self):
        super().__init__(
            rss_urls=[
                "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
                "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146843.cms",
                "https://economictimes.indiatimes.com/markets/rssfeeds/13358272.cms",
                "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
            ],
            source_name="Economic Times",
        )


# ─── Mint Provider ──────────────────────────────────────────────────

class MintNewsProvider(RSSNewsProvider):
    """Mint market commentary via RSS."""

    def __init__(self):
        super().__init__(
            rss_urls=[
                "https://www.livemint.com/rss/markets",
                "https://www.livemint.com/rss/companies",
                "https://www.livemint.com/rss/money",
            ],
            source_name="Mint",
        )


# ─── Official Events Provider (NSE/BSE/RBI) ─────────────────────────

class OfficialEventsProvider(NewsProvider):
    """NSE/BSE corporate announcements, RBI/SEBI circulars."""

    def __init__(self):
        self._source_name = "Official"

    def source_name(self) -> str:
        return self._source_name

    def fetch_news(
        self, query: str = "NIFTY India market", limit: int = 20, max_age_hours: int = 24
    ) -> List[NewsItem]:
        items: List[NewsItem] = []

        # NSE corporate announcements RSS
        nse_rss = "https://www.nseindia.com/api/corporate-announcements?index=equities"
        try:
            resp = requests.get(
                nse_rss, timeout=10,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
            )
            if resp.status_code == 200:
                data = resp.json()
                for ann in data[:limit]:
                    title = ann.get("subject", "") or ann.get("description", "")
                    if not title:
                        continue
                    pub_dt = None
                    dt_str = ann.get("disseminationDate") or ann.get("date")
                    if dt_str:
                        try:
                            pub_dt = datetime.strptime(dt_str, "%d-%b-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
                        except Exception:
                            pass
                    items.append(self._make_item(
                        title=title,
                        source="NSE",
                        url=ann.get("pdfUrl", "") or "",
                        published_at=pub_dt,
                        content_snippet=ann.get("description", "")[:300],
                        raw_data=ann,
                    ))
        except Exception as e:
            log.warning(f"NSE announcements failed: {e}")

        # RBI circulars
        rbi_rss = "https://rbi.org.in/scripts/NotificationUser.aspx?Type=PressRelease"
        try:
            resp = requests.get(rbi_rss, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                import feedparser
                parsed = feedparser.parse(resp.text)
                for entry in parsed.entries[:5]:
                    title = getattr(entry, "title", "") or ""
                    if any(w in title.lower() for w in ["rate", "policy", "repo", "rbi", "reserve bank"]):
                        link = getattr(entry, "link", "") or ""
                        pub_dt = None
                        if hasattr(entry, "published_parsed") and entry.published_parsed:
                            try:
                                pub_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                            except Exception:
                                pass
                        items.append(self._make_item(
                            title=title,
                            source="RBI",
                            url=link,
                            published_at=pub_dt,
                            content_snippet=getattr(entry, "summary", "")[:300],
                            raw_data={"title": title},
                        ))
        except Exception as e:
            log.warning(f"RBI RSS failed: {e}")

        return self.deduplicate(items[:limit])


# ─── Factory ────────────────────────────────────────────────────────

def get_default_providers() -> dict[str, NewsProvider]:
    """Get default news provider instances."""
    return {
        "reuters": ReutersNewsProvider(),
        "moneycontrol": MoneycontrolNewsProvider(),
        "et": EconomicTimesNewsProvider(),
        "mint": MintNewsProvider(),
        "official": OfficialEventsProvider(),
    }