"""Paper-Trader — Overall NIFTY 50 verdict (Stage 18).

Combines the two verdicts the app already computes:

1. **Technical** — backend.verdict.compute_verdict on NIFTY 50 daily
   candles (unchanged calculations, unchanged candle sources).
2. **Macro** — backend.macro.get_macro_snapshot's factor tally
   (unchanged free sources and signal mappings: global/US-futures/Asian
   closes, commodities & rates, India VIX, FII net index futures).

This module only counts and averages those existing signals into one
overall reading: bullish / bearish / neutral with a confidence %. It adds
no new indicators, no new data sources, and no fake data. Descriptive
only — it does not predict the market.
"""

from __future__ import annotations

from typing import Any

DISCLAIMER = ("Combined descriptive reading of existing technical "
              "indicators and macro factors — not a prediction and not "
              "guaranteed. Trade decisions are yours.")


def _norm(signal: str) -> str:
    """Map verdict/macro vocabularies onto one scale."""
    if signal in ("bullish", "positive"):
        return "bullish"
    if signal in ("bearish", "negative"):
        return "bearish"
    if signal in ("neutral",):
        return "neutral"
    return "unclear"  # unclear + unavailable


def combine(technical: dict[str, Any], macro_snapshot: dict[str, Any]) -> dict[str, Any]:
    """Produce the overall verdict from existing computed pieces.

    Weights: technical and macro blocks each contribute their tally; every
    decided signal (bullish/bearish) counts ±1, neutral 0, unclear is
    excluded from both the score and the denominator.
    """
    # --- technical block (existing compute_verdict output) ---
    tech = technical if technical.get("status") != "unavailable" else {}
    tech_checks = tech.get("checks") or []
    tech_decided = 0
    tech_score = 0
    for c in tech_checks:
        s = _norm(c.get("signal", ""))
        if s in ("bullish", "bearish"):
            tech_score += 1 if s == "bullish" else -1
            tech_decided += 1

    tech_summary = {
        "verdict": _norm(tech.get("verdict", "unclear")),
        "confidence": tech.get("confidence", 0),
        "decided": tech_decided,
        "score": tech_score,
        "source": tech.get("source"),
    }

    # --- macro block (existing summary counts + contributors) ---
    summary = macro_snapshot.get("summary") or {}
    counts = summary.get("counts") or {}
    contributors = summary.get("contributors") or {}
    macro_score = counts.get("positive", 0) - counts.get("negative", 0)
    macro_decided = counts.get("positive", 0) + counts.get("negative", 0)
    if macro_decided > 0:
        macro_verdict = ("bullish" if macro_score > 0
                         else "bearish" if macro_score < 0 else "neutral")
        macro_conf = round(min(abs(macro_score) / macro_decided, 1.0) * 100)
    else:
        macro_verdict, macro_conf = "unclear", 0
    macro_summary = {
        "verdict": macro_verdict,
        "confidence": macro_conf,
        "decided": macro_decided,
        "score": macro_score,
        "counts": counts,
        "contributors": contributors,
    }

    # --- combine: technical and macro carry equal weight; decided-only ---
    total_score = tech_score + macro_score
    total_decided = tech_decided + macro_decided
    if total_decided == 0:
        overall, confidence = "unclear", 0
    else:
        share = total_score / total_decided  # -1..1
        if share > 0.15:
            overall = "bullish"
        elif share < -0.15:
            overall = "bearish"
        else:
            overall = "neutral"
        confidence = round(min(abs(share), 1.0) * 100)

    parts = []
    if tech_summary["verdict"] != "unclear":
        parts.append(f"technicals {tech_summary['verdict']} "
                     f"({tech_summary['confidence']}%)")
    if macro_summary["verdict"] != "unclear":
        parts.append(f"macro {macro_summary['verdict']} "
                     f"({macro_summary['confidence']}%)")
    line = ("Overall: " + overall +
            (f" — " + ", ".join(parts) if parts else " — no decided signals") +
            ". Not a prediction.")

    return {
        "verdict": overall,
        "confidence": confidence,
        "technical": tech_summary,
        "macro": macro_summary,
        "one_liner": line,
        "disclaimer": DISCLAIMER,
    }
