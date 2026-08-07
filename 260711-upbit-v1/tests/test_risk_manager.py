from datetime import datetime, timedelta, timezone

import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy
from trading.risk_manager import record_trade_result, today_kst, check_circuit_breaker


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def test_today_kst_matches_manual_kst_calculation():
    kst = timezone(timedelta(hours=9))
    expected = datetime.now(kst).strftime("%Y-%m-%d")
    assert today_kst() == expected


def test_record_trade_result_creates_daily_performance_row_on_first_trade(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=5000.0, capital_after=105_000.0)

    row = dbm.get_daily_performance(strategy_id, today_kst())
    assert row["starting_balance"] == 100_000.0  # 105_000 - 5_000 역산
    assert row["ending_balance"] == 105_000.0
    assert row["realized_pnl"] == 5000.0
    assert row["realized_pnl_pct"] == 5.0
    assert row["trade_count"] == 1
    assert row["win_count"] == 1
    assert row["loss_count"] == 0


def test_record_trade_result_accumulates_on_second_trade_same_day(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=5000.0, capital_after=105_000.0)
    record_trade_result(strategy_id, realized_pnl=-2000.0, capital_after=103_000.0)

    row = dbm.get_daily_performance(strategy_id, today_kst())
    assert row["starting_balance"] == 100_000.0  # 첫 거래 값 유지
    assert row["ending_balance"] == 103_000.0
    assert row["realized_pnl"] == 3000.0  # 5000 + (-2000) 누적
    assert row["realized_pnl_pct"] == 3.0  # 3000 / 100_000 * 100
    assert row["trade_count"] == 2
    assert row["win_count"] == 1
    assert row["loss_count"] == 1


def test_record_trade_result_increments_consecutive_losses_on_loss(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=-1000.0, capital_after=99_000.0)
    record_trade_result(strategy_id, realized_pnl=-500.0, capital_after=98_500.0)

    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["consecutive_losses"] == 2


def test_record_trade_result_resets_consecutive_losses_on_win(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    record_trade_result(strategy_id, realized_pnl=-1000.0, capital_after=99_000.0)
    record_trade_result(strategy_id, realized_pnl=2000.0, capital_after=101_000.0)

    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["consecutive_losses"] == 0


def test_check_circuit_breaker_returns_false_when_within_limits(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    risk_config = {"daily_loss_limit_pct": -5.0, "consecutive_loss_limit": 3}

    record_trade_result(strategy_id, realized_pnl=1000.0, capital_after=101_000.0)

    assert check_circuit_breaker(strategy_id, risk_config) is False
    assert dbm.get_live_strategy(strategy_id)["status"] != "paused"


def test_check_circuit_breaker_trips_on_daily_loss_limit(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    risk_config = {"daily_loss_limit_pct": -5.0, "consecutive_loss_limit": 10}

    record_trade_result(strategy_id, realized_pnl=-6000.0, capital_after=94_000.0)

    assert check_circuit_breaker(strategy_id, risk_config) is True
    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["tripped"] == 1
    assert cb["tripped_reason"] == "daily_loss_limit"
    assert cb["tripped_at"] is not None
    assert dbm.get_live_strategy(strategy_id)["status"] == "paused"


def test_check_circuit_breaker_trips_on_consecutive_loss_limit(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running")
    risk_config = {"daily_loss_limit_pct": -50.0, "consecutive_loss_limit": 2}

    record_trade_result(strategy_id, realized_pnl=-100.0, capital_after=99_900.0)
    record_trade_result(strategy_id, realized_pnl=-100.0, capital_after=99_800.0)

    assert check_circuit_breaker(strategy_id, risk_config) is True
    cb = dbm.get_circuit_breaker_state(strategy_id)
    assert cb["tripped_reason"] == "consecutive_loss_limit"
    assert dbm.get_live_strategy(strategy_id)["status"] == "paused"


def test_check_circuit_breaker_returns_true_immediately_when_already_tripped(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="paused")
    risk_config = {"daily_loss_limit_pct": -5.0, "consecutive_loss_limit": 3}
    dbm.upsert_circuit_breaker_state(strategy_id, today_kst(), 0, 1, "daily_loss_limit", "2026-08-07T00:00:00+00:00")

    assert check_circuit_breaker(strategy_id, risk_config) is True


def test_check_circuit_breaker_ignores_missing_limits(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    risk_config: dict = {}  # 한도 미설정

    record_trade_result(strategy_id, realized_pnl=-999_999.0, capital_after=1.0)

    assert check_circuit_breaker(strategy_id, risk_config) is False
