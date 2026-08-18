import pytest

import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy
from trading.position_manager import calculate_initial_capital, close_position, get_open_position, open_position


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def test_calculate_initial_capital_fixed_mode():
    risk_config = {"position_sizing_mode": "fixed", "position_sizing_value": 100_000}
    assert calculate_initial_capital(risk_config, available_balance=5_000_000.0) == 100_000.0


def test_calculate_initial_capital_percent_mode():
    risk_config = {"position_sizing_mode": "percent", "position_sizing_value": 10}
    assert calculate_initial_capital(risk_config, available_balance=1_000_000.0) == 100_000.0


def test_calculate_initial_capital_clamps_to_max_position_per_market():
    risk_config = {
        "position_sizing_mode": "percent", "position_sizing_value": 50,
        "max_position_per_market": 300_000,
    }
    assert calculate_initial_capital(risk_config, available_balance=1_000_000.0) == 300_000.0


def test_calculate_initial_capital_raises_on_unknown_mode():
    risk_config = {"position_sizing_mode": "unknown", "position_sizing_value": 1}
    try:
        calculate_initial_capital(risk_config, available_balance=1.0)
        assert False, "ValueError가 발생해야 함"
    except ValueError:
        pass


def test_open_position_and_get_open_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)

    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = get_open_position(strategy_id)
    assert result["id"] == position_id
    assert result["market"] == "KRW-BTC"


def test_get_open_position_returns_none_when_no_position(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    assert get_open_position(strategy_id) is None


def test_close_position_computes_pnl_and_updates_capital(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, current_capital=100_000_000.0 * 0.01)
    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = close_position(position_id, 101_000_000.0, 0.01, fee=500.0, close_reason="signal")

    expected_pnl = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 500.0
    expected_pct = expected_pnl / (100_000_000.0 * 0.01) * 100
    expected_capital_after = 101_000_000.0 * 0.01 - 500.0

    assert result["realized_pnl"] == pytest.approx(expected_pnl)
    assert result["realized_pnl_pct"] == pytest.approx(expected_pct)
    assert result["capital_after"] == pytest.approx(expected_capital_after)

    assert dbm.get_live_strategy(strategy_id)["current_capital"] == pytest.approx(expected_capital_after)
    assert get_open_position(strategy_id) is None


def test_close_position_raises_when_position_not_found(monkeypatch, tmp_path):
    _fresh_db(monkeypatch, tmp_path)
    with pytest.raises(ValueError):
        close_position("nonexistent-id", 1.0, 1.0, fee=0.0, close_reason="signal")


def test_close_position_handles_loss(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm)
    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01)

    result = close_position(position_id, 95_000_000.0, 0.01, fee=475.0, close_reason="signal")

    assert result["realized_pnl"] < 0
    assert result["realized_pnl_pct"] < 0


def test_close_position_subtracts_both_entry_and_exit_fee(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, current_capital=100_000_000.0 * 0.01)
    position_id = open_position(strategy_id, "KRW-BTC", 100_000_000.0, 0.01, entry_fee=500.0)

    result = close_position(position_id, 101_000_000.0, 0.01, fee=505.0, close_reason="signal")

    expected_pnl = (101_000_000.0 * 0.01) - (100_000_000.0 * 0.01) - 500.0 - 505.0
    expected_pct = expected_pnl / (100_000_000.0 * 0.01) * 100

    assert result["realized_pnl"] == pytest.approx(expected_pnl)
    assert result["realized_pnl_pct"] == pytest.approx(expected_pct)
    # capital_after는 매도 체결 실수령액 기준이라 entry_fee와 무관해야 함
    assert result["capital_after"] == pytest.approx(101_000_000.0 * 0.01 - 505.0)
