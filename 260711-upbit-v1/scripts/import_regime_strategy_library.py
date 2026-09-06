"""
scripts/import_regime_strategy_library.py

로컬에서 export한 전략 라이브러리 sqlite 파일(예:
data/_incoming_regime_strategy_library.db)을 서버의 trading.db에 완전
거울 동기화한다 — incoming에 있는 (market, regime)은 upsert하고,
서버에만 있고 incoming에는 없는 (market, regime)은 삭제한다. 로컬
/strategy-library 화면이 "진실의 원천"이 되도록 하기 위한 설계 결정
(docs/superpowers/specs_v2/2026-09-06-regime-strategy-library-push-design.md).
Run: PYTHONPATH=. .venv/bin/python scripts/import_regime_strategy_library.py data/_incoming_regime_strategy_library.db
"""
from __future__ import annotations

import argparse
from pathlib import Path

import trading.db as trading_db


def mirror_regime_strategy_library(incoming_path: Path) -> dict[str, int]:
    """incoming_path의 regime_strategy_library를 trading_db.DB_PATH의 같은 테이블로
    완전 거울 동기화한다. Returns {"upserted", "deleted"}."""
    conn = trading_db._connect()
    try:
        conn.execute("ATTACH DATABASE ? AS incoming", (str(incoming_path),))
        try:
            incoming_keys = set(
                conn.execute("SELECT market, regime FROM incoming.regime_strategy_library").fetchall()
            )
            existing_keys = set(
                conn.execute("SELECT market, regime FROM regime_strategy_library").fetchall()
            )

            to_delete = existing_keys - incoming_keys
            for market, regime in to_delete:
                conn.execute(
                    "DELETE FROM regime_strategy_library WHERE market = ? AND regime = ?",
                    (market, regime),
                )

            if incoming_keys:
                conn.execute(
                    "INSERT INTO regime_strategy_library "
                    "SELECT * FROM incoming.regime_strategy_library "
                    "WHERE true "
                    "ON CONFLICT(market, regime) DO UPDATE SET "
                    "source_run_id=excluded.source_run_id, timeframe=excluded.timeframe, "
                    "buy_conditions_json=excluded.buy_conditions_json, "
                    "sell_conditions_json=excluded.sell_conditions_json, updated_at=excluded.updated_at"
                )
            conn.commit()
            return {"upserted": len(incoming_keys), "deleted": len(to_delete)}
        finally:
            conn.execute("DETACH DATABASE incoming")
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="전략 라이브러리를 서버 DB에 완전 거울 동기화")
    parser.add_argument("incoming_path", help="병합할 sqlite 파일 경로")
    args = parser.parse_args()
    incoming_path = Path(args.incoming_path)

    if not incoming_path.exists():
        raise SystemExit(f"입력 파일이 없습니다: {incoming_path}")

    counts = mirror_regime_strategy_library(incoming_path)
    if counts["upserted"] == 0:
        print(
            "경고: 로컬 라이브러리가 비어 있어 서버의 모든 매핑을 삭제했습니다 "
            f"({counts['deleted']}건). 의도한 게 맞는지 확인하세요."
        )
    else:
        print(f"전략 라이브러리 동기화 완료: {counts['upserted']}건 반영, {counts['deleted']}건 삭제")

    incoming_path.unlink()
    print(f"임시 파일 삭제: {incoming_path}")


if __name__ == "__main__":
    main()
