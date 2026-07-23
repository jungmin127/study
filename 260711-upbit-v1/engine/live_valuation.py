"""
engine/live_valuation.py

미청산("보유중(기간종료)") 포지션의 수익률/손익을 최신 가격 기준으로 다시 계산한다.
백테스트가 끝난 뒤에도 실제로는 아직 청산되지 않은 포지션의 현재 가치를 보여주기 위함.
"""
from __future__ import annotations


def has_revaluable_open_trade(trades: list[dict]) -> bool:
    """size가 있는 forceClosed 거래가 하나라도 있으면 True.

    size는 이번 기능과 함께 거래 기록에 추가된 필드라, 그 이전에 저장된 결과에는
    없을 수 있다 — 그런 경우는 재평가 대상에서 제외한다(알려진 제약)."""
    return any(t.get("forceClosed") and "size" in t for t in trades)


def revalue_open_trades(
    trades: list[dict],
    live_price: float,
    live_time: str,
    commission_rate: float,
) -> tuple[list[dict], float]:
    """forceClosed=True이고 size가 있는 거래를 live_price 기준으로 재평가한 새 리스트와,
    그로 인한 총 평가금액 변화량(delta, 원 단위)을 함께 반환한다.
    size가 없는(레거시) 거래나 forceClosed가 아닌 거래는 그대로 둔다.
    holdingPeriod는 갱신하지 않는다 — 봉 개수 기준 재계산에는 baropen이 필요한데
    저장된 거래 기록에 없어, 이번 범위에서는 "백테스트 종료 시점까지의 보유 기간"으로
    고정해 둔다(알려진 제약, 상세 페이지 캡션에 명시).
    """
    updated: list[dict] = []
    delta = 0.0
    for t in trades:
        if t.get("forceClosed") and "size" in t:
            entry_price = t["entryPrice"]
            size = t["size"]
            pnl_gross = (live_price - entry_price) * size
            entry_commission = entry_price * size * commission_rate
            exit_commission = live_price * size * commission_rate
            new_pnl = round(pnl_gross - entry_commission - exit_commission, 4)
            return_rate = (new_pnl / (entry_price * size) * 100) if (entry_price and size) else 0.0
            delta += new_pnl - t["pnl"]
            updated.append({
                **t,
                "exitPrice": round(live_price, 8),
                "exitTime": live_time,
                "returnRate": round(return_rate, 4),
                "pnl": new_pnl,
            })
        else:
            updated.append(t)
    return updated, round(delta, 4)


__all__ = ["has_revaluable_open_trade", "revalue_open_trades"]
