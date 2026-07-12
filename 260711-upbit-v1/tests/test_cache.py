from datetime import datetime, timezone

import backtrader as bt
import pandas as pd
import pytest

import engine.cache as cache_module
from engine.cache import compute_cache_key, load_result, save_result
from engine.cache import run_backtest_cached
from engine.cache import (
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
