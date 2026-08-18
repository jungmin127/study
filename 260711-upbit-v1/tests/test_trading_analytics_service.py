import pytest
import pandas as pd

import backend.trading_analytics_service as svc
import engine.cache as cache_module
import trading.db as db_module
from tests.trading_db_fixtures import insert_live_strategy


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "trading.db")
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "results.db")
    return db_module


def _approve(db, strategy_id, capital=100_000.0):
    db.approve_live_strategy(strategy_id, capital)


def test_journal_summary_empty_when_no_approved_strategies(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    insert_live_strategy(db, status="draft")

    summary = svc.get_journal_summary()

    assert summary["strategies"] == []
    assert summary["equity_curve"] == []
    assert summary["cumulative_pnl"] == 0.0
    assert summary["mdd_pct"] == 0.0
    assert summary["win_rate_pct"] == 0.0


def test_journal_summary_excludes_unapproved_draft(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft")
    _approve(db, strategy_id)
    insert_live_strategy(db, status="draft")  # 승인 안 된 채로 남음

    summary = svc.get_journal_summary()

    assert len(summary["strategies"]) == 1
    assert summary["strategies"][0]["id"] == strategy_id


def test_journal_summary_aggregates_across_strategies(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, status="draft")
    _approve(db, s1, 100_000.0)
    s2 = insert_live_strategy(db, status="draft")
    _approve(db, s2, 200_000.0)

    db.upsert_daily_performance(s1, "2026-08-10", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0)
    db.upsert_daily_performance(s2, "2026-08-10", -2000.0, -1.0, 1, 0, 1, 200_000.0, 198_000.0)
    p1 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.002)
    db.close_position_row(p1, 50_500_000.0, 0.002, 1000.0, 1.0, "take_profit")
    p2 = db.insert_position(s2, "KRW-ETH", 3_000_000.0, 0.06)
    db.close_position_row(p2, 2_940_000.0, 0.06, -2000.0, -1.0, "stop_loss")

    summary = svc.get_journal_summary()

    assert summary["cumulative_pnl"] == -1000.0  # 1000 - 2000
    assert summary["cumulative_pnl_pct"] == pytest.approx(-1000.0 / 300000.0 * 100.0, abs=0.0001)
    assert summary["win_rate_pct"] == 50.0  # 1승 1패
    assert len(summary["equity_curve"]) == 1
    assert summary["equity_curve"][0]["value"] == 300_000.0 - 1000.0  # 원금합 - 순손실


def test_journal_summary_known_limitation_resolved_after_strategy_stops(monkeypatch, tmp_path):
    """stopped된 전략의 과거 손익이 계좌 합산 누적에서 사라지지 않아야 한다(스펙의
    '알려진 한계'를 flow 기반 집계로 해소했는지 확인하는 회귀 테스트)."""
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, status="draft")
    _approve(db, s1, 100_000.0)
    p1 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.002)
    db.close_position_row(p1, 50_500_000.0, 0.002, 1000.0, 1.0, "take_profit")
    db.upsert_daily_performance(s1, "2026-08-10", 1000.0, 1.0, 1, 1, 0, 100_000.0, 101_000.0)
    db.stop_live_strategy_if_no_open_position(s1)

    s2 = insert_live_strategy(db, status="draft")
    _approve(db, s2, 50_000.0)
    # s2는 다음날부터 활동 시작 — s1은 이미 stopped라 이후 daily_performance 행이 없음

    summary = svc.get_journal_summary()

    last_point = summary["equity_curve"][-1]
    assert last_point["value"] == 150_000.0 + 1000.0  # s1의 과거 이익이 그대로 남아있어야 함


def test_market_journal_returns_none_for_missing_market(monkeypatch, tmp_path):
    _fresh(monkeypatch, tmp_path)
    assert svc.get_market_journal("KRW-DOGE") is None


def test_market_journal_returns_none_when_only_unapproved_draft(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    insert_live_strategy(db, status="draft", market="KRW-DOGE")
    assert svc.get_market_journal("KRW-DOGE") is None


def test_market_journal_reflects_twr_after_capital_adjustment(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, market="KRW-BTC", status="draft")
    _approve(db, s1, 500_000.0)

    p1 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.01)
    db.close_position_row(p1, 55_000_000.0, 0.01, 50_000.0, 10.0, "take_profit")
    db.insert_capital_adjustment(s1, 550_000.0, 1_050_000.0)
    p2 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.021)
    db.close_position_row(p2, 47_500_000.0, 0.021, -52_500.0, -5.0, "stop_loss")

    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? ORDER BY rowid ASC", (s1,),
        ).fetchall()
        conn.execute("UPDATE positions SET exit_time = '2026-08-01 10:00:00' WHERE id = ?", (rows[0][0],))
        conn.execute("UPDATE positions SET exit_time = '2026-08-03 10:00:00' WHERE id = ?", (rows[1][0],))
        conn.execute(
            "UPDATE capital_adjustments SET adjusted_at = '2026-08-02 09:00:00' WHERE live_strategy_id = ?",
            (s1,),
        )
        conn.commit()
    finally:
        conn.close()

    journal = svc.get_market_journal("KRW-BTC")

    # TWR: (1.10 * 0.95) - 1 = 4.5%. 단순 계산(순손실 -2500 / 원금 500000 = -0.5%)과는 다르다.
    assert journal["cumulative_pnl_pct"] == pytest.approx(4.5, abs=0.01)


def test_journal_summary_reflects_twr_when_strategy_has_capital_adjustment(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, market="KRW-BTC", status="draft")
    _approve(db, s1, 500_000.0)

    p1 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.01)
    db.close_position_row(p1, 55_000_000.0, 0.01, 50_000.0, 10.0, "take_profit")
    db.insert_capital_adjustment(s1, 550_000.0, 1_050_000.0)
    p2 = db.insert_position(s1, "KRW-BTC", 50_000_000.0, 0.021)
    db.close_position_row(p2, 47_500_000.0, 0.021, -52_500.0, -5.0, "stop_loss")

    conn = db._connect()
    try:
        rows = conn.execute(
            "SELECT id FROM positions WHERE live_strategy_id = ? ORDER BY rowid ASC", (s1,),
        ).fetchall()
        conn.execute("UPDATE positions SET exit_time = '2026-08-01 10:00:00' WHERE id = ?", (rows[0][0],))
        conn.execute("UPDATE positions SET exit_time = '2026-08-03 10:00:00' WHERE id = ?", (rows[1][0],))
        conn.execute(
            "UPDATE capital_adjustments SET adjusted_at = '2026-08-02 09:00:00' WHERE live_strategy_id = ?",
            (s1,),
        )
        conn.commit()
    finally:
        conn.close()

    summary = svc.get_journal_summary()

    assert summary["cumulative_pnl_pct"] == pytest.approx(4.5, abs=0.01)
    assert summary["strategies"][0]["cumulative_pnl_pct"] == pytest.approx(4.5, abs=0.01)


def test_market_journal_includes_trade_log_and_metrics(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(db, status="draft", market="KRW-DOGE")
    _approve(db, strategy_id, 100_000.0)
    position_id = db.insert_position(strategy_id, "KRW-DOGE", 300.0, 300.0)
    db.close_position_row(position_id, 303.51, 300.0, 1053.0, 1.17, "sell_signal")
    db.upsert_daily_performance(
        strategy_id, "2026-08-14", 1053.0, 1.17, 1, 1, 0, 100_000.0, 101_053.0,
    )
    order_id = db.insert_order(
        strategy_id, position_id, "KRW-DOGE", "bid", "market", None, 300.0, 300.0,
    )
    db.update_order_filled(order_id, "uuid-1", 300.06, 300.0, 30.0, 0.02, "done")

    detail = svc.get_market_journal("KRW-DOGE")

    assert detail["market"] == "KRW-DOGE"
    assert detail["timeframes"] == ["minutes60"]
    assert detail["trade_count"] == 1
    assert detail["win_rate_pct"] == 100.0
    assert detail["avg_slippage_pct"] == 0.02
    assert detail["max_slippage_pct"] == 0.02
    assert len(detail["trade_log"]) == 1
    assert detail["trade_log"][0]["close_reason"] == "sell_signal"
    assert detail["backtest_comparison"] is None  # source_run_id 없음
    assert detail["daily"] == [
        {"trading_date": "2026-08-14", "pnl": 1053.0, "pnl_pct": 1.053, "cumulative": 101_053.0},
    ]


def test_market_journal_merges_stopped_and_restarted_strategies_for_same_market(monkeypatch, tmp_path):
    """사용자 결정 — 같은 코인은 타임프레임이 달라도, 중지 후 재시작한 세대여도 전부
    하나로 합친다(전략 단위 상세 화면은 없앰)."""
    db = _fresh(monkeypatch, tmp_path)
    s1 = insert_live_strategy(db, status="draft", market="KRW-DOGE", timeframe="minutes60")
    _approve(db, s1, 100_000.0)
    p1 = db.insert_position(s1, "KRW-DOGE", 300.0, 300.0)
    db.close_position_row(p1, 303.51, 300.0, 1053.0, 1.17, "sell_signal")
    db.upsert_daily_performance(s1, "2026-08-10", 1053.0, 1.17, 1, 1, 0, 100_000.0, 101_053.0)
    db.stop_live_strategy_if_no_open_position(s1)

    s2 = insert_live_strategy(db, status="draft", market="KRW-DOGE", timeframe="minutes240")
    _approve(db, s2, 50_000.0)
    p2 = db.insert_position(s2, "KRW-DOGE", 310.0, 160.0)
    db.close_position_row(p2, 300.0, 160.0, -1600.0, -3.23, "stop_loss")
    db.upsert_daily_performance(s2, "2026-08-11", -1600.0, -3.2, 1, 0, 1, 50_000.0, 48_400.0)

    detail = svc.get_market_journal("KRW-DOGE")

    assert detail["timeframes"] == ["minutes240", "minutes60"]
    assert detail["trade_count"] == 2
    assert detail["win_rate_pct"] == 50.0
    assert [d["trading_date"] for d in detail["daily"]] == ["2026-08-10", "2026-08-11"]
    # 8/10: 원금합(150,000) 기준 +1053 = +0.702%, 8/11: 누적(151,053) 기준 -1600 = 약 -1.0592%
    assert detail["daily"][0]["pnl_pct"] == 0.702
    assert round(detail["daily"][1]["pnl_pct"], 4) == -1.0592


def test_market_journal_backtest_comparison_present_with_source_run(monkeypatch, tmp_path):
    db = _fresh(monkeypatch, tmp_path)
    from engine.cache import save_result
    from datetime import datetime, timezone

    save_result(
        run_id="run-1", strategy_name="ConditionTreeStrategy", strategy_params={},
        market="KRW-DOGE", timeframe="minutes60",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc), end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        risk_config={"initial_capital": 100_000},
        result={
            "final_value": 108_000.0, "sharpe": 1.0, "max_drawdown": 3.2,
            "equity_curve": [
                {"timestamp": "2026-01-01T00:00:00", "value": 100_000.0},
                {"timestamp": "2026-02-01T00:00:00", "value": 108_000.0},
            ],
            "trades": [
                {
                    "entryTime": "2026-01-05T00:00:00", "exitTime": "2026-01-06T00:00:00",
                    "entryPrice": 300.0, "exitPrice": 305.0, "returnRate": 1.6,
                    "holdingPeriod": 24, "pnl": 1600.0,
                },
                {
                    "entryTime": "2026-01-10T00:00:00", "exitTime": "2026-01-11T00:00:00",
                    "entryPrice": 305.0, "exitPrice": 300.0, "returnRate": -1.5,
                    "holdingPeriod": 24, "pnl": -1500.0,
                },
            ],
        },
    )
    strategy_id = insert_live_strategy(
        db, status="draft", market="KRW-DOGE", source_run_id="run-1",
    )
    _approve(db, strategy_id, 100_000.0)
    position_id = db.insert_position(strategy_id, "KRW-DOGE", 300.0, 300.0)
    db.close_position_row(position_id, 303.51, 300.0, 1053.0, 1.17, "sell_signal")

    detail = svc.get_market_journal("KRW-DOGE")

    comparison = detail["backtest_comparison"]
    assert comparison is not None
    assert comparison["backtest"]["trade_count"] == 2
    assert comparison["backtest"]["win_rate_pct"] == 50.0
    assert comparison["live"]["trade_count"] == 1
    assert comparison["sample_size_warning"] is True  # 1건 < MIN_SAMPLE_SIZE


def test_twr_pct_matches_simple_calc_when_no_adjustments():
    closed = [
        {"realized_pnl": 50_000.0, "exit_time": "2026-08-01 10:00:00"},
        {"realized_pnl": -20_000.0, "exit_time": "2026-08-02 10:00:00"},
    ]

    result = svc._twr_pct(closed, 500_000.0, [])

    assert result == pytest.approx((30_000.0 / 500_000.0) * 100.0)


def test_twr_pct_returns_zero_when_baseline_is_zero_and_no_adjustments():
    result = svc._twr_pct([], 0.0, [])
    assert result == 0.0


def test_twr_pct_chains_segment_returns_around_single_capital_increase():
    """50만 원 시작 -> +10%(55만) -> 50만 증액(105만) -> -5%(99.75만).
    TWR = (1.10 * 0.95) - 1 = +4.5%. 단순 계산(순손실 -2500 / 50만 = -0.5%)과는 다르다."""
    closed = [
        {"realized_pnl": 50_000.0, "exit_time": "2026-08-01 10:00:00"},
        {"realized_pnl": -52_500.0, "exit_time": "2026-08-03 10:00:00"},
    ]
    adjustments = [
        {"adjusted_at": "2026-08-02 09:00:00", "new_capital": 1_050_000.0},
    ]

    result = svc._twr_pct(closed, 500_000.0, adjustments)

    assert result == pytest.approx(4.5, abs=0.01)


def test_twr_pct_chains_multiple_adjustments_with_trades_in_each_segment():
    closed = [
        {"realized_pnl": 10_000.0, "exit_time": "2026-08-01 10:00:00"},   # 구간1: 100000 -> +10%
        {"realized_pnl": -6_000.0, "exit_time": "2026-08-05 10:00:00"},   # 구간2: 200000 -> -3%
        {"realized_pnl": 9_700.0, "exit_time": "2026-08-10 10:00:00"},    # 구간3: 194000 -> +5%
    ]
    adjustments = [
        {"adjusted_at": "2026-08-02 09:00:00", "new_capital": 200_000.0},
        {"adjusted_at": "2026-08-06 09:00:00", "new_capital": 194_000.0},
    ]

    result = svc._twr_pct(closed, 100_000.0, adjustments)

    expected = ((1.10) * (1 - 0.03) * (1.05) - 1) * 100.0
    assert result == pytest.approx(expected, abs=0.01)


def test_twr_pct_ignores_input_order_and_sorts_by_exit_time():
    """closed_positions가 exit_time 역순으로 들어와도 결과는 같아야 한다
    (list_closed_positions는 entry_time DESC로 반환하므로 이 정렬이 함수 내부 책임)."""
    closed_forward = [
        {"realized_pnl": 50_000.0, "exit_time": "2026-08-01 10:00:00"},
        {"realized_pnl": -52_500.0, "exit_time": "2026-08-03 10:00:00"},
    ]
    closed_reversed = list(reversed(closed_forward))
    adjustments = [{"adjusted_at": "2026-08-02 09:00:00", "new_capital": 1_050_000.0}]

    assert svc._twr_pct(closed_forward, 500_000.0, adjustments) == pytest.approx(
        svc._twr_pct(closed_reversed, 500_000.0, adjustments)
    )


def test_zero_filled_last_30_days_fills_missing_dates_and_keeps_known_values():
    pnl_by_date = {"2026-08-10": 1500.0, "2026-08-05": -300.0}

    result = svc._zero_filled_last_30_days(pnl_by_date, today="2026-08-10")

    assert len(result) == 30
    assert result[-1] == {"date": "2026-08-10", "pnl": 1500.0}
    assert result[0]["date"] == "2026-07-12"  # 29일 전
    by_date = {d["date"]: d["pnl"] for d in result}
    assert by_date["2026-08-05"] == -300.0
    assert by_date["2026-08-01"] == 0.0  # 거래 없는 날은 0
