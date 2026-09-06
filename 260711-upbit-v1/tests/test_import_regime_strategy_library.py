"""
tests/test_import_regime_strategy_library.py

scripts.import_regime_strategy_library의 mirror_regime_strategy_library()와
main()을 검증한다. incoming 파일은 Task 1의 export_regime_strategy_library()로
만든다(실제 파이프라인과 동일한 파일 형식 보장).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import trading.db as trading_db
from scripts.export_regime_strategy_library import export_regime_strategy_library
from scripts.import_regime_strategy_library import main, mirror_regime_strategy_library

REPO_ROOT = Path(__file__).resolve().parent.parent


def _make_incoming(tmp_path: Path, name: str, mappings: list[tuple[str, str, str]]) -> Path:
    """mappings의 각 (market, regime, source_run_id)를 별도 "로컬" DB_PATH에 저장한 뒤
    export하여 incoming 파일을 만든다. 이 함수 호출 동안 trading_db.DB_PATH를 건드리고
    반드시 호출부가 그 뒤에 원하는 DB_PATH로 다시 monkeypatch해야 한다(픽스처 격리는
    monkeypatch가 테스트 종료 시 자동으로 원복하므로 호출 순서만 주의하면 된다)."""
    from pytest import MonkeyPatch
    mp = MonkeyPatch()
    try:
        source_db = tmp_path / f"{name}_source.db"
        mp.setattr(trading_db, "DB_PATH", source_db)
        for market, regime, source_run_id in mappings:
            trading_db.upsert_regime_strategy_mapping(
                market, regime, source_run_id=source_run_id, timeframe="minutes60",
                buy_conditions_json="{}", sell_conditions_json="{}",
            )
        incoming_path = tmp_path / f"{name}.db"
        export_regime_strategy_library(incoming_path)
        return incoming_path
    finally:
        mp.undo()


def test_mirror_inserts_new_rows_from_incoming(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "run-1")])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 1, "deleted": 0}
    rows = trading_db.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["source_run_id"] == "run-1"


def test_mirror_updates_existing_row_when_value_differs(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="old-run", timeframe="minutes30",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "new-run")])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 1, "deleted": 0}
    rows = trading_db.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["source_run_id"] == "new-run"


def test_mirror_deletes_rows_missing_from_incoming(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    trading_db.upsert_regime_strategy_mapping(
        "KRW-ETH", "하락", source_run_id="run-2", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "run-1")])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 1, "deleted": 1}
    rows = trading_db.list_regime_strategy_mappings()
    assert len(rows) == 1
    assert rows[0]["market"] == "KRW-BTC"


def test_mirror_deletes_all_rows_when_incoming_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [])

    counts = mirror_regime_strategy_library(incoming)

    assert counts == {"upserted": 0, "deleted": 1}
    assert trading_db.list_regime_strategy_mappings() == []


def test_main_raises_when_incoming_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    monkeypatch.setattr(sys, "argv", ["import_regime_strategy_library.py", str(tmp_path / "nope.db")])

    with pytest.raises(SystemExit, match="입력 파일이 없습니다"):
        main()


def test_main_deletes_incoming_file_after_successful_merge(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    incoming = _make_incoming(tmp_path, "incoming", [("KRW-BTC", "상승", "run-1")])
    monkeypatch.setattr(sys, "argv", ["import_regime_strategy_library.py", str(incoming)])

    main()

    assert not incoming.exists()
    output = capsys.readouterr().out
    assert "1건 반영" in output
    assert "임시 파일 삭제" in output


def test_main_warns_when_incoming_is_empty(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "server.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    incoming = _make_incoming(tmp_path, "incoming", [])
    monkeypatch.setattr(sys, "argv", ["import_regime_strategy_library.py", str(incoming)])

    main()

    output = capsys.readouterr().out
    assert "경고" in output
    assert "삭제했습니다" in output


def test_script_runs_as_real_subprocess_entry_point():
    """python scripts/import_regime_strategy_library.py <path>를 실제 서브프로세스로
    직접 실행해 trading.db import가 실패하지 않는지 검증한다(PYTHONPATH 누락 버그는
    pytest의 pythonpath=. 설정에 가려져 인프로세스 테스트로는 못 잡는다).

    이 스크립트의 출력에 한글이 섞여 있어, Windows에서는 두 곳 모두 utf-8을
    명시해야 한다: 자식 프로세스가 cp949가 아니라 utf-8로 쓰게 하려면 env의
    PYTHONIOENCODING이 필요하고, 부모 프로세스가 그 바이트를 올바르게 읽으려면
    subprocess.run() 자체의 encoding= 인자가 필요하다(text=True만으로는 로케일
    기본 인코딩(cp949)으로 디코딩을 시도해 실패한다)."""
    result = subprocess.run(
        [sys.executable, "scripts/import_regime_strategy_library.py", "/nonexistent/nope.db"],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode != 0
    assert "입력 파일이 없습니다" in (result.stdout + result.stderr)
    assert "ModuleNotFoundError" not in (result.stdout + result.stderr)


def test_push_script_sets_pythonpath_for_local_and_remote_invocation():
    """push_regime_strategy_library.sh가 로컬 export 실행과 서버 ssh 실행 양쪽에
    PYTHONPATH=.를 빠뜨리면 trading.db import가 실패한다(위 서브프로세스 테스트가
    검증하는 바로 그 문제) — 이 테스트는 그 방지책이 스크립트 안에 실제로
    남아있는지 직접 확인한다(push_backtest_results.sh의 동일 이름 테스트와 같은
    이유, tests/test_import_backtest_results.py 참고)."""
    script = (REPO_ROOT / "scripts" / "push_regime_strategy_library.sh").read_text(encoding="utf-8")
    assert script.count("PYTHONPATH=.") >= 2  # 로컬 export 1회 + 원격 ssh 1회


def test_push_script_references_both_helper_scripts():
    script = (REPO_ROOT / "scripts" / "push_regime_strategy_library.sh").read_text(encoding="utf-8")
    assert "export_regime_strategy_library.py" in script
    assert "import_regime_strategy_library.py" in script


def test_push_script_never_restarts_server_daemon():
    """이 푸시는 regime_strategy_library 단일 테이블만 건드리는 데이터 동기화이지,
    코드 배포가 아니다 — update.sh/systemctl을 호출하면 daemon이 재시작되어
    실시간 손절/익절 감시가 끊기므로(project convention), 이 스크립트에는 그런
    호출이 있으면 안 된다."""
    script = (REPO_ROOT / "scripts" / "push_regime_strategy_library.sh").read_text(encoding="utf-8")
    assert "update.sh" not in script
    assert "systemctl" not in script
