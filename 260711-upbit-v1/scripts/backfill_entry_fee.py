"""
scripts/backfill_entry_fee.py

1회성 마이그레이션: 매수 수수료(entry_fee) 미반영 버그 수정 이전에 생성된 포지션들을
소급 보정한다. 실행 전 trading.db를 자동 백업한다(--apply일 때만). 기본은 드라이런(무엇을
바꿀지만 출력)이고, --apply를 줘야 실제로 DB를 변경한다.

전략별로 side='bid' AND status='done' 주문을 created_at 순으로, 그 전략의 모든
포지션(열린 것 포함, 전략당 열린 포지션은 최대 1개)을 entry_time 순으로 정렬해 1:1로
매칭한다. 개수가 안 맞으면(주문 유실/재시도 등으로 매칭이 불확실하면) 그 전략은 건드리지
않고 건너뛴다.

청산된 포지션은 entry_fee를 채우고 realized_pnl/realized_pnl_pct를 재계산한다. 아직 열린
포지션은 entry_fee만 채운다(청산 전이라 손익 재계산 대상 아님) — 그래야 코드 수정 이전에
진입해 이후 청산되는 포지션도 정확한 entry_fee로 마감된다.

daily_performance는 영향받은 전략의 청산일별로 재계산한다(realized_pnl 합, win/loss
카운트). starting_balance/ending_balance는 실제 현금 흐름이라 entry_fee 보정과 무관하므로
그대로 둔다.

사용법:
    python scripts/backfill_entry_fee.py            # 드라이런
    python scripts/backfill_entry_fee.py --apply     # 실제 적용
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import trading.db as db


def _exit_kst_date(exit_time: str) -> str:
    utc_dt = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")
    return (utc_dt + timedelta(hours=9)).strftime("%Y-%m-%d")


def _match_positions_to_bid_orders(strategy_id: str) -> list[tuple[dict, dict]] | None:
    closed = sorted(db.list_closed_positions(strategy_id), key=lambda p: p["entry_time"])
    open_pos = db.get_open_position(strategy_id)
    positions = closed + ([open_pos] if open_pos else [])
    positions.sort(key=lambda p: p["entry_time"])

    bid_orders = sorted(
        (o for o in db.list_orders_for_strategy(strategy_id)
         if o["side"] == "bid" and o["status"] == "done"),
        key=lambda o: o["created_at"],
    )

    if len(bid_orders) != len(positions):
        return None
    return list(zip(positions, bid_orders))


def _recompute_daily_performance(strategy_id: str, apply: bool) -> None:
    closed = db.list_closed_positions(strategy_id)
    by_date: dict[str, list[dict]] = {}
    for p in closed:
        by_date.setdefault(_exit_kst_date(p["exit_time"]), []).append(p)

    for trading_date, positions in by_date.items():
        existing = db.get_daily_performance(strategy_id, trading_date)
        if existing is None:
            print(f"  경고: daily_performance 행 없음 strategy={strategy_id} date={trading_date} — 건너뜀")
            continue
        realized_pnl = sum(p["realized_pnl"] for p in positions)
        win_count = sum(1 for p in positions if p["realized_pnl"] >= 0)
        loss_count = len(positions) - win_count
        starting_balance = existing["starting_balance"]
        pct = (realized_pnl / starting_balance * 100.0) if starting_balance else 0.0
        print(
            f"  daily_performance {trading_date}: realized_pnl {existing['realized_pnl']:.2f} -> "
            f"{realized_pnl:.2f}"
        )
        if apply:
            db.upsert_daily_performance(
                strategy_id, trading_date, realized_pnl, pct,
                len(positions), win_count, loss_count, starting_balance, existing["ending_balance"],
            )


def run(apply: bool) -> None:
    if apply:
        backup_path = db.DB_PATH.with_name(
            f"{db.DB_PATH.name}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        )
        shutil.copy2(db.DB_PATH, backup_path)
        print(f"백업 완료: {backup_path}")

    strategies = db.list_live_strategies()
    matched_count = 0
    skipped_count = 0
    touched_strategy_ids: list[str] = []

    for strategy in strategies:
        pairs = _match_positions_to_bid_orders(strategy["id"])
        if pairs is None:
            skipped_count += 1
            print(f"건너뜀 (주문/포지션 개수 불일치): live_strategy_id={strategy['id']}")
            continue
        if not pairs:
            continue

        touched_strategy_ids.append(strategy["id"])
        for position, order in pairs:
            entry_fee = order["fee"] or 0.0
            if position["status"] == "closed":
                new_realized_pnl = position["realized_pnl"] - entry_fee
                denom = position["entry_price"] * position["entry_qty"]
                new_pct = (new_realized_pnl / denom * 100.0) if denom else 0.0
                print(
                    f"  포지션 {position['id']}: entry_fee={entry_fee:.2f}, "
                    f"realized_pnl {position['realized_pnl']:.2f} -> {new_realized_pnl:.2f}"
                )
                if apply:
                    db.update_position_entry_fee(position["id"], entry_fee)
                    db.update_position_realized_pnl(position["id"], new_realized_pnl, new_pct)
            else:
                print(f"  열린 포지션 {position['id']}: entry_fee만 {entry_fee:.2f}로 백필")
                if apply:
                    db.update_position_entry_fee(position["id"], entry_fee)
            matched_count += 1

    for strategy_id in touched_strategy_ids:
        print(f"daily_performance 재계산: live_strategy_id={strategy_id}")
        _recompute_daily_performance(strategy_id, apply)

    print(f"\n완료: 포지션 {matched_count}건 처리, 전략 {skipped_count}건 건너뜀.")
    if not apply:
        print("드라이런입니다. 실제로 적용하려면 --apply를 붙여 다시 실행하세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경한다(기본은 드라이런)")
    args = parser.parse_args()
    run(args.apply)
