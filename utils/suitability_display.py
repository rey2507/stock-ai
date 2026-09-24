"""Option suitability scoring UI components."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from providers.suitability_calculator import SuitabilityScore
from providers.contract_ranker import ContractRanker


def render_suitability_score(score: SuitabilityScore) -> None:
    """Render detailed suitability assessment for a single contract."""
    rec_colors = {
        "BUY": "🟢",
        "ACCEPT": "🟢",
        "CAUTION": "🟡",
        "AVOID": "🔴",
        "DO_NOT_TRADE": "🔴",
    }
    color = rec_colors.get(score.recommendation, "⚪")

    st.markdown(
        f"### {color} {score.option_type} {score.strike:.0f} — "
        f"{score.recommendation} ({score.overall_score:.0f}/100)"
    )

    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Direction", f"{score.direction_fit:.0f}", "")
    with col2:
        st.metric("Theta", f"{score.theta_efficiency:.0f}", "")
    with col3:
        st.metric("Time", f"{score.time_alignment:.0f}", "")
    with col4:
        st.metric("Risk", f"{score.risk_management:.0f}", "")
    with col5:
        st.metric("Liquidity", f"{score.liquidity:.0f}", "")

    with st.expander("📊 Evidence & Reasoning"):
        for evidence in score.evidence:
            st.write(f"• {evidence}")

    with st.expander("📈 Profit/Loss Scenarios"):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric(
                "Profit if 1-STD Move",
                f"₹{score.scenario_profit_1std:.0f}" if score.scenario_profit_1std > 0 else "Loss",
            )
        with col2:
            st.metric("Max Loss (Naked)", f"₹{score.scenario_loss_max:.0f}")
        with col3:
            st.metric("Break-even Move", f"±{score.scenario_breakeven_move:.0f} pts")

    if score.warnings:
        with st.expander("⚠️ Risk Warnings"):
            for warning in score.warnings:
                st.write(f"• {warning}")


def render_suitability_ranking(scores: List[SuitabilityScore]) -> None:
    """Render ranked list of contracts."""
    ranker = ContractRanker()
    ranked = ranker.rank_contracts(scores)

    rec_filter = st.selectbox(
        "Filter by recommendation",
        ["All", "BUY", "ACCEPT", "CAUTION", "AVOID", "DO_NOT_TRADE"],
    )

    if rec_filter != "All":
        ranked = [s for s in ranked if s.recommendation == rec_filter]

    data = []
    for score in ranked:
        data.append({
            "Rank": len(data) + 1,
            "Type": score.option_type,
            "Strike": f"{score.strike:.0f}",
            "DTE": score.days_to_expiry,
            "Score": f"{score.overall_score:.0f}",
            "Rec.": score.recommendation,
            "Dir.": f"{score.direction_fit:.0f}",
            "Theta": f"{score.theta_efficiency:.0f}",
            "Risk": f"{score.risk_management:.0f}",
            "Liq.": f"{score.liquidity:.0f}",
        })

    df = pd.DataFrame(data)

    def color_recommendation(val):
        if val in ("BUY", "ACCEPT"):
            return "background-color: #90EE90"
        if val == "CAUTION":
            return "background-color: #FFFFE0"
        if val in ("AVOID", "DO_NOT_TRADE"):
            return "background-color: #FFB6C6"
        return ""

    styled_df = df.style.map(
        lambda val: color_recommendation(val) if val in ("BUY", "ACCEPT", "CAUTION", "AVOID", "DO_NOT_TRADE") else ""
    )

    st.dataframe(styled_df, use_container_width=True, hide_index=True)

    rec_counts = ranker.group_by_recommendation(scores)
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("🟢 BUY", len(rec_counts["BUY"]))
    with col2:
        st.metric("🟢 ACCEPT", len(rec_counts["ACCEPT"]))
    with col3:
        st.metric("🟡 CAUTION", len(rec_counts["CAUTION"]))
    with col4:
        st.metric("🔴 AVOID", len(rec_counts["AVOID"]))
    with col5:
        st.metric("🔴 NO TRADE", len(rec_counts["DO_NOT_TRADE"]))
