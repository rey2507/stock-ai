"""SnapshotMerger — combines MarketSnapshots from multiple providers.

Provider priority: AngelProvider (live market) > WebSource (macro/NSE).
For SmartAPI-supported fields, AngelProvider's value always wins.
For external fields, takes the first available (GOOD/PARTIAL) value.
"""

from models.snapshot import MarketSnapshot, FieldMeta
from models.source_policy import is_smartapi_field


_QUALITY_RANK = {"GOOD": 2, "PARTIAL": 1, "INVALID": 0}


def _pick(a: FieldMeta, b: FieldMeta) -> FieldMeta:
    """Return the better of two FieldMeta values.

    Preference order:
    1. For SmartAPI-supported fields: AngelBroking (first) always wins
    2. For NSEOptions-owned fields: NSEOptions always wins
    3. Higher quality (GOOD > PARTIAL > INVALID)
    4. Lower freshness (more recent)
    5. First argument wins ties
    """
    a_valid = a.quality in ("GOOD", "PARTIAL") and a.status != "UNAVAILABLE"
    b_valid = b.quality in ("GOOD", "PARTIAL") and b.status != "UNAVAILABLE"

    if a_valid and not b_valid:
        return a
    if b_valid and not a_valid:
        return b
    if not a_valid and not b_valid:
        return a if a.value is not None else b

    # Both valid — check provider priority
    a_source = a.source
    b_source = b.source

    # AngelBroking wins for SmartAPI fields
    if a_source == "AngelBroking" and b_source != "AngelBroking":
        return a
    if b_source == "AngelBroking" and a_source != "AngelBroking":
        return b

    # NSEOptions wins for NSE-owned fields
    if a_source == "NSEOptions" and b_source not in ("AngelBroking", "NSEOptions"):
        return a
    if b_source == "NSEOptions" and a_source not in ("AngelBroking", "NSEOptions"):
        return b

    # Neither is AngelBroking/NSEOptions, or both are — prefer higher quality
    a_rank = _QUALITY_RANK.get(a.quality, 0)
    b_rank = _QUALITY_RANK.get(b.quality, 0)
    if a_rank != b_rank:
        return a if a_rank > b_rank else b

    # Same quality — prefer lower freshness (more recent)
    a_fresh = a.freshness_seconds if a.freshness_seconds is not None else float("inf")
    b_fresh = b.freshness_seconds if b.freshness_seconds is not None else float("inf")
    if a_fresh != b_fresh:
        return a if a_fresh < b_fresh else b

    return a


def merge_snapshots(*snapshots: MarketSnapshot) -> MarketSnapshot:
    """Merge multiple MarketSnapshots into one.

    Iterates over all FieldMeta fields in the dataclass and picks
    the first available value from the snapshots (in order).
    Also merges non-FieldMeta attributes like factor_states, greeks_by_strike, etc.
    """
    if not snapshots:
        return MarketSnapshot(data_status="UNAVAILABLE", missing_fields=["ALL"])

    # Start from the first snapshot
    base = snapshots[0]
    merged = MarketSnapshot(
        snapshot_timestamp=base.snapshot_timestamp,
        timezone=base.timezone,
        source="+".join(s.source for s in snapshots),
        data_status=base.data_status,
        missing_fields=[],
    )

    # Get all FieldMeta field names
    field_names = [
        f for f in dir(base)
        if isinstance(getattr(base, f, None), FieldMeta)
    ]

    for fname in field_names:
        fields = [getattr(s, fname) for s in snapshots]
        best = fields[0]
        for f in fields[1:]:
            best = _pick(best, f)
        setattr(merged, fname, best)

    # Merge non-FieldMeta attributes
    _merge_non_field_attrs(merged, snapshots)

    # Determine merged data status
    available = sum(
        1 for f in field_names
        if getattr(merged, f).quality in ("GOOD", "PARTIAL")
    )
    total = len(field_names)

    if available >= total * 0.5:
        merged.data_status = "LIVE"
    elif available > 0:
        merged.data_status = "DELAYED"
    else:
        merged.data_status = "UNAVAILABLE"

    # Track missing critical fields
    merged.missing_fields = [
        f for f in field_names
        if getattr(merged, f).quality not in ("GOOD", "PARTIAL")
    ]

    return merged


def _merge_non_field_attrs(merged: MarketSnapshot, snapshots: list[MarketSnapshot]) -> None:
    """Merge non-FieldMeta attributes from snapshots.

    Priority: first non-None value wins.
    """
    _NON_FIELD_ATTRS = [
        "factor_states",
        "greeks_by_strike",
        "expected_move_analysis",
        "theta_decay_schedules",
        "suitability_scores",
    ]

    for attr in _NON_FIELD_ATTRS:
        for snap in snapshots:
            val = getattr(snap, attr, None)
            if val is not None:
                setattr(merged, attr, val)
                break
