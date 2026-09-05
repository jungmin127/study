"""
engine/regime_ml_hmm.py

장세 판별 ML의 HMM 상태 피처. 로그수익률+변동성 2변수로 마켓별 Gaussian HMM을
학습해 잠재 상태 확률을 만든다 — 기존 롤링 피처(ATR 등)와 달리 파라미터를 EM으로
학습하는 모델이라, fit()과 score(추론)를 분리해 제공한다: 워크포워드 fold의 train
구간에서만 fit_hmm()을 호출하고(미래 정보 유출 방지), 학습된 모델로
score_hmm_state_probabilities()를 train/test 각각에 대해 호출해야 한다. 이 모듈은
fold 경계를 모르므로(순수 함수), fold 루프는 scripts/train_regime_ml.py가 담당한다.

알려진 한계: score_hmm_state_probabilities()는 hmmlearn의 기본 predict_proba()
(순방향+역방향 스무딩)를 쓴다 — test 구간 "안에서" 미래 시점이 과거 시점의 상태확률
추정을 살짝 도와주는 약한 형태의 정보유출이 있다(모델 파라미터 자체는 train에서만
학습되므로 train->test 누출은 없음). 완전한 실시간 인과적 필터링이 필요하면
hmmlearn의 저수준 forward-pass를 직접 호출해야 한다(비범위, 설계 문서 참고).

설계 문서: docs/superpowers/specs/2026-08-30-regime-ml-hmm-feature-design.md
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM

N_STATES = 3
HMM_STATE_COLUMNS = [f"HMM_STATE_{i}" for i in range(N_STATES)]
_RANDOM_STATE = 42


def build_hmm_observations(df: pd.DataFrame, half_life_bars: float) -> pd.DataFrame:
    """df: close 컬럼을 포함해야 한다. 반환: returns/volatility 2컬럼 DataFrame(df와
    같은 인덱스). 앞부분(워밍업)은 NaN — pct_change 첫 행 + EWM std 초기 구간."""
    returns = df["close"].pct_change(fill_method=None)
    volatility = returns.ewm(halflife=half_life_bars).std()
    return pd.DataFrame({"returns": returns, "volatility": volatility}, index=df.index)


def fit_hmm(observations: pd.DataFrame, n_states: int = N_STATES, random_state: int = _RANDOM_STATE) -> GaussianHMM:
    """observations: build_hmm_observations()가 만든 2컬럼 DataFrame에서 NaN 행을
    제거한 것이어야 한다(호출자 책임). 워크포워드 fold의 train 구간에서만 호출해야
    한다 — test 구간을 섞어 fit하면 미래 정보가 파라미터에 스며든다."""
    model = GaussianHMM(n_components=n_states, covariance_type="diag", n_iter=100, random_state=random_state)
    model.fit(observations.to_numpy())
    return model


def score_hmm_state_probabilities(model: GaussianHMM, observations: pd.DataFrame) -> pd.DataFrame:
    """학습된(고정된) model로 observations 구간 전체의 상태확률을 추론한다(model을
    다시 fit하지 않음). NaN 행(워밍업 구간)은 그대로 NaN으로 남긴다. 반환:
    HMM_STATE_COLUMNS 컬럼, observations와 같은 인덱스."""
    valid = observations.notna().all(axis=1)
    result = pd.DataFrame(np.nan, index=observations.index, columns=HMM_STATE_COLUMNS)
    if valid.any():
        result.loc[valid, HMM_STATE_COLUMNS] = model.predict_proba(observations.loc[valid].to_numpy())
    return result


def compute_dominant_state(
    df: pd.DataFrame, half_life_bars: float, n_states: int = N_STATES, random_state: int = _RANDOM_STATE
) -> pd.Series:
    """df 전체 구간에서 한 번 fit한 뒤(사후 분석 전용 — 워크포워드 fold 분리 없음,
    scripts/analyze_regime_hmm_fact_performance.py가 유일한 호출자) 바별
    지배적 상태(상태확률 argmax)를 반환한다. 반환: df와 같은 인덱스의
    pd.Series(float64) — 유효한 바는 0.0~n_states-1.0, 워밍업 구간은 NaN."""
    observations = build_hmm_observations(df, half_life_bars)
    valid_observations = observations.dropna()
    model = fit_hmm(valid_observations, n_states=n_states, random_state=random_state)
    probabilities = score_hmm_state_probabilities(model, observations)

    states = pd.Series(np.nan, index=probabilities.index, dtype="float64")
    valid_rows = probabilities.notna().all(axis=1)
    states.loc[valid_rows] = probabilities.loc[valid_rows].to_numpy().argmax(axis=1).astype("float64")
    return states
