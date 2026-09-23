"""Instrument and Token Manager for NIFTY options.

Discovers and caches:
- NIFTY spot token
- Nearest futures contract
- ATM ± N strikes for nearest expiry
- Instrument metadata (expiry dates, strike list)
- Dynamic ATM detection from spot price

Never hardcodes tokens. Rebuilds at market open + periodically.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import logging

from providers.cache import cache, get_freshness_window

log = logging.getLogger(__name__)

NIFTY_SPOT_TOKEN = "99926000"
NSE_EXCHANGE = "NSE"
NFO_EXCHANGE = "NFO"

# How many strikes around ATM to track
STRIKE_RANGE = 2  # ATM ± 2 strikes


@dataclass
class InstrumentInfo:
    """Metadata for a single instrument."""
    symbol: str
    token: str
    exchange: str
    instrument_type: str  # INDEX | FUTIDX | OPTIDX
    expiry: Optional[str] = None
    strike: Optional[float] = None
    option_type: Optional[str] = None  # CE | PE
    lot_size: Optional[int] = None


@dataclass
class FuturesContract:
    """Nearest futures contract with metadata."""
    symbol: str
    token: str
    expiry: str
    expiry_date: datetime
    lot_size: Optional[int] = None


@dataclass
class OptionStrike:
    """A single strike with CE/PE tokens."""
    strike: float
    ce_token: Optional[str] = None
    ce_symbol: Optional[str] = None
    pe_token: Optional[str] = None
    pe_symbol: Optional[str] = None


@dataclass
class OptionChain:
    """Option chain for nearest expiry around ATM."""
    atm_strike: float
    expiry: str
    expiry_date: Optional[datetime] = None
    strikes: list[OptionStrike] = field(default_factory=list)
    futures_token: Optional[str] = None
    futures_symbol: Optional[str] = None


MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_expiry_date(expiry_str: str) -> Optional[datetime]:
    """Parse '29SEP26' into datetime."""
    m = re.match(r"(\d{2})([A-Z]{3})(\d{2})", expiry_str)
    if not m:
        return None
    day, month_str, year = m.groups()
    try:
        return datetime(2000 + int(year), MONTH_MAP[month_str], int(day))
    except (KeyError, ValueError):
        return None


class InstrumentManager:
    """Discovers and caches NIFTY instruments from SmartAPI.

    Thread-safe. Rebuilds token maps at market open and periodically.
    """

    def __init__(self, smartapi_client):
        """
        Args:
            smartapi_client: Low-level SmartAPI client instance.
        """
        self._client = smartapi_client
        self._last_build: Optional[datetime] = None
        self._build_interval = timedelta(minutes=30)

    @property
    def needs_rebuild(self) -> bool:
        """Check if token map needs refresh."""
        if self._last_build is None:
            return True
        return datetime.now() - self._last_build > self._build_interval

    def discover_all(self, spot_price: float) -> OptionChain:
        """Full discovery: futures + option chain around ATM.

        Args:
            spot_price: Current NIFTY spot price for ATM calculation.

        Returns:
            OptionChain with all strikes and tokens.
        """
        if self.needs_rebuild:
            self._invalidate_cache()

        # Check cache first
        cached = cache.get("instrument_option_chain")
        if cached and not self.needs_rebuild:
            return cached.value

        log.info("Discovering NIFTY instruments from SmartAPI...")
        all_instruments = self._search_all_nifty()
        if not all_instruments:
            log.warning("No NIFTY instruments found")
            return OptionChain(atm_strike=0, expiry="")

        # Parse into categorized lists
        futures = self._parse_futures(all_instruments)
        options = self._parse_options(all_instruments)

        if not futures:
            log.warning("No NIFTY futures found")
            return OptionChain(atm_strike=0, expiry="")

        # Find nearest futures expiry
        nearest_fut = self._nearest_expiry(futures)
        if not nearest_fut:
            return OptionChain(atm_strike=0, expiry="")

        expiry_str = nearest_fut["expiry"]
        expiry_options = [o for o in options if o["expiry"] == expiry_str]

        if not expiry_options:
            log.warning(f"No options found for expiry {expiry_str}")
            return OptionChain(
                atm_strike=0,
                expiry=expiry_str,
                futures_token=nearest_fut["token"],
                futures_symbol=nearest_fut["symbol"],
            )

        # Find ATM strike
        strikes = sorted(set(o["strike"] for o in expiry_options))
        atm_strike = min(strikes, key=lambda s: abs(s - spot_price)) if strikes else 0

        # Build strike list (ATM ± N)
        target_strikes = [
            s for s in strikes
            if abs(s - atm_strike) <= STRIKE_RANGE * self._strike_step(atm_strike)
        ]
        if not target_strikes and strikes:
            target_strikes = strikes[:5]

        # Map CE/PE to strikes
        strike_map: dict[float, OptionStrike] = {}
        for s in target_strikes:
            strike_map[s] = OptionStrike(strike=s)

        for o in expiry_options:
            if o["strike"] in strike_map:
                os = strike_map[o["strike"]]
                if o["type"] == "CE":
                    os.ce_token = o["token"]
                    os.ce_symbol = o["symbol"]
                elif o["type"] == "PE":
                    os.pe_token = o["token"]
                    os.pe_symbol = o["symbol"]

        chain = OptionChain(
            atm_strike=atm_strike,
            expiry=expiry_str,
            expiry_date=parse_expiry_date(expiry_str),
            strikes=sorted(strike_map.values(), key=lambda x: x.strike),
            futures_token=nearest_fut["token"],
            futures_symbol=nearest_fut["symbol"],
        )

        # Cache the result
        cache.put(
            "instrument_option_chain",
            chain,
            source="SmartAPI",
            freshness_window=get_freshness_window("oi"),
        )
        self._last_build = datetime.now()

        log.info(
            f"Instruments discovered: futures={nearest_fut['symbol']}, "
            f"ATM={atm_strike}, strikes={len(chain.strikes)}, "
            f"expiry={expiry_str}"
        )
        return chain

    def _search_all_nifty(self) -> list[dict]:
        """Search NFO for all NIFTY instruments."""
        try:
            # SmartAPIClient.search_scrip returns the unwrapped list directly
            result = self._client.search_scrip(NFO_EXCHANGE, "NIFTY")
            if isinstance(result, list):
                return result
        except Exception as e:
            log.error(f"Instrument search failed: {e}")
        return []

    def _parse_futures(self, instruments: list[dict]) -> list[dict]:
        """Extract NIFTY 50 futures from search results (excludes NIFTYNXT, NIFTYJR, etc.)."""
        futures = []
        for inst in instruments:
            sym = inst.get("tradingsymbol", "")
            # Match NIFTY 50 futures only: NIFTY29SEP26FUT (not NIFTYNXT, NIFTYJR, FINNIFTY)
            if re.match(r"^NIFTY\d{2}[A-Z]{3}\d{2}FUT$", sym):
                expiry_match = re.match(r"NIFTY(\d{2}[A-Z]{3}\d{2})FUT", sym)
                if expiry_match:
                    expiry_str = expiry_match.group(1)
                    expiry_date = parse_expiry_date(expiry_str)
                    if expiry_date:
                        futures.append({
                            "symbol": sym,
                            "token": inst.get("symboltoken", ""),
                            "expiry": expiry_str,
                            "expiry_date": expiry_date,
                            "lot_size": inst.get("lotsize"),
                        })
        return futures

    def _parse_options(self, instruments: list[dict]) -> list[dict]:
        """Extract NIFTY 50 options from search results (excludes NIFTYNXT, NIFTYJR, etc.)."""
        options = []
        pattern = re.compile(r"^NIFTY(\d{2}[A-Z]{3}\d{2})(\d{5})(CE|PE)$")
        for inst in instruments:
            sym = inst.get("tradingsymbol", "")
            m = pattern.match(sym)
            if m:
                expiry_str, strike_str, opt_type = m.groups()
                options.append({
                    "symbol": sym,
                    "token": inst.get("symboltoken", ""),
                    "expiry": expiry_str,
                    "strike": float(strike_str),
                    "type": opt_type,
                })
        return options

    def _nearest_expiry(self, futures: list[dict]) -> Optional[dict]:
        """Find the nearest expiry futures contract."""
        now = datetime.now()
        # Filter to non-expired (handle both naive and aware datetimes)
        valid = []
        for f in futures:
            exp = f["expiry_date"]
            try:
                if exp.tzinfo is not None:
                    exp = exp.replace(tzinfo=None)
                valid.append(f) if exp > now else None
            except TypeError:
                valid.append(f)
        if not valid:
            valid = futures
        valid.sort(key=lambda f: f["expiry_date"])
        return valid[0] if valid else None

    def _strike_step(self, atm: float) -> float:
        """Determine strike spacing based on ATM level."""
        if atm > 40000:
            return 100.0
        elif atm > 20000:
            return 50.0
        elif atm > 10000:
            return 25.0
        else:
            return 10.0

    def _invalidate_cache(self):
        """Clear cached instrument data."""
        cache.invalidate("instrument_option_chain")
        self._last_build = None
