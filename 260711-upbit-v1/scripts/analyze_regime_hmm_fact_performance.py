"""
scripts/analyze_regime_hmm_fact_performance.py

HMM 비지도 클러스터링으로 뽑아낸 잠재 상태가 "장세별 실전 매매 성과 차이"와
상관이 있는지 확인한다. scripts/analyze_regime_fact_performance.py(Triple
Barrier fact 라벨 버전)와 동일한 방법론(저장된 백테스트 거래를 진입 시점
라벨로 재분류)을 그대로 재사용하되, 라벨 소스만 HMM 상태로 바꾼다. 새
백테스트는 돌리지 않고 engine.cache에 이미 저장된 결과만 재분석한다. 설계
문서: docs/superpowers/specs/2026-09-05-regime-hmm-unsupervised-clustering-design.md

label_for_entry/load_labeled_trades/print_pooled_comparison/print_run_ranking은
라벨 소스(문자열 Triple Barrier 라벨 vs 정수 HMM 상태)에 무관한 순수 로직이라
analyze_regime_fact_performance에서 그대로 재사용한다(중복 없음). MARKETS/
TIMEFRAME/START/END도 같은 마켓·기간을 봐야 두 결과가 비교 가능하므로 같은
값을 그대로 가져온다.

주의(전역 조회 함정): load_labeled_trades(및 그 내부에서 쓰는 label_for_entry)는
이 스크립트가 아니라 **정의부 모듈(scripts.analyze_regime_fact_performance)
자신의 전역** MARKETS/TIMEFRAME/list_backtest_runs를 참조한다(파이썬 클로저
규칙 — 호출자의 지역/전역이 아니라 함수가 정의된 모듈의 전역을 본다). 따라서
synthetic 데이터로 마켓 목록을 줄이는 등 이 값들을 패치하려면 이 스크립트
모듈과 scripts.analyze_regime_fact_performance 모듈 둘 다 패치해야 한다
(계획 문서 Task 2 Step 2 참고).

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/analyze_regime_hmm_fact_performance.py
"""
from __future__ import annotations

import pandas as pd

from engine.regime_math import half_life_bars_for_timeframe
from engine.regime_ml_hmm import N_STATES, build_hmm_observations, compute_dominant_state
from scripts.analyze_regime_fact_performance import (
    END,
    MARKETS,
    START,
    TIMEFRAME,
    load_labeled_trades,
    print_pooled_comparison,
    print_run_ranking,
)
from upbit_data_service import get_candles

RANDOM_STATE = 42


def build_hmm_state_lookup(market: str) -> tuple[pd.Series, pd.DataFrame]:
    """market의 minutes60 HMM 상태 시계열을 만든다. Triple Barrier fact 라벨과
    동일한 사후(ex-post) 분석 목적이라 전체 기간을 한 번에 fit한다(워크포워드
    아님 — 설계 문서 "A. HMM 모델링" 참고). 반환: (state_lookup —
    label_for_entry가 기대하는 naive UTC candle_time 인덱스의 상태 Series,
    값은 Python int 또는 NaN(워밍업 구간); profile_df — 상태별 평균 수익률/
    변동성, 상태 정수의 의미를 사후 해석하기 위한 참고용)."""
    df = get_candles(market, TIMEFRAME, START, END)
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    observations = build_hmm_observations(df, half_life_bars)
    states = compute_dominant_state(df, half_life_bars, n_states=N_STATES, random_state=RANDOM_STATE)

    candle_time = pd.to_datetime(df["candle_time"]).dt.tz_localize(None)
    # label_for_entry의 NaN 판정(`isinstance(label, float) and pd.isna(label)`)이
    # 유효 상태값과 NaN을 정확히 구분하도록, 유효 상태는 Python int로(float가
    # 아니므로 NaN 판정에 안 걸림), 워밍업 구간만 float('nan')으로 남긴다.
    state_values = [int(v) if pd.notna(v) else float("nan") for v in states.to_numpy()]
    state_lookup = pd.Series(state_values, index=candle_time, dtype="object").sort_index()

    profile_df = (
        observations.assign(state=states.to_numpy())
        .dropna(subset=["state"])
        .groupby("state")[["returns", "volatility"]]
        .mean()
    )
    return state_lookup, profile_df


def print_state_profile(profiles_by_market: dict[str, pd.DataFrame]) -> None:
    """상태 정수(0/1/2)는 비지도 학습 결과라 사전에 정해진 의미가 없다 —
    상태별 평균 수익률/변동성을 같이 보여줘 사후에 "이 상태가 상승/하락/횡보
    중 뭐에 가까운지" 해석할 수 있게 한다."""
    print("=== 마켓별 HMM 상태 프로파일 (평균 수익률/변동성) ===")
    for market, profile in profiles_by_market.items():
        print(f"\n{market}:")
        for state, row in profile.iterrows():
            print(f"  state {int(state)}: 평균 수익률={row['returns']:+.5f}  평균 변동성={row['volatility']:.5f}")


def print_per_market_comparison(rows: list[dict]) -> None:
    """풀링 비교(print_pooled_comparison)는 상태 정수만으로 그룹핑해 마켓별로
    독립적으로 학습된 HMM의 상태 번호가 서로 대응한다고 암묵적으로 가정한다 —
    하지만 각 마켓의 HMM은 별도로 fit되므로 "state 0"이 두 마켓에서 같은
    장세를 가리킨다는 보장이 없다(print_state_profile의 상태 프로파일 표로
    사후에만 확인 가능). 이 함수는 (market, state) 조합별로 승률/평균수익률/
    총수익기여/거래수를 따로 보여줘 풀링 결과가 마켓 간 우연한 뒤섞임이
    아닌지 판단할 수 있게 한다."""
    df = pd.DataFrame(rows)
    print("\n=== 마켓별 상태 비교 ===")
    print(f"{'마켓':>8} | {'state':>5} | {'거래수':>6} | {'승률':>7} | {'평균수익률':>10} | {'총수익기여':>10}")
    for (market, label), group in df.groupby(["market", "label"]):
        win_rate = (group["return_rate"] > 0).mean() * 100
        avg_return = group["return_rate"].mean()
        total_return = group["return_rate"].sum()
        print(
            f"{market:>8} | {label:>5} | {len(group):>6} | {win_rate:6.1f}% | "
            f"{avg_return:9.2f}% | {total_return:9.1f}%"
        )


def main() -> None:
    lookup_by_market: dict[str, pd.Series] = {}
    profiles_by_market: dict[str, pd.DataFrame] = {}
    for market in MARKETS:
        lookup, profile = build_hmm_state_lookup(market)
        lookup_by_market[market] = lookup
        profiles_by_market[market] = profile

    print_state_profile(profiles_by_market)
    rows = load_labeled_trades(lookup_by_market)
    print_pooled_comparison(rows)
    print_per_market_comparison(rows)
    print_run_ranking(rows)


if __name__ == "__main__":
    main()
