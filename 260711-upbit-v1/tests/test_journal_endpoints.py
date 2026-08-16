from fastapi.testclient import TestClient

import engine.cache as cache_module
import trading.db as trading_db_module
from backend.main import app
from tests.trading_db_fixtures import insert_live_strategy


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(trading_db_module, "DB_PATH", tmp_path / "trading.db")
    return TestClient(app)


def test_journal_summary_returns_empty_state(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/journal/summary")

    assert resp.status_code == 200
    assert resp.json()["strategies"] == []


def test_journal_summary_includes_approved_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(trading_db_module, status="draft")
    trading_db_module.approve_live_strategy(strategy_id, 100_000.0)

    resp = client.get("/api/v1/journal/summary")

    body = resp.json()
    assert len(body["strategies"]) == 1
    assert body["strategies"][0]["id"] == strategy_id


def test_journal_market_detail_returns_404_for_missing_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/journal/markets/KRW-DOGE")

    assert resp.status_code == 404


def test_journal_market_detail_returns_200_for_approved_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(trading_db_module, status="draft", market="KRW-DOGE")
    trading_db_module.approve_live_strategy(strategy_id, 100_000.0)

    resp = client.get("/api/v1/journal/markets/KRW-DOGE")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-DOGE"
    assert body["trade_log"] == []
    assert body["backtest_comparison"] is None
    assert body["daily"] == []
