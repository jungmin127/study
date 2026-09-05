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
    label_for_entry,
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
    print_run_ranking(rows)


if __name__ == "__main__":
    main()
