"""
tests/test_export_regime_strategy_library.py

scripts.export_regime_strategy_library의 export_regime_strategy_library()와
main()을 검증한다.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import trading.db as trading_db
from scripts.export_regime_strategy_library import export_regime_strategy_library, main

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_export_copies_all_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    trading_db.upsert_regime_strategy_mapping(
        "KRW-ETH", "하락", source_run_id="run-2", timeframe="minutes30",
        buy_conditions_json='{"a":1}', sell_conditions_json='{"b":2}',
    )
    output_path = tmp_path / "export.db"

    count = export_regime_strategy_library(output_path)

    assert count == 2
    assert output_path.exists()
    import sqlite3
    conn = sqlite3.connect(output_path)
    conn.row_factory = sqlite3.Row
    rows = {(r["market"], r["regime"]): dict(r) for r in conn.execute("SELECT * FROM regime_strategy_library")}
    conn.close()
    assert rows[("KRW-BTC", "상승")]["source_run_id"] == "run-1"
    assert rows[("KRW-ETH", "하락")]["buy_conditions_json"] == '{"a":1}'


def test_export_returns_zero_for_empty_library(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    output_path = tmp_path / "export.db"

    count = export_regime_strategy_library(output_path)

    assert count == 0
    assert output_path.exists()


def test_export_overwrites_existing_output_file(tmp_path, monkeypatch):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    output_path = tmp_path / "export.db"
    output_path.write_text("stale content that is not a valid sqlite file")

    count = export_regime_strategy_library(output_path)

    assert count == 0  # 라이브러리가 비어있으므로 0건, 하지만 유효한 sqlite 파일로 덮어써짐
    import sqlite3
    conn = sqlite3.connect(output_path)
    conn.execute("SELECT * FROM regime_strategy_library")  # 예외 없이 통과해야 함(유효한 스키마)
    conn.close()


def test_main_prints_export_count(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(trading_db, "DB_PATH", tmp_path / "trading.db")
    trading_db.upsert_regime_strategy_mapping(
        "KRW-BTC", "상승", source_run_id="run-1", timeframe="minutes60",
        buy_conditions_json="{}", sell_conditions_json="{}",
    )
    output_path = tmp_path / "export.db"
    monkeypatch.setattr(sys, "argv", ["export_regime_strategy_library.py", str(output_path)])

    main()

    output = capsys.readouterr().out
    assert "1건" in output
    assert str(output_path) in output


def test_script_runs_as_real_subprocess_entry_point(tmp_path):
    """`python scripts/export_regime_strategy_library.py <path>`를 실제 서브프로세스로
    직접 실행해 trading.db import가 실패하지 않는지 검증한다(PYTHONPATH 누락 버그는
    pytest의 pythonpath=. 설정에 가려져 인프로세스 테스트로는 못 잡는다 —
    tests/test_import_backtest_results.py의 동일 이름 테스트와 같은 이유)."""
    output_path = tmp_path / "export.db"
    # 이 스크립트의 출력에 한글이 섞여 있어, Windows에서는 두 곳 모두 utf-8을
    # 명시해야 한다(직접 재현 확인): 자식 프로세스가 cp949가 아니라 utf-8로 쓰게
    # 하려면 env의 PYTHONIOENCODING이 필요하고, 부모 프로세스가 그 바이트를
    # 올바르게 읽으려면 subprocess.run() 자체의 encoding= 인자가 필요하다
    # (text=True만으로는 로케일 기본 인코딩(cp949)으로 디코딩을 시도해 실패한다 —
    # 이게 tests/test_import_backtest_results.py의 동일 이름 테스트가 이 환경에서
    # cp949 디코딩 오류로 알려진 flake가 되는 이유이기도 하다).
    result = subprocess.run(
        [sys.executable, "scripts/export_regime_strategy_library.py", str(output_path)],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": ".", "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ModuleNotFoundError" not in (result.stdout + result.stderr)
