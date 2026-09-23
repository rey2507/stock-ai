"""History Manager — persistent JSON storage for snapshots, verdicts, and OI observations.

Provides file-based persistence with daily rotation.
Thread-safe for concurrent writes.
"""

from __future__ import annotations

import json
import os
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from threading import Lock
from pathlib import Path

from models.snapshot import MarketSnapshot, FieldMeta
from models.verdict import Verdict
from models.factor_state import FactorSnapshot

log = logging.getLogger(__name__)

# Base data directory
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOTS_DIR = DATA_DIR / "snapshots"
VERDICTS_DIR = DATA_DIR / "verdicts"
OI_HISTORY_DIR = DATA_DIR / "oi_history"
MACRO_CACHE_FILE = DATA_DIR / "macro_cache.json"
FACTOR_STATES_DIR = DATA_DIR / "factor_states"


class HistoryManager:
    """Manages persistent history of snapshots, verdicts, and OI observations."""

    def __init__(self):
        self._lock = Lock()
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Create data directories if they don't exist."""
        for d in [DATA_DIR, SNAPSHOTS_DIR, VERDICTS_DIR, OI_HISTORY_DIR, FACTOR_STATES_DIR]:
            d.mkdir(parents=True, exist_ok=True)

    def _today_str(self) -> str:
        """Return today's date as YYYY-MM-DD."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # ─── Snapshot History ────────────────────────────────────────────

    def save_snapshot(self, snapshot: MarketSnapshot) -> None:
        """Append a snapshot to today's snapshot file."""
        with self._lock:
            try:
                filepath = SNAPSHOTS_DIR / f"{self._today_str()}.json"

                # Read existing data
                entries = []
                if filepath.exists():
                    with open(filepath, "r") as f:
                        try:
                            entries = json.load(f)
                        except (json.JSONDecodeError, IOError):
                            entries = []

                # Append new snapshot
                entries.append(self._snapshot_to_dict(snapshot))

                # Write back (atomic-ish)
                with open(filepath, "w") as f:
                    json.dump(entries, f, indent=2, default=str)

                # Rotate old files (keep 90 days)
                self._rotate_old_files(SNAPSHOTS_DIR, 90)

            except Exception as e:
                log.error(f"Failed to save snapshot: {e}")

    def get_snapshots(self, date: str) -> list[dict]:
        """Get all snapshots for a given date."""
        with self._lock:
            filepath = SNAPSHOTS_DIR / f"{date}.json"
            if not filepath.exists():
                return []
            try:
                with open(filepath, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return []

    def _snapshot_to_dict(self, snap: MarketSnapshot) -> dict:
        """Serialize MarketSnapshot to dict."""
        fields = {}
        for fname in snap._field_names():
            fm = getattr(snap, fname)
            if isinstance(fm, FieldMeta):
                fields[fname] = {
                    "value": fm.value if not isinstance(fm.value, (dict, list)) else str(fm.value),
                    "status": fm.status,
                    "quality": fm.quality,
                    "source": fm.source,
                    "observed_at": fm.observed_at.isoformat() if fm.observed_at else None,
                    "freshness_seconds": fm.freshness_seconds,
                }

        return {
            "timestamp": snap.snapshot_timestamp.isoformat() if snap.snapshot_timestamp else None,
            "source": snap.source,
            "data_status": snap.data_status,
            "fields": fields,
        }

    # ─── Verdict History ─────────────────────────────────────────────

    def save_verdict(self, verdict: Verdict) -> None:
        """Append verdict to today's verdict file (only if score changed)."""
        with self._lock:
            try:
                filepath = VERDICTS_DIR / f"{self._today_str()}.json"

                # Read existing
                entries = []
                if filepath.exists():
                    with open(filepath, "r") as f:
                        try:
                            entries = json.load(f)
                        except (json.JSONDecodeError, IOError):
                            entries = []

                # Only append if score changed from last entry
                if entries and entries[-1].get("raw_score") == verdict.raw_score:
                    return  # No change, don't store

                # Append new verdict
                entries.append({
                    "timestamp": verdict.timestamp.isoformat() if verdict.timestamp else datetime.now(timezone.utc).isoformat(),
                    "direction": verdict.direction,
                    "state": verdict.state,
                    "raw_score": verdict.raw_score,
                    "conflict": verdict.conflict,
                    "data_quality": verdict.data_quality,
                    "components": {
                        name: {
                            "score": comp.score,
                            "label": comp.label,
                            "reason": comp.reason,
                            "evidence": comp.evidence,
                            "is_primary": comp.is_primary,
                        }
                        for name, comp in verdict.components.items()
                    },
                    "reasons": verdict.reasons,
                })

                with open(filepath, "w") as f:
                    json.dump(entries, f, indent=2)

                # Rotate old files (keep 365 days)
                self._rotate_old_files(VERDICTS_DIR, 365)

            except Exception as e:
                log.error(f"Failed to save verdict: {e}")

    def get_verdict_changes(self, days: int = 1, limit: int = 10) -> list[dict]:
        """Get recent verdict changes across multiple days."""
        with self._lock:
            results = []
            for i in range(days):
                date = (datetime.now(timezone.utc).fromtimestamp(
                    datetime.now(timezone.utc).timestamp() - i * 86400
                )).strftime("%Y-%m-%d")
                filepath = VERDICTS_DIR / f"{date}.json"
                if filepath.exists():
                    try:
                        with open(filepath, "r") as f:
                            day_entries = json.load(f)
                            results.extend(day_entries)
                    except (json.JSONDecodeError, IOError):
                        continue

            # Sort by timestamp descending and limit
            results.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            return results[:limit]

    # ─── OI History ──────────────────────────────────────────────────

    def save_oi_observation(self, data: dict) -> None:
        """Append OI observation to today's OI history file."""
        with self._lock:
            try:
                filepath = OI_HISTORY_DIR / f"{self._today_str()}.json"

                entries = []
                if filepath.exists():
                    with open(filepath, "r") as f:
                        try:
                            entries = json.load(f)
                        except (json.JSONDecodeError, IOError):
                            entries = []

                entries.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    **data,
                })

                with open(filepath, "w") as f:
                    json.dump(entries, f, indent=2)

                # Rotate old files (keep 90 days)
                self._rotate_old_files(OI_HISTORY_DIR, 90)

            except Exception as e:
                log.error(f"Failed to save OI observation: {e}")

    def get_oi_history(self, days: int = 5) -> list[dict]:
        """Get OI history for the last N days."""
        with self._lock:
            results = []
            for i in range(days):
                date = (datetime.now(timezone.utc).fromtimestamp(
                    datetime.now(timezone.utc).timestamp() - i * 86400
                )).strftime("%Y-%m-%d")
                filepath = OI_HISTORY_DIR / f"{date}.json"
                if filepath.exists():
                    try:
                        with open(filepath, "r") as f:
                            results.extend(json.load(f))
                    except (json.JSONDecodeError, IOError):
                        continue
            return results

    # ─── Macro Cache ─────────────────────────────────────────────────

    def load_macro_cache(self) -> dict[str, Any]:
        """Load last successful macro fetch from cache file."""
        if not MACRO_CACHE_FILE.exists():
            return {}
        try:
            with open(MACRO_CACHE_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

    def save_macro_cache(self, data: dict[str, Any]) -> None:
        """Save successful macro fetch to cache file."""
        with self._lock:
            try:
                with open(MACRO_CACHE_FILE, "w") as f:
                    json.dump(data, f, indent=2, default=str)
            except Exception as e:
                log.error(f"Failed to save macro cache: {e}")

    # ─── Factor State History ─────────────────────────────────────────

    def save_factor_snapshot(self, snapshot: FactorSnapshot) -> None:
        """Append factor snapshot to today's factor state file."""
        with self._lock:
            try:
                date_str = snapshot.timestamp.date().isoformat()
                filepath = FACTOR_STATES_DIR / f"{date_str}.json"

                if filepath.exists():
                    with open(filepath, "r") as f:
                        try:
                            data = json.load(f)
                        except (json.JSONDecodeError, IOError):
                            data = {"date": date_str, "snapshots": []}
                else:
                    data = {"date": date_str, "snapshots": []}

                data["snapshots"].append({
                    "timestamp": snapshot.timestamp.isoformat(),
                    "factors": {
                        factor_name: [
                            self._factor_state_to_dict(state)
                            for state in states
                        ]
                        for factor_name, states in snapshot.factors.items()
                    },
                })

                data["snapshots"] = data["snapshots"][-1000:]

                with open(filepath, "w") as f:
                    json.dump(data, f, indent=2)

            except Exception as e:
                log.error(f"Failed to save factor snapshot: {e}")

    def get_factor_snapshots(self, date: str) -> list[dict]:
        """Get factor snapshots for a given date."""
        with self._lock:
            filepath = FACTOR_STATES_DIR / f"{date}.json"
            if not filepath.exists():
                return []
            try:
                with open(filepath, "r") as f:
                    return json.load(f).get("snapshots", [])
            except (json.JSONDecodeError, IOError):
                return []

    def _factor_state_to_dict(self, state: FactorState) -> dict:
        """Serialize FactorState to dict."""
        return {
            "factor_name": state.factor_name,
            "timeframe": state.timeframe,
            "current_value": state.current_value,
            "direction": state.direction.value if isinstance(state.direction, FactorDirection) else state.direction,
            "confidence": state.confidence,
            "previous_value": state.previous_value,
            "change_absolute": state.change_absolute,
            "change_pct": state.change_pct,
            "acceleration": state.acceleration.value if isinstance(state.acceleration, Acceleration) else state.acceleration,
            "persistence": state.persistence.value if isinstance(state.persistence, Persistence) else state.persistence,
            "days_in_current_direction": state.days_in_current_direction,
            "is_reversing": state.is_reversing,
            "reversal_strength": state.reversal_strength,
            "nifty_relevance": state.nifty_relevance,
            "nifty_interpretation": state.nifty_interpretation,
            "source": state.source,
            "observed_at": state.observed_at.isoformat() if state.observed_at else None,
            "data_age_seconds": state.data_age_seconds,
            "data_points_in_window": state.data_points_in_window,
            "history_quality": state.history_quality,
            "evidence": state.evidence,
        }

    # ─── Utilities ───────────────────────────────────────────────────

    def _rotate_old_files(self, directory: Path, keep_days: int) -> None:
        """Delete files older than keep_days."""
        try:
            cutoff = datetime.now(timezone.utc).timestamp() - (keep_days * 86400)
            for f in directory.glob("*.json"):
                try:
                    mtime = f.stat().st_mtime
                    if mtime < cutoff:
                        f.unlink()
                        log.debug(f"Rotated old file: {f.name}")
                except OSError:
                    continue
        except Exception as e:
            log.debug(f"File rotation error: {e}")


# Global history manager instance
history_manager = HistoryManager()
