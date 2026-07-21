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


def test_sharpe_and_sortino_ratios_reflect_return_distribution():
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00", "value": 10000.0},
        {"timestamp": "2026-01-02T00:00:00", "value": 10100.0},
        {"timestamp": "2026-01-03T00:00:00", "value": 10200.0},
        {"timestamp": "2026-01-04T00:00:00", "value": 10300.0},
    ]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 100, 100, 100]), "days")
    # 등락폭 없이 계속 상승만 하므로 마이너스 수익률 구간이 없어 sortino는 0.0이어야 한다
    assert result["sortino_ratio"] == 0.0
    # 평균 수익률>0, 표준편차>0이므로 샤프비율은 양수여야 한다
    assert result["sharpe_ratio"] > 0


def test_calmar_ratio_zero_when_mdd_is_zero():
    equity_curve = [
        {"timestamp": "2026-01-01T00:00:00", "value": 10000.0},
        {"timestamp": "2026-01-02T00:00:00", "value": 10100.0},
        {"timestamp": "2026-01-03T00:00:00", "value": 10200.0},
    ]
    result = calculate_metrics(equity_curve, [], 10000.0, _df([100, 100, 100]), "days")
    assert result["mdd"] == 0.0
    assert result["calmar_ratio"] == 0.0


def test_profit_factor_sentinel_when_no_losses():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [{"pnl": 50.0, "holdingPeriod": 1}, {"pnl": 30.0, "holdingPeriod": 2}]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "days")
    assert result["profit_factor"] == 999.0


def test_avg_holding_period_converts_bars_to_days_for_intraday_timeframe():
    equity_curve = [{"timestamp": "2026-01-01T00:00:00", "value": 10000.0}]
    trades = [{"pnl": 1.0, "holdingPeriod": 4}, {"pnl": 1.0, "holdingPeriod": 8}]
    result = calculate_metrics(equity_curve, trades, 10000.0, _df([100]), "minutes15")
    # minutes15: 1봉=15분. 4봉=60분=1/24일, 8봉=120분=2/24일 → 평균 0.0625일 → 반올림해서 0.06
    assert result["avg_holding_period"] == pytest.approx(0.06)
