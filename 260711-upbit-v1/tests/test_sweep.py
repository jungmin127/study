from datetime import datetime, timezone

import pandas as pd
import pytest

import engine.sweep as sweep_module
from engine.sweep import run_sweep


def _fake_df() -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=5, freq="D", tz="UTC")
    return pd.DataFrame(
        {"candle_time": idx, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
    )


def test_run_sweep_calls_backtest_and_saves_history_for_each_combo(monkeypatch, tmp_path):
    import engine.cache as cache_module

    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")

    fetch_calls = []
    monkeypatch.setattr(sweep_module, "get_candles", lambda market, timeframe, start, end: (
        fetch_calls.append((market, timeframe)), _fake_df()
    )[1])

    saved = []
    monkeypatch.setattr(
        sweep_module,
        "run_backtest_cached",
        lambda **kwargs: {"final_value": 11000.0, "sharpe": 1.0, "max_drawdown": 5.0, "run_id": "r1"},
    )
    monkeypatch.setattr(
        sweep_module,
        "save_sweep_result",
        lambda **kwargs: saved.append(kwargs),
    )

    class DummySignal:
        def setup(self, strategy): pass
        def should_buy(self, strategy): return False
        def should_sell(self, strategy): return False

    run_sweep(
        markets=["KRW-BTC", "KRW-ETH"],
        timeframes=["days"],
        signal_sets=[("dummy", [DummySignal()], False)],
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 5, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
    )

    assert len(fetch_calls) == 2  # 마켓 2개 x 봉타입 1개
    assert len(saved) == 2
    assert saved[0]["return_rate"] == pytest.approx(10.0)  # (11000-10000)/10000*100
    assert saved[0]["run_id"] == "r1"


def test_run_sweep_skips_failing_combo_and_continues(monkeypatch, tmp_path):
    import engine.cache as cache_module

    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    monkeypatch.setattr(sweep_module, "get_candles", lambda market, timeframe, start, end: _fake_df())

    call_count = {"n": 0}

    def failing_then_ok(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise ValueError("존재하지 않는 마켓")
        return {"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "run_id": "r2"}

    monkeypatch.setattr(sweep_module, "run_backtest_cached", failing_then_ok)

    saved = []
    monkeypatch.setattr(sweep_module, "save_sweep_result", lambda **kwargs: saved.append(kwargs))

    class DummySignal:
        def setup(self, strategy): pass
        def should_buy(self, strategy): return False
        def should_sell(self, strategy): return False

    run_sweep(
        markets=["KRW-FAKE", "KRW-ETH"],
        timeframes=["days"],
        signal_sets=[("dummy", [DummySignal()], False)],
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 5, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
    )

    assert call_count["n"] == 2  # 첫 번째 실패해도 두 번째는 계속 실행됨
    assert len(saved) == 1  # 실패한 조합은 저장되지 않음
