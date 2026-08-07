import trading.db as db
from tests.trading_db_fixtures import insert_live_strategy
from trading.position_manager import calculate_initial_capital, get_open_position, open_position


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
