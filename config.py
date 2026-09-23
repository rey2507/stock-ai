APP_NAME = "Nifty 50 Verdict Dashboard"
APP_ICON = "\U0001f4ca"

# --- Component names ---
INTRADAY_COMPONENTS = ["Momentum", "Volume", "Futures", "Options", "Participation"]
WEEKLY_COMPONENTS = ["Capital Flows", "Macro", "Earnings", "Participation", "Derivatives"]

# --- High-priority components for conflict detection ---
INTRADAY_PRIMARY = {"Momentum", "Futures", "Participation"}
WEEKLY_PRIMARY = {"Capital Flows", "Macro", "Earnings"}

# --- Verdict thresholds (raw score) ---
INTRADAY_THRESHOLDS = {
    (4, 5): ("BULLISH", "SETUP"),
    (2, 3): ("BULLISH", "BIAS"),
    (-1, 1): ("MIXED", "WAIT"),
    (-3, -2): ("BEARISH", "BIAS"),
    (-5, -4): ("BEARISH", "SETUP"),
}

WEEKLY_THRESHOLDS = {
    (4, 5): ("BULLISH", "SETUP"),
    (2, 3): ("BULLISH", "BIAS"),
    (-1, 1): ("MIXED", "WAIT"),
    (-3, -2): ("BEARISH", "BIAS"),
    (-5, -4): ("BEARISH", "SETUP"),
}

# --- Indicator thresholds ---
MOMENTUM_VWAP_SLOPE_THRESHOLD = 0.01
VOLUME_CONFIRMATION_RATIO = 1.15
PCR_SUPPORTIVE = 1.0
PCR_RESISTIVE = 0.8
AD_STRONG = 1.5
AD_WEAK = 0.7
SECTOR_STRONG_PARTICIPATION = 0.6
SECTOR_WEAK_PARTICIPATION = 0.4
EARNINGS_STRONG = 12.0
EARNINGS_WEAK = 0.0
MACRO_CRUDE_SUPPORTIVE = 80.0
MACRO_CRUDE_HEADWIND = 90.0
MACRO_INR_STRONG = 83.5
MACRO_INR_WEAK = 84.5
MACRO_YIELD_SUPPORTIVE = 4.2
MACRO_YIELD_HEADWIND = 4.6

# --- Factor direction ---
FACTOR_THRESHOLDS = {
    "crude_threshold_pct": 2.0,
    "usdinr_threshold_pct": 0.5,
    "us10y_threshold_pct": 0.2,
    "default_threshold_pct": 2.0,
}

FACTOR_NIFTY_INTERPRETATIONS = {
    "crude": {
        "bullish_for_nifty": "NEGATIVE",
        "bullish_explanation": "Crude rising → increased input costs → headwind for corporates",
        "bearish_for_nifty": "POSITIVE",
        "bearish_explanation": "Crude falling → reduced input costs → tailwind for corporates",
    },
    "usdinr": {
        "bullish_for_nifty": "NEGATIVE",
        "bullish_explanation": "INR weakening → capital outflows → bearish",
        "bearish_for_nifty": "POSITIVE",
        "bearish_explanation": "INR strengthening → improved export competitiveness → bullish",
    },
    "us10y": {
        "bullish_for_nifty": "POSITIVE",
        "bullish_explanation": "US yields falling → EM capital inflows positive → bullish",
        "bearish_for_nifty": "NEGATIVE",
        "bearish_explanation": "US yields rising → EM outflows risk → bearish",
    },
    "fii": {
        "bullish_for_nifty": "POSITIVE",
        "bullish_explanation": "FII buying → foreign capital inflows → bullish",
        "bearish_for_nifty": "NEGATIVE",
        "bearish_explanation": "FII selling → foreign capital outflows → bearish",
    },
    "dii": {
        "bullish_for_nifty": "POSITIVE",
        "bullish_explanation": "DII buying → domestic support → bullish",
        "bearish_for_nifty": "NEGATIVE",
        "bearish_explanation": "DII selling → domestic pressure → bearish",
    },
    "rbi_rate": {
        "bullish_for_nifty": "POSITIVE",
        "bullish_explanation": "Stable/cutting rates → positive for rate-sensitive sectors",
        "bearish_for_nifty": "NEGATIVE",
        "bearish_explanation": "Rising rates → cost pressure, negative for equities",
    },
    "inflation": {
        "bullish_for_nifty": "POSITIVE",
        "bullish_explanation": "Stable/falling inflation → room for growth, rate stability",
        "bearish_for_nifty": "NEGATIVE",
        "bearish_explanation": "Rising inflation → rate hike risk, cost pressure",
    },
    "gdp": {
        "bullish_for_nifty": "POSITIVE",
        "bullish_explanation": "GDP accelerating → economic expansion → bullish",
        "bearish_for_nifty": "NEGATIVE",
        "bearish_explanation": "GDP decelerating → growth concerns → bearish",
    },
    "pmi": {
        "bullish_for_nifty": "POSITIVE",
        "bullish_explanation": "PMI > 55 → manufacturing expansion → bullish",
        "bearish_for_nifty": "NEGATIVE",
        "bearish_explanation": "PMI < 50 → contraction → bearish",
    },
}

HISTORY_QUALITY_THRESHOLDS = {
    "sufficient_days": 15,
    "moderate_days": 8,
    "limited_days": 3,
}

# --- Greeks / Black-Scholes ---
RISK_FREE_RATE = 0.065
DIVIDEND_YIELD = 0.0
