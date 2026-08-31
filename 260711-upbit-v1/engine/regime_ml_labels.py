"""
engine/regime_ml_labels.py

장세 판별 ML 분류기의 레이블(정답 카테고리)을 만든다. Triple Barrier Method(Marcos
Lopez de Prado) — 상단 익절선/하단 손절선/만기 중 무엇이 먼저 터치되는지로 라벨을
정한다. 이전의 "다음 n_bars 평균수익률을 fold별 훈련구간 분위수로 나누는" 방식은
fold마다 카테고리 경계가 달라지는 불안정성이 있어 폐기했다(2026-08-27 도입,
2026-08-29 문제 재정의에서 교체). 설계 문서:
docs/superpowers/specs/2026-08-29-regime-ml-problem-redefinition-design.md

2026-08-30 이진분류 전환: 3단계(하락/횡보/상승)는 walk-forward 실측(pooled weighted
kappa)에서 이진분류(하락 vs 하락아님)보다 일관되게 낮았다(0.072 vs 0.0914, 14마켓
기준) — 상단/하단 경계는 그대로 Triple Barrier로 계산하되, 상승/횡보를 "하락아님"
하나로 합쳐 반환한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CATEGORY_LABELS: list[str] = ["하락", "하락아님"]


def compute_triple_barrier_labels(
    df: pd.DataFrame,
    half_life_bars: float,
    n_bars: int,
    k: float,
    candle_time: pd.Series | None = None,
) -> pd.Series:
    """각 시점 t에서 상단(+k*vol_t)/하단(-k*vol_t) 경계와 n_bars 만기 중 무엇이
    먼저 터치되는지로 라벨링한다. vol_t는 t까지의 과거 수익률만으로 계산한
    EWM 변동성이라(pandas ewm은 인과적) 추론 시점에도 동일하게 재현 가능하다.
    두 경계가 같은 봉에서 동시에 터치되는 경우는 없다(k>0이면 상단·하단이
    서로 반대 부호). 하단이 먼저(또는 유일하게) 터치되면 "하락", 상단이 먼저
    터치되거나 만기까지 어느 쪽도 안 터치되면(횡보) "하락아님"이다. 반환:
    CATEGORY_LABELS 값 또는 NaN(미래 데이터 부족)으로 이뤄진 object Series,
    df와 같은 길이/인덱스.
    vol_t는 t-1까지의 수익률만으로 계산한다(.shift(1)) — t 시점 자신의 수익률까지
    포함하면 급락이 일어난 바로 그 봉에서 vol이 급등해 barrier가 넓어지고, 그 결과
    "이미 크게 빠진 봉"이 역설적으로 "하락아님"으로 라벨링되는 문제가 있었다
    (docs/regime-ml-backlog.md 기술부채 항목, 2026-08-31 KRW-SHIB 실측으로 확인).

    candle_time: 제공하면(캔들 시각 Series, df와 같은 인덱스) 라벨 i의 미래
    윈도우가 실제로 걸치는 경과시간이 n_bars * (candle_time 간 최빈 간격)의
    1.5배를 넘는 경우(=캔들 결측 구간을 걸침) 그 라벨을 NaN 처리한다. None이면
    (기본값) 이 검사를 생략해 기존 동작과 완전히 동일하다."""
    returns = df["close"].pct_change(fill_method=None)
    volatility = returns.ewm(halflife=half_life_bars).std().shift(1)
    close = df["close"].to_numpy()
    n = len(df)

    labels: list[object] = [float("nan")] * n
    for t in range(max(n - n_bars, 0)):
        vol_t = volatility.iloc[t]
        if pd.isna(vol_t) or vol_t <= 0:
            continue
        upper = k * vol_t
        lower = -k * vol_t
        entry = close[t]
        future = close[t + 1 : t + 1 + n_bars] / entry - 1.0
        up_hits = np.flatnonzero(future >= upper)
        down_hits = np.flatnonzero(future <= lower)
        up_first = up_hits[0] if up_hits.size else None
        down_first = down_hits[0] if down_hits.size else None
        if up_first is not None and (down_first is None or up_first <= down_first):
            labels[t] = "하락아님"
        elif down_first is not None:
            labels[t] = "하락"
        else:
            labels[t] = "하락아님"
    result = pd.Series(labels, index=df.index)
    if candle_time is not None:
        result = _mask_labels_spanning_gaps(result, candle_time, n_bars)
    return result


def _mask_labels_spanning_gaps(labels: pd.Series, candle_time: pd.Series, n_bars: int) -> pd.Series:
    """candle_time 간 최빈 간격(median)을 "정상 간격"으로 추정하고, 라벨 i의
    미래 윈도우 [i+1, i+1+n_bars](행 기준)가 실제로 걸치는 경과시간이
    n_bars*정상간격*1.5를 넘으면 그 라벨을 NaN으로 덮어쓴다."""
    intervals = candle_time.diff().dropna()
    if intervals.empty:
        return labels
    normal_interval = intervals.median()
    threshold = normal_interval * n_bars * 1.5

    n = len(labels)
    masked = labels.copy()
    for i in range(n):
        if pd.isna(masked.iloc[i]):
            continue
        window_end = min(i + n_bars, n - 1)
        elapsed = candle_time.iloc[window_end] - candle_time.iloc[i]
        if elapsed > threshold:
            masked.iloc[i] = float("nan")
    return masked


def compute_sample_uniqueness_weights(labels: pd.Series, n_bars: int) -> pd.Series:
    """AFML(López de Prado)의 sample uniqueness 가중치. 라벨 i의 활성구간은
    [i, i+n_bars](Triple Barrier가 최대 n_bars 앞을 내다보므로)다. 각 시점 t에서
    동시에 활성인 라벨 개수 c_t를 구한 뒤, 라벨 i의 가중치 = i의 활성구간에 속한
    모든 t에 대한 1/c_t의 평균이다 — 겹치는 라벨이 많을수록(=서로 독립적이지
    않을수록) 가중치가 작아져 LightGBM이 그 구간을 과도하게 반복학습하지 않게
    한다. class_weight="balanced"와는 별개 축이라 sample_weight로 곱해서 함께
    쓴다(scripts/train_regime_ml.py 참고). NaN 라벨은 애초에 학습에 안 쓰이므로
    동시활성 카운트에도 안 넣고, 반환값도 NaN으로 남긴다."""
    active = labels.notna().astype(float)
    # c_t = t를 활성구간에 포함하는 라벨 개수 = sum(active[i] for i in [t-n_bars, t])
    # (라벨 i의 구간이 [i, i+n_bars]이므로 t를 포함하려면 t-n_bars <= i <= t).
    concurrency = active.rolling(window=n_bars + 1, min_periods=1).sum()
    with np.errstate(divide="ignore"):
        inverse_concurrency = 1.0 / concurrency
    # 라벨 i의 가중치 = t in [i, i+n_bars] 구간에 대한 1/c_t의 평균(전방 롤링) ->
    # 역순으로 뒤집어 trailing rolling mean을 적용한 뒤 다시 뒤집는 표준 트릭.
    forward_mean = inverse_concurrency[::-1].rolling(window=n_bars + 1, min_periods=1).mean()[::-1]
    return forward_mean.where(labels.notna())
