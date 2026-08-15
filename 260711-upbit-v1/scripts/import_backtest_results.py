"""
scripts/import_backtest_results.py

로컬에서 만든 백테스트 결과 DB(예: data/_incoming_backtest_results.db)를 서버의
data/backtest_results.db(engine.cache.DB_PATH)에 병합한다. run_id(compute_cache_key의
내용 기반 해시)가 이미 있으면 created_at을 비교해 incoming 쪽이 더 최신일 때만 교체하고,
그렇지 않으면 건너뛴다 — "최신 데이터로 갱신" 버튼으로 로컬 DB의 같은 run_id 내용이
바뀐 뒤 다시 push해도 그 갱신분이 서버에 반영된다. 같은 조건으로 로컬에서 grid search를
다시 돌려 여러 번 실행해도 안전하다.
Run: PYTHONPATH=. .venv/bin/python scripts/import_backtest_results.py data/_incoming_backtest_results.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import engine.cache as cache_module


def merge_databases(incoming_path: Path) -> dict[str, int]:
    """incoming_path의 backtest_runs/backtest_results를 engine.cache.DB_PATH로 병합한다.

    같은 run_id가 서버에 이미 있으면 backtest_runs.created_at을 비교해, incoming 쪽이
    더 최신일 때만 교체(REPLACE)하고 그렇지 않으면(동일 시각 포함) 건너뛴다.

    Returns:
        {"runs_inserted", "runs_replaced", "runs_skipped",
         "results_inserted", "results_replaced", "results_skipped"}
    """
    conn = cache_module._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS incoming", (str(incoming_path),))
        try:
            runs_inserted = runs_replaced = runs_skipped = 0
            results_inserted = results_replaced = results_skipped = 0

            incoming_runs = conn.execute(
                "SELECT id, created_at FROM incoming.backtest_runs"
            ).fetchall()

            for run_id, incoming_created_at in incoming_runs:
                existing = conn.execute(
                    "SELECT created_at FROM backtest_runs WHERE id = ?", (run_id,)
                ).fetchone()
                has_incoming_result = conn.execute(
                    "SELECT 1 FROM incoming.backtest_results WHERE run_id = ?", (run_id,)
                ).fetchone() is not None

                if existing is None:
                    conn.execute(
                        "INSERT INTO backtest_runs SELECT * FROM incoming.backtest_runs WHERE id = ?",
                        (run_id,),
                    )
                    runs_inserted += 1
                    if has_incoming_result:
                        conn.execute(
                            "INSERT INTO backtest_results SELECT * FROM incoming.backtest_results WHERE run_id = ?",
                            (run_id,),
                        )
                        results_inserted += 1
                elif incoming_created_at > existing[0]:
                    conn.execute(
                        "INSERT OR REPLACE INTO backtest_runs SELECT * FROM incoming.backtest_runs WHERE id = ?",
                        (run_id,),
                    )
                    runs_replaced += 1
                    if has_incoming_result:
                        conn.execute(
                            "INSERT OR REPLACE INTO backtest_results SELECT * FROM incoming.backtest_results WHERE run_id = ?",
                            (run_id,),
                        )
                        results_replaced += 1
                else:
                    runs_skipped += 1
                    if has_incoming_result:
                        results_skipped += 1

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
        "runs_replaced": runs_replaced,
        "runs_skipped": runs_skipped,
        "results_inserted": results_inserted,
        "results_replaced": results_replaced,
        "results_skipped": results_skipped,
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
        f"갱신 {counts['runs_replaced']}건 교체, "
        f"기존 {counts['runs_skipped']}건 건너뜀"
    )
    if (
        counts["results_inserted"] != counts["runs_inserted"]
        or counts["results_replaced"] != counts["runs_replaced"]
    ):
        print(
            f"경고: runs와 results의 추가/교체 건수가 다릅니다 "
            f"(runs_inserted={counts['runs_inserted']}, results_inserted={counts['results_inserted']}, "
            f"runs_replaced={counts['runs_replaced']}, results_replaced={counts['results_replaced']}) — "
            f"결과가 없는 run은 프론트엔드 목록에 안 보일 수 있습니다."
        )

    incoming_path.unlink()
    print(f"임시 파일 삭제: {incoming_path}")


if __name__ == "__main__":
    main()
