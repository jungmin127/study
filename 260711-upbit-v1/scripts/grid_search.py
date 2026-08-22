"""
scripts/grid_search.py

'grid search' 스킬의 연산 엔진. 오실레이터 9종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/
BB_PERCENT_B/MACD_PPO/MACD_PPO_signal/ATR_PCT — ATR_PCT만 양방향) + 매도전용 3종
(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(20,700개 조합)를
계산하고, 거래 시퀀스가 동일한 조합은 dedup한 뒤 상위 N개만 백테스트 결과에 저장한다.
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
"""
from __future__ import annotations

import argparse
import json
import multiprocessing
import time
from datetime import datetime, timezone

from fastapi import HTTPException

from backend.main import _fetch_backtest_dataframe
from engine.cache import get_run_config, run_backtest_cached
from engine.condition_strategy import ConditionTreeStrategy
from engine.condition_tree import collect_blocks, max_required_period
from engine.grid_search_pool import (
    INDICATOR_POOL_SPECS,
    MARKET_SENTIMENT_SPECS,
    OSCILLATOR_SPECS,
    PRICE_LEVEL_SPECS,
    SELL_ONLY,
    TRADE_VALUE_SPECS,
    TREND_SPECS,
    VOLUME_SPECS,
    build_condition_grid,
)
from engine.runner import run_backtest
from engine.sweep import DEFAULT_RISK_CONFIG
from upbit_data_service import get_candles

# MAX_TASKS_PER_CHILD은 83.57 KB/call(9-오실레이터 그리드, ETH/1시간봉/2026-06~07 기준 실측)을
# 전제로 계산됐다. 이 세션에서 측정한 leak rate는 상황에 따라 20~177 KB/call까지 편차가 있었으므로,
# 더 무거운 데이터셋(캔들 수가 많거나 지표 조합이 늘어나는 경우)에서는 워커당 실제 누적 메모리가
# 이 값이 전제한 예산(약 916MB)을 초과할 수 있다.
WORKER_COUNT = 4
MAX_TASKS_PER_CHILD = 11223
WATCHDOG_TIMEOUT_SEC = 300
PROGRESS_LOG_INTERVAL = 1000


def _macd_required_bars(params: dict) -> int:
    """MACD_PPO/MACD_PPO_signal의 실제 필요 워밍업 봉 수를 계산한다.

    backtrader PPO의 시그널 라인 minperiod는 max(fast,slow) + signal - 1이다(실측 확인:
    PPO(fast=16,slow=32,signal=12)는 42봉에서 IndexError, 43봉에서 정상). max_required_period()는
    개별 파라미터의 max만 취해 이 조합형 요구량을 과소평가하므로(예: 위 파라미터에 32만 반환)
    별도로 계산해 보정한다.
    """
    fast = params.get("fast")
    slow = params.get("slow")
    signal = params.get("signal")
    if fast is None or slow is None or signal is None:
        return 0
    return max(fast, slow) + signal - 1


def _check_candle_warmup(
    df, buy_conditions: list[dict], sell_conditions: list[dict],
    base_buy_group: dict | None = None, base_sell_group: dict | None = None,
) -> None:
    """그리드에 등장하는 파라미터의 최대 워밍업 봉 수보다 캔들이 적으면 명확한 에러로 중단한다.

    사전 체크 없이 계산을 시작하면 backtrader 내부에서 IndexError로 불명확하게 죽는다.
    max_required_period()는 MACD류의 조합형 요구량(_macd_required_bars 참고)을 과소평가하므로
    별도로 보정한다. 체이닝 시 베이스 조건(base_buy_group/base_sell_group)의 파라미터도
    포함해야 한다 — 베이스가 새 풀보다 긴 워밍업을 요구하는 지표를 쓰는 경우, 이 체크 없이는
    backtrader 내부에서 불명확하게 죽는다(최종 리뷰 Critical #1)."""
    all_buy_group = {"type": "AND", "conditions": buy_conditions + ([base_buy_group] if base_buy_group else [])}
    all_sell_group = {"type": "AND", "conditions": sell_conditions + ([base_sell_group] if base_sell_group else [])}
    required_bars = max(max_required_period(all_buy_group), max_required_period(all_sell_group))
    base_blocks = (
        (collect_blocks(base_buy_group) if base_buy_group else [])
        + (collect_blocks(base_sell_group) if base_sell_group else [])
    )
    for block in buy_conditions + sell_conditions + base_blocks:
        if block["indicator"] in ("MACD_PPO", "MACD_PPO_signal"):
            required_bars = max(required_bars, _macd_required_bars(block["params"]))
    if len(df) < required_bars:
        raise SystemExit(
            f"선택된 그리드가 최소 {required_bars}개의 봉을 필요로 하지만, "
            f"해당 기간에는 {len(df)}개의 봉만 있습니다. 기간을 늘리세요."
        )


def _watchdog_expired(last_progress_time: float, now: float, timeout_sec: float) -> bool:
    """마지막 진행(워커 결과 완료) 이후 timeout_sec를 초과했으면 True.

    워커가 죽어서 응답이 없는 상황을 감지하기 위한 순수 판정 함수."""
    return (now - last_progress_time) > timeout_sec


def _wrap_condition(block: dict, base_group: dict | None, combinator: str) -> dict:
    """block(단일 ConditionBlock)을 실행 가능한 ConditionGroup으로 감싼다.

    base_group이 None이면 기존과 동일하게 {"type": "AND", "conditions": [block]}로
    감싼다. base_group이 있으면(체이닝) combinator로 베이스와 block을 함께 묶는다 —
    이렇게 만들어진 트리를 그대로 저장해야 재체이닝 시 베이스 정보가 안 사라진다.

    손절/익절/보유기간(SELL_ONLY) 조건은 combinator와 무관하게 항상 OR로 묶는다 — AND로
    묶으면 "손절 조건이면서 동시에 새 조건도 참"이어야 청산되어 포지션 청산 안전장치가
    사실상 무력화되기 때문이다(최종 리뷰 Important #2). block이 이후 체이닝의 base_group으로
    다시 쓰이므로, 이 시점에 한 번 OR로 고정해두면 이후 라운드의 combinator 선택과 무관하게
    유지된다."""
    if block["indicator"] in SELL_ONLY:
        combinator = "OR"
    if base_group is None:
        return {"type": "AND", "conditions": [block]}
    return {"type": combinator, "conditions": [base_group, block]}


def _run_one_combo(
    df, risk_config: dict, buy_block: dict, sell_block: dict,
    base_buy_group: dict | None = None, base_sell_group: dict | None = None, combinator: str = "AND",
) -> dict:
    """조합 하나(매수 블록 1개 x 매도 블록 1개)에 대해 run_backtest를 1회 호출한다.

    순차 실행(compute_grid_results)과 병렬 워커(compute_grid_results_parallel) 양쪽에서
    공유하는 단일 진입점 — 조합당 실제로 무엇을 계산하는지는 여기 한 곳에만 있다.
    base_buy_group/base_sell_group이 주어지면(체이닝) 베이스 조건과 combinator로 묶는다.
    """
    buy_group = _wrap_condition(buy_block, base_buy_group, combinator)
    sell_group = _wrap_condition(sell_block, base_sell_group, combinator)
    result = run_backtest(
        df,
        ConditionTreeStrategy,
        risk_config,
        {"buy_conditions": buy_group, "sell_conditions": sell_group},
    )
    initial_capital = float(risk_config.get("initial_capital", 10000))
    return_pct = (result["final_value"] - initial_capital) / initial_capital * 100
    return {
        "return_pct": return_pct,
        "buy_block": buy_block,
        "sell_block": sell_block,
        "trades": result["trades"],
        "final_value": result["final_value"],
    }


_worker_df = None
_worker_risk_config: dict | None = None
_worker_base_buy_group: dict | None = None
_worker_base_sell_group: dict | None = None
_worker_combinator: str = "AND"


def _init_worker(
    df, risk_config: dict,
    base_buy_group: dict | None = None, base_sell_group: dict | None = None, combinator: str = "AND",
) -> None:
    """Pool 워커 프로세스가 (재)시작될 때마다 호출 — df/risk_config/베이스 조건을 워커
    전역에 저장해 태스크마다 재직렬화하지 않는다."""
    global _worker_df, _worker_risk_config, _worker_base_buy_group, _worker_base_sell_group, _worker_combinator
    _worker_df = df
    _worker_risk_config = risk_config
    _worker_base_buy_group = base_buy_group
    _worker_base_sell_group = base_sell_group
    _worker_combinator = combinator


def _run_one_combo_worker(buy_block: dict, sell_block: dict) -> dict:
    """Pool.apply_async에 전달되는 워커 측 진입점. 모듈 최상위 함수여야 Windows spawn에서
    pickle 가능하다."""
    return _run_one_combo(
        _worker_df, _worker_risk_config, buy_block, sell_block,
        _worker_base_buy_group, _worker_base_sell_group, _worker_combinator,
    )


def compute_grid_results(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
    base_buy_group: dict | None = None,
    base_sell_group: dict | None = None,
    combinator: str = "AND",
) -> list[dict]:
    """buy_conditions x sell_conditions 전 조합을 순차로 계산한다(테스트/소규모 실행용).

    대규모 실행(main())은 compute_grid_results_parallel을 쓴다.

    Returns:
        각 조합의 결과 딕셔너리 리스트: _run_one_combo와 동일한 shape.
    """
    results: list[dict] = []
    total = len(buy_conditions) * len(sell_conditions)

    for i, buy_block in enumerate(buy_conditions):
        for sell_block in sell_conditions:
            results.append(
                _run_one_combo(df, risk_config, buy_block, sell_block, base_buy_group, base_sell_group, combinator)
            )
        if (i + 1) % 5 == 0 or (i + 1) == len(buy_conditions):
            done = (i + 1) * len(sell_conditions)
            print(f"    매수조건 {i + 1}/{len(buy_conditions)} 완료 ({done}/{total}건)", flush=True)

    return results


def compute_grid_results_parallel(
    df,
    buy_conditions: list[dict],
    sell_conditions: list[dict],
    risk_config: dict,
    processes: int = WORKER_COUNT,
    max_tasks_per_child: int = MAX_TASKS_PER_CHILD,
    watchdog_timeout: float = WATCHDOG_TIMEOUT_SEC,
    base_buy_group: dict | None = None,
    base_sell_group: dict | None = None,
    combinator: str = "AND",
) -> list[dict]:
    """buy_conditions x sell_conditions 전 조합을 워커 풀로 병렬 계산한다(대규모 실행용).

    조합을 processes개 워커로 분산하는 것 자체가 워커 하나가 누적하는 backtrader 메모리를
    (전체 조합 수 / processes) 만큼으로 이미 제한한다 — 20,700개 조합/워커 4개 기준 워커당
    약 5,175회 호출로, 이는 max_tasks_per_child(11223)보다 작아 이번 규모에서는 재시작이
    실제로 일어나지 않는다. max_tasks_per_child는 그래도 필요한 안전장치다: 앞으로 그리드가
    더 커져서 워커 하나가 처리할 조합 수가 이 값을 넘어서면, 그 시점부터 자동 재시작이
    실제로 개입해 누적 메모리를 주기적으로 회수한다. 이 임계값 자체가 특정 측정치(83.57 KB/call)를
    전제로 하므로, 다른 데이터셋에서는 실제 워커당 누적량이 이 전제보다 클 수 있다는 점은 감안해야
    한다. 마지막 진행 이후 watchdog_timeout초간 응답이 없으면 워커가 죽어서 멈춘 것으로 보고
    중단한다.
    """
    combos = [(b, s) for b in buy_conditions for s in sell_conditions]
    total = len(combos)

    pool = multiprocessing.Pool(
        processes=processes,
        maxtasksperchild=max_tasks_per_child,
        initializer=_init_worker,
        initargs=(df, risk_config, base_buy_group, base_sell_group, combinator),
    )
    try:
        async_results = [pool.apply_async(_run_one_combo_worker, (b, s)) for b, s in combos]
        completed = [False] * total
        results: list[dict | None] = [None] * total
        done_count = 0
        last_logged = 0
        last_progress = time.monotonic()

        while done_count < total:
            progressed = False
            for i, ar in enumerate(async_results):
                if not completed[i] and ar.ready():
                    try:
                        results[i] = ar.get()
                    except Exception as exc:
                        buy_block, sell_block = combos[i]
                        raise RuntimeError(
                            f"조합 실패 (buy={buy_block}, sell={sell_block}): {exc}"
                        ) from exc
                    completed[i] = True
                    done_count += 1
                    progressed = True

            if progressed:
                last_progress = time.monotonic()
                if done_count - last_logged >= PROGRESS_LOG_INTERVAL or done_count == total:
                    pct = done_count / total * 100
                    print(f"    완료 {done_count:,}/{total:,}건 ({pct:.1f}%)", flush=True)
                    last_logged = done_count
            elif _watchdog_expired(last_progress, time.monotonic(), watchdog_timeout):
                raise RuntimeError(
                    f"워커 응답 없음 — {watchdog_timeout:.0f}초간 진행 없어 중단합니다. "
                    "일부 워커가 예기치 않게 종료됐을 수 있습니다."
                )

            if done_count < total:
                time.sleep(1)

        return results
    finally:
        pool.terminate()
        pool.join()


def _effective_period(params: dict) -> int:
    if "period" in params:
        return params["period"]
    if "k_period" in params:
        return params["k_period"]
    return params.get("fast", 0) + params.get("slow", 0) + params.get("signal", 0)


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
    parser = argparse.ArgumentParser(description="그리드서치 백테스트")
    parser.add_argument("--market", required=True, help="마켓코드 (예: KRW-ETH)")
    parser.add_argument("--timeframe", required=True, help="timeframe 코드 (예: minutes60)")
    parser.add_argument("--capital", required=True, type=float, help="운용자금(원)")
    parser.add_argument("--start", required=True, help="시작일 YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="종료일 YYYY-MM-DD")
    parser.add_argument("--top-n", type=_positive_int, default=20, help="저장할 상위 개수 (기본 20, 상한 50)")
    parser.add_argument(
        "--categories", default=None,
        help="콤마로 구분된 지표 카테고리 목록 (예: 오실레이터,추세). 미지정 시 오실레이터만.",
    )
    parser.add_argument(
        "--exclude-indicators", default=None,
        help="콤마로 구분된, 선택된 카테고리 안에서 제외할 개별 지표 키",
    )
    parser.add_argument("--base-run-id", default=None, help="체이닝 베이스로 삼을 결과의 run_id")
    parser.add_argument("--combinator", choices=["AND", "OR"], default=None, help="베이스 조건과 새 후보를 결합하는 연산자")
    args = parser.parse_args()

    if args.base_run_id and not args.combinator:
        raise SystemExit("--base-run-id를 주면 --combinator(AND 또는 OR)도 함께 지정해야 합니다.")

    base_buy_group: dict | None = None
    base_sell_group: dict | None = None
    if args.base_run_id:
        base_config = get_run_config(args.base_run_id)
        if base_config is None:
            raise SystemExit(f"베이스 결과를 찾을 수 없습니다(삭제되었을 수 있습니다): {args.base_run_id}")
        if base_config["buy_conditions"] is None or base_config["sell_conditions"] is None:
            raise SystemExit(
                f"베이스 결과에 매수/매도 조건이 없습니다(체이닝을 지원하지 않는 결과 유형일 수 있습니다): {args.base_run_id}"
            )
        base_buy_group = base_config["buy_conditions"]
        base_sell_group = base_config["sell_conditions"]

    top_n = min(args.top_n, 50)

    pool = None
    if args.categories:
        pool = {
            "categories": [c.strip() for c in args.categories.split(",") if c.strip()],
            "excluded_indicators": (
                [i.strip() for i in args.exclude_indicators.split(",") if i.strip()]
                if args.exclude_indicators else []
            ),
        }

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )

    buy_conditions, sell_conditions = build_condition_grid(pool, market=args.market)
    total_combos = len(buy_conditions) * len(sell_conditions)
    print(
        f"[2] 매수 조건 {len(buy_conditions)}개 x 매도 조건 {len(sell_conditions)}개 = 총 {total_combos:,}개 조합",
        flush=True,
    )

    print(f"[1] 캔들 조회: {args.market} {args.timeframe} {args.start} ~ {args.end}", flush=True)
    # 체이닝 시 베이스 조건(base_buy_group/base_sell_group)에 등장하는 지표도 감지용
    # 트리에 포함해야 한다 — 그래야 _fetch_backtest_dataframe가 베이스가 요구하는 보조
    # 데이터(btc_close/fear_greed_value 등)를 함께 병합한다(최종 리뷰 Critical #1).
    detect_buy_group = {"type": "AND", "conditions": buy_conditions + ([base_buy_group] if base_buy_group else [])}
    detect_sell_group = {"type": "AND", "conditions": sell_conditions + ([base_sell_group] if base_sell_group else [])}
    try:
        df = _fetch_backtest_dataframe(args.market, args.timeframe, start_dt, end_dt, detect_buy_group, detect_sell_group)
    except HTTPException as exc:
        raise SystemExit(f"캔들/보조 데이터 조회 실패: {exc.detail}") from exc
    print(f"    캔들 수: {len(df)}", flush=True)

    _check_candle_warmup(df, buy_conditions, sell_conditions, base_buy_group, base_sell_group)

    risk_config = {**DEFAULT_RISK_CONFIG, "initial_capital": args.capital}

    t0 = time.perf_counter()
    results = compute_grid_results_parallel(
        df, buy_conditions, sell_conditions, risk_config,
        base_buy_group=base_buy_group, base_sell_group=base_sell_group,
        combinator=args.combinator or "AND",
    )
    elapsed = time.perf_counter() - t0
    print(f"\n[3] 전체 계산 완료: {len(results)}건, {elapsed:.1f}초 ({elapsed / 60:.1f}분)", flush=True)

    top_results = dedup_top_results(results, top_n)
    print(f"\n[4] dedup 후 상위 {len(top_results)}개를 백테스트 결과에 저장 중...", flush=True)

    saved_summaries = []
    for rank, r in enumerate(top_results, start=1):
        buy_block, sell_block = r["buy_block"], r["sell_block"]
        buy_group = _wrap_condition(buy_block, base_buy_group, args.combinator or "AND")
        sell_group = _wrap_condition(sell_block, base_sell_group, args.combinator or "AND")
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
        trades = saved["trades"]
        win_rate_pct = (
            round(sum(1 for t in trades if t["pnl"] > 0) / len(trades) * 100, 2) if trades else None
        )
        print(f"  {rank:2d}. {r['return_pct']:+.2f}%  run_id={saved['run_id'][:12]}...", flush=True)
        saved_summaries.append({
            "rank": rank,
            "run_id": saved["run_id"],
            "return_pct": round(r["return_pct"], 2),
            "title": title,
            "trade_count": len(trades),
            "candle_count": saved["candle_count"],
            "max_drawdown_pct": saved.get("max_drawdown"),
            "win_rate_pct": win_rate_pct,
        })

    result_json = {"total_combos": total_combos, "elapsed_sec": round(elapsed, 1), "saved": saved_summaries}
    print("\n완료.")
    print(f"RESULT_JSON: {json.dumps(result_json, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
