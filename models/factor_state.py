"""Factor state data contracts.

Stores directional assessment for each tracked factor across timeframes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class FactorDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    REVERSING = "REVERSING"
    MIXED = "MIXED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class Acceleration(str, Enum):
    ACCELERATING = "ACCELERATING"
    STEADY = "STEADY"
    DECELERATING = "DECELERATING"
    UNKNOWN = "UNKNOWN"


class Persistence(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


@dataclass
class FactorState:
    """State of a single factor at a single timeframe."""
    factor_name: str
    timeframe: str
    current_value: Optional[float] = None
    direction: FactorDirection = FactorDirection.INSUFFICIENT_DATA
    confidence: str = "LOW"
    previous_value: Optional[float] = None
    change_absolute: Optional[float] = None
    change_pct: Optional[float] = None
    acceleration: Acceleration = Acceleration.UNKNOWN
    persistence: Persistence = Persistence.UNKNOWN
    days_in_current_direction: int = 0
    is_reversing: bool = False
    reversal_strength: Optional[str] = None
    nifty_relevance: str = "UNCLEAR"
    nifty_interpretation: str = ""
    source: str = "UNKNOWN"
    observed_at: Optional[datetime] = None
    data_age_seconds: int = 0
    data_points_in_window: int = 0
    history_quality: str = "INSUFFICIENT"
    evidence: list[str] = field(default_factory=list)


@dataclass
class FactorSnapshot:
    """All factor states at a single point in time."""
    timestamp: datetime
    factors: dict[str, list[FactorState]] = field(default_factory=dict)
