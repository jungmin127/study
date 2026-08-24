"""
engine/regime_features.py

장세 판별기(engine/regime_detector.py)가 쓰는 보조 신호 — 거래량 확인, VPIN 매수/매도
불균형, 지지/저항 근접도. 전부 순수 pandas 함수(backtrader 의존 없음, I/O 없음)라
백테스트/그리드서치/라이브 데몬 어디서든 재사용 가능하다. 설계 문서:
docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md

engine/indicators/volume.py, price_levels.py의 backtrader 지표(Cerebro 전략 객체 모델
안에서만 동작)와 동일한 계산 로직을 pandas Series 기반으로 재구현한다 — regime_detector가
Cerebro 없이 순수 DataFrame만으로 호출돼야 하므로 기존 지표 클래스를 그대로 재사용할 수
없다.
"""
from __future__ import annotations

import statistics
from collections import deque

import numpy as np
import pandas as pd


def volume_confirm(trade_value: pd.Series, period: int = 20) -> pd.Series:
    """거래대금이 자체 이동평균(period봉) 대비 얼마나 실렸는지를 [0.7, 1.3] 배율로
    변환한다. engine/indicators/volume.py:111-124(TradeValueRatio)와 동일한 정의를
    pandas로 재구현. 방향(상승/하락) 무관 — 평균보다 거래대금이 실린 봉이면 모멘텀
    점수를 증폭, 안 실렸으면 감쇠시키는 용도."""
    sma = trade_value.rolling(period).mean()
    ratio = (trade_value - sma) / sma.replace(0.0, np.nan)
    ratio = ratio.fillna(0.0)
    return 1.0 + ratio.clip(-0.3, 0.3)


def pivot_levels(high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[pd.Series, pd.Series]:
    """직전 봉 고가/저가/종가로 계산하는 Pivot Point 저항선(R1)/지지선(S1).
    engine/indicators/price_levels.py:35-51(PivotPoints)와 동일한 정의를 shift(1)로
    벡터화한다."""
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)
    pivot = (prev_high + prev_low + prev_close) / 3.0
    r1 = pivot * 2 - prev_low
    s1 = pivot * 2 - prev_high
    return r1, s1


def vpin_score(volume: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    """거래량 버킷(volume bar) 기반 VPIN(매수/매도 불균형 비율). Bulk Volume
    Classification(Easley/López de Prado/O'Hara, 2012). engine/indicators/volume.py:131-199
    (VolumeBarVPIN)과 동일한 알고리즘을 backtrader Cerebro 없이 순수 파이썬 루프로
    재구현한다 — 버킷 경계가 봉 수가 아니라 누적거래량 기준이라 고정폭 rolling으로는
    벡터화할 수 없다(버킷 하나가 몇 봉으로 구성될지 데이터에 따라 달라짐).

    반환값: [0, 1], 매수/매도 쏠림이 클수록 1에 가까움. 버킷이 period개 쌓이기 전(워밍업
    구간)은 NaN."""
    n = len(volume)
    result = [float("nan")] * n
    recent_volumes: deque[float] = deque(maxlen=period)
    bucket_cum_volume = 0.0
    last_bucket_close: float | None = None
    bucket_deltas: deque[float] = deque(maxlen=period)
    bucket_imbalance_ratios: deque[float] = deque(maxlen=period)

    for i in range(n):
        v = float(volume.iloc[i])
        recent_volumes.append(v)
        bucket_cum_volume += v

        target = statistics.mean(recent_volumes) if len(recent_volumes) == period else None
        if target is not None and bucket_cum_volume >= target:
            bucket_close = float(close.iloc[i])
            bucket_volume = bucket_cum_volume
            if last_bucket_close is not None:
                delta = bucket_close - last_bucket_close
                bucket_deltas.append(delta)
                sigma = statistics.stdev(bucket_deltas) if len(bucket_deltas) >= 2 else 0.0
                z = delta / sigma if sigma > 0 else 0.0
                buy_ratio = statistics.NormalDist().cdf(z)
                buy_volume = bucket_volume * buy_ratio
                sell_volume = bucket_volume - buy_volume
                imbalance_ratio = abs(buy_volume - sell_volume) / bucket_volume if bucket_volume else 0.0
                bucket_imbalance_ratios.append(imbalance_ratio)
            last_bucket_close = bucket_close
            bucket_cum_volume = 0.0

        if len(bucket_imbalance_ratios) == period:
            result[i] = statistics.mean(bucket_imbalance_ratios)

    return pd.Series(result, index=volume.index)
