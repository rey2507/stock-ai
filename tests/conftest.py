"""Shared Stage 3/4 test fixtures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import db as dbm
from backend import main
from backend.market_data import MockMarketData
from backend.market_data_manager import MarketManager
from backend.providers.mock_provider import MockMarketDataProvider

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point the app at a throwaway database and a deterministic,
    mock-only market manager."""
    db_path = tmp_path / "trader.db"
    monkeypatch.setattr(dbm, "DB_PATH", db_path)
    md = MockMarketData(volatility=0.0)
    monkeypatch.setattr(
        main, "market_data",
        MarketManager(providers=[MockMarketDataProvider(md)], quote_ttl=0.0),
    )
    dbm.init_db()
    yield md  # tests reach .md to shift spots / tick the theta clock
