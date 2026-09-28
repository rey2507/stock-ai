"""Paper-Trader — SQLite data layer (Stage 3).

F&O-capable schema v3:

- ``instruments`` — F&O underlyings (indices + stocks) with lot size,
  multiplier, and margin metadata from ``nse_data``.
- ``orders`` — EQ/FUT/OPT orders with stop-loss and bracket support
  (``parent_id`` + ``leg_role`` link children to parents).
- ``positions`` — signed quantity (long +, short −) keyed by
  (symbol, instrument_type, expiry, strike, option_type).
- ``watchlist`` — user's tracked symbols.
- ``account`` — single row: cash + starting capital; margin is derived.

Schema versioning: an existing pre-v3 database is detected via ``meta`` and
reset (paper trading — positions from Stage 2 do not survive the upgrade).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from backend.nse_data import ALL_META, SHORT_OPTION_MARGIN_PCT, INDEX_SPOT

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "trader.db"

STARTING_CAPITAL = 1_000_000.0
SCHEMA_VERSION = 4  # v4: + macro_journal (Stage 16)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instruments (
    symbol       TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('index', 'stock')),
    lot_size     INTEGER NOT NULL,
    multiplier   REAL NOT NULL,
    margin_pct   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS account (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    cash             REAL NOT NULL,
    starting_capital REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    instrument_type TEXT NOT NULL DEFAULT 'EQ'
                    CHECK (instrument_type IN ('EQ', 'FUT', 'CE', 'PE')),
    expiry          TEXT NOT NULL DEFAULT '',
    strike          REAL NOT NULL DEFAULT 0,
    side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity        REAL NOT NULL CHECK (quantity > 0),
    order_type      TEXT NOT NULL
                    CHECK (order_type IN ('MARKET', 'LIMIT', 'SL', 'BRACKET')),
    price           REAL,
    trigger_price   REAL,
    status          TEXT NOT NULL DEFAULT 'OPEN'
                    CHECK (status IN ('PENDING', 'OPEN', 'FILLED',
                                      'CANCELLED', 'REJECTED')),
    leg_role        TEXT NOT NULL DEFAULT 'ENTRY'
                    CHECK (leg_role IN ('ENTRY', 'TARGET', 'STOPLOSS')),
    parent_id       INTEGER REFERENCES orders(id),
    filled_price    REAL,
    filled_at       TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT NOT NULL,
    instrument_type TEXT NOT NULL DEFAULT 'EQ'
                    CHECK (instrument_type IN ('EQ', 'FUT', 'CE', 'PE')),
    expiry          TEXT NOT NULL DEFAULT '',
    strike          REAL NOT NULL DEFAULT 0,
    side            TEXT NOT NULL DEFAULT 'LONG'
                    CHECK (side IN ('LONG', 'SHORT')),
    quantity        REAL NOT NULL,
    avg_price       REAL NOT NULL,
    realized_pnl    REAL NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (symbol, instrument_type, expiry, strike, side)
);

CREATE TABLE IF NOT EXISTS watchlist (
    symbol    TEXT PRIMARY KEY,
    added_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS macro_journal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date      TEXT NOT NULL UNIQUE,
    expected        TEXT NOT NULL CHECK (expected IN ('UP', 'DOWN', 'FLAT')),
    confidence      INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),
    reasons         TEXT NOT NULL DEFAULT '',
    actual          TEXT,
    outcome         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    """Yield a connection; commit on success, roll back on error."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _existing_schema_version(conn: sqlite3.Connection) -> int:
    has_meta = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
    ).fetchone()
    if not has_meta:
        # Pre-v3 database (Stage 2 had no meta table).
        has_orders = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone()
        return 2 if has_orders else 0
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    return int(row["value"]) if row else 0


def init_db() -> None:
    """Create/upgrade the schema and seed instruments + account + watchlist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        version = _existing_schema_version(conn)
        if version < SCHEMA_VERSION:
            # Paper DB: reset rather than migrate (Stage 2 book is cash-equity
            # only and incompatible with signed-quantity F&O positions).
            for table in ("positions", "orders", "account", "instruments",
                          "watchlist", "meta"):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
        if version < 4:
            # v4 upgrade is purely additive: create the journal table without
            # dropping the trading book (macro_journal schema lives in SCHEMA).
            conn.execute(
                "CREATE TABLE IF NOT EXISTS macro_journal ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "trade_date TEXT NOT NULL UNIQUE,"
                "expected TEXT NOT NULL CHECK (expected IN ('UP','DOWN','FLAT')),"
                "confidence INTEGER NOT NULL CHECK (confidence BETWEEN 1 AND 5),"
                "reasons TEXT NOT NULL DEFAULT '',"
                "actual TEXT, outcome TEXT,"
                "created_at TEXT NOT NULL DEFAULT (datetime('now')),"
                "resolved_at TEXT)")

        conn.executescript(SCHEMA)

        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

        for symbol, meta in sorted(ALL_META.items()):
            conn.execute(
                "INSERT INTO instruments (symbol, name, kind, lot_size, multiplier, margin_pct) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, kind=excluded.kind, "
                "lot_size=excluded.lot_size, multiplier=excluded.multiplier, margin_pct=excluded.margin_pct",
                (symbol, meta.name, meta.kind, meta.lot_size,
                 meta.contract_multiplier, meta.margin_pct),
            )

        conn.execute(
            "INSERT OR IGNORE INTO account (id, cash, starting_capital) VALUES (1, ?, ?)",
            (STARTING_CAPITAL, STARTING_CAPITAL),
        )

        # Sensible default watchlist on first run.
        for symbol in ("NIFTY", "BANKNIFTY", "RELIANCE"):
            conn.execute("INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol,))


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

def get_account(conn: sqlite3.Connection) -> dict[str, float]:
    row = conn.execute("SELECT * FROM account WHERE id = 1").fetchone()
    if row is None:
        raise RuntimeError("account row missing — call init_db() first")
    return dict(row)


def update_account_cash(conn: sqlite3.Connection, cash: float) -> None:
    conn.execute("UPDATE account SET cash = ? WHERE id = 1", (cash,))


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def record_order(
    conn: sqlite3.Connection,
    symbol: str,
    instrument_type: str,
    side: str,
    quantity: float,
    order_type: str,
    expiry: str = "",
    strike: float = 0.0,
    price: float | None = None,
    trigger_price: float | None = None,
    status: str = "OPEN",
    leg_role: str = "ENTRY",
    parent_id: int | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO orders (symbol, instrument_type, expiry, strike, side, quantity, "
        "order_type, price, trigger_price, status, leg_role, parent_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (symbol, instrument_type, expiry, strike, side, quantity, order_type,
         price, trigger_price, status, leg_role, parent_id),
    )
    return int(cursor.lastrowid)


def set_order_status(
    conn: sqlite3.Connection,
    order_id: int,
    status: str,
    filled_price: float | None = None,
) -> None:
    if filled_price is not None:
        conn.execute(
            "UPDATE orders SET status = ?, filled_price = ?, "
            "filled_at = datetime('now') WHERE id = ?",
            (status, filled_price, order_id),
        )
    else:
        conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))


def get_order(conn: sqlite3.Connection, order_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def get_child_orders(conn: sqlite3.Connection, parent_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM orders WHERE parent_id = ? ORDER BY id", (parent_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_working_orders(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Orders that can still trigger:
    - standalone SL orders (leg_role ENTRY, no parent),
    - bracket exit legs (TARGET / STOPLOSS),
    - bracket LIMIT entries awaiting a touch."""
    rows = conn.execute(
        "SELECT * FROM orders WHERE status = 'OPEN' AND ("
        "  (order_type = 'SL' AND leg_role = 'ENTRY' AND parent_id IS NULL)"
        "  OR leg_role IN ('TARGET', 'STOPLOSS')"
        "  OR (order_type = 'LIMIT' AND leg_role = 'ENTRY' AND parent_id IS NOT NULL)"
        ")"
    ).fetchall()
    return [dict(r) for r in rows]


def get_recent_orders(conn: sqlite3.Connection, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def get_position(
    conn: sqlite3.Connection,
    symbol: str,
    instrument_type: str = "EQ",
    expiry: str = "",
    strike: float = 0.0,
    side: str = "LONG",
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM positions WHERE symbol = ? AND instrument_type = ? "
        "AND expiry = ? AND strike = ? AND side = ?",
        (symbol, instrument_type, expiry, strike, side),
    ).fetchone()
    return dict(row) if row else None


def upsert_position(
    conn: sqlite3.Connection,
    symbol: str,
    instrument_type: str,
    expiry: str,
    strike: float,
    side: str,
    quantity: float,
    avg_price: float,
) -> None:
    conn.execute(
        """
        INSERT INTO positions (symbol, instrument_type, expiry, strike, side,
                               quantity, avg_price, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(symbol, instrument_type, expiry, strike, side) DO UPDATE SET
            quantity = excluded.quantity,
            avg_price = excluded.avg_price,
            updated_at = excluded.updated_at
        """,
        (symbol, instrument_type, expiry, strike, side, quantity, avg_price),
    )


def update_position_realized(
    conn: sqlite3.Connection, position_id: int, realized_delta: float
) -> None:
    conn.execute(
        "UPDATE positions SET realized_pnl = realized_pnl + ?, "
        "updated_at = datetime('now') WHERE id = ?",
        (realized_delta, position_id),
    )


def delete_position(conn: sqlite3.Connection, position_id: int) -> None:
    conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))


def get_all_positions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM positions ORDER BY instrument_type, symbol, expiry, strike"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Watchlist + instruments
# ---------------------------------------------------------------------------

def get_watchlist(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT symbol FROM watchlist ORDER BY added_at").fetchall()
    return [r["symbol"] for r in rows]


def add_to_watchlist(conn: sqlite3.Connection, symbol: str) -> bool:
    """Returns False if the symbol was already present."""
    cursor = conn.execute(
        "INSERT OR IGNORE INTO watchlist (symbol) VALUES (?)", (symbol,)
    )
    return cursor.rowcount > 0


def remove_from_watchlist(conn: sqlite3.Connection, symbol: str) -> bool:
    cursor = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
    return cursor.rowcount > 0


# ---------------------------------------------------------------------------
# Macro journal (Stage 16)
# ---------------------------------------------------------------------------

def upsert_journal_entry(
    conn: sqlite3.Connection, trade_date: str, expected: str,
    confidence: int, reasons: str,
) -> bool:
    """Insert or update today's expectation. Returns True if inserted."""
    cursor = conn.execute(
        "INSERT INTO macro_journal (trade_date, expected, confidence, reasons) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(trade_date) DO UPDATE SET expected=excluded.expected, "
        "confidence=excluded.confidence, reasons=excluded.reasons, "
        "actual=NULL, outcome=NULL, resolved_at=NULL",
        (trade_date, expected, confidence, reasons),
    )
    return cursor.rowcount > 0


def get_journal(conn: sqlite3.Connection, limit: int = 60) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM macro_journal ORDER BY trade_date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def delete_journal_entry(conn: sqlite3.Connection, entry_id: int) -> bool:
    cursor = conn.execute("DELETE FROM macro_journal WHERE id = ?", (entry_id,))
    return cursor.rowcount > 0


def journal_stats(conn: sqlite3.Connection) -> dict[str, int]:
    """Simple descriptive tally — correct/wrong/unclear/pending counts."""
    row = conn.execute(
        "SELECT "
        "SUM(CASE WHEN outcome='correct' THEN 1 ELSE 0 END) AS correct, "
        "SUM(CASE WHEN outcome='wrong' THEN 1 ELSE 0 END) AS wrong, "
        "SUM(CASE WHEN outcome='unclear' THEN 1 ELSE 0 END) AS unclear, "
        "SUM(CASE WHEN actual IS NULL THEN 1 ELSE 0 END) AS pending "
        "FROM macro_journal"
    ).fetchone()
    return {k: int(row[k] or 0) for k in ("correct", "wrong", "unclear", "pending")}


def get_instrument(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM instruments WHERE symbol = ?", (symbol,)
    ).fetchone()
    return dict(row) if row else None


def get_instruments(conn: sqlite3.Connection, kind: str | None = None) -> list[dict[str, Any]]:
    if kind:
        rows = conn.execute(
            "SELECT * FROM instruments WHERE kind = ? ORDER BY symbol", (kind,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM instruments ORDER BY symbol").fetchall()
    return [dict(r) for r in rows]
