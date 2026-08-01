"""
scripts/grid_search.py

'grid search' 스킬의 연산 엔진. 오실레이터 5종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) +
매도전용 3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드를
계산하고, 거래 시퀀스가 동일한 조합은 dedup한 뒤 상위 N개만 백테스트 결과에 저장한다.
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone

from engine.cache import run_backtest_cached
from engine.condition_strategy import ConditionTreeStrategy
from engine.runner import run_backtest
from engine.sweep import DEFAULT_RISK_CONFIG
from upbit_data_service import get_candles

PERIOD_GRID = [10, 14, 20]

OSCILLATORS: dict[str, dict[str, list[int]]] = {
    "RSI": {"low": [20, 30, 40], "high": [60, 70, 80]},
    "STOCH_K": {"low": [10, 20, 30], "high": [70, 80, 90]},
    "STOCH_D": {"low": [10, 20, 30], "high": [70, 80, 90]},
    "CCI": {"low": [-140, -100, -60], "high": [60, 100, 140]},
    "WILLIAMS_R": {"low": [-90, -80, -70], "high": [-30, -20, -10]},
}

# STOCH_K/STOCH_D는 create_stoch_k/create_stoch_d(engine/indicators/momentum.py)가
# "period"가 아니라 "k_period"를 읽는다. period 그리드가 실제로 반영되도록
# 지표별로 올바른 파라미터 키를 매핑한다.
PERIOD_PARAM_KEY: dict[str, str] = {
    "STOCH_K": "k_period",
    "STOCH_D": "k_period",
}

SELL_ONLY: dict[str, tuple[str, list[int]]] = {
    "STOP_LOSS_PCT": ("<=", [-3, -5, -7, -10]),
    "TAKE_PROFIT_PCT": (">=", [5, 10, 15, 20]),
    "HOLDING_PERIOD_BARS": (">=", [5, 10, 20, 40]),
}


def build_condition_grid() -> tuple[list[dict], list[dict]]:
    """오실레이터 5종 + 매도전용 3종의 매수/매도 ConditionBlock 그리드를 생성한다.

    Returns:
        (buy_conditions, sell_conditions) — 각각 ConditionBlock 딕셔너리 리스트
        ({"indicator": str, "params": dict, "operator": str, "threshold": float}).
    """
    buy_conditions: list[dict] = []
    sell_conditions: list[dict] = []

    for indicator, bounds in OSCILLATORS.items():
        param_key = PERIOD_PARAM_KEY.get(indicator, "period")
        for period in PERIOD_GRID:
            for t in bounds["low"]:
                buy_conditions.append(
                    {"indicator": indicator, "params": {param_key: period}, "operator": "<", "threshold": t}
                )
            for t in bounds["high"]:
                sell_conditions.append(
                    {"indicator": indicator, "params": {param_key: period}, "operator": ">", "threshold": t}
                )

    for indicator, (operator, thresholds) in SELL_ONLY.items():
        for t in thresholds:
            sell_conditions.append({"indicator": indicator, "params": {}, "operator": operator, "threshold": t})

    return buy_conditions, sell_conditions


def compute_grid_results(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
) -> list[dict]:
    """buy_conditions x sell_conditions 전 조합을 run_backtest로 계산한다.

    Returns:
        각 조합의 결과 딕셔너리 리스트:
        {"return_pct": float, "buy_block": dict, "sell_block": dict,
         "trades": list[dict], "final_value": float}
    """
    results: list[dict] = []
    initial_capital = float(risk_config.get("initial_capital", 10000))
    total = len(buy_conditions) * len(sell_conditions)

    for i, buy_block in enumerate(buy_conditions):
        buy_group = {"type": "AND", "conditions": [buy_block]}
        for sell_block in sell_conditions:
            sell_group = {"type": "AND", "conditions": [sell_block]}
            result = run_backtest(
                df,
                ConditionTreeStrategy,
                risk_config,
                {"buy_conditions": buy_group, "sell_conditions": sell_group},
            )
            return_pct = (result["final_value"] - initial_capital) / initial_capital * 100
            results.append(
                {
                    "return_pct": return_pct,
                    "buy_block": buy_block,
                    "sell_block": sell_block,
                    "trades": result["trades"],
                    "final_value": result["final_value"],
                }
            )
        if (i + 1) % 5 == 0 or (i + 1) == len(buy_conditions):
            done = (i + 1) * len(sell_conditions)
            print(f"    매수조건 {i + 1}/{len(buy_conditions)} 완료 ({done}/{total}건)", flush=True)

    return results


def _effective_period(params: dict) -> int:
    return params.get("period", params.get("k_period", 0))


def _trade_sequence_key(trades: list[dict]) -> tuple:
    return tuple((t["entryTime"], t["exitTime"]) for t in trades)


def dedup_top_results(results: list[dict], top_n: int) -> list[dict]:
    """동일 거래 시퀀스를 만든 조합 중 매수+매도 period 합이 가장 작은 것만 남기고,
    수익률 내림차순 상위 top_n개를 반환한다. 거래가 0건인 조합은 제외한다.

    각 그룹의 대표 결과에는 동일 거래 시퀀스를 만든 조합 개수를 "dup_count"로 포함한다.
    """
    groups: dict[tuple, dict] = {}
    dup_counts: dict[tuple, int] = {}
    for r in results:
        if not r["trades"]:
            continue
        key = _trade_sequence_key(r["trades"])
        dup_counts[key] = dup_counts.get(key, 0) + 1
        period_sum = _effective_period(r["buy_block"]["params"]) + _effective_period(r["sell_block"]["params"])
        existing = groups.get(key)
        if existing is None or period_sum < existing["_period_sum"]:
            groups[key] = {**r, "_period_sum": period_sum}

    deduped = sorted(groups.items(), key=lambda kv: kv[1]["return_pct"], reverse=True)
    return [
        {**{k: v for k, v in r.items() if k != "_period_sum"}, "dup_count": dup_counts[key]}
        for key, r in deduped[:top_n]
    ]


def _positive_int(value: str) -> int:
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError(f"--top-n must be >= 1 (got {n})")
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description="오실레이터 그리드서치 백테스트")
    parser.add_argument("--market", required=True, help="마켓코드 (예: KRW-ETH)")
    parser.add_argument("--timeframe", required=True, help="timeframe 코드 (예: minutes60)")
    parser.add_argument("--capital", required=True, type=float, help="운용자금(원)")
    parser.add_argument("--start", required=True, help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="종료일 YYYY-MM-DD")
    parser.add_argument("--top-n", type=_positive_int, default=20, help="저장할 상위 개수 (기본 20, 상한 50)")
    args = parser.parse_args()

    top_n = min(args.top_n, 50)

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    print(f"[1] 캔들 조회: {args.market} {args.timeframe} {args.start} ~ {args.end}", flush=True)
    df = get_candles(args.market, args.timeframe, start_dt, end_dt)
    print(f"    캔들 수: {len(df)}", flush=True)
    if len(df) == 0:
        raise SystemExit(f"캔들 데이터가 없습니다: {args.market} {args.timeframe} {args.start}~{args.end}")

    buy_conditions, sell_conditions = build_condition_grid()
    total_combos = len(buy_conditions) * len(sell_conditions)
    print(
        f"[2] 매수 조건 {len(buy_conditions)}개 x 매도 조건 {len(sell_conditions)}개 = 총 {total_combos:,}개 조합",
        flush=True,
    )

    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": args.capital}

    t0 = time.perf_counter()
    results = compute_grid_results(df, buy_conditions, sell_conditions, risk_config)
    elapsed = time.perf_counter() - t0
    print(f"\n[3] 전체 계산 완료: {len(results)}건, {elapsed:.1f}초 ({elapsed / 60:.1f}분)", flush=True)

    top_results = dedup_top_results(results, top_n)
    print(f"\n[4] dedup 후 상위 {len(top_results)}개를 백테스트 결과에 저장 중...", flush=True)

    saved_summaries = []
    for rank, r in enumerate(top_results, start=1):
        buy_block, sell_block = r["buy_block"], r["sell_block"]
        buy_group = {"type": "AND", "conditions": [buy_block]}
        sell_group = {"type": "AND", "conditions": [sell_block]}
        title = (
            f"[Grid] 매수 {buy_block['indicator']}{buy_block['params']}{buy_block['operator']}{buy_block['threshold']} "
            f"/ 매도 {sell_block['indicator']}{sell_block['params']}{sell_block['operator']}{sell_block['threshold']}"
        )
        description = (
            f"grid search - {args.market}/{args.timeframe}/{args.start}~{args.end}, "
            f"수익률 {r['return_pct']:+.2f}% (상위 {rank}위)"
            f", 동일 매매를 만든 조합 {r['dup_count']}개 중 대표"
        )
        saved = run_backtest_cached(
            df=df,
            strategy_cls=ConditionTreeStrategy,
            risk_config=risk_config,
            market=args.market,
            timeframe=args.timeframe,
            start=start_dt,
            end=end_dt,
            strategy_params={"buy_conditions": buy_group, "sell_conditions": sell_group},
            title=title,
            description=description,
        )
        print(f"  {rank:2d}. {r['return_pct']:+.2f}%  run_id={saved['run_id'][:12]}...", flush=True)
        saved_summaries.append(
            {"rank": rank, "run_id": saved["run_id"], "return_pct": round(r["return_pct"], 2), "title": title}
        )

    result_json = {"total_combos": total_combos, "elapsed_sec": round(elapsed, 1), "saved": saved_summaries}
    print("\n완료.")
    print(f"RESULT_JSON: {json.dumps(result_json, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
