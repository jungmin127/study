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
    df: pd.DataFrame, half_life_bars: float, n_bars: int, k: float
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
    (docs/regime-ml-backlog.md 기술부채 항목, 2026-08-31 KRW-SHIB 실측으로 확인)."""
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
    return pd.Series(labels, index=df.index)
