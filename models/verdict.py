"""Verdict output contract. Structured, testable, independent of Streamlit."""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from models.factor_state import FactorState


@dataclass
class ComponentResult:
    """Result from a single verdict component."""
    name: str
    score: int  # +1, 0, -1
    label: str
    reason: str
    evidence: list[str] = field(default_factory=list)
    is_primary: bool = False  # True = high-priority for conflict detection
    factor_influence: Optional[str] = None


@dataclass
class Verdict:
    """Structured verdict output.

    direction: BULLISH | BEARISH | MIXED | NONE
    state:     SETUP | BIAS | WAIT | INSUFFICIENT_DATA
    """
    direction: str  # BULLISH | BEARISH | MIXED | NONE
    state: str  # SETUP | BIAS | WAIT | INSUFFICIENT_DATA
    raw_score: int
    conflict: bool
    data_quality: str  # GOOD | PARTIAL | POOR
    components: dict[str, ComponentResult] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    timestamp: Optional[datetime] = None
    emoji: str = ""
    display_label: str = ""
    persistence: str = ""
    expiry_context: str = ""
    market_regime: str = ""
    trend_strength: int = 0
    factor_states: dict[str, list[FactorState]] = field(default_factory=dict)
    factor_contributions: dict[str, int] = field(default_factory=dict)
    timeframe_conflicts: list[str] = field(default_factory=list)
    factor_evidence: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Compute display label and emoji from direction + state."""
        label_map = {
            ("BULLISH", "SETUP"): ("BULLISH SETUP", "🟢"),
            ("BULLISH", "BIAS"): ("BULLISH BIAS", "🟢"),
            ("BEARISH", "SETUP"): ("BEARISH SETUP", "🔴"),
            ("BEARISH", "BIAS"): ("BEARISH BIAS", "🔴"),
            ("MIXED", "WAIT"): ("MIXED / WAIT", "🟡"),
            ("NONE", "INSUFFICIENT_DATA"): ("INSUFFICIENT DATA", "⚪"),
        }
        if not self.display_label:
            self.display_label, self.emoji = label_map.get(
                (self.direction, self.state), ("UNKNOWN", "⚪")
            )

    def to_evidence_package(self) -> dict:
        """Serialize verdict into a compact structured evidence package.

        This is the format a future AI synthesis layer would consume.
        Does not include raw market data — only derived evidence and context.
        """
        return {
            "current_state": {
                "direction": self.direction,
                "state": self.state,
                "raw_score": self.raw_score,
                "display_label": self.display_label,
                "data_quality": self.data_quality,
                "trend_strength": self.trend_strength,
            },
            "persistence": {
                "value": self.persistence,
                "history_available": bool(self.persistence),
            },
            "market_context": {
                "expiry_context": self.expiry_context,
                "market_regime": self.market_regime,
            },
            "components": {
                name: {
                    "score": comp.score,
                    "label": comp.label,
                    "reason": comp.reason,
                    "evidence": comp.evidence,
                    "is_primary": comp.is_primary,
                }
                for name, comp in self.components.items()
            },
            "changes": {
                "conflict": self.conflict,
                "reasons": self.reasons[:5],
            },
            "meta": {
                "timestamp": self.timestamp.isoformat() if self.timestamp else None,
            },
        }
