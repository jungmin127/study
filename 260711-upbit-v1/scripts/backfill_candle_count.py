"""
scripts/backfill_candle_count.py

1회성 마이그레이션: candle_count 컬럼(engine/cache.py의 backtest_results) 도입 이전에
저장된 백테스트 결과들에 실제 캔들 개수를 채워 넣는다. 신규 백테스트/Grid Search는
engine/runner.py의 run_backtest()가 candle_count를 계산해 자동으로 저장하므로,
이 스크립트는 마이그레이션 이전 데이터만 대상으로 한다.

실행 전 backtest_results.db를 자동 백업한다(--apply일 때만). 기본은 드라이런(무엇을
채울지만 출력)이고, --apply를 줘야 실제로 DB를 변경한다.

사용법:
    python scripts/backfill_candle_count.py            # 드라이런
    python scripts/backfill_candle_count.py --apply     # 실제 적용
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import engine.cache as cache
from upbit_data_service import get_candles


def _backup_db() -> Path:
    """raw shutil.copy2 대신 sqlite3 온라인 백업 API를 쓴다 — WAL 모드에서 아직
    체크포인트되지 않은 커밋 데이터가 -wal 사이드카에만 있어 누락될 수 있어서다
    (scripts/backfill_entry_fee.py와 동일한 이유)."""
    backup_path = cache.DB_PATH.with_name(
        f"{cache.DB_PATH.name}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    src = sqlite3.connect(cache.DB_PATH)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def run(apply: bool) -> None:
    if apply:
        backup_path = _backup_db()
        print(f"백업 완료: {backup_path}")

    targets = cache.list_runs_missing_candle_count()
    filled_count = 0
    failed_count = 0

    for entry in targets:
        try:
            start_dt = datetime.fromisoformat(entry["start"])
            end_dt = datetime.fromisoformat(entry["end"])
            df = get_candles(entry["market"], entry["timeframe"], start_dt, end_dt)
            candle_count = len(df)
        except Exception as e:
            failed_count += 1
            print(f"  실패: run_id={entry['run_id']} market={entry['market']} - {e}")
            continue

        print(
            f"  run_id={entry['run_id']} market={entry['market']} "
            f"timeframe={entry['timeframe']}: candle_count={candle_count}"
        )
        if apply:
            cache.set_candle_count(entry["run_id"], candle_count)
        filled_count += 1

    print(f"\n완료: {filled_count}건 채움, {failed_count}건 실패 (대상 총 {len(targets)}건).")
    if not apply:
        print("드라이런입니다. 실제로 적용하려면 --apply를 붙여 다시 실행하세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경한다(기본은 드라이런)")
    args = parser.parse_args()
    run(args.apply)
