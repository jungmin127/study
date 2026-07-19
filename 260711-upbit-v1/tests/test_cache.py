from datetime import datetime, timezone

import backtrader as bt
import pandas as pd
import pytest

import engine.cache as cache_module
from engine.cache import compute_cache_key, load_result, save_result
from engine.cache import run_backtest_cached
from engine.cache import (
    list_backtest_runs,
    list_combined_ranking,
    list_distinct_combos,
    list_latest_sweep_results,
    list_sweep_history,
    save_sweep_result,
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
                              final_value: float = 11000.0, initial_capital: float = 10000.0):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id=run_id,
        strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC",
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


def test_connect_migration_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    # _connect()를 여러 번 호출해도(=ALTER TABLE을 여러 번 시도해도) 에러가 나면 안 됨
    cache_module._connect().close()
    cache_module._connect().close()
    cache_module._connect().close()
