import json
import threading
from datetime import datetime, timezone

import backtrader as bt
import pandas as pd
import pytest

import engine.cache as cache_module
from engine.cache import compute_cache_key, delete_backtest_run, get_run_config, load_result, save_result, update_backtest_run_metadata
from engine.cache import run_backtest_cached
from engine.cache import (
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_runs_missing_candle_count,
    list_segment_classification,
    list_sweep_history,
    save_segment_classification,
    save_sweep_result,
    set_candle_count,
)
from engine.cache import (
    create_grid_search_job,
    delete_grid_search_job,
    finish_grid_search_job,
    get_grid_search_job,
    list_grid_search_jobs,
    remove_grid_search_result,
    update_grid_search_job_progress,
)


class _StrategyA(bt.Strategy):
    def next(self):
        pass


class _StrategyB(bt.Strategy):
    def next(self):
        self.buy()


def _key(strategy_cls=_StrategyA, params=None, risk=None):
    return compute_cache_key(
        strategy_cls,
        params or {"threshold": 1},
        "KRW-BTC",
        "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk or {"initial_capital": 10000},
    )


def test_same_inputs_produce_same_key():
    assert _key() == _key()


def test_different_params_produce_different_key():
    assert _key(params={"threshold": 1}) != _key(params={"threshold": 2})


def test_different_strategy_source_produces_different_key():
    assert _key(strategy_cls=_StrategyA) != _key(strategy_cls=_StrategyB)


def test_different_risk_config_produces_different_key():
    assert _key(risk={"initial_capital": 10000}) != _key(risk={"initial_capital": 5000})


class _DummySignal:
    """signals.py의 Signal 구현체를 흉내낸, __dict__ 속성을 가진 평범한 객체."""

    def __init__(self, threshold: float):
        self.threshold = threshold


def test_compute_cache_key_is_stable_for_object_params_regardless_of_identity():
    a = _key(params={"signals": [_DummySignal(30)]})
    b = _key(params={"signals": [_DummySignal(30)]})
    assert a == b


def test_compute_cache_key_differs_for_different_object_attributes():
    a = _key(params={"signals": [_DummySignal(30)]})
    b = _key(params={"signals": [_DummySignal(70)]})
    assert a != b


class _SignalWithRuntimeState:
    """setup() 호출 후의 실제 Signal처럼, 원시 타입이 아니고 순환 참조까지 있는
    런타임 속성(예: backtrader 지표 객체)이 붙은 상태를 흉내낸다."""

    def __init__(self, period: int):
        self.period = period
        # 실제 Signal 클래스들처럼(예: MacdCrossSignal), 런타임 상태 속성은
        # setup()이 호출되기 전까지 아예 존재하지 않는다 - 여기서 미리 선언하지 않는다.

    def attach_runtime_state(self) -> None:
        # backtrader 지표는 종종 자기 자신이나 상위 객체를 참조하는 복잡한 그래프를 가진다.
        node = {"self_ref": None}
        node["self_ref"] = node
        self._indicator = node


def test_compute_cache_key_ignores_non_primitive_runtime_attributes():
    a = _SignalWithRuntimeState(14)
    b = _SignalWithRuntimeState(14)
    b.attach_runtime_state()

    # a는 setup() 전, b는 setup() 후(순환 참조 포함) 상태를 흉내낸다.
    # 캐시 키는 오직 생성자 파라미터(period)에만 의존해야 하므로 동일해야 한다.
    assert _key(params={"signals": [a]}) == _key(params={"signals": [b]})


class _SignalWithNonPrimitiveConfig:
    """리스트/딕셔너리처럼 원시 타입이 아닌 생성자 파라미터를 가진 (가상의) 신호.
    현재 signals.py의 4개 신호는 전부 원시 타입 파라미터만 쓰지만, 향후 이런
    신호가 추가되면 캐시 키에서 조용히 빠져 false cache hit 위험이 생긴다 —
    최소한 경고는 나와야 한다."""

    def __init__(self, thresholds: list[float]):
        self.thresholds = thresholds


def test_compute_cache_key_warns_when_dropping_non_primitive_config():
    with pytest.warns(UserWarning, match="thresholds"):
        _key(params={"signals": [_SignalWithNonPrimitiveConfig([30.0, 70.0])]})


def test_save_result_accepts_object_valued_strategy_params(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    run_id = _key(params={"signals": [_DummySignal(30)]})
    save_result(
        run_id=run_id,
        strategy_name="SignalStrategy",
        strategy_params={"signals": [_DummySignal(30)]},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
    )

    loaded = load_result(run_id)
    assert loaded["final_value"] == 10500.0


def test_save_then_load_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    result = {
        "equity_curve": [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}],
        "trades": [{"entryTime": "2026-01-01T00:00:00", "pnl": 5.0}],
        "final_value": 10500.0,
        "sharpe": 1.2,
        "max_drawdown": 3.4,
    }

    save_result(
        run_id="abc123",
        strategy_name="_StrategyA",
        strategy_params={"threshold": 1},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result=result,
    )

    loaded = load_result("abc123")

    assert loaded is not None
    assert loaded["final_value"] == 10500.0
    assert loaded["sharpe"] == 1.2
    assert loaded["max_drawdown"] == 3.4
    assert loaded["equity_curve"] == result["equity_curve"]
    assert loaded["trades"] == result["trades"]
    assert loaded["from_cache"] is True


def test_load_missing_run_id_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert load_result("does-not-exist") is None


def _synthetic_df() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=10, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "candle_time": idx,
            "open": range(10),
            "high": range(10),
            "low": range(10),
            "close": range(10),
            "volume": [1.0] * 10,
        }
    )


def test_run_backtest_cached_hits_cache_on_second_call(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    call_count = {"n": 0}

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None):
        call_count["n"] += 1
        return {
            "equity_curve": [],
            "trades": [],
            "final_value": 10000.0,
            "sharpe": None,
            "max_drawdown": None,
        }

    monkeypatch.setattr(cache_module, "run_backtest", fake_run_backtest)

    kwargs = dict(
        df=_synthetic_df(),
        strategy_cls=_StrategyA,
        risk_config={"initial_capital": 10000},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        strategy_params={"threshold": 1},
    )

    first = run_backtest_cached(**kwargs)
    second = run_backtest_cached(**kwargs)

    assert call_count["n"] == 1
    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert second["final_value"] == 10000.0


def test_run_backtest_cached_does_not_cache_on_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    def failing_run_backtest(df, strategy_cls, risk_config, strategy_params=None):
        raise ValueError("전략 실행 실패")

    monkeypatch.setattr(cache_module, "run_backtest", failing_run_backtest)

    with pytest.raises(ValueError):
        run_backtest_cached(
            df=_synthetic_df(),
            strategy_cls=_StrategyA,
            risk_config={"initial_capital": 10000},
            market="KRW-BTC",
            timeframe="days",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        )

    run_id = compute_cache_key(
        _StrategyA, {}, "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
        {"initial_capital": 10000},
    )
    assert load_result(run_id) is None


def test_run_backtest_cached_exposes_run_id(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    def fake_run_backtest(df, strategy_cls, risk_config, strategy_params=None):
        return {"equity_curve": [], "trades": [], "final_value": 10000.0, "sharpe": None, "max_drawdown": None}

    monkeypatch.setattr(cache_module, "run_backtest", fake_run_backtest)

    result = run_backtest_cached(
        df=_synthetic_df(), strategy_cls=_StrategyA, risk_config={"initial_capital": 10000},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
    )
    expected_run_id = compute_cache_key(
        _StrategyA, {}, "KRW-BTC", "days",
        datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 10, tzinfo=timezone.utc),
        {"initial_capital": 10000},
    )
    assert result["run_id"] == expected_run_id


def test_save_and_list_latest_sweep_results(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_sweep_result(
        run_id="run-1", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=5.0, sharpe=1.1, max_drawdown=2.0,
    )
    # 같은 조합을 다시 스윕 — append-only이므로 새 행이 추가돼야 함
    save_sweep_result(
        run_id="run-2", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 11, tzinfo=timezone.utc),
        return_rate=7.0, sharpe=1.3, max_drawdown=2.5,
    )

    latest = list_latest_sweep_results()
    assert len(latest) == 1  # 같은 (signal_set_name, is_combined, market, timeframe) 조합은 최신 1건만
    assert latest[0]["return_rate"] == 7.0

    history = list_sweep_history("macd_cross", "KRW-BTC", "days", is_combined=False)
    assert len(history) == 2  # 히스토리는 append-only로 둘 다 보여야 함


def test_combined_ranking_filters_and_sorts_by_return_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_sweep_result(
        run_id="run-a", signal_set_name="macd_cross", is_combined=False,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=100.0, sharpe=None, max_drawdown=None,
    )
    save_sweep_result(
        run_id="run-b", signal_set_name="mixed_all", is_combined=True,
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=3.0, sharpe=None, max_drawdown=None,
    )
    save_sweep_result(
        run_id="run-c", signal_set_name="mixed_all", is_combined=True,
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=8.0, sharpe=None, max_drawdown=None,
    )

    ranking = list_combined_ranking()
    assert [r["market"] for r in ranking] == ["KRW-BTC", "KRW-ETH"]  # is_combined=False(run-a)는 제외, 수익률 내림차순


def test_list_distinct_combos(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_sweep_result(
        run_id="run-x", signal_set_name="rsi_zone", is_combined=False,
        market="KRW-BTC", timeframe="minutes60",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        return_rate=1.0, sharpe=None, max_drawdown=None,
    )
    combos = list_distinct_combos()
    assert combos == [
        {"signal_set_name": "rsi_zone", "is_combined": False, "market": "KRW-BTC", "timeframe": "minutes60"}
    ]


def _save_condition_tree_run(monkeypatch, tmp_path, run_id: str, title: str | None, description: str | None,
                              final_value: float = 11000.0, initial_capital: float = 10000.0,
                              market: str = "KRW-BTC"):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id=run_id,
        strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market=market,
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": initial_capital},
        result={"final_value": final_value, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
        title=title,
        description=description,
    )


def test_save_result_stores_and_returns_title_and_description(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "run-1", title="내 첫 포트", description="RSI 전략 테스트")

    runs = list_backtest_runs()
    assert len(runs) == 1
    assert runs[0]["title"] == "내 첫 포트"
    assert runs[0]["description"] == "RSI 전략 테스트"
    assert runs[0]["return_rate"] == 10.0  # (11000-10000)/10000*100


def test_list_backtest_runs_excludes_sweep_based_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    # sweep 시스템이 남기는 SignalStrategy 행 (히트맵/랭킹 전용, title 없음)
    save_result(
        run_id="sweep-run", strategy_name="SignalStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )
    _save_condition_tree_run(monkeypatch, tmp_path, "ondemand-run", title="온디맨드", description=None)

    runs = list_backtest_runs()
    assert [r["run_id"] for r in runs] == ["ondemand-run"]


def test_list_backtest_runs_orders_by_created_at_desc(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "older", title="먼저 실행", description=None)
    _save_condition_tree_run(monkeypatch, tmp_path, "newer", title="나중 실행", description=None)

    runs = list_backtest_runs()
    assert [r["run_id"] for r in runs] == ["newer", "older"]


def test_delete_backtest_run_removes_run_and_result(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "run-1", title="삭제 대상", description=None)

    assert delete_backtest_run("run-1") is True
    assert list_backtest_runs() == []
    assert load_result("run-1") is None


def test_delete_backtest_run_returns_false_for_missing_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert delete_backtest_run("does-not-exist") is False


def test_update_backtest_run_metadata_updates_title_and_description(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "run-1", title="원래 제목", description="원래 설명")

    updated = update_backtest_run_metadata("run-1", "새 제목", "새 설명")

    assert updated is True
    runs = list_backtest_runs()
    run = next(r for r in runs if r["run_id"] == "run-1")
    assert run["title"] == "새 제목"
    assert run["description"] == "새 설명"


def test_update_backtest_run_metadata_returns_false_for_missing_run(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    assert update_backtest_run_metadata("does-not-exist", "제목", None) is False


def test_load_result_includes_title_description_and_created_at(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "run-1", title="제목", description="설명")

    result = load_result("run-1")

    assert result["title"] == "제목"
    assert result["description"] == "설명"
    assert result["created_at"] is not None


def test_connect_migration_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    # _connect()를 여러 번 호출해도(=ALTER TABLE을 여러 번 시도해도) 에러가 나면 안 됨
    cache_module._connect().close()
    cache_module._connect().close()
    cache_module._connect().close()


def test_load_result_includes_market_timeframe_and_initial_capital(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="minutes15",
        start=datetime(2026, 4, 22, tzinfo=timezone.utc), end=datetime(2026, 7, 21, tzinfo=timezone.utc),
        risk_config={"initial_capital": 1_000_000},
        result={"final_value": 1_100_000.0, "sharpe": 1.0, "max_drawdown": 2.0, "equity_curve": [], "trades": []},
    )

    loaded = load_result("r1")
    assert loaded["market"] == "KRW-BTC"
    assert loaded["timeframe"] == "minutes15"
    assert loaded["start"] == datetime(2026, 4, 22, tzinfo=timezone.utc).isoformat()
    assert loaded["end"] == datetime(2026, 7, 21, tzinfo=timezone.utc).isoformat()
    assert loaded["initial_capital"] == 1_000_000


def test_load_result_includes_commission_rate(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000, "commission_rate": 0.002},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )
    loaded = load_result("r1")
    assert loaded["commission_rate"] == 0.002


def test_list_backtest_runs_includes_revaluation_fields_and_strategy_conditions(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="run-1", strategy_name="ConditionTreeStrategy",
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
        risk_config={"initial_capital": 10000, "commission_rate": 0.001},
        result={
            "final_value": 11000.0, "sharpe": None, "max_drawdown": None, "equity_curve": [],
            "trades": [{"forceClosed": True, "size": 1.0, "entryPrice": 100.0, "pnl": 5.0}],
        },
        title="테스트",
    )

    runs = list_backtest_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["initial_capital"] == 10000
    assert run["commission_rate"] == 0.001
    assert run["buy_conditions"]["conditions"][0]["indicator"] == "RSI"
    assert run["sell_conditions"]["conditions"][0]["threshold"] == 70
    assert run["trades"][0]["size"] == 1.0


def _sample_segment_row(**overrides) -> dict:
    row = {
        "market": "KRW-BTC",
        "korean_name": "비트코인",
        "segment": "large",
        "trade_value_24h": 45_700_000_000.0,
        "volatility_30d": 0.012,
        "trade_value_percentile": 99.0,
        "volatility_percentile": 10.0,
        "is_caution": False,
        "computed_at": "2026-07-25T00:00:00+00:00",
    }
    row.update(overrides)
    return row


def test_save_and_list_segment_classification_round_trips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_segment_classification([
        _sample_segment_row(),
        _sample_segment_row(
            market="KRW-XXX", korean_name="잡코인", segment="junk",
            trade_value_24h=1_000_000.0, volatility_30d=0.09,
            trade_value_percentile=2.0, volatility_percentile=95.0, is_caution=True,
        ),
    ])

    rows = list_segment_classification()
    assert [r["market"] for r in rows] == ["KRW-BTC", "KRW-XXX"]
    assert rows[0]["segment"] == "large"
    assert rows[0]["is_caution"] is False
    assert rows[1]["is_caution"] is True
    assert rows[1]["trade_value_percentile"] == 2.0


def test_save_segment_classification_replaces_previous_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    save_segment_classification([_sample_segment_row(market="KRW-OLD")])
    save_segment_classification([_sample_segment_row(market="KRW-NEW")])

    rows = list_segment_classification()
    assert [r["market"] for r in rows] == ["KRW-NEW"]


def test_get_run_config_returns_stored_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={
            "buy_conditions": {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": "<", "threshold": 30}]},
            "sell_conditions": {"type": "AND", "conditions": [{"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70}]},
        },
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 3, 1, tzinfo=timezone.utc),
        risk_config={"initial_capital": 1_000_000, "commission_rate": 0.0005},
        result={"final_value": 1_100_000.0, "sharpe": 1.0, "max_drawdown": 5.0, "equity_curve": [], "trades": []},
        title="추적용", description="설명",
    )

    config = get_run_config("r1")

    assert config is not None
    assert config["strategy_name"] == "ConditionTreeStrategy"
    assert config["market"] == "KRW-BTC"
    assert config["timeframe"] == "days"
    assert config["start"].startswith("2026-01-01")
    assert config["risk_config"] == {"initial_capital": 1_000_000, "commission_rate": 0.0005}
    assert config["buy_conditions"]["conditions"][0]["indicator"] == "RSI"
    assert config["sell_conditions"]["conditions"][0]["threshold"] == 70
    assert config["title"] == "추적용"
    assert config["description"] == "설명"


def test_get_run_config_returns_none_for_missing_run(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert get_run_config("does-not-exist") is None


def test_create_and_get_grid_search_job_roundtrips(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    job = get_grid_search_job("job-1")
    assert job["id"] == "job-1"
    assert job["market"] == "KRW-SOL"
    assert job["capital"] == 1_000_000.0
    assert job["top_n"] == 20
    assert job["status"] == "running"
    assert job["done_combos"] == 0
    assert job["total_combos"] is None
    assert job["finished_at"] is None
    assert job["result_json"] is None


def test_get_grid_search_job_returns_none_for_missing_id(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert get_grid_search_job("does-not-exist") is None


def test_update_grid_search_job_progress_updates_counts(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    update_grid_search_job_progress("job-1", done_combos=1005, total_combos=20700)

    job = get_grid_search_job("job-1")
    assert job["done_combos"] == 1005
    assert job["total_combos"] == 20700
    assert job["status"] == "running"


def test_finish_grid_search_job_marks_completed_with_results(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=1617.9,
        result_json='[{"rank": 1, "run_id": "abc", "return_pct": 33.65, "title": "[Grid] ..."}]',
    )

    job = get_grid_search_job("job-1")
    assert job["status"] == "completed"
    assert job["elapsed_sec"] == 1617.9
    assert job["finished_at"] is not None
    assert job["result_json"] == [{"rank": 1, "run_id": "abc", "return_pct": 33.65, "title": "[Grid] ..."}]


def test_finish_grid_search_job_marks_failed_with_error_message(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    finish_grid_search_job("job-1", status="failed", error_message="워커 응답 없음")

    job = get_grid_search_job("job-1")
    assert job["status"] == "failed"
    assert job["error_message"] == "워커 응답 없음"
    assert job["result_json"] is None


def test_remove_grid_search_result_removes_matching_entry(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=100.0,
        result_json=(
            '[{"rank": 1, "run_id": "run-a", "return_pct": 10.0, "title": "a"}, '
            '{"rank": 2, "run_id": "run-b", "return_pct": 5.0, "title": "b"}]'
        ),
    )

    assert remove_grid_search_result("job-1", "run-a") is True

    job = get_grid_search_job("job-1")
    assert job["result_json"] == [{"rank": 2, "run_id": "run-b", "return_pct": 5.0, "title": "b"}]


def test_remove_grid_search_result_returns_false_for_missing_run_id(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    finish_grid_search_job(
        "job-1", status="completed", elapsed_sec=100.0,
        result_json='[{"rank": 1, "run_id": "run-a", "return_pct": 10.0, "title": "a"}]',
    )

    assert remove_grid_search_result("job-1", "does-not-exist") is False

    job = get_grid_search_job("job-1")
    assert job["result_json"] == [{"rank": 1, "run_id": "run-a", "return_pct": 10.0, "title": "a"}]


def test_remove_grid_search_result_returns_false_when_result_json_is_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    assert remove_grid_search_result("job-1", "run-a") is False


def test_remove_grid_search_result_returns_false_for_missing_job(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert remove_grid_search_result("does-not-exist", "run-a") is False


def test_remove_grid_search_result_is_safe_under_concurrent_deletes(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )
    entries = [
        {"rank": i + 1, "run_id": f"run-{i}", "return_pct": float(i), "title": f"t{i}"}
        for i in range(20)
    ]
    finish_grid_search_job("job-1", status="completed", elapsed_sec=1.0, result_json=json.dumps(entries))

    threads = [
        threading.Thread(target=remove_grid_search_result, args=("job-1", f"run-{i}"))
        for i in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    job = get_grid_search_job("job-1")
    assert job["result_json"] == []


def test_list_grid_search_jobs_returns_newest_first(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-BTC", timeframe="days", capital=1_000_000.0,
        start="2026-01-01", end="2026-02-01", top_n=20,
    )
    create_grid_search_job(
        job_id="job-2", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    jobs = list_grid_search_jobs()
    assert [j["id"] for j in jobs] == ["job-2", "job-1"]


def test_delete_grid_search_job_removes_the_job(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    create_grid_search_job(
        job_id="job-1", market="KRW-SOL", timeframe="minutes60", capital=1_000_000.0,
        start="2026-06-05", end="2026-08-03", top_n=20,
    )

    assert delete_grid_search_job("job-1") is True
    assert get_grid_search_job("job-1") is None


def test_delete_grid_search_job_returns_false_for_missing_job(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    assert delete_grid_search_job("does-not-exist") is False


def test_create_grid_search_job_persists_chaining_fields(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    job_id = "test-chain-job-1"
    create_grid_search_job(
        job_id=job_id, market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
        start="2026-01-01", end="2026-02-01", top_n=10,
        indicator_pool_json=json.dumps({"categories": ["추세"], "excluded_indicators": []}),
        base_run_id="base-run-abc",
        combinator="OR",
    )
    job = get_grid_search_job(job_id)
    assert job is not None
    assert job["indicator_pool"] == {"categories": ["추세"], "excluded_indicators": []}
    assert job["base_run_id"] == "base-run-abc"
    assert job["combinator"] == "OR"


def test_pre_existing_grid_search_job_row_survives_chaining_columns_migration(monkeypatch, tmp_path):
    """ALTER TABLE로 indicator_pool/base_run_id/combinator 3개 컬럼이 추가되기 전에 이미
    저장돼 있던 job row가, 마이그레이션(engine.cache._connect) 이후에도 손상 없이 그대로
    조회돼야 한다(최종 리뷰가 이월한 Task 1 백로그 — 실 DB 수동 확인은 됐지만 자동 회귀
    테스트가 없었다)."""
    import sqlite3

    db_path = tmp_path / "results.db"
    monkeypatch.setattr(cache_module, "DB_PATH", db_path)

    # 구버전 스키마(신규 3개 컬럼 없음)를 직접 만들고 행을 하나 심어둔다.
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE grid_search_jobs (
            id             TEXT PRIMARY KEY,
            market         TEXT NOT NULL,
            timeframe      TEXT NOT NULL,
            capital        REAL NOT NULL,
            start          TEXT NOT NULL,
            end            TEXT NOT NULL,
            top_n          INTEGER NOT NULL,
            status         TEXT NOT NULL,
            total_combos   INTEGER,
            done_combos    INTEGER NOT NULL DEFAULT 0,
            started_at     TEXT NOT NULL,
            finished_at    TEXT,
            elapsed_sec    REAL,
            error_message  TEXT,
            result_json    TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO grid_search_jobs "
        "(id, market, timeframe, capital, start, end, top_n, status, done_combos, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("legacy-job", "KRW-BTC", "minutes60", 1_000_000.0, "2026-01-01", "2026-02-01", 20, "completed", 100, "2026-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    # get_grid_search_job() 내부에서 _connect()가 호출되며 ALTER TABLE 마이그레이션이 실행된다.
    job = get_grid_search_job("legacy-job")

    assert job is not None
    assert job["id"] == "legacy-job"
    assert job["market"] == "KRW-BTC"
    assert job["status"] == "completed"
    assert job["done_combos"] == 100
    assert job["indicator_pool"] is None
    assert job["base_run_id"] is None
    assert job["combinator"] is None


def test_create_grid_search_job_without_chaining_fields_defaults_to_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    job_id = "test-chain-job-2"
    create_grid_search_job(
        job_id=job_id, market="KRW-ETH", timeframe="minutes60", capital=1_000_000,
        start="2026-01-01", end="2026-02-01", top_n=10,
    )
    job = get_grid_search_job(job_id)
    assert job is not None
    assert job["indicator_pool"] is None
    assert job["base_run_id"] is None
    assert job["combinator"] is None


def test_list_backtest_runs_filters_by_market(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "btc-run", title="BTC", description=None, market="KRW-BTC")
    _save_condition_tree_run(monkeypatch, tmp_path, "eth-run", title="ETH", description=None, market="KRW-ETH")

    runs = list_backtest_runs(market="KRW-ETH")

    assert [r["run_id"] for r in runs] == ["eth-run"]


def test_list_backtest_runs_without_market_returns_all(monkeypatch, tmp_path):
    _save_condition_tree_run(monkeypatch, tmp_path, "btc-run", title="BTC", description=None, market="KRW-BTC")
    _save_condition_tree_run(monkeypatch, tmp_path, "eth-run", title="ETH", description=None, market="KRW-ETH")

    runs = list_backtest_runs()

    assert {r["run_id"] for r in runs} == {"btc-run", "eth-run"}


def test_save_result_stores_and_returns_candle_count(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [], "trades": [], "candle_count": 240,
        },
    )

    loaded = load_result("r1")
    assert loaded["candle_count"] == 240

    runs = list_backtest_runs()
    assert runs[0]["candle_count"] == 240


def test_save_result_without_candle_count_stores_none(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    loaded = load_result("r1")
    assert loaded["candle_count"] is None


def test_list_runs_missing_candle_count_returns_only_null_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="has-count", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [], "trades": [], "candle_count": 100,
        },
    )
    save_result(
        run_id="missing-count", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-ETH", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    missing = list_runs_missing_candle_count()
    assert [r["run_id"] for r in missing] == ["missing-count"]
    assert missing[0]["market"] == "KRW-ETH"
    assert missing[0]["timeframe"] == "days"


def test_set_candle_count_updates_existing_row(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="r1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )

    set_candle_count("r1", 365)

    assert load_result("r1")["candle_count"] == 365
    assert list_runs_missing_candle_count() == []


