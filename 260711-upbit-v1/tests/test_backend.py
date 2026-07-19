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


def test_get_markets_returns_krw_markets_only(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_get_krw_markets():
        return [{"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"}]

    monkeypatch.setattr(backend_module, "get_krw_markets", _fake_get_krw_markets)

    resp = client.get("/api/v1/markets")
    assert resp.status_code == 200
    assert resp.json() == [{"market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin"}]


def test_get_indicator_catalog_covers_all_registered_indicators(monkeypatch, tmp_path):
    from engine.indicators import INDICATOR_FACTORY

    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/indicators/catalog")
    assert resp.status_code == 200
    body = resp.json()

    catalog_values = {item["value"] for item in body}
    assert catalog_values == set(INDICATOR_FACTORY.keys())

    for item in body:
        assert item["description"], f"{item['value']}에 description이 없음"
        assert item["example"], f"{item['value']}에 example이 없음"
        assert item["category"] in {"추세", "오실레이터", "거래량"}


def _patch_get_candles(monkeypatch, df: pd.DataFrame | None = None):
    monkeypatch.setattr(
        backend_module, "get_candles",
        lambda market, timeframe, start, end: df if df is not None else make_oscillating_df(),
    )


_VALID_BUY = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 60}]}
_VALID_SELL = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 40}]}


def _run_request(**overrides) -> dict:
    body = {
        "market": "KRW-BTC",
        "timeframe": "days",
        "start": "2026-01-01",
        "end": "2026-03-01",
        "initial_capital": 1_000_000,
        "buy_conditions": _VALID_BUY,
        "sell_conditions": _VALID_SELL,
    }
    body.update(overrides)
    return body


def test_run_backtest_returns_run_id_and_is_retrievable(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/run", json=_run_request())
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    assert run_id

    detail_resp = client.get(f"/api/v1/backtests/{run_id}")
    assert detail_resp.status_code == 200
    assert "final_value" in detail_resp.json()


def test_run_backtest_rejects_empty_buy_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(buy_conditions={"type": "AND", "conditions": []}),
    )
    assert resp.status_code == 400
    assert "매수 조건" in resp.json()["detail"]


def test_run_backtest_rejects_empty_sell_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(sell_conditions={"type": "AND", "conditions": []}),
    )
    assert resp.status_code == 400
    assert "매도 조건" in resp.json()["detail"]


def test_run_backtest_rejects_unknown_indicator(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    bad_buy = {"type": "AND", "conditions": [{"indicator": "NOPE", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(buy_conditions=bad_buy))
    assert resp.status_code == 400
    assert "NOPE" in resp.json()["detail"]


def test_run_backtest_rejects_reversed_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/run", json=_run_request(start="2026-03-01", end="2026-01-01"))
    assert resp.status_code == 400


def test_run_backtest_rejects_market_not_in_krw_list(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-NOTLISTED"))
    assert resp.status_code == 400
    assert "KRW-NOTLISTED" in resp.json()["detail"]


def test_run_backtest_rejects_empty_candle_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    empty_df = pd.DataFrame(columns=["candle_time", "open", "high", "low", "close", "volume"])
    _patch_get_candles(monkeypatch, df=empty_df)

    resp = client.post("/api/v1/backtests/run", json=_run_request())
    assert resp.status_code == 400


def test_run_backtest_uses_requested_initial_capital(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/run", json=_run_request(initial_capital=5_000_000))
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    resp2 = client.post("/api/v1/backtests/run", json=_run_request(initial_capital=9_000_000))
    assert resp2.status_code == 200
    run_id2 = resp2.json()["run_id"]

    assert run_id != run_id2, "운용자금이 다른데 같은 run_id(캐시 hit)가 나옴 — initial_capital이 캐시 키에 반영 안 됨"
