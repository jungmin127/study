"""
tests/test_regime_autoswap.py

trading.regime_autoswap의 determine_target_regime()과 process_autoswap_tick()을
검증한다. compute_adx_di/classify_regime/get_candles는 전부 monkeypatch로
대체해 실제 캔들 데이터 없이 판정 로직만 단위 검증한다.
"""
from __future__ import annotations

from unittest.mock import Mock

import pandas as pd

import trading.db as db
import trading.regime_autoswap as regime_autoswap
from tests.trading_db_fixtures import insert_live_strategy


def _fresh_db(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "trading.db")
    return db


def _fake_raw_df(n: int) -> pd.DataFrame:
    return pd.DataFrame({"close": [1.0] * n})


def _fake_adx_df(n: int) -> pd.DataFrame:
    return pd.DataFrame({"adx": [30.0] * n, "plus_di": [25.0] * n, "minus_di": [10.0] * n})


def test_determine_target_regime_confirms_when_last_three_bars_agree(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(5))
    monkeypatch.setattr(regime_autoswap, "compute_adx_di", lambda df: _fake_adx_df(5))
    monkeypatch.setattr(regime_autoswap, "classify_regime", Mock(side_effect=["상승", "상승", "상승"]))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "상승"


def test_determine_target_regime_falls_back_when_last_three_bars_disagree(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(5))
    monkeypatch.setattr(regime_autoswap, "compute_adx_di", lambda df: _fake_adx_df(5))
    monkeypatch.setattr(regime_autoswap, "classify_regime", Mock(side_effect=["상승", "상승", "하락"]))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "기본"


def test_determine_target_regime_falls_back_when_unclassified(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(5))
    monkeypatch.setattr(regime_autoswap, "compute_adx_di", lambda df: _fake_adx_df(5))
    monkeypatch.setattr(regime_autoswap, "classify_regime", Mock(side_effect=[None, None, None]))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "기본"


def test_determine_target_regime_falls_back_when_not_enough_bars(monkeypatch):
    monkeypatch.setattr(regime_autoswap, "get_candles", lambda *a, **k: _fake_raw_df(2))

    assert regime_autoswap.determine_target_regime("KRW-BTC") == "기본"


def test_process_autoswap_tick_skips_when_already_synced(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(strategy_id, True)
    dbm.set_active_regime(strategy_id, "상승")
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    assert dbm.get_live_strategy(strategy_id)["active_regime"] == "상승"
    assert dbm.list_regime_swap_log(strategy_id) == []


def test_process_autoswap_tick_logs_when_no_mapping(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(strategy_id, True)
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    assert dbm.get_live_strategy(strategy_id)["active_regime"] is None
    logs = dbm.list_regime_swap_log(strategy_id)
    assert len(logs) == 1
    assert logs[0]["event"] == "swap_skipped_no_mapping"
    assert logs[0]["to_regime"] == "상승"


def test_process_autoswap_tick_logs_and_waits_when_position_open(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(strategy_id, True)
    dbm.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-up", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    dbm.insert_position(strategy_id, "KRW-BTC", 50_000_000.0, 0.01)
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    assert dbm.get_live_strategy(strategy_id)["active_regime"] is None
    logs = dbm.list_regime_swap_log(strategy_id)
    assert len(logs) == 1
    assert logs[0]["event"] == "swap_skipped_open_position"


def test_process_autoswap_tick_swaps_and_updates_active_regime(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(
        dbm, status="running", market="KRW-BTC", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    dbm.set_auto_swap_enabled(strategy_id, True)
    dbm.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-up", timeframe="minutes30",
        buy_conditions_json='{"buy": true}', sell_conditions_json='{"sell": true}',
    )
    monkeypatch.setattr(regime_autoswap, "determine_target_regime", lambda market: "상승")

    regime_autoswap.process_autoswap_tick()

    strategy = dbm.get_live_strategy(strategy_id)
    assert strategy["active_regime"] == "상승"
    assert strategy["source_run_id"] == "run-up"
    assert strategy["timeframe"] == "minutes30"
    assert strategy["buy_conditions_json"] == '{"buy": true}'
    logs = dbm.list_regime_swap_log(strategy_id)
    assert len(logs) == 1
    assert logs[0]["event"] == "swap_success"
    assert logs[0]["to_regime"] == "상승"


def test_process_autoswap_tick_ignores_strategies_with_auto_swap_disabled(monkeypatch, tmp_path):
    dbm = _fresh_db(monkeypatch, tmp_path)
    strategy_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    # auto_swap_enabled 기본값 0(꺼짐) — 켜지 않음
    monkeypatch.setattr(
        regime_autoswap, "determine_target_regime",
        lambda market: (_ for _ in ()).throw(AssertionError("호출되면 안 됨")),
    )

    regime_autoswap.process_autoswap_tick()  # 예외 없이 조용히 넘어가야 함

    assert dbm.list_regime_swap_log(strategy_id) == []


def test_process_autoswap_tick_continues_after_one_strategy_raises(monkeypatch, tmp_path, caplog):
    dbm = _fresh_db(monkeypatch, tmp_path)
    broken_id = insert_live_strategy(dbm, status="running", market="KRW-BTC")
    dbm.set_auto_swap_enabled(broken_id, True)
    ok_id = insert_live_strategy(dbm, status="running", market="KRW-ETH")
    dbm.set_auto_swap_enabled(ok_id, True)
    dbm.set_active_regime(ok_id, "상승")

    def fake_determine(market):
        if market == "KRW-BTC":
            raise RuntimeError("캔들 조회 실패")
        return "상승"

    monkeypatch.setattr(regime_autoswap, "determine_target_regime", fake_determine)

    with caplog.at_level("ERROR"):
        regime_autoswap.process_autoswap_tick()  # 예외가 밖으로 새면 테스트 실패

    assert any(broken_id in r.message for r in caplog.records)  # 실패한 전략도 로그로 남음
    assert dbm.list_regime_swap_log(ok_id) == []  # 정상 전략은 이미 동기화 상태라 로그 없이 계속 처리됨
