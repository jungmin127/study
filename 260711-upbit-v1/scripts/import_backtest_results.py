"""
scripts/import_backtest_results.py

로컬에서 만든 백테스트 결과 DB(예: data/_incoming_backtest_results.db)를 서버의
data/backtest_results.db(engine.cache.DB_PATH)에 병합한다. run_id(compute_cache_key의
내용 기반 해시)가 이미 있으면 건너뛰므로, 같은 조건으로 로컬에서 grid search를 다시
돌려 여러 번 실행해도 안전하다.
Run: PYTHONPATH=. .venv/bin/python scripts/import_backtest_results.py data/_incoming_backtest_results.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import engine.cache as cache_module


def merge_databases(incoming_path: Path) -> dict[str, int]:
    """incoming_path의 backtest_runs/backtest_results를 engine.cache.DB_PATH로 병합한다.

    Returns:
        {"runs_inserted", "runs_skipped", "results_inserted", "results_skipped"}
    """
    conn = cache_module._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS incoming", (str(incoming_path),))
        try:
            before_runs = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            incoming_runs = conn.execute("SELECT COUNT(*) FROM incoming.backtest_runs").fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO backtest_runs SELECT * FROM incoming.backtest_runs")
            after_runs = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            runs_inserted = after_runs - before_runs

            before_results = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
            incoming_results = conn.execute("SELECT COUNT(*) FROM incoming.backtest_results").fetchone()[0]
            conn.execute("INSERT OR IGNORE INTO backtest_results SELECT * FROM incoming.backtest_results")
            after_results = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
            results_inserted = after_results - before_results

            conn.commit()
        finally:
            try:
                conn.execute("DETACH DATABASE incoming")
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()

    return {
        "runs_inserted": runs_inserted,
        "runs_skipped": incoming_runs - runs_inserted,
        "results_inserted": results_inserted,
        "results_skipped": incoming_results - results_inserted,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="로컬 백테스트 결과 DB를 서버 DB에 병합")
    parser.add_argument("incoming_path", help="병합할 sqlite 파일 경로")
    args = parser.parse_args()
    incoming_path = Path(args.incoming_path)

    if not incoming_path.exists():
        raise SystemExit(f"입력 파일이 없습니다: {incoming_path}")

    counts = merge_databases(incoming_path)
    print(
        f"백테스트 결과 병합 완료: 신규 {counts['runs_inserted']}건 추가, "
        f"기존 {counts['runs_skipped']}건 건너뜀"
    )
    if counts["results_inserted"] != counts["runs_inserted"]:
        print(
            f"경고: runs와 results 삽입 건수가 다릅니다 (runs_inserted={counts['runs_inserted']}, "
            f"results_inserted={counts['results_inserted']}) — 결과가 없는 run은 프론트엔드 "
            f"목록에 안 보일 수 있습니다."
        )

    incoming_path.unlink()
    print(f"임시 파일 삭제: {incoming_path}")


if __name__ == "__main__":
    main()
