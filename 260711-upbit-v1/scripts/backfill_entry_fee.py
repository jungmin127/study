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
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import trading.db as db


def _backup_db() -> Path:
    """raw shutil.copy2로는 WAL 모드에서 아직 체크포인트되지 않은(그러나 이미 커밋된)
    거래가 -wal 사이드카 파일에만 있어 백업에서 누락될 수 있고, 다른 프로세스가 쓰는
    중인 파일을 그대로 복사하면 깨진 스냅샷이 될 수도 있다. sqlite3의 온라인 백업 API를
    쓰면 journal 모드와 무관하게 항상 일관된 완전한 스냅샷을 얻는다."""
    backup_path = db.DB_PATH.with_name(
        f"{db.DB_PATH.name}.bak-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    src = sqlite3.connect(db.DB_PATH)
    try:
        dst = sqlite3.connect(backup_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return backup_path


def _exit_kst_date(exit_time: str) -> str:
    utc_dt = datetime.strptime(exit_time, "%Y-%m-%d %H:%M:%S")
    return (utc_dt + timedelta(hours=9)).strftime("%Y-%m-%d")


def _match_positions_to_bid_orders(strategy_id: str) -> list[tuple[dict, dict]] | None:
    closed = db.list_closed_positions(strategy_id)
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


def _recompute_daily_performance(
    strategy_id: str, apply: bool, corrected_pnl_by_position: dict[str, float],
) -> dict[str, float]:
    """청산일별 daily_performance를 재계산한다. 드라이런에서도 미리보기가 실제로
    --apply 했을 때 쓰일 값을 그대로 반영해야 하므로, DB에서 막 다시 읽은 (아직 갱신되지
    않았을 수 있는) position["realized_pnl"]을 신뢰하지 않고, run()에서 이번 실행 중
    계산한 corrected_pnl_by_position 맵을 우선 사용한다(맵에 없으면 — 이번 실행이 건드리지
    않은 포지션이라는 뜻이므로 — DB에 저장된 값으로 폴백). 반환값은
    {trading_date: 재계산된 realized_pnl}로, 테스트에서 미리보기 값을 직접 검증할 수 있게 한다.

    realized_pnl이 NULL인 청산 포지션(trading/reconciler.py의 _self_heal_unexplained()가
    가격 근거 없는 잔고 소멸을 닫을 때 만드는 상태, close_reason="manual_unexplained")은
    애초에 risk_manager.record_trade_result()를 거친 적이 없어 어느 daily_performance
    행의 trade_count에도 포함된 적이 없다 — 합계/승패 카운트 대상에서 통째로 제외한다.

    existing["trade_count"]와 이번에 실제로 찾은 거래 수가 다르면(가장 그럴듯한 원인:
    KST 자정 경계에서 record_trade_result()가 쓴 today_kst()와 여기서 exit_time으로
    역산한 날짜가 어긋난 경우) 어느 쪽이 맞는지 알 수 없으므로 조용히 덮어쓰지 않고
    경고만 남기고 그 날짜의 행은 건드리지 않는다."""
    closed = db.list_closed_positions(strategy_id)
    by_date: dict[str, list[tuple[dict, float]]] = {}
    for p in closed:
        if p["realized_pnl"] is None:
            continue
        effective_pnl = corrected_pnl_by_position.get(p["id"], p["realized_pnl"])
        by_date.setdefault(_exit_kst_date(p["exit_time"]), []).append((p, effective_pnl))

    preview: dict[str, float] = {}
    for trading_date, entries in by_date.items():
        existing = db.get_daily_performance(strategy_id, trading_date)
        if existing is None:
            print(f"  경고: daily_performance 행 없음 strategy={strategy_id} date={trading_date} — 건너뜀")
            continue
        if len(entries) != existing["trade_count"]:
            print(
                f"  경고: daily_performance {trading_date} 거래 수 불일치(기존 trade_count="
                f"{existing['trade_count']}, 재계산 결과 {len(entries)}건) — 덮어쓰지 않고 건너뜀"
            )
            continue
        realized_pnl = sum(pnl for _, pnl in entries)
        win_count = sum(1 for _, pnl in entries if pnl >= 0)
        loss_count = len(entries) - win_count
        starting_balance = existing["starting_balance"]
        pct = (realized_pnl / starting_balance * 100.0) if starting_balance else 0.0
        print(
            f"  daily_performance {trading_date}: realized_pnl {existing['realized_pnl']:.2f} -> "
            f"{realized_pnl:.2f}"
        )
        preview[trading_date] = realized_pnl
        if apply:
            db.upsert_daily_performance(
                strategy_id, trading_date, realized_pnl, pct,
                len(entries), win_count, loss_count, starting_balance, existing["ending_balance"],
            )
    return preview


def run(apply: bool) -> None:
    if apply:
        backup_path = _backup_db()
        print(f"백업 완료: {backup_path}")

    strategies = db.list_live_strategies()
    matched_count = 0
    skipped_count = 0
    touched_strategy_ids: list[str] = []
    corrected_pnl_by_position: dict[str, float] = {}

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
            if position["status"] == "closed" and position["realized_pnl"] is None:
                # 가격 근거 없이 닫힌 포지션(trading/reconciler.py의
                # _self_heal_unexplained(), close_reason="manual_unexplained") — realized_pnl이
                # NULL이라 보정할 기준선 자체가 없다. entry_fee만 채우고(향후 일관성/
                # 추적용) realized_pnl 재계산과 daily_performance 집계 대상에서는 제외한다.
                print(
                    f"  포지션 {position['id']}: 가격 근거 없음(manual_unexplained) — "
                    f"entry_fee만 채우고 realized_pnl 재계산 대상에서 제외"
                )
                if apply:
                    db.update_position_entry_fee(position["id"], entry_fee)
            elif position["status"] == "closed" and position["entry_fee"]:
                # entry_fee가 이미 채워져 있다 — 이전 실행에서 이미 백필됐거나, 버그
                # 수정 이후 코드로 진입/청산되어 close_position()이 이미 반영했다는 뜻.
                # 여기서 다시 차감하면 이중 차감이 되므로 realized_pnl은 그대로 둔다.
                print(
                    f"  포지션 {position['id']}: entry_fee가 이미 {position['entry_fee']:.2f}로 "
                    f"설정됨 — 이미 반영된 것으로 보고 건너뜀"
                )
                corrected_pnl_by_position[position["id"]] = position["realized_pnl"]
            elif position["status"] == "closed":
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
                corrected_pnl_by_position[position["id"]] = new_realized_pnl
            else:
                print(f"  열린 포지션 {position['id']}: entry_fee만 {entry_fee:.2f}로 백필")
                if apply:
                    db.update_position_entry_fee(position["id"], entry_fee)
            matched_count += 1

    for strategy_id in touched_strategy_ids:
        print(f"daily_performance 재계산: live_strategy_id={strategy_id}")
        _recompute_daily_performance(strategy_id, apply, corrected_pnl_by_position)

    print(f"\n완료: 포지션 {matched_count}건 처리, 전략 {skipped_count}건 건너뜀.")
    if not apply:
        print("드라이런입니다. 실제로 적용하려면 --apply를 붙여 다시 실행하세요.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경한다(기본은 드라이런)")
    args = parser.parse_args()
    run(args.apply)
