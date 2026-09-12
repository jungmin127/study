"""
.claude/skills/live-trading-retrospective/retrospective.py

AWS 라이브 서버의 trading.db 사본을 읽어 3가지 관점의 회고 데이터를 출력한다:
1. 마켓별/청산사유별 전체 성과
2. 계단식 손절 반복 패턴(같은 전략이 연속 N회 이상 손절)
3. 조기 익절 패턴(TAKE_PROFIT_PCT 청산 후 일정 기간 내 가격이 더 오른 경우)

읽기 전용 사본만 다룬다 — 이 스크립트는 어떤 DB도 수정하지 않는다.

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python .claude/skills/live-trading-retrospective/retrospective.py <trading.db 경로>
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import upbit_data_service as uds


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def overall_summary(conn: sqlite3.Connection) -> None:
    print("\n=== 1. 마켓별 전체 성과 ===")
    rows = conn.execute("""
        SELECT market,
               COUNT(*) AS trades,
               SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
               ROUND(SUM(realized_pnl), 0) AS total_pnl,
               ROUND(AVG(realized_pnl_pct), 2) AS avg_pnl_pct,
               ROUND(MIN(realized_pnl_pct), 2) AS worst_pct
        FROM positions
        WHERE status = 'closed'
        GROUP BY market
        ORDER BY total_pnl ASC
    """).fetchall()
    if not rows:
        print("  청산된 포지션 없음")
        return
    for r in rows:
        win_rate = round(100.0 * r["wins"] / r["trades"], 1) if r["trades"] else 0.0
        print(
            f"  {r['market']:10} 거래{r['trades']:4}건  승률{win_rate:5.1f}%  "
            f"합계 {r['total_pnl']:+,.0f}원  평균 {r['avg_pnl_pct']:+.2f}%  최악 {r['worst_pct']:+.2f}%"
        )

    print("\n=== 청산 사유별 분포 ===")
    rows = conn.execute("""
        SELECT close_reason, COUNT(*) AS n, ROUND(SUM(realized_pnl), 0) AS total_pnl,
               ROUND(AVG(realized_pnl_pct), 2) AS avg_pct
        FROM positions WHERE status = 'closed'
        GROUP BY close_reason ORDER BY total_pnl ASC
    """).fetchall()
    for r in rows:
        print(
            f"  {r['close_reason'] or '(없음)':20} {r['n']:4}건  "
            f"합계 {r['total_pnl']:+,.0f}원  평균 {r['avg_pct']:+.2f}%"
        )


def detect_losing_streaks(conn: sqlite3.Connection, min_streak: int = 3) -> None:
    """"계단식 손절"은 반드시 close_reason='stop_loss_pct'로만 나타나지 않는다 —
    STOP_LOSS_PCT 조건 자체를 안 쓰는 전략은 매수/매도 신호(close_reason='signal')만으로
    같은 패턴(진입 직후 손실 청산 반복)을 만들 수 있다. 그래서 사유 라벨이 아니라
    실현 손익 부호(realized_pnl_pct < 0)로 연속 손실 구간을 찾는다."""
    print(f"\n=== 2. 계단식 손절 반복 패턴 (같은 전략이 연속 {min_streak}회 이상 손실) ===")
    rows = conn.execute("""
        SELECT live_strategy_id, market, entry_time, exit_time, close_reason, realized_pnl_pct
        FROM positions WHERE status = 'closed'
        ORDER BY live_strategy_id, entry_time
    """).fetchall()

    streak: list[sqlite3.Row] = []
    found_any = False
    for row in rows:
        is_loss = (row["realized_pnl_pct"] or 0) < 0
        same_strategy = streak and streak[-1]["live_strategy_id"] == row["live_strategy_id"]
        if is_loss and (not streak or same_strategy):
            streak.append(row)
        else:
            if len(streak) >= min_streak:
                found_any = True
                _print_streak(streak)
            streak = [row] if is_loss else []
    if len(streak) >= min_streak:
        found_any = True
        _print_streak(streak)
    if not found_any:
        print(f"  연속 {min_streak}회 이상 손실 패턴 없음")


def _print_streak(streak: list[sqlite3.Row]) -> None:
    market = streak[0]["market"]
    start = streak[0]["entry_time"]
    end = streak[-1]["exit_time"]
    total_pct = sum((r["realized_pnl_pct"] or 0) for r in streak)
    reasons = {r["close_reason"] for r in streak}
    print(
        f"  {market}: {start} ~ {end} 사이 {len(streak)}연속 손실, "
        f"누적 {total_pct:+.2f}% (사유: {', '.join(sorted(reasons))})"
    )


def detect_early_take_profit(
    conn: sqlite3.Connection, timeframe: str = "minutes60", horizon_hours: int = 72,
    min_missed_pct: float = 3.0,
) -> None:
    """"조기 익절"도 close_reason='take_profit_pct'에 국한하지 않는다 — 이 지표
    자체를 안 쓰는 전략은 수익 청산이 전부 close_reason='signal'로 남는다. 그래서
    사유와 무관하게 "수익으로 청산된 모든 거래"(realized_pnl_pct > 0)를 대상으로,
    청산 이후 가격이 더 올랐는지 확인한다."""
    print(f"\n=== 3. 조기 익절 패턴 (수익 청산 후 {horizon_hours}시간 내 가격이 더 올랐는지) ===")
    rows = conn.execute("""
        SELECT market, exit_time, exit_price, realized_pnl_pct, close_reason
        FROM positions
        WHERE status = 'closed' AND realized_pnl_pct > 0
        ORDER BY exit_time
    """).fetchall()
    if not rows:
        print("  수익으로 청산된 거래 없음")
        return

    found_any = False
    now = datetime.now(timezone.utc)
    for r in rows:
        exit_time = datetime.fromisoformat(r["exit_time"]).replace(tzinfo=timezone.utc)
        window_end = exit_time + timedelta(hours=horizon_hours)
        if window_end > now:
            continue  # 기간이 아직 다 지나지 않은 최근 거래는 판단 보류
        df = uds.get_candles(r["market"], timeframe, exit_time, window_end)
        if df.empty:
            continue
        max_close = df["close"].max()
        missed_pct = (max_close - r["exit_price"]) / r["exit_price"] * 100
        if missed_pct > min_missed_pct:
            found_any = True
            print(
                f"  {r['market']} {exit_time.date()} {r['close_reason']} 청산({r['realized_pnl_pct']:+.2f}%) "
                f"이후 {horizon_hours}시간 내 최고가 대비 {missed_pct:+.2f}% 추가 상승 놓침"
            )
    if not found_any:
        print(f"  {horizon_hours}시간 내 {min_missed_pct}% 넘게 추가 상승한 사례 없음")


def main() -> None:
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/trading.db"
    conn = _connect(db_path)
    try:
        overall_summary(conn)
        detect_losing_streaks(conn)
        detect_early_take_profit(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
