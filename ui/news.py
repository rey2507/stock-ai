"""Pre-market news UI rendering - compact, expandable cards."""

from __future__ import annotations

import streamlit as st
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from models.news_model import NewsItem, NewsSnapshot


# ─── Color / Style Constants ───────────────────────────────────────

CATEGORY_COLORS = {
    "NIFTY_50": "#1f77b4",
    "BANK_NIFTY": "#2ca02c",
    "RBI": "#d62728",
    "SEBI": "#ff7f0e",
    "FII_DII": "#9467bd",
    "USD_INR": "#8c564b",
    "CRUDE": "#e377c2",
    "BONDS_YIELDS": "#7f7f7f",
    "FED": "#bcbd22",
    "EARNINGS": "#17becf",
    "GEOPOLITICAL": "#ff9896",
    "GOVERNMENT_POLICY": "#c5b0d5",
    "BANKING": "#c49c94",
    "IPO": "#f7b6d2",
    "INDIA_MARKET": "#c7c7c7",
}

STATUS_COLORS = {
    "LIVE": "#22c55e",
    "STALE": "#f59e0b",
    "UNAVAILABLE": "#ef4444",
    "EMPTY": "#6b7280",
    "ERROR": "#ef4444",
}

OPENING_EXPECTATION_ICONS = {
    "HIGHER_OPEN": "▲",
    "LOWER_OPEN": "▼",
    "STABLE_OPEN": "■",
    "MIXED_OPEN": "◆",
}

OPENING_EXPECTATION_LABELS = {
    "HIGHER_OPEN": "Higher Open",
    "LOWER_OPEN": "Lower Open",
    "STABLE_OPEN": "Flat/Stable",
    "MIXED_OPEN": "Mixed/Cautious",
}


# ─── Helpers ────────────────────────────────────────────────────────

def _age_str(published_at: datetime) -> str:
    """Human-readable age string."""
    now = datetime.now(timezone.utc)
    pub = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    diff = now - pub
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    elif seconds < 86400:
        return f"{int(seconds / 3600)}h ago"
    else:
        return f"{int(seconds / 86400)}d ago"


def _status_badge(status: str) -> str:
    """HTML badge for status."""
    color = STATUS_COLORS.get(status, "#6b7280")
    return f'<span style="background:{color};color:white;padding:2px 6px;border-radius:3px;font-size:0.7rem;font-weight:600;">{status}</span>'


def _category_badge(category: str) -> str:
    """HTML badge for category."""
    color = CATEGORY_COLORS.get(category, "#6b7280")
    label = category.replace("_", " ")
    return f'<span style="background:{color};color:white;padding:2px 6px;border-radius:3px;font-size:0.7rem;font-weight:600;">{label}</span>'


def _opening_badge(item: NewsItem) -> str:
    """HTML badge for opening expectation."""
    if not item.is_opening_direction or not item.reported_expectation:
        return ""
    label = OPENING_EXPECTATION_LABELS.get(item.reported_expectation, item.reported_expectation)
    icon = OPENING_EXPECTATION_ICONS.get(item.reported_expectation, "●")
    color = "#22c55e" if "HIGHER" in item.reported_expectation else ("#ef4444" if "LOWER" in item.reported_expectation else "#f59e0b")
    return f'<span style="background:{color};color:white;padding:2px 6px;border-radius:3px;font-size:0.7rem;font-weight:600;">{icon} {label}</span>'


def _source_badge(source: str) -> str:
    """HTML badge for source."""
    colors = {
        "Reuters": "#1e3a8a",
        "Economic Times": "#7c2d12",
        "Moneycontrol": "#14532d",
        "Mint": "#4c1d95",
        "NSE": "#7f1d1d",
        "RBI": "#1f2937",
        "Official": "#374151",
    }
    color = colors.get(source, "#374151")
    return f'<span style="background:{color};color:white;padding:2px 6px;border-radius:3px;font-size:0.7rem;font-weight:600;">{source}</span>'


# ─── Main Render Functions ──────────────────────────────────────────

def render_news_section(snapshot: Optional[NewsSnapshot], title: str = "Pre-Market News") -> None:
    """Render complete pre-market news section."""
    if not snapshot or not snapshot.items:
        st.info("No pre-market news available. Refreshing...")
        return

    # Section header with stats
    col1, col2, col3 = st.columns([3, 1, 1])
    with col1:
        st.markdown(f"### {title}")
    with col2:
        st.caption(f"{len(snapshot.items)} articles")
    with col3:
        if snapshot.fetch_timestamp:
            age = _age_str(snapshot.fetch_timestamp)
            st.caption(f"Updated {age}")

    # Source health indicator
    _render_source_health(snapshot.source_status)

    st.divider()

    # Group by category for organized display
    _render_news_by_category(snapshot.items)

    # Footer with fetch info
    if snapshot.fetch_timestamp:
        st.caption(
            f"Fetched: {snapshot.fetch_timestamp.strftime('%H:%M:%S UTC')} | "
            f"Total fetched: {snapshot.total_fetched} | "
            f"Deduped: {snapshot.total_deduped}"
        )


def _render_source_health(source_status: dict) -> None:
    """Render source health badges."""
    if not source_status:
        return

    cols = st.columns(len(source_status))
    for i, (source, status) in enumerate(source_status.items()):
        with cols[i]:
            st.markdown(f"{_source_badge(source)} {_status_badge(status)}", unsafe_allow_html=True)


def _render_news_by_category(items: List[NewsItem]) -> None:
    """Render news grouped by category."""
    # Priority order for categories
    category_order = [
        "NIFTY_50", "BANK_NIFTY", "RBI", "SEBI", "FII_DII",
        "USD_INR", "CRUDE", "BONDS_YIELDS", "FED",
        "EARNINGS", "GEOPOLITICAL", "GOVERNMENT_POLICY",
        "BANKING", "IPO", "INDIA_MARKET",
    ]

    # Group items by category
    grouped: dict[str, List[NewsItem]] = {}
    for item in items:
        cat = item.category or "INDIA_MARKET"
        grouped.setdefault(cat, []).append(item)

    # Render in priority order
    for cat in category_order:
        if cat not in grouped:
            continue
        cat_items = grouped[cat]
        if not cat_items:
            continue

        with st.expander(f"{_category_badge(cat)} {len(cat_items)} article(s)", expanded=(cat in ["NIFTY_50", "BANK_NIFTY", "RBI"])):
            for item in cat_items:
                _render_news_card(item)


def _render_news_card(item: NewsItem) -> None:
    """Render a single news item as a compact card."""
    with st.container(border=True):
        # Header row: title + badges
        cols = st.columns([5, 1, 1])
        with cols[0]:
            st.markdown(f"**{item.title}**")
        with cols[1]:
            st.markdown(_source_badge(item.source), unsafe_allow_html=True)
        with cols[2]:
            st.markdown(_status_badge(item.status), unsafe_allow_html=True)

        # Meta row: age + category + opening expectation
        meta_cols = st.columns([2, 2, 3])
        with meta_cols[0]:
            st.caption(f"🕐 {_age_str(item.published_at)}")
        with meta_cols[1]:
            if item.category:
                st.markdown(_category_badge(item.category), unsafe_allow_html=True)
        with meta_cols[2]:
            if item.is_opening_direction:
                st.markdown(_opening_badge(item), unsafe_allow_html=True)

        # Entity / Sector tags
        tags = []
        if item.entity:
            tags.append(f"🏢 {item.entity}")
        if item.sector:
            tags.append(f"📊 {item.sector}")
        if tags:
            st.caption(" | ".join(tags))

        # Snippet / Summary
        if item.content_snippet:
            with st.expander("Read more", expanded=False):
                st.write(item.content_snippet)
                if item.url:
                    st.markdown(f"[Source]({item.url})", unsafe_allow_html=True)
        elif item.url:
            st.markdown(f"[Read full article]({item.url})", unsafe_allow_html=True)


def render_news_fragment(news_items: List[NewsItem], max_items: int = 10) -> None:
    """Lightweight fragment for live news updates."""
    if not news_items:
        st.caption("No news items")
        return

    # Show only top items
    for item in news_items[:max_items]:
        cols = st.columns([6, 1, 1])
        with cols[0]:
            st.markdown(f"**{item.title}**")
        with cols[1]:
            st.markdown(_source_badge(item.source), unsafe_allow_html=True)
        with cols[2]:
            st.caption(_age_str(item.published_at))

        if item.is_opening_direction:
            st.markdown(_opening_badge(item), unsafe_allow_html=True)

        st.divider()


# ─── Sidebar Filters ────────────────────────────────────────────────

def render_news_sidebar(snapshot: Optional[NewsSnapshot]) -> dict:
    """Render sidebar filters for news section."""
    filters = {"categories": [], "sources": [], "opening_only": False}

    if not snapshot or not snapshot.items:
        return filters

    with st.sidebar.expander("📰 News Filters", expanded=False):
        # Category filter
        all_categories = sorted(set(i.category or "INDIA_MARKET" for i in snapshot.items))
        selected_cats = st.multiselect(
            "Categories",
            all_categories,
            default=all_categories,
            key="news_cat_filter",
        )
        filters["categories"] = selected_cats

        # Source filter
        all_sources = sorted(set(i.source for i in snapshot.items))
        selected_sources = st.multiselect(
            "Sources",
            all_sources,
            default=all_sources,
            key="news_src_filter",
        )
        filters["sources"] = selected_sources

        # Opening direction only
        filters["opening_only"] = st.checkbox(
            "Opening direction only",
            value=False,
            key="news_opening_filter",
        )

    return filters


def apply_news_filters(items: List[NewsItem], filters: dict) -> List[NewsItem]:
    """Apply sidebar filters to news items."""
    filtered = items

    if filters.get("categories"):
        filtered = [i for i in filtered if (i.category or "INDIA_MARKET") in filters["categories"]]

    if filters.get("sources"):
        filtered = [i for i in filtered if i.source in filters["sources"]]

    if filters.get("opening_only"):
        filtered = [i for i in filtered if i.is_opening_direction]

    return filtered