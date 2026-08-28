import ast
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
from fastapi.testclient import TestClient

import backend.main as backend_module
import engine.cache as cache_module
import engine.trend_segments as trend_segments_module
from backend.main import app
from engine.cache import save_result, save_sweep_result, finish_grid_search_job
from engine.cache import save_segment_classification
from tests.signal_fixtures import make_oscillating_df
import trading.db as trading_db_module
from trading.upbit_client import UpbitCredentialsError, UpbitRateLimitError


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(trading_db_module, "DB_PATH", tmp_path / "trading.db")
    return TestClient(app)


def test_resolve_allowed_origin_defaults_to_localhost_when_env_unset(monkeypatch):
    """ALLOWED_ORIGIN 환경변수가 없으면 기존 기본값(localhost:3000)을 그대로 써야
    한다 — 로컬 개발 흐름에 회귀가 없어야 한다(배포 스펙 결정4)."""
    monkeypatch.delenv("ALLOWED_ORIGIN", raising=False)

    assert backend_module._resolve_allowed_origin() == "http://localhost:3000"


def test_resolve_allowed_origin_uses_env_var_when_set(monkeypatch):
    """ALLOWED_ORIGIN이 설정되면(예: 서버 배포 시 Tailscale 주소) 그 값을 그대로
    써야 한다."""
    monkeypatch.setenv("ALLOWED_ORIGIN", "http://oracle-server.tailnet.ts.net:3000")

    assert (
        backend_module._resolve_allowed_origin()
        == "http://oracle-server.tailnet.ts.net:3000"
    )


def test_resolve_allowed_origin_treats_empty_string_as_unset(monkeypatch):
    """load_dotenv()가 빈 값의 .env 항목을 os.environ에 빈 문자열로 심는 경우
    (트레일링 blank ALLOWED_ORIGIN= 줄) 기본값으로 폴백해야 한다 — 그러지 않으면
    allow_origins=[""]로 CORS가 조용히 깨진다(리뷰에서 실증된 버그)."""
    monkeypatch.setenv("ALLOWED_ORIGIN", "")

    assert backend_module._resolve_allowed_origin() == "http://localhost:3000"


def test_health_check(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_no_bare_testclient_bypasses_db_isolation():
    """TestClient(app)를 DB_PATH 격리 없이 만들면, FastAPI startup 이벤트
    (_fail_orphaned_grid_search_jobs)가 실제 운영 DB(data/backtest_results.db)를
    건드린다 — 실사고: 로컬에서 진짜 돌고 있던 grid search job이, DB를 격리하지 않던
    test_health_check가 포함된 전체 테스트 스위트 실행 때문에 고아로 오판되어 failed로
    덮어써졌다. AST로 각 test_* 함수를 검사해, TestClient(app)를 쓰면서 _client() 헬퍼도
    안 쓰고 monkeypatch로 DB_PATH도 안 바꾸는 함수가 없는지 확인한다(단순 문자열 검색은
    docstring/assert 메시지 안의 "TestClient(app)" 문자열에도 오탐하므로 AST를 쓴다)."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
            continue
        calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
        uses_testclient = any(
            isinstance(c.func, ast.Name) and c.func.id == "TestClient" for c in calls
        )
        if not uses_testclient:
            continue
        uses_client_helper = any(
            isinstance(c.func, ast.Name) and c.func.id == "_client" for c in calls
        )
        sets_db_path = any(
            isinstance(c.func, ast.Attribute)
            and c.func.attr == "setattr"
            and any(isinstance(arg, ast.Constant) and arg.value == "DB_PATH" for arg in c.args)
            for c in calls
        )
        if not uses_client_helper and not sets_db_path:
            offenders.append(node.name)
    assert offenders == [], f"DB_PATH를 격리하지 않고 TestClient(app)를 쓰는 테스트: {offenders}"


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


def test_update_backtest_metadata_updates_title_and_description(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="원래 제목", description="원래 설명",
    )

    resp = client.patch("/api/v1/backtests/r1", json={"title": "새 제목", "description": "새 설명"})

    assert resp.status_code == 200
    assert resp.json()["title"] == "새 제목"
    assert resp.json()["description"] == "새 설명"

    detail_resp = client.get("/api/v1/backtests/r1")
    detail = detail_resp.json()
    assert detail["title"] == "새 제목"
    assert detail["description"] == "새 설명"
    assert detail["created_at"].endswith("+00:00")


def test_update_backtest_metadata_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.patch("/api/v1/backtests/does-not-exist", json={"title": "제목", "description": None})

    assert resp.status_code == 404


def test_update_backtest_metadata_returns_422_when_field_omitted(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="원래 제목", description="원래 설명",
    )

    resp = client.patch("/api/v1/backtests/r1", json={"title": "새 제목"})

    assert resp.status_code == 422


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


def test_backtest_config_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/backtests/does-not-exist/config")
    assert resp.status_code == 404


def test_backtest_config_returns_market_timeframe_and_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_candles(monkeypatch)

    create_resp = client.post("/api/v1/backtests/run", json=_run_request())
    run_id = create_resp.json()["run_id"]

    resp = client.get(f"/api/v1/backtests/{run_id}/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert body["timeframe"] == "days"
    assert body["buy_conditions"] == _VALID_BUY
    assert body["sell_conditions"] == _VALID_SELL


def test_get_signals_returns_registered_signal_keys(monkeypatch, tmp_path):
    from signals import SIGNAL_REGISTRY

    client = _client(monkeypatch, tmp_path)
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


def _live_strategy_request(**overrides) -> dict:
    body = {
        "source_run_id": None,
        "market": "KRW-BTC",
        "timeframe": "minutes60",
        "buy_conditions": _VALID_BUY,
        "sell_conditions": _VALID_SELL,
        "risk_config": {
            "position_sizing_mode": "fixed",
            "position_sizing_value": 100000,
            "max_position_per_market": 500000,
            "order_execution_mode": "market",
            "order_timeout_sec": 10,
            "manual_intervention_policy": "all_stop",
            "daily_loss_limit_pct": -5.0,
            "consecutive_loss_limit": 3,
        },
    }
    body.update(overrides)
    return body


def test_create_live_strategy_creates_draft(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "draft"
    assert body["market"] == "KRW-BTC"
    assert body["timeframe"] == "minutes60"
    assert body["current_capital"] is None


def test_create_live_strategy_rejects_unknown_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request(market="KRW-NOPE"))
    assert resp.status_code == 400


def test_create_live_strategy_rejects_invalid_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request(timeframe="not-a-timeframe"))
    assert resp.status_code == 400


def test_create_live_strategy_rejects_non_negative_daily_loss_limit(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    req = _live_strategy_request()
    req["risk_config"]["daily_loss_limit_pct"] = 5.0

    resp = client.post("/api/v1/live-strategies", json=req)
    assert resp.status_code == 400


def test_create_live_strategy_rejects_empty_buy_conditions(monkeypatch, tmp_path):
    """Fix 3(Important) — 백테스트 생성 경로(_validate_backtest_request)는 이미
    is_empty()로 빈 조건을 막지만 라이브 전략 생성 경로는 그 검증이 빠져 있었다.
    빈 매수 조건으로 라이브 전략을 만들면 daemon이 항상 매수 불가능한 죽은 전략을
    승인해버리는 결과로 이어진다."""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    req = _live_strategy_request(buy_conditions={"type": "AND", "conditions": []})

    resp = client.post("/api/v1/live-strategies", json=req)
    assert resp.status_code == 400


def test_create_live_strategy_rejects_empty_sell_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    req = _live_strategy_request(sell_conditions={"type": "AND", "conditions": []})

    resp = client.post("/api/v1/live-strategies", json=req)
    assert resp.status_code == 400


def test_create_live_strategy_rejects_unknown_indicator(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    req = _live_strategy_request(buy_conditions={
        "type": "AND",
        "conditions": [{"indicator": "NOT_A_REAL_INDICATOR", "params": {}, "operator": "<", "threshold": 1}],
    })

    resp = client.post("/api/v1/live-strategies", json=req)
    assert resp.status_code == 400


def test_list_live_strategies_returns_empty_when_none(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/live-strategies")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_live_strategies_includes_open_position_summary(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    create_resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    strategy_id = create_resp.json()["id"]
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    _patch_get_current_prices(monkeypatch, {"KRW-BTC": 55_000_000.0})

    resp = client.get("/api/v1/live-strategies")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    position = body[0]["open_position"]
    assert position["entry_price"] == 50_000_000.0
    assert position["entry_qty"] == 0.01
    assert position["unrealized_pnl_pct"] == pytest.approx(10.0)


def test_list_live_strategies_last_buy_at_reflects_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    create_resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    strategy_id = create_resp.json()["id"]
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    _patch_get_current_prices(monkeypatch, {"KRW-BTC": 55_000_000.0})

    resp = client.get("/api/v1/live-strategies")

    body = resp.json()[0]
    assert body["last_buy_at"] is not None
    assert body["last_sell_at"] is None


def test_list_live_strategies_last_buy_and_sell_reflect_closed_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    create_resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    strategy_id = create_resp.json()["id"]
    position_id = trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    trading_db_module.close_position_row(position_id, 51_000_000.0, 0.01, 10000.0, 2.0, "signal")

    resp = client.get("/api/v1/live-strategies")

    body = resp.json()[0]
    assert body["last_buy_at"] is not None
    assert body["last_sell_at"] is not None


def test_list_live_strategies_last_buy_and_sell_are_null_without_trades(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    body = resp.json()[0]
    assert body["last_buy_at"] is None
    assert body["last_sell_at"] is None


def test_list_live_strategies_falls_back_when_current_price_fetch_fails(monkeypatch, tmp_path):
    """Fix 2(Important) — get_current_prices()가 업비트 장애/타임아웃으로 예외를 던져도
    목록 엔드포인트 전체가 500으로 죽으면 안 된다. 열린 포지션이 있는 전략의
    unrealized_pnl_pct만 None으로 빠지고 나머지는 정상 응답해야 한다(하필 포지션이
    있어 pause/stop 버튼이 가장 필요한 순간에 목록 자체가 안 뜨는 걸 막는다)."""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    create_resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    strategy_id = create_resp.json()["id"]
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    def _raise(markets):
        raise RuntimeError("업비트 ticker 호출 실패")

    monkeypatch.setattr(backend_module, "get_current_prices", _raise)

    resp = client.get("/api/v1/live-strategies")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["open_position"]["unrealized_pnl_pct"] is None


def test_list_live_strategies_open_position_is_null_when_no_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    assert resp.json()[0]["open_position"] is None


def test_list_live_strategies_includes_buy_sell_conditions_and_risk_config(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    assert resp.status_code == 200
    body = resp.json()[0]
    assert body["buy_conditions"] == _VALID_BUY
    assert body["sell_conditions"] == _VALID_SELL
    assert body["risk_config"]["position_sizing_mode"] == "fixed"
    assert body["risk_config"]["position_sizing_value"] == 100000
    assert body["risk_config"]["daily_loss_limit_pct"] == -5.0


def test_list_live_strategies_includes_empty_capital_adjustments_by_default(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    client.post("/api/v1/live-strategies", json=_live_strategy_request())

    resp = client.get("/api/v1/live-strategies")

    assert resp.json()[0]["capital_adjustments"] == []


def test_list_live_strategies_includes_capital_adjustment_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 300000})

    resp = client.get("/api/v1/live-strategies")

    adjustments = resp.json()[0]["capital_adjustments"]
    assert len(adjustments) == 1
    assert adjustments[0]["previous_capital"] == 100000
    assert adjustments[0]["new_capital"] == 300000
    assert adjustments[0]["delta"] == 200000
    assert adjustments[0]["adjusted_at"].endswith(("+00:00", "Z"))


def _accounts_with_krw_balance(balance: float):
    async def _fake(*args, **kwargs):
        return [{"currency": "KRW", "balance": str(balance), "locked": "0"}]
    return _fake


def test_approve_live_strategy_transitions_to_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["current_capital"] == 100000.0


def test_approve_live_strategy_returns_404_for_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.post("/api/v1/live-strategies/does-not-exist/approve")
    assert resp.status_code == 404


def test_approve_live_strategy_returns_409_when_already_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    assert resp.status_code == 409


def test_approve_live_strategy_rejects_when_balance_insufficient(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(50000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 400
    assert trading_db_module.get_live_strategy(strategy_id)["status"] == "draft"


def test_approve_live_strategy_sums_other_running_strategies_capital(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        backend_module, "get_krw_markets",
        lambda: [{"market": "KRW-BTC"}, {"market": "KRW-ETH"}],
    )
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(150000))
    first_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(market="KRW-BTC"),
    ).json()["id"]
    assert client.post(f"/api/v1/live-strategies/{first_id}/approve").status_code == 200

    second_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(market="KRW-ETH"),
    ).json()["id"]
    resp = client.post(f"/api/v1/live-strategies/{second_id}/approve")

    assert resp.status_code == 400
    assert trading_db_module.get_live_strategy(second_id)["status"] == "draft"


def test_approve_live_strategy_returns_400_when_upbit_credentials_missing(monkeypatch, tmp_path):
    """Fix 5(Important) — get_accounts()가 UpbitCredentialsError를 던지면 raw 500 대신
    사용자에게 무엇이 잘못됐는지 알려주는 400을 반환해야 하고, 전략은 draft에 머물러야
    한다(승인이 절반만 진행되면 안 된다)."""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    async def _raise(*args, **kwargs):
        raise UpbitCredentialsError("no keys")

    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _raise)
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 400
    assert "인증" in resp.json()["detail"]
    assert trading_db_module.get_live_strategy(strategy_id)["status"] == "draft"


def test_approve_live_strategy_returns_400_when_upbit_rate_limited(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    async def _raise(*args, **kwargs):
        raise UpbitRateLimitError("429 exhausted")

    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _raise)
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 400
    assert trading_db_module.get_live_strategy(strategy_id)["status"] == "draft"


def test_approve_live_strategy_distinguishes_auth_rejection_from_network_failure(
    monkeypatch, tmp_path,
):
    """코드 리뷰/백로그 발견 — get_accounts()가 업비트로부터 401(invalid_access_key)을
    받으면 httpx.HTTPStatusError가 나는데, 이걸 순수 네트워크 장애와 같은
    "업비트 서버와 통신할 수 없습니다" 메시지로 뭉뚱그리면 안 된다. 사용자가 실제로 이
    메시지 때문에 원인 파악(API 키 오류였음)에 시간을 썼다."""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    async def _raise(*args, **kwargs):
        request = httpx.Request("GET", "https://api.upbit.com/v1/accounts")
        response = httpx.Response(401, request=request, json={"error": {"message": "invalid_access_key"}})
        raise httpx.HTTPStatusError("401", request=request, response=response)

    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _raise)
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 400
    assert "인증" in resp.json()["detail"]
    assert "401" in resp.json()["detail"]
    assert trading_db_module.get_live_strategy(strategy_id)["status"] == "draft"


def test_approve_live_strategy_reports_upbit_server_error_status(monkeypatch, tmp_path):
    """401/403이 아닌 다른 상태코드(예: 500)는 인증 문제로 오인하지 않되, 여전히
    status 코드를 메시지에 남겨 원인 파악을 돕는다."""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    async def _raise(*args, **kwargs):
        request = httpx.Request("GET", "https://api.upbit.com/v1/accounts")
        response = httpx.Response(500, request=request, json={})
        raise httpx.HTTPStatusError("500", request=request, response=response)

    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _raise)
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 400
    assert "인증" not in resp.json()["detail"]
    assert "500" in resp.json()["detail"]


def test_approve_live_strategy_keeps_generic_message_for_network_failure(monkeypatch, tmp_path):
    """진짜 네트워크 장애(connect/timeout, HTTP 응답 자체가 없음)는 여전히 기존 일반
    메시지를 유지한다 — status_code가 없는 경우이므로 HTTPStatusError 분기가 아니라
    httpx.HTTPError 분기로 빠져야 한다."""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    async def _raise(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _raise)
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    assert resp.status_code == 400
    assert resp.json()["detail"] == "업비트 서버와 통신할 수 없습니다"


def test_create_live_strategy_returns_400_when_market_list_fetch_fails(monkeypatch, tmp_path):
    """Fix 5(Important) — get_krw_markets()가 네트워크 장애로 예외를 던지면 라이브 전략
    생성 엔드포인트가 raw 500 대신 400 검증 오류를 반환해야 한다."""
    client = _client(monkeypatch, tmp_path)

    def _raise():
        raise RuntimeError("network down")

    monkeypatch.setattr(backend_module, "get_krw_markets", _raise)

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request())
    assert resp.status_code == 400


def test_pause_and_resume_live_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    pause_resp = client.post(f"/api/v1/live-strategies/{strategy_id}/pause")
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == "paused"
    assert trading_db_module.get_live_strategy(strategy_id)["manual_pause"] == 1

    resume_resp = client.post(f"/api/v1/live-strategies/{strategy_id}/resume")
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "running"
    assert trading_db_module.get_live_strategy(strategy_id)["manual_pause"] == 0


def test_resume_live_strategy_clears_tripped_circuit_breaker(monkeypatch, tmp_path):
    """Fix 1 — 승인 -> 일시정지 -> (서킷브레이커 트립 상태를 직접 삽입해 시뮬레이션) ->
    API로 재개하면 전략이 running으로 돌아갈 뿐 아니라 서킷브레이커 tripped도 0으로
    풀려야 한다. 그렇지 않으면 daemon이 다음 신호 평가에서 여전히 트립 상태로 취급해
    사용자가 재개를 눌러도 실질적으로 거래가 재개되지 않는다."""
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    client.post(f"/api/v1/live-strategies/{strategy_id}/pause")
    trading_db_module.upsert_circuit_breaker_state(
        strategy_id, "2026-08-11", 3, 1, "daily_loss_limit", "2026-08-11T01:00:00+00:00",
    )

    resume_resp = client.post(f"/api/v1/live-strategies/{strategy_id}/resume")

    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == "running"
    cb_state = trading_db_module.get_circuit_breaker_state(strategy_id)
    assert cb_state["tripped"] == 0


def test_pause_live_strategy_returns_409_when_not_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/pause")
    assert resp.status_code == 409


def test_resume_live_strategy_returns_409_when_not_paused(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/resume")
    assert resp.status_code == 409


def test_stop_live_strategy_succeeds_when_no_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    assert resp.status_code == 200
    assert resp.json()["status"] == "stopped"


def test_stop_live_strategy_rejects_when_open_position_exists(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    assert resp.status_code == 400
    assert trading_db_module.get_live_strategy(strategy_id)["status"] == "running"


def test_update_capital_succeeds_when_no_open_position(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 300000})

    assert resp.status_code == 200
    assert resp.json()["current_capital"] == 300000


def test_update_capital_rejects_when_open_position_exists(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 300000})

    assert resp.status_code == 400


def test_update_capital_rejects_non_positive_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 0})

    assert resp.status_code == 400


def test_update_capital_rejects_draft_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 800000})

    assert resp.status_code == 409


def test_update_capital_returns_404_for_missing_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.patch("/api/v1/live-strategies/does-not-exist/capital", json={"new_capital": 800000})

    assert resp.status_code == 404


def test_update_capital_rejects_when_exceeds_max_position_per_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 600000})

    assert resp.status_code == 400


def test_update_capital_rejects_stopped_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.patch(f"/api/v1/live-strategies/{strategy_id}/capital", json={"new_capital": 300000})

    assert resp.status_code == 409


def test_update_capital_rejects_infinite_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.patch(
        f"/api/v1/live-strategies/{strategy_id}/capital",
        content='{"new_capital": Infinity}',
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400


def test_update_capital_rejects_nan_value(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")

    resp = client.patch(
        f"/api/v1/live-strategies/{strategy_id}/capital",
        content='{"new_capital": NaN}',
        headers={"Content-Type": "application/json"},
    )

    assert resp.status_code == 400


def test_stop_live_strategy_returns_409_when_already_stopped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    assert resp.status_code == 409


def test_delete_live_strategy_soft_deletes_stopped_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.delete(f"/api/v1/live-strategies/{strategy_id}")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}
    strategy = trading_db_module.get_live_strategy(strategy_id)
    assert strategy is not None
    assert strategy["deleted_at"] is not None


def test_delete_live_strategy_returns_409_when_already_deleted(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    client.delete(f"/api/v1/live-strategies/{strategy_id}")

    resp = client.delete(f"/api/v1/live-strategies/{strategy_id}")

    assert resp.status_code == 409


def test_delete_live_strategy_returns_409_when_not_stopped(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]

    resp = client.delete(f"/api/v1/live-strategies/{strategy_id}")

    assert resp.status_code == 409
    assert trading_db_module.get_live_strategy(strategy_id) is not None


def test_delete_live_strategy_returns_404_for_missing_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.delete("/api/v1/live-strategies/does-not-exist")

    assert resp.status_code == 404


def test_list_live_strategies_excludes_soft_deleted(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    client.delete(f"/api/v1/live-strategies/{strategy_id}")

    resp = client.get("/api/v1/live-strategies")

    assert resp.status_code == 200
    assert resp.json() == []


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


def test_run_backtest_rejects_unsupported_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(timeframe="minutes999"),
    )
    assert resp.status_code == 400
    assert "지원하지 않는 봉데이터" in resp.json()["detail"]


def test_run_backtest_rejects_malformed_date(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(start="01-01-2026"),
    )
    assert resp.status_code == 400
    assert "형식이 올바르지 않습니다" in resp.json()["detail"]


def test_run_backtest_accepts_non_zero_padded_valid_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    _patch_get_candles(monkeypatch)

    resp = client.post(
        "/api/v1/backtests/run",
        json=_run_request(start="2026-6-5", end="2026-12-01"),
    )
    assert resp.status_code == 200


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


def test_get_backtests_top_trade_contribution_pct_uses_revalued_open_trade(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    _patch_get_current_prices(monkeypatch, {"KRW-BTC": 200.0})

    save_result(
        run_id="r-open-contrib", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.0},
        result={
            "final_value": 10050.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [
                {
                    "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-02T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 5.0,
                    "holdingPeriod": 1, "pnl": 50.0, "forceClosed": False, "size": 100.0,
                },
                {
                    "entryTime": "2026-01-05T00:00:00", "exitTime": "2026-01-10T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 100.0, "returnRate": 0.0,
                    "holdingPeriod": 5, "pnl": 0.0, "forceClosed": True, "size": 10.0,
                },
            ],
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    # open trade revalued at live_price=200.0: (200.0 - 100.0) * 10.0 = 1000.0
    # gross_profit = 50.0 (closed win) + 1000.0 (revalued open win) = 1050.0
    assert body[0]["top_trade_contribution_pct"] == pytest.approx(1000.0 / 1050.0 * 100.0)


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


def test_get_backtests_includes_top_trade_contribution_pct(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10920.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [
                {
                    "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-02T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 190.0, "returnRate": 90.0,
                    "holdingPeriod": 1, "pnl": 900.0, "forceClosed": False, "size": 100.0,
                },
                {
                    "entryTime": "2026-01-03T00:00:00", "exitTime": "2026-01-04T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 5.0,
                    "holdingPeriod": 1, "pnl": 50.0, "forceClosed": False, "size": 100.0,
                },
                {
                    "entryTime": "2026-01-05T00:00:00", "exitTime": "2026-01-06T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 95.0, "returnRate": -5.0,
                    "holdingPeriod": 1, "pnl": -30.0, "forceClosed": False, "size": 100.0,
                },
            ],
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["top_trade_contribution_pct"] == pytest.approx(900 / 950 * 100.0)


def test_get_backtests_top_trade_contribution_pct_none_without_wins(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10000.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    body = resp.json()
    assert body[0]["top_trade_contribution_pct"] is None


def test_get_backtests_includes_trade_count_and_candle_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [
                {
                    "entryTime": "2026-01-01T00:00:00", "exitTime": "2026-01-02T00:00:00",
                    "entryPrice": 100.0, "exitPrice": 105.0, "returnRate": 5.0,
                    "holdingPeriod": 1, "pnl": 50.0, "forceClosed": False, "size": 100.0,
                },
            ],
            "candle_count": 240,
        },
    )

    resp = client.get("/api/v1/backtests")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["trade_count"] == 1
    assert body[0]["candle_count"] == 240


def test_get_backtests_candle_count_is_none_when_not_backfilled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10000.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.get("/api/v1/backtests")
    body = resp.json()
    assert body[0]["trade_count"] == 0
    assert body[0]["candle_count"] is None


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


def test_startup_event_spawns_segment_batch_as_daemon_thread(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
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


def test_create_grid_search_job_rejects_unsupported_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes999", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026-08-03", "top_n": 20,
    })
    assert resp.status_code == 400
    assert "지원하지 않는 봉데이터" in resp.json()["detail"]


def test_create_grid_search_job_rejects_malformed_date(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-06-05", "end": "2026/08/03", "top_n": 20,
    })
    assert resp.status_code == 400
    assert "형식이 올바르지 않습니다" in resp.json()["detail"]


def test_create_grid_search_job_accepts_non_zero_padded_valid_date_range(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-SOL"}])
    monkeypatch.setattr(backend_module, "start_job", lambda **kwargs: "job-1")

    from engine.cache import create_grid_search_job
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-6-5", end="2026-12-01", top_n=20,
    )

    resp = client.post("/api/v1/grid-search/jobs", json={
        "market": "KRW-SOL", "timeframe": "minutes60", "capital": 1_000_000,
        "start": "2026-6-5", "end": "2026-12-01", "top_n": 20,
    })
    assert resp.status_code == 200


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


def test_grid_search_estimate_endpoint_returns_combo_counts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/grid-search/estimate", params={"categories": "추세"})
    assert resp.status_code == 200
    data = resp.json()
    # SMA_PCT/EMA_PCT/WMA_PCT/MOMENTUM_PCT * 3 param_grid values * 3 low thresholds each
    assert data["buy_count"] == 36
    assert data["total_combos"] == data["buy_count"] * data["sell_count"]
    assert data["estimated_seconds"] > 0


def test_grid_search_estimate_endpoint_rejects_unknown_category(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/grid-search/estimate", params={"categories": "존재안함"})
    assert resp.status_code == 400
    assert "존재안함" in resp.json()["detail"]


def test_grid_search_indicator_pool_endpoint_returns_per_category_indicators(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.get("/api/v1/grid-search/indicator-pool")
    assert resp.status_code == 200
    data = resp.json()
    trend = {item["value"] for item in data["추세"]}
    assert trend == {"SMA_PCT", "EMA_PCT", "WMA_PCT", "MOMENTUM_PCT"}
    # 카탈로그 라벨이 채워져 있어야 한다(빈 문자열/값 그대로 폴백이 아니라 실제 한글 라벨)
    sma_pct = next(item for item in data["추세"] if item["value"] == "SMA_PCT")
    assert sma_pct["label"] not in ("", "SMA_PCT")
    # 손익(SELL_ONLY)은 카테고리 토글 대상이 아니므로 어떤 카테고리에도 안 나온다
    all_values = {item["value"] for items in data.values() for item in items}
    assert not {"STOP_LOSS_PCT", "TAKE_PROFIT_PCT", "HOLDING_PERIOD_BARS"} & all_values


def test_create_grid_search_job_rejects_base_run_id_without_combinator(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-ETH"}])
    monkeypatch.setattr(backend_module, "start_job", lambda **kwargs: "should-not-be-called")
    resp = client.post(
        "/api/v1/grid-search/jobs",
        json={
            "market": "KRW-ETH", "timeframe": "minutes60", "capital": 1000000,
            "start": "2026-01-01", "end": "2026-02-01", "top_n": 10,
            "base_run_id": "some-run-id",
        },
    )
    assert resp.status_code == 400
    assert "combinator" in resp.json()["detail"]


def test_create_grid_search_job_rejects_deleted_base_run_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-ETH"}])
    monkeypatch.setattr(backend_module, "get_run_config", lambda run_id: None)
    resp = client.post(
        "/api/v1/grid-search/jobs",
        json={
            "market": "KRW-ETH", "timeframe": "minutes60", "capital": 1000000,
            "start": "2026-01-01", "end": "2026-02-01", "top_n": 10,
            "base_run_id": "deleted-run-id", "combinator": "AND",
        },
    )
    assert resp.status_code == 400
    assert "베이스 결과" in resp.json()["detail"]


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


def test_reset_grid_search_active_job_clears_stuck_slot_and_fails_running_row(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    monkeypatch.setattr(backend_module, "reset_active_job", lambda: "job-1")

    resp = client.post("/api/v1/grid-search/jobs/reset")
    assert resp.status_code == 200
    assert resp.json() == {"reset_job_id": "job-1"}

    job = next(j for j in client.get("/api/v1/grid-search/jobs").json() if j["id"] == "job-1")
    assert job["status"] == "failed"


def test_reset_grid_search_active_job_returns_none_when_nothing_stuck(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "reset_active_job", lambda: None)

    resp = client.post("/api/v1/grid-search/jobs/reset")
    assert resp.status_code == 200
    assert resp.json() == {"reset_job_id": None}


def test_delete_grid_search_job_resets_active_slot_when_job_matches(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    calls: list[str | None] = []
    monkeypatch.setattr(
        backend_module, "reset_active_job",
        lambda expected_job_id=None: calls.append(expected_job_id),
    )

    resp = client.delete("/api/v1/grid-search/jobs/job-1")
    assert resp.status_code == 200
    assert calls == ["job-1"]


def test_delete_grid_search_result_removes_run_and_updates_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    save_result(
        run_id="run-a", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="[Grid] 매수 A / 매도 B",
    )
    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-01-10", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=10.0,
        result_json='[{"rank": 1, "run_id": "run-a", "return_pct": 5.0, "title": "[Grid] 매수 A / 매도 B"}]',
    )

    resp = client.delete("/api/v1/grid-search/jobs/job-1/results/run-a")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    job_resp = client.get("/api/v1/grid-search/jobs")
    job = next(j for j in job_resp.json() if j["id"] == "job-1")
    assert job["result_json"] == []

    backtests_resp = client.get("/api/v1/backtests")
    assert backtests_resp.json() == []


def test_delete_grid_search_result_returns_404_for_missing_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.delete("/api/v1/grid-search/jobs/does-not-exist/results/run-a")
    assert resp.status_code == 404


def test_delete_grid_search_result_returns_404_when_run_id_not_in_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-01-10", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=10.0,
        result_json='[{"rank": 1, "run_id": "run-a", "return_pct": 5.0, "title": "x"}]',
    )

    resp = client.delete("/api/v1/grid-search/jobs/job-1/results/run-b")
    assert resp.status_code == 404


def test_delete_grid_search_job_removes_job_and_its_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    save_result(
        run_id="run-a", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="[Grid] 매수 A / 매도 B",
    )
    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-01-10", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=10.0,
        result_json='[{"rank": 1, "run_id": "run-a", "return_pct": 5.0, "title": "[Grid] 매수 A / 매도 B"}]',
    )

    resp = client.delete("/api/v1/grid-search/jobs/job-1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    jobs_resp = client.get("/api/v1/grid-search/jobs")
    assert jobs_resp.json() == []

    backtests_resp = client.get("/api/v1/backtests")
    assert backtests_resp.json() == []


def test_delete_grid_search_job_removes_job_with_no_results(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_grid_search_job

    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-01-10", top_n=20,
    )
    finish_grid_search_job("job-1", status="canceled")

    resp = client.delete("/api/v1/grid-search/jobs/job-1")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True}

    jobs_resp = client.get("/api/v1/grid-search/jobs")
    assert jobs_resp.json() == []


def test_delete_grid_search_job_returns_404_for_missing_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    resp = client.delete("/api/v1/grid-search/jobs/does-not-exist")
    assert resp.status_code == 404


def test_get_trend_segments_endpoint_returns_segments_and_ohlcv(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({
        "candle_time": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    resp = client.get("/api/v1/analysis/trend-segments/KRW-BTC")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert len(body["segments"]) >= 1
    assert len(body["ohlcv"]) == 4
    assert body["ohlcv"][0]["close"] == 100


def test_refresh_trend_segments_endpoint_forces_recompute(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    closes = [100, 130, 90, 140]
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    df = pd.DataFrame({
        "candle_time": dates, "open": closes, "high": closes, "low": closes, "close": closes,
    })
    monkeypatch.setattr(trend_segments_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(backend_module, "get_candles", lambda market, tf, start, end: df)
    monkeypatch.setattr(trend_segments_module, "_compute_volatility", lambda market: 0.02)

    resp = client.post("/api/v1/analysis/trend-segments/KRW-BTC/refresh")

    assert resp.status_code == 200
    body = resp.json()
    assert body["market"] == "KRW-BTC"
    assert len(body["segments"]) >= 1


def test_get_backtest_runs_filters_by_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="btc-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="BTC 결과",
    )
    save_result(
        run_id="eth-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="ETH 결과",
    )

    resp = client.get("/api/v1/backtests", params={"market": "KRW-ETH"})

    assert resp.status_code == 200
    run_ids = [r["run_id"] for r in resp.json()]
    assert run_ids == ["eth-run"]


def test_get_backtest_runs_without_market_returns_all(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    save_result(
        run_id="btc-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="BTC 결과",
    )

    resp = client.get("/api/v1/backtests")

    assert resp.status_code == 200
    assert len(resp.json()) == 1


def _seed_backtest_run(run_id: str, market: str, timeframe: str, buy_conditions: dict, sell_conditions: dict) -> None:
    save_result(
        run_id=run_id, strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": buy_conditions, "sell_conditions": sell_conditions},
        market=market, timeframe=timeframe,
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="교체용",
    )


def test_live_strategy_response_includes_source_run_id(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])

    resp = client.post("/api/v1/live-strategies", json=_live_strategy_request(source_run_id="orig-run"))

    assert resp.json()["source_run_id"] == "orig-run"


def test_replace_live_strategy_swaps_timeframe_and_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post(
        "/api/v1/live-strategies", json=_live_strategy_request(source_run_id="old-run"),
    ).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")  # draft -> stopped, 승인 없이도 가능
    new_buy = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]}
    new_sell = {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]}
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", new_buy, new_sell)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["timeframe"] == "minutes30"
    assert body["buy_conditions"] == new_buy
    assert body["sell_conditions"] == new_sell
    assert body["source_run_id"] == "new-run"
    assert body["status"] == "stopped"  # 상태는 교체 전과 동일하게 유지


def test_replace_live_strategy_returns_404_for_missing_strategy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    resp = client.post(
        "/api/v1/live-strategies/does-not-exist/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 404


def test_replace_live_strategy_returns_404_for_missing_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "does-not-exist"},
    )

    assert resp.status_code == 404


def test_replace_live_strategy_returns_409_for_draft_status(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 409


def test_replace_live_strategy_returns_400_for_market_mismatch(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    _seed_backtest_run("eth-run", "KRW-ETH", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "eth-run"},
    )

    assert resp.status_code == 400


def test_replace_live_strategy_returns_400_for_empty_conditions(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    save_result(
        run_id="empty-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="minutes30",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="빈 조건",
    )

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "empty-run"},
    )

    assert resp.status_code == 400


def test_replace_live_strategy_returns_400_for_unknown_indicator(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    bad_buy = {"type": "AND", "conditions": [{"indicator": "NOT_A_REAL_INDICATOR", "params": {}, "operator": "<", "threshold": 1}]}
    save_result(
        run_id="bad-indicator-run", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": bad_buy, "sell_conditions": _VALID_SELL},
        market="KRW-BTC", timeframe="minutes30",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title="알 수 없는 지표",
    )

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "bad-indicator-run"},
    )

    assert resp.status_code == 400


def test_replace_live_strategy_returns_400_for_non_condition_tree_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/stop")
    save_result(
        run_id="sweep-run", strategy_name="SignalStrategy", strategy_params={},
        market="KRW-BTC", timeframe="minutes30",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "sweep-run"},
    )

    assert resp.status_code == 400


def test_replace_live_strategy_returns_409_when_position_open(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(backend_module, "get_krw_markets", lambda: [{"market": "KRW-BTC"}])
    monkeypatch.setattr(backend_module.upbit_client, "get_accounts", _accounts_with_krw_balance(1_000_000))
    strategy_id = client.post("/api/v1/live-strategies", json=_live_strategy_request()).json()["id"]
    client.post(f"/api/v1/live-strategies/{strategy_id}/approve")
    trading_db_module.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    _seed_backtest_run("new-run", "KRW-BTC", "minutes30", _VALID_BUY, _VALID_SELL)

    resp = client.post(
        f"/api/v1/live-strategies/{strategy_id}/replace-strategy",
        json={"source_run_id": "new-run"},
    )

    assert resp.status_code == 409


def test_regime_ml_current_prediction_returns_result(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    captured = {}

    def _fake_predict(market, timeframe):
        captured["args"] = (market, timeframe)
        return {
            "predicted_category": "횡보",
            "probs": {"급하락": 0.1, "완만하락": 0.2, "횡보": 0.3, "완만상승": 0.25, "급상승": 0.15},
            "model_trained_at": "2026-08-27T05:20:47+00:00",
            "model_fold_index": 5,
        }

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-ETH", "timeframe": "minutes60"},
    )

    assert resp.status_code == 200
    assert resp.json()["predicted_category"] == "횡보"
    assert captured["args"] == ("KRW-ETH", "minutes60")


def test_regime_ml_current_prediction_returns_400_for_wrong_timeframe(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_predict(market, timeframe):
        raise ValueError("ML 모델은 1시간봉(minutes60)으로만 학습되어 있습니다")

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-ETH", "timeframe": "days"},
    )

    assert resp.status_code == 400
    assert "1시간봉" in resp.json()["detail"]


def test_regime_ml_current_prediction_returns_400_for_unsupported_market(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_predict(market, timeframe):
        raise ValueError("이 모델은 KRW-BTC, KRW-ETH, KRW-XRP로만 학습되어 있습니다")

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-DOGE", "timeframe": "minutes60"},
    )

    assert resp.status_code == 400
    assert "만 학습되어" in resp.json()["detail"]


def test_regime_ml_current_prediction_returns_404_when_no_model(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    def _fake_predict(market, timeframe):
        raise FileNotFoundError("학습된 ML 모델이 없습니다. scripts/train_regime_ml.py를 먼저 실행하세요")

    monkeypatch.setattr(backend_module, "predict_current_ml_regime", _fake_predict)

    resp = client.get(
        "/api/v1/regime/ml-current-prediction",
        params={"market": "KRW-ETH", "timeframe": "minutes60"},
    )

    assert resp.status_code == 404
    assert "학습된 ML 모델이 없습니다" in resp.json()["detail"]


def test_ml_train_enabled_defaults_to_false(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLE_ML_TRAINING_UI", raising=False)

    resp = client.get("/api/v1/regime/ml-train-enabled")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


def test_ml_train_enabled_true_when_env_set(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    resp = client.get("/api/v1/regime/ml-train-enabled")
    assert resp.json() == {"enabled": True}


def test_start_regime_ml_train_job_rejects_when_flag_disabled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLE_ML_TRAINING_UI", raising=False)

    resp = client.post("/api/v1/regime/ml-train")
    assert resp.status_code == 403


def test_start_regime_ml_train_job_returns_running_job(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")
    monkeypatch.setattr(backend_module, "start_regime_ml_training_job", lambda: "job-1")

    from engine.cache import create_regime_ml_job
    create_regime_ml_job("job-1")

    resp = client.post("/api/v1/regime/ml-train")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == "job-1"
    assert body["status"] == "running"


def test_start_regime_ml_train_job_returns_409_when_already_running(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    def _raise():
        raise backend_module.RegimeMlJobAlreadyRunningError("job-existing")

    monkeypatch.setattr(backend_module, "start_regime_ml_training_job", _raise)

    resp = client.post("/api/v1/regime/ml-train")
    assert resp.status_code == 409


def test_list_regime_ml_train_jobs_returns_saved_jobs(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    from engine.cache import create_regime_ml_job
    create_regime_ml_job("job-1")

    resp = client.get("/api/v1/regime/ml-train/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == "job-1"


def test_startup_fails_orphaned_running_regime_ml_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    class _FakeThread:
        def __init__(self, target=None, daemon=None):
            pass

        def start(self):
            pass

    monkeypatch.setattr(backend_module.threading, "Thread", _FakeThread)

    from engine.cache import create_regime_ml_job, get_regime_ml_job
    create_regime_ml_job("orphan-1")

    with TestClient(app):
        pass

    job = get_regime_ml_job("orphan-1")
    assert job["status"] == "failed"
    assert "재시작" in job["error_message"]


def test_list_regime_ml_models_endpoint_returns_models(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        backend_module, "list_trained_models",
        lambda: [{"model_timestamp": "regime_ml_1", "trained_at": "2026-01-01T00:00:00+00:00",
                  "performance": None, "is_deployed": True}],
    )

    resp = client.get("/api/v1/regime/ml-models")
    assert resp.status_code == 200
    assert resp.json()[0]["model_timestamp"] == "regime_ml_1"


def test_deploy_regime_ml_model_rejects_when_flag_disabled(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.delenv("ENABLE_ML_TRAINING_UI", raising=False)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_20260827T223633Z"})
    assert resp.status_code == 403


def test_deploy_regime_ml_model_returns_404_when_model_missing(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    def _raise(model_timestamp):
        raise FileNotFoundError(f"모델을 찾을 수 없습니다: {model_timestamp}")

    monkeypatch.setattr(backend_module, "deploy_model", _raise)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_20260827T223633Z"})
    assert resp.status_code == 404


def test_deploy_regime_ml_model_returns_500_when_script_fails(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    def _raise(model_timestamp):
        raise RuntimeError("scp 실패")

    monkeypatch.setattr(backend_module, "deploy_model", _raise)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_20260827T223633Z"})
    assert resp.status_code == 500
    assert "scp 실패" in resp.json()["detail"]


def test_deploy_regime_ml_model_succeeds(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")
    monkeypatch.setattr(backend_module, "deploy_model", lambda model_timestamp: None)

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "regime_ml_20260827T223633Z"})
    assert resp.status_code == 200
    assert resp.json() == {"deployed": True, "model_timestamp": "regime_ml_20260827T223633Z"}


def test_deploy_regime_ml_model_rejects_malformed_timestamp(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("ENABLE_ML_TRAINING_UI", "true")

    resp = client.post("/api/v1/regime/ml-deploy", json={"model_timestamp": "../../etc/passwd"})
    assert resp.status_code == 422
