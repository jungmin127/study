"""
scripts/absorb_dust_position.py

1회성 정리 스크립트(설계 문서
docs/superpowers/specs_v1/2026-08-21-live-trading-dust-position-auto-recovery-design.md
Part 3): reconciler가 최소주문금액 미만 잔고 불일치를 "설명 안 됨"으로 오인해 재오픈한
더스트 포지션을 안전하게 종결하고 baseline_qty에 흡수한다. Part 1(reconciler.py 수정)이
배포된 뒤 앞으로 재발하는 건은 자동으로 처리되므로, 이 스크립트는 그 수정 이전에 이미
생겨버린 건을 1회 정리하는 용도다.

position_manager.close_position()을 쓰면 db.update_live_strategy_capital()이 호출되어
current_capital이 exit_price*exit_qty(-수수료)로 통째로 대체(replace)된다 — 부풀려지는 게
아니라, 원래 전략이 갖고 있던 current_capital(예: 100만원)이 이 더스트 포지션의 가치
(예: 4,500원) 수준으로 리셋되어 버린다는 뜻이다. 그래서 db.close_position_row()를 직접
호출해 realized_pnl=0으로 종결하고 current_capital은 건드리지 않는다.

실행 전 trading.db를 자동 백업한다(--apply일 때만, scripts/backfill_entry_fee.py와 동일
패턴). 기본은 드라이런(무엇을 바꿀지만 출력).

사용법:
    python scripts/absorb_dust_position.py <live_strategy_id>              # 드라이런
    python scripts/absorb_dust_position.py <live_strategy_id> --apply      # 실제 적용
    python scripts/absorb_dust_position.py <live_strategy_id> --apply --force  # 안전장치 무시
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import trading.db as db
import trading.position_manager as position_manager
import trading.upbit_client as upbit_client

# 업비트 원화마켓 최소 주문금액. order_executor.py/reconciler.py의 동일 상수와 값은 같지만,
# 이 스크립트도 다른 trading 서브모듈에 의존하지 않는다는 원칙을 지키기 위해 값을 복제한다.
_MIN_ORDER_AMOUNT_KRW = 5000
# trading/reconciler.py와 동일한 오차 허용치.
_QTY_EPSILON = 1e-6


def _backup_db() -> Path:
    """raw shutil.copy2로는 WAL 모드에서 아직 체크포인트되지 않은 거래가 -wal 사이드카
    파일에만 있어 백업에서 누락될 수 있다. sqlite3의 온라인 백업 API를 쓰면 journal 모드와
    무관하게 항상 일관된 완전한 스냅샷을 얻는다(scripts/backfill_entry_fee.py와 동일)."""
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


async def _get_actual_balance(market: str) -> float:
    currency = market.split("-", 1)[1]
    accounts = await upbit_client.get_accounts()
    for account in accounts:
        if account["currency"] == currency:
            return float(account["balance"]) + float(account["locked"])
    return 0.0


async def run(strategy_id: str, apply: bool, force: bool) -> None:
    strategy = db.get_live_strategy(strategy_id)
    if strategy is None:
        print(f"전략을 찾을 수 없습니다: {strategy_id}")
        return

    position = position_manager.get_open_position(strategy_id)
    if position is None:
        print(f"정리할 오픈 포지션이 없습니다: strategy_id={strategy_id}")
        return

    market = strategy["market"]
    actual_balance = await _get_actual_balance(market)
    entry_qty = position["entry_qty"]
    entry_price = position["entry_price"]
    baseline_before = strategy["baseline_qty"] or 0.0

    if abs(actual_balance - (baseline_before + entry_qty)) > _QTY_EPSILON:
        print(
            f"중단: 포지션 수량({entry_qty})과 baseline({baseline_before})의 합이 실제 "
            f"잔고({actual_balance})와 오차범위를 벗어나 일치하지 않습니다 - 더 큰 포지션과 "
            "혼동될 위험이 있어 자동 처리를 거부합니다. 직접 확인하세요."
        )
        return

    value_krw = entry_qty * entry_price
    if value_krw >= _MIN_ORDER_AMOUNT_KRW and not force:
        print(
            f"중단: 이 포지션의 가치({value_krw:.0f}원)가 최소주문금액"
            f"({_MIN_ORDER_AMOUNT_KRW}원) 이상이라 더스트로 보기 어렵습니다. "
            "정말 정리하려면 --force를 붙이세요."
        )
        return

    print(
        f"strategy_id={strategy_id} market={market} entry_qty={entry_qty} "
        f"entry_price={entry_price} value_krw={value_krw:.0f} "
        f"baseline_qty(before)={baseline_before}"
    )

    if not apply:
        print("드라이런입니다. 실제로 적용하려면 --apply를 붙여 다시 실행하세요.")
        return

    backup_path = _backup_db()
    print(f"백업 완료: {backup_path}")

    db.close_position_row(position["id"], entry_price, entry_qty, 0.0, 0.0, "dust_cleanup")
    new_baseline = baseline_before + entry_qty
    db.update_live_strategy_baseline_qty(strategy_id, new_baseline)
    db.insert_manual_intervention_event(
        market,
        f"더스트 포지션 수동 정리: entry_qty={entry_qty}(≈{value_krw:.0f}원) baseline에 흡수",
        "dust_absorbed_manual_cleanup",
    )
    print(f"완료: 포지션 종결, baseline_qty {baseline_before} -> {new_baseline}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("strategy_id", help="정리할 live_strategy_id")
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경한다(기본은 드라이런)")
    parser.add_argument(
        "--force", action="store_true",
        help="포지션 가치가 최소주문금액 이상이어도 강제로 진행한다",
    )
    args = parser.parse_args()
    asyncio.run(run(args.strategy_id, args.apply, args.force))
