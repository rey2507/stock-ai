"""Expected Move & Theta Decay UI components."""

from __future__ import annotations

import streamlit as st
import pandas as pd

from providers.expected_move_analyzer import ExpectedMoveAnalysis


def render_expected_move_analysis(analysis: ExpectedMoveAnalysis) -> None:
    """Render expected move analysis for a single contract."""
    assessment_colors = {
        "FAVORABLE": "🟢",
        "NEUTRAL": "🟡",
        "UNFAVORABLE": "🔴",
        "HIGHLY_UNFAVORABLE": "🔴",
        "EXPIRED": "⚪",
        "INSUFFICIENT_DATA": "⚪",
    }
    color = assessment_colors.get(analysis.assessment, "⚪")

    st.markdown(f"### {color} {analysis.option_type} {analysis.strike:.0f} — {analysis.assessment}")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            "Expected Move (1 STD)",
            f"±{analysis.expected_move_1std:.0f} pts",
            help=f"{analysis.expected_move_1std / analysis.spot * 100:.2f}% of spot",
        )
    with col2:
        st.metric(
            "Theta Decay",
            f"₹{abs(analysis.theta_daily):.2f}/day",
            f"₹{abs(analysis.theta_total):.0f} total",
        )
    with col3:
        st.metric(
            "Premium Required",
            f"₹{analysis.premium_required_to_breakeven:.0f}",
            f"{analysis.pct_of_premium:.1f}% of premium",
        )
    with col4:
        st.metric("Confidence", analysis.confidence)

    with st.expander("📊 Evidence & Details"):
        for evidence in analysis.evidence:
            st.write(f"• {evidence}")

    if analysis.warnings:
        with st.expander("⚠️ Warnings"):
            for warning in analysis.warnings:
                st.write(f"• {warning}")


def render_expected_move_comparison_table(analyses: dict[str, ExpectedMoveAnalysis]) -> None:
    """Render comparison table for multiple contracts."""
    data = []
    for key, analysis in analyses.items():
        data.append(
            {
                "Contract": key,
                "Strike": f"{analysis.strike:.0f}",
                "Type": analysis.option_type,
                "Expected Move": f"±{analysis.expected_move_1std:.0f}",
                "Theta/Day": f"₹{abs(analysis.theta_daily):.2f}",
                "Premium Req'd": f"₹{analysis.premium_required_to_breakeven:.0f}",
                "Assessment": analysis.assessment,
                "Confidence": analysis.confidence,
            }
        )
    df = pd.DataFrame(data)
    st.dataframe(df, use_container_width=True, hide_index=True)
