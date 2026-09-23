"""Source Conflict Detection.

When multiple sources provide the same field, detect and record conflicts.
Does NOT auto-resolve — surfaces the conflict to the UI.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from threading import Lock

log = logging.getLogger(__name__)


@dataclass
class ConflictRecord:
    """A detected conflict between two sources for the same field."""
    field_name: str
    primary_source: str
    secondary_source: str
    primary_value: float
    secondary_value: float
    primary_timestamp: Optional[datetime] = None
    secondary_timestamp: Optional[datetime] = None
    difference: float = 0.0
    difference_pct: float = 0.0
    tolerance: float = 0.0
    detected_at: Optional[datetime] = None

    @property
    def is_significant(self) -> bool:
        """Check if conflict exceeds tolerance."""
        return abs(self.difference) > self.tolerance

    @property
    def display_text(self) -> str:
        """Human-readable conflict description."""
        return (
            f"DATA CONFLICT: {self.field_name}\n"
            f"  {self.primary_source}: {self.primary_value}\n"
            f"  {self.secondary_source}: {self.secondary_value}\n"
            f"  Difference: {self.difference:.4f} ({self.difference_pct:.2f}%)\n"
            f"  Tolerance: {self.tolerance}"
        )


class ConflictDetector:
    """Detects and records source conflicts for the same field.

    Does NOT auto-resolve. Records conflicts for UI display.
    """

    def __init__(self, default_tolerance_pct: float = 0.5):
        """
        Args:
            default_tolerance_pct: Default percentage tolerance for conflict detection.
        """
        self._default_tolerance_pct = default_tolerance_pct
        self._conflicts: dict[str, ConflictRecord] = {}
        self._lock = Lock()

    def check_conflict(
        self,
        field_name: str,
        source_a: str,
        value_a: float,
        timestamp_a: Optional[datetime] = None,
        source_b: str = "",
        value_b: Optional[float] = None,
        timestamp_b: Optional[datetime] = None,
        tolerance_pct: Optional[float] = None,
    ) -> Optional[ConflictRecord]:
        """Check if two values from different sources conflict.

        Args:
            field_name: Canonical field name
            source_a: First source name
            value_a: First source value
            timestamp_a: When first source observed
            source_b: Second source name (if available)
            value_b: Second source value (if available)
            timestamp_b: When second source observed
            tolerance_pct: Override default tolerance

        Returns:
            ConflictRecord if conflict detected, None otherwise.
        """
        if value_b is None or not source_b:
            return None

        if not isinstance(value_a, (int, float)) or not isinstance(value_b, (int, float)):
            return None

        tolerance = tolerance_pct if tolerance_pct is not None else self._default_tolerance_pct
        tolerance_abs = abs(value_a) * (tolerance / 100) if value_a != 0 else 0.001

        difference = abs(value_a - value_b)
        difference_pct = (difference / abs(value_a) * 100) if value_a != 0 else 0.0

        if difference > tolerance_abs:
            record = ConflictRecord(
                field_name=field_name,
                primary_source=source_a,
                secondary_source=source_b,
                primary_value=value_a,
                secondary_value=value_b,
                primary_timestamp=timestamp_a,
                secondary_timestamp=timestamp_b,
                difference=difference,
                difference_pct=difference_pct,
                tolerance=tolerance_abs,
                detected_at=datetime.now(timezone.utc),
            )

            with self._lock:
                self._conflicts[field_name] = record

            log.warning(record.display_text)
            return record

        return None

    def get_conflict(self, field_name: str) -> Optional[ConflictRecord]:
        """Get the most recent conflict for a field."""
        with self._lock:
            return self._conflicts.get(field_name)

    def get_all_conflicts(self) -> dict[str, ConflictRecord]:
        """Get all active conflicts."""
        with self._lock:
            return dict(self._conflicts)

    def clear_conflict(self, field_name: str):
        """Clear a resolved conflict."""
        with self._lock:
            self._conflicts.pop(field_name, None)

    def clear_all(self):
        """Clear all conflicts."""
        with self._lock:
            self._conflicts.clear()

    @property
    def has_conflicts(self) -> bool:
        """Check if any conflicts exist."""
        with self._lock:
            return len(self._conflicts) > 0

    @property
    def conflict_count(self) -> int:
        """Number of active conflicts."""
        with self._lock:
            return len(self._conflicts)


# Global conflict detector
conflict_detector = ConflictDetector()
