"""
scripts/regime_strategy_pipeline.py

코인 하나를 지정하면 하락/횡보/상승 장세 각각에 대해 최근 구간을 찾아 grid
search로 전략을 발굴하고, 손절/익절 OR조건을 부착한 뒤 regime_strategy_library에
매핑까지 자동으로 수행한다. 라이브 전략 생성/자동스왑 토글은 범위 밖 — 사용자가
/strategy-library에서 최종 확인 후 수동으로 진행한다. 설계 문서:
docs/superpowers/specs_v2/2026-09-06-regime-strategy-auto-pipeline-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_strategy_pipeline.py \
     --market KRW-ETH --history-start 2026-01-01
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta
from datetime import timezone

from backend.regime_adx_service import compute_adx_regime_history
from scripts.grid_search import build_condition_grid, compute_grid_results_parallel, _check_candle_warmup, dedup_top_results
from backend.main import _fetch_backtest_dataframe
from engine.sweep import DEFAULT_RISK_CONFIG
from scripts.grid_search import _wrap_condition
from engine.runner import run_backtest
from engine.condition_strategy import ConditionTreeStrategy
import trading.db as trading_db
from engine.cache import run_backtest_cached
from fastapi import HTTPException
from engine.regime_adx_constants import MAJOR_MARKETS

TIMEFRAME = "minutes60"
ALL_CATEGORIES = ["오실레이터", "추세", "가격대", "거래량", "거래대금", "시장 심리"]


def min_trades_for_days(period_days: float) -> int:
    """기간이 길수록 요구 거래횟수도 늘어난다(5일당 1회, 최소 3회)."""
    return max(3, math.ceil(period_days / 5))


def adjust_window(seg: dict, min_days: int, history_start: datetime) -> tuple[datetime, datetime]:
    """세그먼트 길이가 min_days 미만이면 start를 당겨 채운다. end는 항상 세그먼트의
    end 그대로 두고(사용자가 지정한 장세가 "끝난 시점"은 그대로 유지), history_start
    보다 앞으로는 당기지 않는다(요청한 데이터 범위 밖으로 나가지 않기 위함)."""
    end = datetime.fromisoformat(seg["end"])
    start = datetime.fromisoformat(seg["start"])
    min_start = end - timedelta(days=min_days)
    if start > min_start:
        start = max(min_start, history_start)
    return start, end


def select_target_segments(market: str, history_start: datetime) -> dict[str, dict | None]:
    """라벨(하락/횡보/상승)별 history_start 이후 시작하는 가장 최근 세그먼트를
    고른다. 해당 라벨의 세그먼트가 없으면 None."""
    history = compute_adx_regime_history(market, TIMEFRAME)
    by_label: dict[str, dict] = {}
    for seg in history["segments"]:
        if datetime.fromisoformat(seg["start"]) < history_start:
            continue
        label = seg["label"]
        if label not in by_label or seg["end"] > by_label[label]["end"]:
            by_label[label] = seg
    return {label: by_label.get(label) for label in ("하락", "횡보", "상승")}


def augment_with_tp_sl(sell_group: dict, stop_loss_pct: float, take_profit_pct: float) -> dict:
    """원래 매도조건에 손절/익절 OR조건을 얹는다. STOP_LOSS_PCT/TAKE_PROFIT_PCT는
    포지션 진입가 대비 수익률로 평가되는 기존 조건트리 지표(engine/condition_tree.py의
    POSITION_RELATIVE_INDICATORS)라 그대로 재사용한다."""
    return {
        "type": "OR",
        "conditions": [
            sell_group,
            {"indicator": "STOP_LOSS_PCT", "params": {}, "operator": "<=", "threshold": stop_loss_pct},
            {"indicator": "TAKE_PROFIT_PCT", "params": {}, "operator": ">=", "threshold": take_profit_pct},
        ],
    }


def top_candidates(results: list[dict], min_trades: int, pool_size: int) -> list[dict]:
    """거래횟수 미달 결과를 먼저 버리고, 남은 것에 기존 dedup_top_results를 적용해
    수익률 내림차순 상위 pool_size개를 돌려준다."""
    filtered = [r for r in results if len(r["trades"]) >= min_trades]
    return dedup_top_results(filtered, pool_size)


def run_grid_for_window(market: str, start: datetime, end: datetime, capital: float) -> dict:
    """grid search를 실행하고, 이후 단계(후보 재검증/최종 저장)가 그대로 재사용할
    df/risk_config까지 함께 반환한다(같은 df로 캔들을 두 번 조회하지 않기 위함)."""
    pool = {"categories": ALL_CATEGORIES, "excluded_indicators": []}
    buy_conditions, sell_conditions = build_condition_grid(pool, market=market)
    df = _fetch_backtest_dataframe(
        market, TIMEFRAME, start, end,
        {"type": "AND", "conditions": buy_conditions},
        {"type": "AND", "conditions": sell_conditions},
    )
    _check_candle_warmup(df, buy_conditions, sell_conditions)
    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": capital}
    results = compute_grid_results_parallel(df, buy_conditions, sell_conditions, risk_config)
    return {"df": df, "risk_config": risk_config, "results": results}


def pick_final_strategy(
    df, candidates: list[dict], risk_config: dict, min_trades: int,
    stop_loss_pct: float, take_profit_pct: float,
) -> dict | None:
    """후보를 수익률 내림차순으로 순회하며 TP/SL 증강 후에도 거래횟수를 만족하는
    첫 번째를 채택한다. 전부 실패하면 None."""
    for cand in candidates:
        buy_group = _wrap_condition(cand["buy_block"], None, "AND")
        base_sell_group = _wrap_condition(cand["sell_block"], None, "AND")
        augmented_sell = augment_with_tp_sl(base_sell_group, stop_loss_pct, take_profit_pct)
        result = run_backtest(
            df, ConditionTreeStrategy, risk_config,
            {"buy_conditions": buy_group, "sell_conditions": augmented_sell},
        )
        if len(result["trades"]) >= min_trades:
            return_pct = (
                (result["final_value"] - risk_config["initial_capital"])
                / risk_config["initial_capital"] * 100
            )
            return {
                "buy_conditions": buy_group, "sell_conditions": augmented_sell,
                "return_pct": return_pct, "trades": result["trades"],
                "raw_return_pct": cand["return_pct"], "raw_trade_count": len(cand["trades"]),
            }
    return None


def save_and_map(
    market: str, regime: str, start: datetime, end: datetime, final: dict,
    df, risk_config: dict, stop_loss_pct: float, take_profit_pct: float,
) -> str:
    title = (
        f"[{regime}] {market} {start.date()}~{end.date()} "
        f"그리드+TP{take_profit_pct}%/SL{abs(stop_loss_pct)}%"
    )
    description = (
        f"regime_strategy_pipeline - {market}/{TIMEFRAME}/{start.date()}~{end.date()}, "
        f"원본 수익률 {final['raw_return_pct']:+.2f}%({final['raw_trade_count']}건) -> "
        f"TP/SL 부착 후 {final['return_pct']:+.2f}%({len(final['trades'])}건)"
    )
    saved = run_backtest_cached(
        df=df, strategy_cls=ConditionTreeStrategy, risk_config=risk_config,
        market=market, timeframe=TIMEFRAME, start=start, end=end,
        strategy_params={
            "buy_conditions": final["buy_conditions"],
            "sell_conditions": final["sell_conditions"],
        },
        title=title, description=description,
    )
    trading_db.upsert_regime_strategy_mapping(
        market, regime, source_run_id=saved["run_id"], timeframe=TIMEFRAME,
        buy_conditions_json=json.dumps(final["buy_conditions"]),
        sell_conditions_json=json.dumps(final["sell_conditions"]),
    )
    return saved["run_id"]


def _ensure_supported_market(market: str) -> None:
    if market not in MAJOR_MARKETS:
        raise SystemExit(f"{market}은(는) 지원하지 않는 마켓입니다.")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="장세별 전략 자동 발굴 파이프라인")
    parser.add_argument("--market", required=True, help="마켓코드 (예: KRW-ETH)")
    parser.add_argument("--history-start", required=True, help="시작일 YYYY-MM-DD (이 날짜 이후 세그먼트만 사용)")
    parser.add_argument("--capital", type=float, default=10_000_000)
    parser.add_argument("--min-days", type=int, default=10)
    parser.add_argument("--stop-loss-pct", type=float, default=-5.0)
    parser.add_argument("--take-profit-pct", type=float, default=8.0)
    parser.add_argument("--candidate-pool", type=int, default=20)
    return parser.parse_args(argv)


def run_pipeline(
    market: str, history_start: datetime, capital: float, min_days: int,
    stop_loss_pct: float, take_profit_pct: float, candidate_pool: int,
) -> list[dict]:
    segments = select_target_segments(market, history_start)
    summary: list[dict] = []
    for regime, seg in segments.items():
        if seg is None:
            summary.append({"regime": regime, "status": "skipped", "reason": "탐지된 구간 없음"})
            continue
        try:
            start, end = adjust_window(seg, min_days, history_start)
            period_days = (end - start).days
            min_trades = min_trades_for_days(period_days)

            grid = run_grid_for_window(market, start, end, capital)
            candidates = top_candidates(grid["results"], min_trades, candidate_pool)
            if not candidates:
                summary.append({"regime": regime, "status": "skipped", "reason": "거래횟수 조건을 만족하는 후보 없음"})
                continue

            final = pick_final_strategy(
                grid["df"], candidates, grid["risk_config"], min_trades,
                stop_loss_pct, take_profit_pct,
            )
            if final is None:
                summary.append({"regime": regime, "status": "skipped", "reason": "TP/SL 부착 후 거래횟수 조건을 만족하는 후보 없음"})
                continue

            run_id = save_and_map(
                market, regime, start, end, final,
                grid["df"], grid["risk_config"], stop_loss_pct, take_profit_pct,
            )
            summary.append({
                "regime": regime, "status": "mapped", "run_id": run_id,
                "period": f"{start.date()}~{end.date()}",
                "return_pct": round(final["return_pct"], 2),
                "trade_count": len(final["trades"]),
            })
        except (HTTPException, SystemExit) as exc:
            summary.append({"regime": regime, "status": "failed", "reason": str(exc)})
    return summary


def print_summary_table(summary: list[dict]) -> None:
    print(f"{'장세':6}{'상태':10}{'기간':24}{'수익률':>10}{'거래':>6}  비고")
    for row in summary:
        period = row.get("period", "-")
        return_pct = f"{row['return_pct']:+.2f}%" if "return_pct" in row else "-"
        trade_count = str(row.get("trade_count", "-"))
        reason = row.get("reason", row.get("run_id", ""))
        print(f"{row['regime']:6}{row['status']:10}{period:24}{return_pct:>10}{trade_count:>6}  {reason}")


def main() -> None:
    args = parse_args()
    _ensure_supported_market(args.market)
    history_start = datetime.strptime(args.history_start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    summary = run_pipeline(
        args.market, history_start, args.capital, args.min_days,
        args.stop_loss_pct, args.take_profit_pct, args.candidate_pool,
    )
    print_summary_table(summary)
    print(f"RESULT_JSON: {json.dumps({'market': args.market, 'segments': summary}, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
