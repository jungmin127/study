"""
scripts/export_regime_strategy_library.py

로컬 trading.db의 regime_strategy_library 테이블 행만 별도의 작은 sqlite
파일로 뽑아낸다. trading.db 파일 전체를 옮기지 않는 이유는
docs/superpowers/specs_v2/2026-09-06-regime-strategy-library-push-design.md
참고(로컬 trading.db에는 실거래와 무관한 개발용 live_strategies 등이 섞여있음).
"CREATE TABLE ... AS SELECT ... WHERE 0"로 export 쪽 테이블을 만들어서,
trading/db.py의 스키마가 나중에 바뀌어도 이 스크립트가 자동으로 따라간다.
Run: PYTHONPATH=. python scripts/export_regime_strategy_library.py data/_export_regime_strategy_library.db
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import trading.db as trading_db


def export_regime_strategy_library(output_path: Path) -> int:
    """regime_strategy_library 전체 행을 output_path의 새 sqlite 파일로 복사한다.
    output_path가 이미 있으면 지우고 새로 만든다. 반환값은 복사한 행 수."""
    if output_path.exists():
        output_path.unlink()

    conn = trading_db._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS export", (str(output_path),))
        try:
            conn.execute(
                "CREATE TABLE export.regime_strategy_library AS "
                "SELECT * FROM regime_strategy_library WHERE 0"
            )
            conn.execute(
                "INSERT INTO export.regime_strategy_library SELECT * FROM regime_strategy_library"
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM export.regime_strategy_library"
            ).fetchone()[0]
            conn.commit()
            return count
        finally:
            try:
                conn.execute("DETACH DATABASE export")
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="로컬 전략 라이브러리를 별도 sqlite 파일로 export")
    parser.add_argument("output_path", help="생성할 sqlite 파일 경로")
    args = parser.parse_args()

    count = export_regime_strategy_library(Path(args.output_path))
    print(f"전략 라이브러리 {count}건을 {args.output_path}에 export했습니다.")


if __name__ == "__main__":
    main()
