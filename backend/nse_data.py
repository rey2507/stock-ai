"""Paper-Trader — static NSE F&O metadata (Stage 3).

Lot sizes, contract multipliers, and margin rules. In production these are
fetched/maintained from NSE circulars; here they are static and versioned in
code. All numbers are approximations for paper-trading realism.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InstrumentMeta:
    symbol: str          # canonical NSE symbol
    name: str
    kind: str            # "index" | "stock"
    lot_size: int        # contracts per lot
    contract_multiplier: float  # rupees per point per unit quantity
    margin_pct: float    # SPAN-like margin for futures, % of notional


# --- Index futures (NSE circular FAOP70616: revised lots effective from
# December 30, 2025 / January 2026 series expiries) ---------------------------
# P&L per point = lot size (units), so contract_multiplier stays 1.0:
# NIFTY 1 lot = 65 units -> ₹65 per index point.
INDEX_META: dict[str, InstrumentMeta] = {
    "NIFTY":      InstrumentMeta("NIFTY", "Nifty 50", "index", 65, 1.0, 0.12),
    "BANKNIFTY":  InstrumentMeta("BANKNIFTY", "Nifty Bank", "index", 30, 1.0, 0.13),
    "FINNIFTY":   InstrumentMeta("FINNIFTY", "Nifty Financial Services", "index", 60, 1.0, 0.13),
    "MIDCPNIFTY": InstrumentMeta("MIDCPNIFTY", "Nifty Midcap Select", "index", 120, 1.0, 0.14),
}

# --- Stock futures (per NSE lot-size reference, Sep 2026) ---------------------
STOCK_META: dict[str, InstrumentMeta] = {
    "RELIANCE":  InstrumentMeta("RELIANCE", "Reliance Industries Ltd", "stock", 500, 1.0, 0.20),
    "TCS":       InstrumentMeta("TCS", "Tata Consultancy Services Ltd", "stock", 225, 1.0, 0.20),
    "INFY":      InstrumentMeta("INFY", "Infosys Ltd", "stock", 400, 1.0, 0.20),
    "HDFCBANK":  InstrumentMeta("HDFCBANK", "HDFC Bank Ltd", "stock", 650, 1.0, 0.20),
    "ICICIBANK": InstrumentMeta("ICICIBANK", "ICICI Bank Ltd", "stock", 700, 1.0, 0.20),
    "SBIN":      InstrumentMeta("SBIN", "State Bank of India", "stock", 750, 1.0, 0.20),
    "ITC":       InstrumentMeta("ITC", "ITC Ltd", "stock", 1725, 1.0, 0.20),
    "TATAMOTORS": InstrumentMeta("TATAMOTORS", "Tata Motors Passenger Vehicles Ltd", "stock", 1600, 1.0, 0.20),
    "AXISBANK":  InstrumentMeta("AXISBANK", "Axis Bank Ltd", "stock", 625, 1.0, 0.20),
    "LT":        InstrumentMeta("LT", "Larsen & Toubro Ltd", "stock", 175, 1.0, 0.20),
}

ALL_META: dict[str, InstrumentMeta] = {**INDEX_META, **STOCK_META}

# Spot prices for indices (stock spot = futures base in market_data.py).
INDEX_SPOT: dict[str, float] = {
    "NIFTY": 24_850.0,
    "BANKNIFTY": 51_200.0,
    "FINNIFTY": 23_400.0,
    "MIDCPNIFTY": 12_150.0,
}

# Margin for short options (% of notional; long options = premium paid).
SHORT_OPTION_MARGIN_PCT = 0.15


def lot_size(symbol: str) -> int:
    meta = ALL_META.get(symbol)
    return meta.lot_size if meta else 1


def contract_multiplier(symbol: str) -> float:
    meta = ALL_META.get(symbol)
    return meta.contract_multiplier if meta else 1.0


def margin_pct(symbol: str) -> float:
    meta = ALL_META.get(symbol)
    return meta.margin_pct if meta else 0.20


def is_fno(symbol: str) -> bool:
    return symbol in ALL_META


def fno_symbols() -> list[str]:
    return sorted(ALL_META)
