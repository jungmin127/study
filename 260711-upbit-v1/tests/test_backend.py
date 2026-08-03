from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi.testclient import TestClient

import backend.main as backend_module
import engine.cache as cache_module
from backend.main import app
from engine.cache import save_result, save_sweep_result
from engine.cache import save_segment_classification
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
    _patch_get_candles(monkeypatch)
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
    body = resp.json()
    assert body["final_value"] == 10500.0
    assert body["market"] == "KRW-BTC"
    assert body["timeframe"] == "days"
    assert body["initial_capital"] == 10000
    assert body["metrics"]["total_trades"] == 0
    assert isinstance(body["ohlcv"], list)
    assert len(body["ohlcv"]) > 0
    # candle_time에 UTC 오프셋이 명시돼야 프론트의 new Date(...)가 이를 로컬 시간대로
    # 잘못 해석하지 않는다(naive 문자열은 브라우저 로컬 시간대로 파싱되어 캔들이
    # 엉뚱한 날짜/시간에 그려지는 버그가 있었다). trades의 entryTime/exitTime에도
    # 동일하게 오프셋을 붙여, 캔들과 거래 마커가 같은 기준으로 파싱되게 한다.
    assert body["ohlcv"][0]["time"].endswith("+00:00")


def test_delete_backtest_removes_run_from_list(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="삭제될 포트",
    )

    resp = client.delete("/api/v1/backtests/r1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    list_resp = client.get("/api/v1/backtests")
    assert list_resp.json() == []


def test_delete_backtest_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.delete("/api/v1/backtests/does-not-exist")
    assert resp.status_code == 404


def test_refresh_backtest_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/api/v1/backtests/does-not-exist/refresh")
    assert resp.status_code == 404


def test_refresh_backtest_keeps_same_run_id_and_updates_end(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    _patch_get_current_prices(monkeypatch)

    create_resp = client.post("/api/v1/backtests/run", json=_run_request())
    run_id = create_resp.json()["run_id"]
    original_end = client.get(f"/api/v1/backtests/{run_id}").json()["end"]

    refresh_resp = client.post(f"/api/v1/backtests/{run_id}/refresh")

    assert refresh_resp.status_code == 200
    assert refresh_resp.json() == {"run_id": run_id}

    detail_resp = client.get(f"/api/v1/backtests/{run_id}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["end"] != original_end

    list_resp = client.get("/api/v1/backtests")
    assert len(list_resp.json()) == 1, "덮어쓰기이므로 목록에 런이 늘어나면 안 됨"


def test_refresh_backtest_preserves_title_and_description(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    _patch_get_current_prices(monkeypatch)

    create_resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(title="추적용", description="설명"),
    )
    run_id = create_resp.json()["run_id"]

    client.post(f"/api/v1/backtests/{run_id}/refresh")

    list_resp = client.get("/api/v1/backtests")
    run = next(r for r in list_resp.json() if r["run_id"] == run_id)
    assert run["title"] == "추적용"
    assert run["description"] == "설명"


def test_get_signals_returns_registered_signal_keys():
    from signals import SIGNAL_REGISTRY

    client = TestClient(app)
    resp = client.get("/api/v1/eda/signals")
    assert resp.status_code == 200
    assert resp.json() == sorted(SIGNAL_REGISTRY.keys())


def test_get_markets_returns_krw_markets_with_ticker(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_get_krw_markets_with_ticker():
        return [{
            "market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin",
            "price": 150_000_000.0, "change_rate": 0.0235, "change_price": 3_500_000.0,
            "trade_price_24h": 123_400_000_000.0,
        }]

    monkeypatch.setattr(backend_module, "get_krw_markets_with_ticker", _fake_get_krw_markets_with_ticker)

    resp = client.get("/api/v1/markets")
    assert resp.status_code == 200
    assert resp.json() == [{
        "market": "KRW-BTC", "korean_name": "비트코인", "english_name": "Bitcoin",
        "price": 150_000_000.0, "change_rate": 0.0235, "change_price": 3_500_000.0,
        "trade_price_24h": 123_400_000_000.0,
    }]


def test_get_indicator_catalog_covers_all_registered_indicators(monkeypatch, tmp_path):
    from engine.condition_tree import POSITION_RELATIVE_INDICATORS
    from engine.indicators import INDICATOR_FACTORY

    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/indicators/catalog")
    assert resp.status_code == 200
    body = resp.json()

    catalog_values = {item["value"] for item in body}
    assert catalog_values == set(INDICATOR_FACTORY.keys()) | POSITION_RELATIVE_INDICATORS
    assert len(body) == len(catalog_values), "INDICATOR_CATALOG에 중복된 value가 있습니다"

    for item in body:
        assert item["description"], f"{item['value']}에 description이 없음"
        assert item["example"], f"{item['value']}에 example이 없음"
        assert item["category"] in {"추세", "오실레이터", "거래량", "거래대금", "손익", "시장 심리", "가격대"}


def test_stop_loss_and_take_profit_catalog_items_are_sell_only_with_fixed_operator(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/indicators/catalog")
    body = resp.json()
    by_value = {item["value"]: item for item in body}

    stop_loss = by_value["STOP_LOSS_PCT"]
    assert stop_loss["category"] == "손익"
    assert stop_loss["sellOnly"] is True
    assert stop_loss["fixedOperator"] == "<="

    take_profit = by_value["TAKE_PROFIT_PCT"]
    assert take_profit["category"] == "손익"
    assert take_profit["sellOnly"] is True
    assert take_profit["fixedOperator"] == ">="


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


def test_run_backtest_persists_title_and_description(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    _patch_get_current_prices(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(title="내 포트", description="테스트 설명"),
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    list_resp = client.get("/api/v1/backtests")
    assert list_resp.status_code == 200
    body = list_resp.json()
    matching = [r for r in body if r["run_id"] == run_id]
    assert len(matching) == 1
    assert matching[0]["title"] == "내 포트"
    assert matching[0]["description"] == "테스트 설명"


def test_get_backtests_omits_sweep_only_runs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="sweep-only", strategy_name="SignalStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    assert resp.json() == []


def test_validate_reports_multiple_errors_at_once(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.post(
        "/api/v1/backtests/validate",
        json=_run_request(
            buy_conditions={"type": "AND", "conditions": []},
            sell_conditions={"type": "AND", "conditions": []},
            start="2026-03-01",
            end="2026-01-01",
        ),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert any("매수 조건" in e for e in body["errors"])
    assert any("매도 조건" in e for e in body["errors"])
    assert any("시작일" in e for e in body["errors"])


def test_validate_passes_for_well_formed_request(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    resp = client.post("/api/v1/backtests/validate", json=_run_request())
    assert resp.status_code == 200
    assert resp.json() == {"valid": True, "errors": []}


def test_validate_flags_insufficient_candle_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    short_df = make_oscillating_df(n=5)
    _patch_get_candles(monkeypatch, df=short_df)

    long_period_buy = {
        "type": "AND",
        "conditions": [{"indicator": "SMA", "params": {"period": 200}, "operator": ">", "threshold": 0}],
    }
    resp = client.post("/api/v1/backtests/validate", json=_run_request(buy_conditions=long_period_buy))
    assert resp.status_code == 200
    body = resp.json()
    assert body["valid"] is False
    assert any("200" in e for e in body["errors"])


def test_backtest_detail_revalues_open_position_with_extended_candles(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    extended_df = make_oscillating_df(n=40)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, timeframe, start, end: extended_df)

    save_result(
        run_id="r-open", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 20000.0, "exitPrice": 20105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 1.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests/r-open")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live_price_as_of"] is not None
    last_close = float(extended_df["close"].iloc[-1])
    assert body["trades"][0]["exitPrice"] == round(last_close, 8)
    assert body["trades"][0]["pnl"] != 500.0
    assert len(body["ohlcv"]) == 40
    # 저장된(naive) entryTime과 재평가로 새로 채워진 exitTime 모두 UTC 오프셋이
    # 붙어서 나가야 프론트가 new Date(...)로 둘 다 같은 기준으로 파싱한다.
    assert body["trades"][0]["entryTime"].endswith("+00:00")
    assert body["trades"][0]["exitTime"].endswith("+00:00")
    assert body["live_price_as_of"].endswith("+00:00")


def test_backtest_detail_skips_revaluation_for_legacy_trade_without_size(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch, df=make_oscillating_df(n=40))

    save_result(
        run_id="r-legacy", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True,
            }],
        },
    )

    resp = client.get("/api/v1/backtests/r-legacy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["live_price_as_of"] is None
    assert body["trades"][0]["pnl"] == 500.0
    assert body["trades"][0]["exitPrice"] == 105.0
    # 재평가 대상이 아니어도(레거시 거래) 저장된 naive 시각에는 여전히 UTC
    # 오프셋을 붙여 내보내야 한다.
    assert body["trades"][0]["entryTime"].endswith("+00:00")
    assert body["trades"][0]["exitTime"].endswith("+00:00")


def test_backtest_detail_falls_back_when_extended_candle_fetch_fails(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    original_df = make_oscillating_df(n=30)
    call_count = {"n": 0}

    def flaky_get_candles(market, timeframe, start, end):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("업비트 API 오류")
        return original_df

    monkeypatch.setattr(backend_module, "get_candles", flaky_get_candles)

    save_result(
        run_id="r-fallback", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests/r-fallback")
    assert resp.status_code == 200
    assert call_count["n"] == 2  # 확장 조회 실패 → 원래 end_dt로 재시도


def _patch_get_current_prices(monkeypatch, prices: dict[str, float] | None = None):
    monkeypatch.setattr(
        backend_module, "get_current_prices",
        lambda markets: prices if prices is not None else {},
    )


def test_get_backtests_marks_is_live_and_updates_return_rate_for_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_current_prices(monkeypatch, {"KRW-BTC": 120.0})

    save_result(
        run_id="r-open", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["is_live"] is True
    assert body[0]["final_value"] != 10500.0
    assert body[0]["return_rate"] != 5.0


def test_get_backtests_includes_strategy_condition_summaries(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={
            "buy_conditions": {
                "type": "AND",
                "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}],
            },
            "sell_conditions": {
                "type": "AND",
                "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}],
            },
        },
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    body = resp.json()
    assert body[0]["buy_conditions"]["conditions"][0]["indicator"] == "RSI"
    assert body[0]["sell_conditions"]["conditions"][0]["threshold"] == 70
    assert body[0]["is_live"] is False


def test_get_backtests_created_at_has_utc_offset(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    body = resp.json()
    # created_at은 SQLite datetime('now')로 저장되는 "YYYY-MM-DD HH:MM:SS" naive
    # UTC 문자열이다. 프론트가 이를 로컬 시간대로 잘못 해석하지 않도록 오프셋을
    # 붙여 내보내야 한다.
    assert body[0]["created_at"].endswith("+00:00")


def test_get_backtests_falls_back_when_ticker_fetch_fails(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def failing_get_current_prices(markets):
        raise RuntimeError("업비트 ticker 오류")

    monkeypatch.setattr(backend_module, "get_current_prices", failing_get_current_prices)

    save_result(
        run_id="r-open", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [{
                "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-10T00:00:00",
                "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 4.9,
                "holdingPeriod": 9, "pnl": 500.0, "forceClosed": True, "size": 100.0,
            }],
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["is_live"] is False
    assert body[0]["final_value"] == 10500.0


def test_get_segment_size_analysis_returns_saved_rows(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_segment_classification([
        {
            "market": "KRW-BTC",
            "korean_name": "비트코인",
            "segment": "large",
            "trade_value_24h": 45_700_000_000.0,
            "volatility_30d": 0.012,
            "trade_value_percentile": 99.0,
            "volatility_percentile": 10.0,
            "is_caution": False,
            "computed_at": "2026-07-25T00:00:00+00:00",
        },
    ])

    resp = client.get("/api/v1/analysis/segments/size")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["market"] == "KRW-BTC"
    assert body[0]["segment"] == "large"
    assert body[0]["is_caution"] is False


def test_get_segment_size_analysis_returns_empty_list_before_first_batch(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/analysis/segments/size")

    assert resp.status_code == 200
    assert resp.json() == []


def test_startup_event_spawns_segment_batch_as_daemon_thread(monkeypatch):
    calls = []

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            calls.append((target, daemon))

        def start(self):
            pass

    monkeypatch.setattr(backend_module.threading, "Thread", _FakeThread)

    with TestClient(app):
        pass

    assert calls == [(backend_module._run_segment_batch_safely, True)]


def test_startup_fails_orphaned_running_grid_search_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(backend_module.threading, "Thread", _FakeThread)

    from engine.cache import create_grid_search_job, get_grid_search_job
    create_grid_search_job(
        job_id="orphan-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    with TestClient(app):
        pass

    job = get_grid_search_job("orphan-1")
    assert job["status"] == "failed"
    assert "재시작" in job["error_message"]


def test_run_segment_batch_safely_swallows_exceptions(monkeypatch):
    def _raise():
        raise RuntimeError("업비트 API 실패")

    monkeypatch.setattr(backend_module, "run_segment_batch", _raise)

    backend_module._run_segment_batch_safely()  # 예외가 여기서 밖으로 나오면 테스트 실패


def test_run_segment_batch_safely_prints_count_on_success(monkeypatch, capsys):
    monkeypatch.setattr(backend_module, "run_segment_batch", lambda: 271)

    backend_module._run_segment_batch_safely()

    assert "271" in capsys.readouterr().out


def test_indicator_catalog_includes_new_indicators(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.get("/api/v1/indicators/catalog")
    values = {item["value"] for item in resp.json()}

    assert "HOLDING_PERIOD_BARS" in values
    assert "MARKET_TREND" in values
    assert "MOMENTUM_PCT" in values


def test_run_backtest_fetches_btc_candles_when_market_trend_used_on_other_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_get_candles(market, timeframe, start, end):
        calls.append(market)
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200
    assert calls == ["KRW-ETH", "KRW-BTC"]


def test_run_backtest_reuses_own_close_when_market_is_btc_itself(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_get_candles(market, timeframe, start, end):
        calls.append(market)
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-BTC", buy_conditions=buy))

    assert resp.status_code == 200
    assert calls == ["KRW-BTC"]


def test_run_backtest_skips_btc_fetch_when_market_trend_not_used(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    calls: list[str] = []

    def _fake_get_candles(market, timeframe, start, end):
        calls.append(market)
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH"))

    assert resp.status_code == 200
    assert calls == ["KRW-ETH"]


def test_run_backtest_returns_400_when_btc_fetch_raises_value_error(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            raise ValueError("BTC 캔들 데이터를 조회할 수 없습니다")
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    assert "BTC 캔들 데이터를 조회할 수 없습니다" in resp.json()["detail"]


def test_run_backtest_returns_500_when_btc_fetch_raises_runtime_error(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            raise RuntimeError("업비트 API 오류")
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 500
    assert "업비트 API 오류" in resp.json()["detail"]


def test_run_backtest_merges_btc_close_at_correct_scale_and_fills_gaps(monkeypatch, tmp_path):
    # target(KRW-ETH)의 close와 BTC의 close가 뒤바뀌는 버그, 그리고 candle_time이
    # 일부 겹치지 않을 때 ffill().bfill()이 실제로 동작하는지를 함께 검증한다.
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    btc_df = target_df.copy()
    btc_df["close"] = btc_df["close"] * 2 + 1000  # target과 스케일이 다른 별도 시세임을 검증하기 위해 배율을 둠
    gap_positions = [5, 6, 150]  # 5,6은 연속 gap, 150은 단독 gap
    btc_df = btc_df.drop(btc_df.index[gap_positions]).reset_index(drop=True)

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            return btc_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200

    merged = captured["df"].reset_index(drop=True)
    expected_btc_close = target_df["close"] * 2 + 1000

    # btc_close는 target 자신의 close가 아니라 BTC의(스케일이 다른) close를 따라야 한다.
    assert merged.loc[0, "btc_close"] != target_df.loc[0, "close"]
    assert abs(merged.loc[0, "btc_close"] - expected_btc_close.iloc[0]) < 1e-6

    # gap이 없는 행들은 정확히 BTC의 스케일된 close와 일치해야 한다.
    for i in range(len(merged)):
        if i in gap_positions:
            continue
        assert abs(merged.loc[i, "btc_close"] - expected_btc_close.iloc[i]) < 1e-6

    # 연속된 gap(5,6)은 이전 값(index 4)으로 ffill 되어야 한다.
    assert abs(merged.loc[5, "btc_close"] - expected_btc_close.iloc[4]) < 1e-6
    assert abs(merged.loc[6, "btc_close"] - expected_btc_close.iloc[4]) < 1e-6

    # 단독 gap(150)도 이전 값(index 149)으로 ffill 되어야 한다.
    assert abs(merged.loc[150, "btc_close"] - expected_btc_close.iloc[149]) < 1e-6

    # ffill/bfill 이후에는 NaN이 하나도 남아있으면 안 된다.
    assert merged["btc_close"].isna().sum() == 0


def test_run_backtest_merges_both_btc_and_usdt_when_both_correlations_present(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    btc_df = target_df.copy()
    btc_df["close"] = btc_df["close"] * 2 + 1000
    usdt_df = target_df.copy()
    usdt_df["close"] = 1300.0

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            return btc_df
        if market == "KRW-USDT":
            return usdt_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {
        "type": "AND",
        "conditions": [
            {"indicator": "BTC_CORRELATION", "params": {"period": 10}, "operator": ">", "threshold": -1},
            {"indicator": "USDT_CORRELATION", "params": {"period": 10}, "operator": ">", "threshold": -1},
        ],
    }
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200
    merged = captured["df"]
    assert "btc_close" in merged.columns
    assert "usdt_close" in merged.columns


def test_run_backtest_returns_400_when_btc_candles_are_empty_for_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            return make_oscillating_df(n=0)
        return make_oscillating_df()

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    assert "이 조건에 필요한 KRW-BTC 캔들 데이터가 해당 기간에 없습니다" in resp.json()["detail"]


def test_run_backtest_returns_400_when_btc_candles_have_no_overlapping_candle_time(monkeypatch, tmp_path):
    # btc_df에 행은 있지만 target과 candle_time이 전혀 겹치지 않는 경우 (merge 후 btc_close 전체가 NaN)
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    disjoint_btc_df = target_df.copy()
    disjoint_btc_df["candle_time"] = disjoint_btc_df["candle_time"] + pd.Timedelta(days=10000)

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-BTC":
            return disjoint_btc_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    buy = {"type": "AND", "conditions": [{"indicator": "MARKET_TREND", "params": {"period": 5}, "operator": "<", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    assert "이 조건에 필요한 KRW-BTC 캔들 데이터가 해당 기간에 없습니다" in resp.json()["detail"]


def test_run_backtest_forward_fills_fear_greed_across_hourly_candles(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    hourly_df = make_oscillating_df()  # 300시간 = 2026-01-01 00:00 ~ 2026-01-13 11:00, UTC
    _patch_get_candles(monkeypatch, hourly_df)

    fng_df = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-01", "2026-01-02"], utc=True), "fear_greed_value": [30.0, 70.0]}
    )
    monkeypatch.setattr(backend_module, "get_fear_greed_cmc", lambda start, end: fng_df)

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "FEAR_GREED_CMC", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post(
        "/api/v1/backtests/run", json=_run_request(buy_conditions=buy, timeframe="minutes60")
    )

    assert resp.status_code == 200
    merged = captured["df"].reset_index(drop=True)
    day1 = merged[merged["candle_time"] < pd.Timestamp("2026-01-02", tz="UTC")]
    day2plus = merged[merged["candle_time"] >= pd.Timestamp("2026-01-02", tz="UTC")]
    assert (day1["fear_greed_value"] == 30.0).all()
    assert (day2plus["fear_greed_value"] == 70.0).all()


def test_run_backtest_rejects_fear_greed_when_date_range_predates_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    old_df = make_oscillating_df()
    old_df["candle_time"] = pd.date_range("2017-01-01", periods=len(old_df), freq="h", tz="UTC")
    _patch_get_candles(monkeypatch, old_df)
    monkeypatch.setattr(
        backend_module, "get_fear_greed_cmc",
        lambda start, end: pd.DataFrame(columns=["date", "fear_greed_value"]),
    )

    buy = {"type": "AND", "conditions": [{"indicator": "FEAR_GREED_CMC", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(buy_conditions=buy, timeframe="minutes60", start="2017-01-01", end="2017-01-05"),
    )

    assert resp.status_code == 400
    assert "공포탐욕지수" in resp.json()["detail"]


def test_run_backtest_computes_korea_premium_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    target_df["close"] = 1_050_000.0
    usdt_df = target_df.copy()
    usdt_df["close"] = 1_000.0

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-USDT":
            return usdt_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    binance_df = pd.DataFrame({"candle_time": target_df["candle_time"], "close": 1_000.0})
    monkeypatch.setattr(
        backend_module, "get_binance_close", lambda symbol, timeframe, start, end: binance_df
    )

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}]}
    # sell_conditions는 기본값(RSI(14))을 쓰지 않는다: 이 테스트는 korea_premium_value가 모든 행에서
    # 정확히 5.0이 되도록 target_df["close"]를 상수로 고정하는데, 종가가 완전히 평평하면
    # backtrader의 기본 RSI(safediv=False)가 maup=madown=0을 나누며 ZeroDivisionError를 던진다.
    # 이는 KOREA_PREMIUM 병합 로직과 무관한 충돌이므로 sell 쪽도 KOREA_PREMIUM으로 바꿔 피한다.
    sell = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": "<", "threshold": -100}]}
    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(market="KRW-ETH", buy_conditions=buy, sell_conditions=sell),
    )

    assert resp.status_code == 200
    merged = captured["df"]
    # close=1,050,000 / (binance_close=1,000 * usdt_close=1,000) - 1) * 100 = 5.0(%)
    assert merged["korea_premium_value"].round(4).eq(5.0).all()


def test_run_backtest_rejects_korea_premium_when_binance_symbol_not_found(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    def _raise_not_found(symbol, timeframe, start, end):
        raise backend_module.BinanceSymbolNotFoundError(symbol)

    monkeypatch.setattr(backend_module, "get_binance_close", _raise_not_found)

    buy = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(buy_conditions=buy))

    assert resp.status_code == 400
    assert "바이낸스" in resp.json()["detail"]


def test_run_backtest_rejects_korea_premium_when_binance_data_partially_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    target_df = make_oscillating_df()
    usdt_df = target_df.copy()
    usdt_df["close"] = 1_000.0

    def _fake_get_candles(market, timeframe, start, end):
        if market == "KRW-USDT":
            return usdt_df
        return target_df

    monkeypatch.setattr(backend_module, "get_candles", _fake_get_candles)

    # binance_df only covers the first half of target_df's candle_times — partial coverage,
    # a valid symbol with real data, just not for the whole requested range.
    half = len(target_df) // 2
    partial_binance_df = pd.DataFrame(
        {"candle_time": target_df["candle_time"].iloc[:half], "close": 1_000.0}
    )
    monkeypatch.setattr(
        backend_module, "get_binance_close",
        lambda symbol, timeframe, start, end: partial_binance_df,
    )

    buy = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    # must be the "no data in range" message, NOT the "not listed" message —
    # this is the whole point of the fix: the two causes must not collapse into one message.
    assert "캔들 데이터가 없습니다" in resp.json()["detail"]


def test_run_backtest_returns_400_when_binance_candles_have_no_overlapping_candle_time(monkeypatch, tmp_path):
    # 바이낸스 응답에 행은 있지만 target과 candle_time이 전혀 겹치지 않는 경우(merge 후 전부 NaN)
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    target_df = make_oscillating_df()
    disjoint_binance_df = pd.DataFrame(
        {"candle_time": target_df["candle_time"] + pd.Timedelta(days=10000), "close": 1000.0}
    )
    monkeypatch.setattr(
        backend_module, "get_binance_close",
        lambda symbol, timeframe, start, end: disjoint_binance_df,
    )

    buy = {"type": "AND", "conditions": [{"indicator": "KOREA_PREMIUM", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    assert "캔들 데이터가 없습니다" in resp.json()["detail"]


def test_run_backtest_computes_funding_rate_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    target_df = make_oscillating_df()
    funding_df = pd.DataFrame({
        "funding_time": target_df["candle_time"] - pd.Timedelta(minutes=1),
        "funding_rate": 0.03,
    })
    monkeypatch.setattr(
        backend_module, "get_binance_funding_rate",
        lambda symbol, start, end: funding_df,
    )

    captured = {}
    real_run_backtest_cached = backend_module.run_backtest_cached

    def _capture(**kwargs):
        captured["df"] = kwargs["df"].copy()
        return real_run_backtest_cached(**kwargs)

    monkeypatch.setattr(backend_module, "run_backtest_cached", _capture)

    buy = {"type": "AND", "conditions": [{"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": -100}]}
    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(market="KRW-ETH", buy_conditions=buy),
    )

    assert resp.status_code == 200
    merged = captured["df"]
    assert merged["funding_rate_value"].round(4).eq(0.03).all()


def test_run_backtest_rejects_funding_rate_when_no_data_in_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)
    monkeypatch.setattr(
        backend_module, "get_binance_funding_rate",
        lambda symbol, start, end: pd.DataFrame(columns=["funding_time", "funding_rate"]),
    )

    buy = {"type": "AND", "conditions": [{"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": 0}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 400
    assert "펀딩비" in resp.json()["detail"]


def test_run_backtest_allows_funding_rate_with_partial_leading_nan(monkeypatch, tmp_path):
    # 구간 앞부분(첫 펀딩비 이벤트 이전)에 NaN이 남는 건 정상 — 400 에러가 나면 안 된다.
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    target_df = make_oscillating_df()
    half = len(target_df) // 2
    funding_df = pd.DataFrame({
        "funding_time": target_df["candle_time"].iloc[half:] - pd.Timedelta(minutes=1),
        "funding_rate": 0.02,
    })
    monkeypatch.setattr(
        backend_module, "get_binance_funding_rate",
        lambda symbol, start, end: funding_df,
    )

    buy = {"type": "AND", "conditions": [{"indicator": "FUNDING_RATE", "params": {}, "operator": ">", "threshold": -100}]}
    resp = client.post("/api/v1/backtests/run", json=_run_request(market="KRW-ETH", buy_conditions=buy))

    assert resp.status_code == 200


def test_create_grid_search_job_returns_running_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    from engine.cache import create_grid_search_job
    monkeypatch.setattr(backend_module, "start_job", lambda **kwargs: "job-1")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "job-1"
    assert body["status"] == "running"


def test_create_grid_search_job_rejects_market_not_in_krw_list(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 400


def test_create_grid_search_job_rejects_reversed_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-08-03", "end": "2026-06-05", "top_n": 20,
    })
    assert resp.status_code == 400


def test_create_grid_search_job_rejects_top_n_out_of_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 51,
    })
    assert resp.status_code == 400


def test_create_grid_search_job_returns_409_when_already_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    def _raise(**kwargs):
        raise backend_module.JobAlreadyRunningError("job-existing")

    monkeypatch.setattr(backend_module, "start_job", _raise)

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 409


def test_get_grid_search_job_returns_404_for_missing_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/grid-search/jobs/does-not-exist")
    assert resp.status_code == 404


def test_list_grid_search_jobs_returns_saved_jobs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    resp = client.get("/api/v1/grid-search/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "job-1"


def test_cancel_grid_search_job_returns_409_when_not_active(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _raise(job_id):
        raise backend_module.JobNotActiveError(job_id)

    monkeypatch.setattr(backend_module, "cancel_job", _raise)

    resp = client.post("/api/v1/grid-search/jobs/job-1/cancel")
    assert resp.status_code == 409
