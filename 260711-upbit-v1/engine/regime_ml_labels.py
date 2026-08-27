"""
engine/regime_ml_labels.py

장세 판별 ML 분류기의 레이블(정답 카테고리)을 만든다. 정규화 실현수익률(다음 n_bars
평균수익률을 이후 EWM변동성으로 정규화한 값)은 과거 규칙기반 판별기(E 작업으로
2026-08-28 삭제됨)가 쓰던 것과 같은 정규화 방식이다 — 카테고리 경계만 고정값이 아니라
fold별 훈련구간 분위수로 계산한다는 점이 다르다. 설계 문서:
docs/superpowers/specs/2026-08-27-regime-detector-ml-classifier-design.md
"""
from __future__ import annotations

import pandas as pd

from engine.regime_math import ewm_volatility

CATEGORY_LABELS: list[str] = ["급하락", "완만하락", "횡보", "완만상승", "급상승"]


def compute_normalized_realized_series(
    df: pd.DataFrame, half_life_bars: float, n_bars: int
) -> pd.Series:
    """df["close"] 기준 각 시점 t에서 "다음 n_bars 평균수익률 / 이후 EWM변동성"을 계산한다.
    미래 데이터가 부족한 마지막 n_bars 구간, 또는 구간 내 결측이 있으면 NaN."""
    returns = df["close"].pct_change(fill_method=None)
    values: list[float] = [float("nan")] * len(df)
    for t in range(max(len(df) - n_bars, 0)):
        future_returns = returns.iloc[t + 1 : t + 1 + n_bars]
        if future_returns.empty or future_returns.isna().any():
            continue
        realized_volatility = ewm_volatility(future_returns, half_life_bars)
        if realized_volatility <= 0:
            continue
        values[t] = future_returns.mean() / realized_volatility
    return pd.Series(values, index=df.index)


def compute_quantile_boundaries(
    values: pd.Series, quantiles: tuple[float, ...] = (0.02, 0.16, 0.84, 0.98)
) -> list[float]:
    """values(NaN 제외)에서 quantiles에 해당하는 경계값을 오름차순으로 반환한다."""
    clean = values.dropna()
    if clean.empty:
        raise ValueError("경계값을 계산할 표본이 없습니다")
    return [float(clean.quantile(q)) for q in quantiles]


def bucket_to_category(value: float, boundaries: list[float]) -> str:
    """boundaries(오름차순 4개)를 기준으로 value를 5개 카테고리 중 하나로 분류한다.
    engine.regime_detector.classify_score_to_category와 같은 "미만이면 그 카테고리"
    규칙을 쓴다."""
    for label, boundary in zip(CATEGORY_LABELS[:-1], boundaries):
        if value < boundary:
            return label
    return CATEGORY_LABELS[-1]


def category_representative_scores(
    values: pd.Series, boundaries: list[float]
) -> dict[str, float]:
    """각 카테고리 구간에 속한 values의 중앙값을 대표값으로 반환한다(회귀 상관계수 계산용
    expected_score 산출에 씀). 구간에 표본이 하나도 없으면(fold 초반 등) 양끝 카테고리는
    해당 경계값, 중간 카테고리는 인접 경계값의 중점으로 대체한다."""
    clean = values.dropna()
    labels_per_value = clean.apply(lambda v: bucket_to_category(v, boundaries))

    result: dict[str, float] = {}
    for i, label in enumerate(CATEGORY_LABELS):
        bucket_values = clean[labels_per_value == label]
        if not bucket_values.empty:
            result[label] = float(bucket_values.median())
        elif i == 0:
            result[label] = boundaries[0]
        elif i == len(CATEGORY_LABELS) - 1:
            result[label] = boundaries[-1]
        else:
            result[label] = (boundaries[i - 1] + boundaries[i]) / 2
    return result
