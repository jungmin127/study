import pandas as pd
import pytest

from engine.metrics import calculate_metrics


def _df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D", tz="UTC")
    return pd.DataFrame({
        "candle_time": idx,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1.0] * len(closes),
    })


def test_total_return_and_cagr():
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00", "value": 10000.0},
        {"timestamp": "2026-07-01T00:00:00", "value": 11000.0},
    ]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 110]), "days")
    assert result["total_return"] == 10.0
    assert result["cagr"] > 0


def test_mdd_is_max_drawdown_from_peak():
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00", "value": 10000.0},
        {"timestamp": "2026-01-02T00:00:00", "value": 12000.0},
        {"timestamp": "2026-01-03T00:00:00", "value": 9000.0},
    ]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 100, 100]), "days")
    assert result["mdd"] == pytest.approx(-25.0)  # (9000-12000)/12000*100


def test_win_rate_and_profit_factor_from_trades():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [
        {"pnl": 100.0, "holdingPeriod": 2},
        {"pnl": -50.0, "holdingPeriod": 3},
        {"pnl": 200.0, "holdingPeriod": 1},
    ]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "days")
    assert result["total_trades"] == 3
    assert result["win_rate"] == pytest.approx(200 / 3)
    assert result["profit_factor"] == pytest.approx(300 / 50)


def test_max_consecutive_loss():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [{"pnl": p, "holdingPeriod": 1} for p in [10, -5, -3, -1, 8, -2]]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "days")
    assert result["max_consecutive_loss"] == 3


def test_buy_and_hold_return_from_df():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 150]), "days")
    assert result["buy_and_hold_return"] == pytest.approx(50.0)


def test_empty_equity_curve_returns_zeroed_metrics():
    result = calculate_metrics([], [], 10000.0, _df([100]), "days")
    assert result["total_trades"] == 0
    assert result["total_return"] == 0.0
    assert result["max_consecutive_loss"] == 0
