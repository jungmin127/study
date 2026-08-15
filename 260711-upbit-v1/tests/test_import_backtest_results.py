import sys
from datetime import datetime, timezone

import pytest

import engine.cache as cache_module
from engine.cache import save_result
from scripts.import_backtest_results import main, merge_databases


def _seed(monkeypatch, db_path, run_id: str, final_value: float) -> None:
    """cache_module.DB_PATH를 db_path로 잠깐 바꿔 save_result()로 한 건을 저장한다.
    monkeypatch를 통해서만 바꿔야 각 테스트 종료 시 원래 값으로 정확히 복원된다."""
    monkeypatch.setattr(cache_module, "DB_PATH", db_path)
    save_result(
        run_id=run_id,
        strategy_name="ConditionTreeStrategy",
        strategy_params={"buy_conditions": {}, "sell_conditions": {}},
        market="KRW-BTC",
        timeframe="days",
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 10, tzinfo=timezone.utc),
        risk_config={"initial_capital": 10000},
        result={
            "final_value": final_value, "sharpe": 1.0, "max_drawdown": 2.0,
            "equity_curve": [], "trades": [],
        },
    )


def test_merge_databases_inserts_new_runs_and_results(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0)
    _seed(monkeypatch, incoming_db, "run-b", 12000.0)

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts == {
        "runs_inserted": 1, "runs_skipped": 0,
        "results_inserted": 1, "results_skipped": 0,
    }

    from engine.cache import load_result
    assert load_result("run-a")["final_value"] == 11000.0
    assert load_result("run-b")["final_value"] == 12000.0


def test_merge_databases_skips_existing_run_id_without_overwriting(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0)
    _seed(monkeypatch, incoming_db, "run-a", 99999.0)  # 같은 run_id, 다른 값 — 서버 값이 우선해야 함

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts == {
        "runs_inserted": 0, "runs_skipped": 1,
        "results_inserted": 0, "results_skipped": 1,
    }

    from engine.cache import load_result
    assert load_result("run-a")["final_value"] == 11000.0  # 서버 쪽 값 그대로


def test_merge_databases_handles_run_without_matching_result(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0)

    # incoming.db에 backtest_runs 행만 있고 backtest_results가 없는 비정상 상태를
    # 직접 만든다(save_result()는 항상 둘 다 넣으므로 raw SQL로 흉내낸다).
    monkeypatch.setattr(cache_module, "DB_PATH", incoming_db)
    conn = cache_module._connect()
    conn.execute(
        "INSERT INTO backtest_runs "
        "(id, strategy_name, params_json, market, timeframe, start, end, "
        " risk_config_json, created_at) "
        "VALUES ('run-orphan', 'ConditionTreeStrategy', '{}', 'KRW-BTC', 'days', "
        "        '2026-01-01T00:00:00+00:00', '2026-01-10T00:00:00+00:00', '{}', "
        "        datetime('now'))"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts["runs_inserted"] == 1  # run-orphan
    assert counts["results_inserted"] == 0  # 짝이 없으니 결과는 삽입될 게 없음


def test_main_raises_when_incoming_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_module, "DB_PATH", tmp_path / "server.db")
    monkeypatch.setattr(sys, "argv", ["import_backtest_results.py", str(tmp_path / "nope.db")])

    with pytest.raises(SystemExit, match="입력 파일이 없습니다"):
        main()


def test_main_deletes_incoming_file_after_successful_merge(tmp_path, monkeypatch, capsys):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"
    _seed(monkeypatch, incoming_db, "run-c", 13000.0)

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    monkeypatch.setattr(sys, "argv", ["import_backtest_results.py", str(incoming_db)])

    main()

    assert not incoming_db.exists()
    output = capsys.readouterr().out
    assert "신규 1건 추가" in output
    assert "임시 파일 삭제" in output
