from datetime import datetime, timezone

import pandas as pd

import engine.cache as cache_module
from engine.cache import load_result, save_result
from scripts import backfill_candle_count as bf


def _seed_run_without_candle_count(run_id="r1", market="KRW-BTC"):
    save_result(
        run_id=run_id, strategy_name="ConditionTreeStrategy", strategy_params={},
        market=market, timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={"final_value": 10500.0, "sharpe": None, "max_drawdown": None, "equity_curve": [], "trades": []},
    )


def _fake_candles(n: int) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({"candle_time": idx, "close": range(n)})


def test_backfill_apply_fills_candle_count(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    _seed_run_without_candle_count()
    monkeypatch.setattr(bf, "get_candles", lambda market, timeframe, start, end: _fake_candles(10))

    bf.run(apply=True)

    assert load_result("r1")["candle_count"] == 10


def test_backfill_dry_run_does_not_modify_db(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    _seed_run_without_candle_count()
    monkeypatch.setattr(bf, "get_candles", lambda market, timeframe, start, end: _fake_candles(10))

    bf.run(apply=False)

    assert load_result("r1")["candle_count"] is None


def test_backfill_skips_runs_that_already_have_candle_count(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    save_result(
        run_id="already-filled", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-BTC", timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": 10500.0, "sharpe": None, "max_drawdown": None,
            "equity_curve": [], "trades": [], "candle_count": 999,
        },
    )
    calls = {"n": 0}

    def _counting_get_candles(market, timeframe, start, end):
        calls["n"] += 1
        return _fake_candles(10)

    monkeypatch.setattr(bf, "get_candles", _counting_get_candles)

    bf.run(apply=True)

    assert calls["n"] == 0
    assert load_result("already-filled")["candle_count"] == 999


def test_backfill_continues_after_a_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    _seed_run_without_candle_count(run_id="ok", market="KRW-BTC")
    _seed_run_without_candle_count(run_id="bad", market="KRW-ETH")

    def _flaky_get_candles(market, timeframe, start, end):
        if market == "KRW-ETH":
            raise RuntimeError("네트워크 오류")
        return _fake_candles(5)

    monkeypatch.setattr(bf, "get_candles", _flaky_get_candles)

    bf.run(apply=True)

    assert load_result("ok")["candle_count"] == 5
    assert load_result("bad")["candle_count"] is None
