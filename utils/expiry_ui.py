"""Expiry Dashboard — option chain visualization and expiry-specific metrics."""

from __future__ import annotations

import streamlit as st
import pandas as pd
from typing import Optional

from models.snapshot import MarketSnapshot
from providers.history_manager import history_manager


def render_expiry_dashboard(snap: MarketSnapshot) -> None:
    """Render the Expiry page with option chain heatmap."""
    st.header("Expiry Trading Dashboard")

    if snap.data_status == "UNAVAILABLE":
        st.error("No data available.")
        return

    # Show main verdict at top
    try:
        from engines.intraday_verdict_v2 import compute_verdict
        from utils.ui import render_verdict_header, verdict_panel, contribution_panel, what_changed_panel
        
        result = compute_verdict(snap)
        result.persistence = snap.persistence if hasattr(snap, 'persistence') and snap.persistence else ""
        result.expiry_context = snap.expiry_context if hasattr(snap, 'expiry_context') and snap.expiry_context else ""
        result.market_regime = snap.market_regime if hasattr(snap, 'market_regime') and snap.market_regime else ""
        result.trend_strength = snap.trend_strength if hasattr(snap, 'trend_strength') and snap.trend_strength else 0
        
        render_verdict_header(result, snap)
        what_changed_panel(result)
        contribution_panel(result)
        verdict_panel(result)
        st.markdown("---")
    except Exception as e:
        st.caption(f"Verdict unavailable: {e}")

    # Get option chain data from NSEOptionsProvider
    # The snapshot should have option chain data if NSEOptionsProvider succeeded
    from providers.registry import get_provider
    try:
        nse_provider = get_provider("NSEOptions")
        chain_snap = nse_provider.fetch()
    except Exception as e:
        st.error(f"Failed to fetch option chain: {e}")
        return

    if chain_snap.data_status == "UNAVAILABLE":
        st.warning("Option chain data unavailable. Check NSE connectivity.")
        return

    # Expiry info
    atm_strike = chain_snap.get("atm_strike")
    pcr = chain_snap.get("pcr")
    max_pain = chain_snap.get("max_pain")
    atm_iv = chain_snap.get("atm_iv")

    st.subheader("Expiry Overview")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        display, status, _ = _field_display_value(chain_snap.atm_strike)
        st.metric("ATM Strike", display, status)
    with c2:
        display, status, _ = _field_display_value(chain_snap.pcr)
        st.metric("PCR", display, status)
    with c3:
        display, status, _ = _field_display_value(chain_snap.max_pain)
        st.metric("Max Pain", display, status)
    with c4:
        display, status, _ = _field_display_value(chain_snap.atm_iv)
        st.metric("ATM IV", display, status)

    # Option chain heatmap
    st.subheader("Option Chain Heatmap")
    try:
        _render_option_chain_heatmap(chain_snap, atm_strike)
    except Exception as e:
        st.error(f"Failed to render option chain: {e}")

    # OI changes
    st.subheader("OI Changes")
    c1, c2 = st.columns(2)
    with c1:
        display, status, _ = _field_display_value(chain_snap.call_oi_change)
        st.metric("Call OI Change", display, status)
    with c2:
        display, status, _ = _field_display_value(chain_snap.put_oi_change)
        st.metric("Put OI Change", display, status)

    # Volume
    st.subheader("Options Volume")
    c1, c2, c3 = st.columns(3)
    with c1:
        display, status, _ = _field_display_value(chain_snap.total_option_volume)
        st.metric("Total Option Volume", display, status)
    with c2:
        display, status, _ = _field_display_value(chain_snap.call_oi)
        st.metric("Total Call OI", display, status)
    with c3:
        display, status, _ = _field_display_value(chain_snap.put_oi)
        st.metric("Total Put OI", display, status)

    # Phase D: Expected Move vs Theta Analysis
    st.markdown("---")
    st.subheader("📉 Expected Move vs Theta Analysis")
    try:
        _render_expected_move_analysis(snap, chain_snap, atm_strike)
    except Exception as e:
        st.caption(f"Expected move analysis unavailable: {e}")

    # Phase E: Option Suitability Ranking
    st.markdown("---")
    st.subheader("✅ Option Suitability Ranking")
    try:
        _render_suitability_ranking(snap, chain_snap, atm_strike)
    except Exception as e:
        st.caption(f"Suitability analysis unavailable: {e}")


def _render_expected_move_analysis(snap: MarketSnapshot, chain_snap: MarketSnapshot, atm_strike: Optional[float]) -> None:
    """Render expected move analysis for option contracts."""
    from providers.expected_move_analyzer import ExpectedMoveAnalyzer
    from providers.theta_decay_calculator import ThetaDecayCalculator
    from utils.expected_move_display import render_expected_move_analysis, render_expected_move_comparison_table

    greeks_by_strike = getattr(snap, "greeks_by_strike", None)
    if not greeks_by_strike:
        st.caption("Greeks data not available. Ensure GreeksProvider is registered and data is fresh.")
        return

    analyzer = ExpectedMoveAnalyzer(config={})
    decay_calc = ThetaDecayCalculator()
    all_analyses: dict[str, ExpectedMoveAnalysis] = {}

    for strike, expiries in greeks_by_strike.items():
        for expiry_date, results in expiries.items():
            for result in results:
                try:
                    analysis = analyzer.analyze_contract(
                        strike=result.strike,
                        spot=result.spot,
                        days_to_expiry=result.days_to_expiry,
                        implied_vol=result.implied_vol,
                        option_type=result.option_type,
                        current_premium=result.premium,
                        theta_daily=result.theta,
                        delta=result.delta,
                        vega=result.vega,
                    )
                    key = f"{result.option_type} {result.strike:.0f} ({expiry_date})"
                    all_analyses[key] = analysis

                    with st.expander(f"{result.option_type} {result.strike:.0f} ({expiry_date}) — {analysis.assessment}"):
                        render_expected_move_analysis(analysis)

                        decay_schedule = decay_calc.calculate_decay_schedule(
                            original_premium=result.premium,
                            days_to_expiry=result.days_to_expiry,
                            theta_daily_now=result.theta,
                            option_type=result.option_type,
                        )
                        if decay_schedule:
                            decay_df = pd.DataFrame([
                                {
                                    "Day": d.day,
                                    "Theta": f"₹{d.theta_daily:.2f}",
                                    "Cumulative": f"₹{d.cumulative_decay:.0f}",
                                    "Premium": f"₹{d.premium_estimate:.0f}",
                                    "% Remaining": f"{d.pct_of_original:.1f}%",
                                }
                                for d in decay_schedule
                            ])
                            st.markdown("**Theta Decay Schedule**")
                            st.dataframe(decay_df, use_container_width=True, hide_index=True)
                except Exception as e:
                    log.warning(f"Expected move analysis failed for {result.option_type} {result.strike}: {e}")

    if all_analyses:
        st.subheader("📊 Contract Comparison")
        render_expected_move_comparison_table(all_analyses)


def _render_suitability_ranking(snap: MarketSnapshot, chain_snap: MarketSnapshot, atm_strike: Optional[float]) -> None:
    """Render option suitability ranking."""
    from providers.suitability_calculator import SuitabilityCalculator
    from providers.contract_ranker import ContractRanker
    from utils.suitability_display import render_suitability_ranking, render_suitability_score

    greeks_by_strike = getattr(snap, "greeks_by_strike", None)
    if not greeks_by_strike:
        st.caption("Greeks data not available. Ensure GreeksProvider is registered and data is fresh.")
        return

    analyzer = SuitabilityCalculator(config={})
    all_scores = []

    for strike, expiries in greeks_by_strike.items():
        for expiry_date, results in expiries.items():
            for result in results:
                try:
                    expected_move = snap.expected_move_analysis.get(strike, {}).get(expiry_date, [None])[0] if hasattr(snap, "expected_move_analysis") and snap.expected_move_analysis else None
                    if expected_move is None:
                        from providers.expected_move_analyzer import ExpectedMoveAnalyzer
                        ema = ExpectedMoveAnalyzer(config={})
                        expected_move = ema.analyze_contract(
                            strike=result.strike,
                            spot=result.spot,
                            days_to_expiry=result.days_to_expiry,
                            implied_vol=result.implied_vol,
                            option_type=result.option_type,
                            current_premium=result.premium,
                            theta_daily=result.theta,
                            delta=result.delta,
                            vega=result.vega,
                        )

                    score = analyzer.calculate_suitability(
                        strike=result.strike,
                        spot=result.spot,
                        option_type=result.option_type,
                        days_to_expiry=result.days_to_expiry,
                        expiry_date=expiry_date,
                        current_premium=result.premium,
                        implied_vol=result.implied_vol,
                        greeks_result=result,
                        expected_move_analysis=expected_move,
                        market_view="BULLISH",
                        holding_period_days=result.days_to_expiry,
                        risk_tolerance="MEDIUM",
                    )
                    all_scores.append(score)
                except Exception as e:
                    log.warning(f"Suitability calculation failed for {result.option_type} {result.strike}: {e}")

    if not all_scores:
        st.caption("No suitability scores available.")
        return

    render_suitability_ranking(all_scores)

    top_score = max(all_scores, key=lambda s: s.overall_score)
    st.subheader(f"🎯 Top Recommendation: {top_score.option_type} {top_score.strike:.0f}")
    render_suitability_score(top_score)


def _render_option_chain_heatmap(snap: MarketSnapshot, atm_strike: Optional[float]) -> None:
    """Render option chain as a styled table with OI/IV by strike."""
    if atm_strike is None:
        st.caption("ATM strike not available.")
        return

    # Get strike map from option chain data
    # The strike_map is embedded in the source field of call_oi/put_oi FieldMeta
    # Actually, we need to fetch it from the provider directly
    from providers.registry import get_provider
    try:
        nse_provider = get_provider("NSEOptions")
        # Access the internal option chain cache
        from providers.cache import cache
        cached = cache.get("nse_options_chain")
        if not cached or not cached.value:
            st.caption("Option chain not cached. Refresh to load.")
            return

        chain_data = cached.value
        strike_map = getattr(chain_data, 'strike_map', {})
        if not strike_map:
            # Try to get from the snapshot source field (stored as string)
            st.caption("Strike data not available for heatmap.")
            return

        # Build heatmap rows for ATM ± 4 strikes
        strikes = sorted(strike_map.keys())
        if not strikes:
            st.caption("No strikes available.")
            return

        # Find nearest strikes to ATM
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - atm_strike))
        start_idx = max(0, atm_idx - 4)
        end_idx = min(len(strikes), atm_idx + 5)
        display_strikes = strikes[start_idx:end_idx]

        rows = []
        for strike in display_strikes:
            data = strike_map.get(strike, {})
            ce_oi = data.get("ce_oi", 0)
            pe_oi = data.get("pe_oi", 0)
            ce_iv = data.get("ce_iv")
            pe_iv = data.get("pe_iv")
            ce_change = data.get("ce_change", 0)
            pe_change = data.get("pe_change", 0)

            ce_oi_str = f"{ce_oi:,.0f}" if ce_oi else "—"
            pe_oi_str = f"{pe_oi:,.0f}" if pe_oi else "—"
            ce_iv_str = f"{ce_iv:.1f}%" if ce_iv is not None else "—"
            pe_iv_str = f"{pe_iv:.1f}%" if pe_iv is not None else "—"

            ce_arrow = "↑" if ce_change > 0 else ("↓" if ce_change < 0 else "→")
            pe_arrow = "↑" if pe_change > 0 else ("↓" if pe_change < 0 else "→")

            marker = " ◆" if abs(strike - atm_strike) < 1 else ""
            strike_str = f"{strike:,.0f}{marker}"

            rows.append({
                "Strike": strike_str,
                "Call OI": f"{ce_oi_str} {ce_arrow}",
                "Call IV": ce_iv_str,
                "Put OI": f"{pe_oi_str} {pe_arrow}",
                "Put IV": pe_iv_str,
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Legend
        st.caption("◆ = ATM strike | ↑ = OI increasing | ↓ = OI decreasing | → = no change")

    except Exception as e:
        st.caption(f"Heatmap unavailable: {e}")


def _field_display_value(fm) -> tuple[str, str, str]:
    """Simple field display helper (duplicated from ui.py to avoid circular import)."""
    if not hasattr(fm, 'value') or fm.value is None:
        return "UNAVAILABLE", getattr(fm, 'status', "UNAVAILABLE"), "gray"
    val = fm.value
    status = getattr(fm, 'status', "UNAVAILABLE")
    if isinstance(val, float):
        if abs(val) >= 1000:
            display = f"{val:,.0f}"
        else:
            display = f"{val:,.2f}"
    elif isinstance(val, int):
        display = f"{val:,}"
    else:
        display = str(val)
    color = "green" if isinstance(val, (int, float)) and val > 0 else ("red" if isinstance(val, (int, float)) and val < 0 else "gray")
    return display, status, color
