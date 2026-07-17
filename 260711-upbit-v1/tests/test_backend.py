from datetime import datetime, timezone

import pandas as pd
from fastapi.testclient import TestClient

import backend.main as backend_module
import engine.cache as cache_module
from backend.main import app
from engine.cache import save_result, save_sweep_result
from tests.signal_fixtures import make_oscillating_df


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    return TestClient(app)


def test_health_check():
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_heatmap_returns_latest_sweep_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_sweep_result(
        run_id="r1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=5.0, sharpe=1.0, max_drawdown=2.0,
    )

    resp = client.get("/api/v1/eda/heatmap")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["market"] == "KRW-BTC"
    assert body[0]["return_rate"] == 5.0


def test_ranking_returns_combined_only(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_sweep_result(
        run_id="r1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=100.0, sharpe=None, max_drawdown=None,
    )
    save_sweep_result(
        run_id="r2", signal_set_name="mixed_all", is_combined=True,
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=8.0, sharpe=None, max_drawdown=None,
    )

    resp = client.get("/api/v1/eda/ranking")
    body = resp.json()
    assert len(body) == 1
    assert body[0]["market"] == "KRW-ETH"


def test_combos_and_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_sweep_result(
        run_id="r1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=5.0, sharpe=1.0, max_drawdown=2.0,
    )

    combos_resp = client.get("/api/v1/eda/combos")
    assert combos_resp.json() == [
        {"signal_set_name": "macd_cross", "is_combined": False, "market": "KRW-BTC", "timeframe": "days"}
    ]

    history_resp = client.get(
        "/api/v1/eda/history",
        params={"signal_set_name": "macd_cross", "market": "KRW-BTC", "timeframe": "days", "is_combined": False},
    )
    assert len(history_resp.json()) == 1


def test_backtest_detail_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/backtests/does-not-exist")
    assert resp.status_code == 404


def test_backtest_detail_returns_result_for_known_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="SignalStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": 1.2, "max_drawdown": 3.0,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [],
        },
    )

    resp = client.get("/api/v1/backtests/r1")
    assert resp.status_code == 200
    assert resp.json()["final_value"] == 10500.0


def test_get_signals_returns_registered_signal_keys():
    from signals import SIGNAL_REGISTRY

    client = TestClient(app)
    resp = client.get("/api/v1/eda/signals")
    assert resp.status_code == 200
    assert resp.json() == sorted(SIGNAL_REGISTRY.keys())


def _patch_get_candles(monkeypatch, df: pd.DataFrame | None = None):
    monkeypatch.setattr(
        backend_module, "get_candles",
        lambda market, timeframe, start, end: df if df is not None else make_oscillating_df(),
    )


def test_run_backtest_returns_run_id_and_is_retrievable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": ["macd_cross"],
        },
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert run_id

    detail_resp = client.get(f"/api/v1/backtests/{run_id}")
    assert detail_resp.status_code == 200
    assert "final_value" in detail_resp.json()


def test_run_backtest_rejects_empty_signal_keys(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": [],
        },
    )
    assert resp.status_code == 400


def test_run_backtest_rejects_unknown_signal_key(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": ["no_such_signal"],
        },
    )
    assert resp.status_code == 400


def test_run_backtest_rejects_reversed_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-03-01",
            "end": "2026-01-01",
            "signal_keys": ["macd_cross"],
        },
    )
    assert resp.status_code == 400


def test_run_backtest_rejects_empty_candle_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    empty_df = pd.DataFrame(columns=["candle_time", "open", "high", "low", "close", "volume"])
    _patch_get_candles(monkeypatch, df=empty_df)

    resp = client.post(
        "/api/v1/backtests/run",
        json={
            "market": "KRW-BTC",
            "timeframe": "days",
            "start": "2026-01-01",
            "end": "2026-03-01",
            "signal_keys": ["macd_cross"],
        },
    )
    assert resp.status_code == 400
