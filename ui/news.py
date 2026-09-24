"""News Intelligence UI."""

from __future__ import annotations

import streamlit as st
from datetime import datetime, timezone
from typing import Optional

from models.news_model import NewsSnapshot


def render_news_section(news_snapshot: Optional[NewsSnapshot]) -> None:
    if not news_snapshot or not news_snapshot.items:
        return

    live_sources = sum(1 for s in news_snapshot.source_status.values() if s == "LIVE")
    total_sources = len(news_snapshot.source_status)
    item_count = len(news_snapshot.items)

    st.markdown("### 📰 Latest Market News")
    st.caption(f"🟢 {live_sources}/{total_sources} sources live | {item_count} headlines")

    top = news_snapshot.items[:3]
    for item in top:
        col1, col2 = st.columns([4, 1])
        with col1:
            st.markdown(f"**{item.title}**")
            st.caption(f"{item.source} • {item.published_at.strftime('%H:%M IST')}")
            st.markdown(f"[Read →]({item.url})")
        with col2:
            st.caption("🟢 LIVE" if item.status == "LIVE" else "🟠 STALE")
        st.divider()

    with st.expander(f"▶ Show all headlines ({item_count} total)"):
        for item in news_snapshot.items:
            st.markdown(f"**{item.title}**")
            st.caption(f"{item.source} • {item.published_at.strftime('%H:%M IST')} • {item.source_api}")
            st.markdown(f"[{item.url}]({item.url})")
            st.divider()

    with st.expander("▶ Source health details"):
        for source, status in news_snapshot.source_status.items():
            icon = "🟢" if status == "LIVE" else "🟡" if status == "DEGRADED" else "🔴"
            st.write(f"{icon} {source}: {status}")
