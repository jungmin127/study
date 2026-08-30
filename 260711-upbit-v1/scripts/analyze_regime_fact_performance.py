"""
scripts/analyze_regime_fact_performance.py

장세 fact 라벨(compute_triple_barrier_labels) 기준으로, 저장된 BTC/XLM minutes60
백테스트 결과의 거래를 진입 시점 라벨로 재분류해 "장세별로 전략 성과가 실제로
갈리는가"를 확인한다. 새 백테스트는 돌리지 않고 engine.cache에 이미 저장된 결과만
재분석한다. 설계 문서:
docs/superpowers/specs/2026-08-30-regime-fact-label-backtest-analysis-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/analyze_regime_fact_performance.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from engine.cache import list_backtest_runs
from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_labels import compute_triple_barrier_labels
from upbit_data_service import get_candles

TIMEFRAME = "minutes60"
MARKETS = ["KRW-BTC", "KRW-XLM"]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc)
# scripts/train_regime_ml.py:BARRIER_K와 동일 값(2026-08-29 select_barrier_k.py로 결정,
# 프로덕션 학습 파이프라인이 실제로 쓰는 값). 이 스크립트는 학습 모듈을 import하지
# 않으므로 값만 복제한다.
BARRIER_K = 6.25
MIN_TRADES_FOR_RANKING = 5
TOP_N = 10


def build_label_lookup(market: str) -> pd.Series:
    """market의 minutes60 fact 라벨 시계열을, tz를 제거한 naive UTC candle_time을
    인덱스로 반환한다. tz를 제거하는 이유: 저장된 거래의 entryTime은
    engine/runner.py가 backtrader에 넘기기 전 tz를 벗겨낸 naive datetime의
    isoformat이라(원래 tz-aware UTC였던 값을 wall-clock 그대로 넘김), 같은 기준으로
    맞춰야 매칭된다."""
    df = get_candles(market, TIMEFRAME, START, END)
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    labels = compute_triple_barrier_labels(df, half_life_bars, n_bars, BARRIER_K)
    candle_time = pd.to_datetime(df["candle_time"]).dt.tz_localize(None)
    return pd.Series(labels.to_numpy(), index=candle_time).sort_index()


def label_for_entry(lookup: pd.Series, entry_time_str: str) -> object:
    """entryTime(ISO 문자열)에 가장 가까운 과거 캔들의 라벨을 반환한다. lookup 시작
    시각보다 이전이면 None(있을 수 없지만 방어적으로), 그 외엔 라벨 값(문자열 또는
    NaN)을 그대로 반환한다."""
    entry_time = pd.to_datetime(entry_time_str)
    pos = lookup.index.searchsorted(entry_time, side="right") - 1
    if pos < 0:
        return None
    return lookup.iloc[pos]


def load_labeled_trades(lookup_by_market: dict[str, pd.Series]) -> list[dict]:
    """저장된 BTC/XLM minutes60 백테스트 결과의 모든 거래를 진입 시점 라벨과 함께
    평평한 행 리스트로 반환한다. 라벨이 없는(NaN 또는 매칭 실패) 거래는 제외하고
    제외 건수를 출력한다."""
    rows: list[dict] = []
    excluded = 0
    for market in MARKETS:
        lookup = lookup_by_market[market]
        runs = [r for r in list_backtest_runs(market=market) if r["timeframe"] == TIMEFRAME]
        for run in runs:
            for trade in run["trades"]:
                label = label_for_entry(lookup, trade["entryTime"])
                if label is None or (isinstance(label, float) and pd.isna(label)):
                    excluded += 1
                    continue
                rows.append({
                    "run_id": run["run_id"],
                    "title": run["title"] or "(제목없음)",
                    "market": market,
                    "label": label,
                    "return_rate": trade["returnRate"],
                })
    print(f"라벨 없음으로 제외된 거래: {excluded}건")
    return rows


def print_pooled_comparison(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    print("\n=== 전체 풀링 비교 ===")
    print(f"{'라벨':>8} | {'거래수':>6} | {'승률':>7} | {'평균수익률':>10} | {'총수익기여':>10}")
    for label, group in df.groupby("label"):
        win_rate = (group["return_rate"] > 0).mean() * 100
        avg_return = group["return_rate"].mean()
        total_return = group["return_rate"].sum()
        print(f"{label:>8} | {len(group):>6} | {win_rate:6.1f}% | {avg_return:9.2f}% | {total_return:9.1f}%")


def print_run_ranking(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    categories = sorted(df["label"].unique())

    per_run: dict[str, dict] = {}
    for (run_id, title, market), group in df.groupby(["run_id", "title", "market"]):
        stats = {"run_id": run_id, "title": title, "market": market}
        for label in categories:
            sub = group[group["label"] == label]
            stats[f"{label}_n"] = len(sub)
            stats[f"{label}_avg"] = sub["return_rate"].mean() if len(sub) else float("nan")
        per_run[run_id] = stats
    per_run_df = pd.DataFrame(per_run.values())

    rankings: dict[str, set] = {}
    for label in categories:
        eligible = per_run_df[per_run_df[f"{label}_n"] >= MIN_TRADES_FOR_RANKING]
        excluded_low_sample = per_run_df[per_run_df[f"{label}_n"] < MIN_TRADES_FOR_RANKING]
        ranked = eligible.sort_values(f"{label}_avg", ascending=False).head(TOP_N)
        rankings[label] = set(ranked["run_id"])
        print(f"\n=== '{label}' 진입 거래 평균수익률 상위 {TOP_N} (최소 {MIN_TRADES_FOR_RANKING}건) ===")
        for _, row in ranked.iterrows():
            print(f"  {row['market']:>8} | {row[f'{label}_avg']:7.2f}% ({int(row[f'{label}_n'])}건) | {row['title']}")
        print(f"  (표본 부족으로 랭킹 제외된 run: {len(excluded_low_sample)}개)")

    if len(categories) == 2:
        a, b = categories
        overlap = rankings[a] & rankings[b]
        print(f"\n'{a}' 상위 {TOP_N}와 '{b}' 상위 {TOP_N}의 겹치는 전략: {len(overlap)}개")


def main() -> None:
    lookup_by_market = {market: build_label_lookup(market) for market in MARKETS}
    rows = load_labeled_trades(lookup_by_market)
    print_pooled_comparison(rows)
    print_run_ranking(rows)


if __name__ == "__main__":
    main()
