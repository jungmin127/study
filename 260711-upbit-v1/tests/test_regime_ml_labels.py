"""
tests/test_regime_ml_labels.py

engine.regime_ml_labels.compute_triple_barrier_labels()를 검증한다. Triple
Barrier Method — 상단/하단 경계 중 어느 쪽이 먼저 터치되는지, 둘 다 안
터치되면 만기(횡보), 미래 데이터가 부족하면 NaN인지 확인한다.
"""
from __future__ import annotations

import pandas as pd

from engine.regime_ml_labels import CATEGORY_LABELS, compute_triple_barrier_labels

_HALF_LIFE_BARS = 5.0
_N_BARS = 10
_K = 1.0
# 앞 50봉: ±1 오실레이션으로 EWM 변동성을 0보다 크게 만드는 워밍업 구간.
# 마지막 값(인덱스 49, 홀수라 -1 적용)이 99.0이라 이후 케이스의 기준가로 쓴다.
_WARMUP = [100.0 + (1.0 if i % 2 == 0 else -1.0) for i in range(50)]
_BASE = _WARMUP[-1]
assert _BASE == 99.0


def _make_close_df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"close": closes})


def test_compute_triple_barrier_labels_assigns_up_when_upper_touched_first():
    future_up = [_BASE * (1.05**i) for i in range(1, 11)]  # 5%/bar 복리 급등
    closes = _WARMUP + future_up + [future_up[-1]] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "상승"


def test_compute_triple_barrier_labels_assigns_down_when_lower_touched_first():
    future_down = [_BASE * (0.95**i) for i in range(1, 11)]  # 5%/bar 복리 급락
    closes = _WARMUP + future_down + [future_down[-1]] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "하락"


def test_compute_triple_barrier_labels_assigns_sideways_when_neither_touched():
    future_flat = [_BASE] * 10  # 완전 횡보(수익률 0) -> 어떤 임계값도 못 넘음
    closes = _WARMUP + future_flat + [_BASE] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "횡보"


def test_compute_triple_barrier_labels_picks_whichever_barrier_hits_first():
    # 먼저 하단을 살짝 터치(-3%/-4%)한 뒤에야 상단을 크게 터치(+10%) — 크기가 아니라
    # "몇 봉째 터치했는지"만으로 결정돼야 하므로 하락이 정답.
    future_tie = [_BASE * 0.97, _BASE * 0.96, _BASE * 1.10] + [_BASE * 1.10] * 7
    closes = _WARMUP + future_tie + [_BASE * 1.10] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "하락"


def test_compute_triple_barrier_labels_nan_when_future_data_insufficient():
    future_flat = [_BASE] * 10
    closes = _WARMUP + future_flat + [_BASE] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[-_N_BARS:].isna().all()


def test_compute_triple_barrier_labels_preserves_length_and_index():
    closes = _WARMUP + [_BASE] * 15
    df = _make_close_df(closes)
    labels = compute_triple_barrier_labels(df, _HALF_LIFE_BARS, _N_BARS, _K)

    assert len(labels) == len(df)
    assert list(labels.index) == list(df.index)


def test_category_labels_has_three_ordered_classes():
    assert CATEGORY_LABELS == ["하락", "횡보", "상승"]
