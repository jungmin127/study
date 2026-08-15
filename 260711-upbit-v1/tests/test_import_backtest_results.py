import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

import engine.cache as cache_module
from engine.cache import save_result
from scripts.import_backtest_results import main, merge_databases

REPO_ROOT = Path(__file__).resolve().parent.parent


def _seed(monkeypatch, db_path, run_id: str, final_value: float, created_at: str | None = None) -> None:
    """cache_module.DB_PATH를 db_path로 잠깐 바꿔 save_result()로 한 건을 저장한다.
    monkeypatch를 통해서만 바꿔야 각 테스트 종료 시 원래 값으로 정확히 복원된다.
    created_at을 지정하면 save_result()가 넣은 datetime('now') 값을 덮어써서,
    실행 속도에 좌우되지 않고 신구 관계를 결정적으로 테스트할 수 있다."""
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
    if created_at is not None:
        conn = cache_module._connect()
        conn.execute("UPDATE backtest_runs SET created_at = ? WHERE id = ?", (created_at, run_id))
        conn.commit()
        conn.close()


def test_merge_databases_inserts_new_runs_and_results(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0)
    _seed(monkeypatch, incoming_db, "run-b", 12000.0)

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts == {
        "runs_inserted": 1, "runs_replaced": 0, "runs_skipped": 0,
        "results_inserted": 1, "results_replaced": 0, "results_skipped": 0,
    }

    from engine.cache import load_result
    assert load_result("run-a")["final_value"] == 11000.0
    assert load_result("run-b")["final_value"] == 12000.0


def test_merge_databases_skips_when_incoming_is_not_newer(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    _seed(monkeypatch, server_db, "run-a", 11000.0, created_at="2026-01-05 00:00:00")
    # 같은 run_id, 다른 값이지만 incoming 쪽이 서버보다 최신이 아님(동일 시각) — 서버 값이 우선해야 함
    _seed(monkeypatch, incoming_db, "run-a", 99999.0, created_at="2026-01-05 00:00:00")

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts == {
        "runs_inserted": 0, "runs_replaced": 0, "runs_skipped": 1,
        "results_inserted": 0, "results_replaced": 0, "results_skipped": 1,
    }

    from engine.cache import load_result
    assert load_result("run-a")["final_value"] == 11000.0  # 서버 쪽 값 그대로


def test_merge_databases_replaces_when_incoming_is_newer(tmp_path, monkeypatch):
    server_db = tmp_path / "server.db"
    incoming_db = tmp_path / "incoming.db"

    # 같은 run_id를 로컬에서 "최신 데이터로 갱신"한 뒤 다시 push하는 상황을 재현한다:
    # 내용은 다르지만 run_id는 같고, incoming 쪽 created_at이 서버보다 미래다.
    _seed(monkeypatch, server_db, "run-a", 11000.0, created_at="2026-01-05 00:00:00")
    _seed(monkeypatch, incoming_db, "run-a", 99999.0, created_at="2026-01-06 00:00:00")

    monkeypatch.setattr(cache_module, "DB_PATH", server_db)
    counts = merge_databases(incoming_db)

    assert counts == {
        "runs_inserted": 0, "runs_replaced": 1, "runs_skipped": 0,
        "results_inserted": 0, "results_replaced": 1, "results_skipped": 0,
    }

    from engine.cache import load_result
    assert load_result("run-a")["final_value"] == 99999.0  # 더 최신인 incoming 값으로 교체됨


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
    assert counts["runs_replaced"] == 0
    assert counts["results_inserted"] == 0  # 짝이 없으니 결과는 삽입될 게 없음
    assert counts["results_replaced"] == 0


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


def test_script_runs_as_real_subprocess_entry_point():
    """실제 운영 환경처럼 `python scripts/import_backtest_results.py <path>`를 서브프로세스로
    직접 실행해, engine.cache import가 실패하지 않는지 검증한다. pytest는 pytest.ini의
    pythonpath=.로 이 문제를 가려서, main()을 인프로세스로 호출하는 테스트로는 잡을 수 없었다
    (실제로 이 테스트가 없어서 PYTHONPATH 누락 버그가 4차례의 태스크 리뷰를 통과했다)."""
    result = subprocess.run(
        [sys.executable, "scripts/import_backtest_results.py", "/nonexistent/nope.db"],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "입력 파일이 없습니다" in (result.stdout + result.stderr)
    assert "ModuleNotFoundError" not in (result.stdout + result.stderr)


def test_push_script_sets_pythonpath_for_remote_invocation():
    """push_backtest_results.sh가 서버에서 실행하는 ssh 명령에 PYTHONPATH=.가 빠지면
    engine.cache import가 실패한다(위 서브프로세스 테스트가 검증하는 바로 그 문제) —
    이 테스트는 그 방지책이 실제로 스크립트 안에 남아있는지 직접 확인한다."""
    script = (REPO_ROOT / "scripts" / "push_backtest_results.sh").read_text(encoding="utf-8")
    assert "PYTHONPATH=." in script
