"""
tests/test_regime_ml_labels.py

engine.regime_ml_labels.compute_triple_barrier_labels()를 검증한다. Triple
Barrier Method — 하단 경계가 먼저 터치되면 "하락", 상단이 먼저 터치되거나
둘 다 안 터치되면(횡보) "하락아님", 미래 데이터가 부족하면 NaN인지 확인한다.
"""
from __future__ import annotations

import pandas as pd
import pytest

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


def test_compute_triple_barrier_labels_assigns_not_down_when_upper_touched_first():
    future_up = [_BASE * (1.05**i) for i in range(1, 11)]  # 5%/bar 복리 급등
    closes = _WARMUP + future_up + [future_up[-1]] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "하락아님"


def test_compute_triple_barrier_labels_assigns_down_when_lower_touched_first():
    future_down = [_BASE * (0.95**i) for i in range(1, 11)]  # 5%/bar 복리 급락
    closes = _WARMUP + future_down + [future_down[-1]] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "하락"


def test_compute_triple_barrier_labels_assigns_not_down_when_neither_touched():
    future_flat = [_BASE] * 10  # 완전 횡보(수익률 0) -> 어떤 임계값도 못 넘음
    closes = _WARMUP + future_flat + [_BASE] * 5
    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[49] == "하락아님"


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


def test_category_labels_has_two_ordered_classes():
    assert CATEGORY_LABELS == ["하락", "하락아님"]


def test_compute_triple_barrier_labels_vol_t_excludes_current_bar_return():
    """급락이 일어난 바로 그 봉(t=crash_index) 자신의 수익률이 그 봉의 barrier 폭
    계산에 포함되면(이전 구현), 급락 자체가 vol_t를 급등시켜 barrier가 넓어지고,
    급락 직후의 완만한 추가 하락은 그 넓어진 barrier를 못 건드려 "하락아님"으로
    잘못 라벨링될 수 있다(docs/regime-ml-backlog.md 기술부채 항목, KRW-SHIB 실측
    사례 재현). vol_t를 t-1까지만 쓰도록 shift하면, 급락 봉의 barrier는 급락 이전의
    평온한 vol 기준으로 좁게 잡히므로 같은 완만한 추가 하락도 "하락"으로 잡혀야
    한다. 추가 하락폭은 "직전 평온 구간의 vol"보다 큰 값으로 직접 계산해서 정하므로
    (하드코딩된 %가 아님) 어떤 halflife/k 조합에서도 재현 가능하다."""
    crash_index = 59  # _WARMUP(인덱스 0~49) 이후 stable 구간의 10번째 봉
    crashed_close = _BASE * 0.5  # 그 봉 자체가 -50% 급락

    # shift(1) 적용 시 t=crash_index의 barrier에 쓰이는 vol은 급락 이전(워밍업
    # 오실레이션) 수준으로 안정돼 있다 — 그 크기를 먼저 직접 계산해, 이후의
    # 완만한 추가 하락폭을 "좁은 barrier는 반드시 넘지만, 급락을 포함해 부풀려진
    # barrier는 절대 못 넘을" 크기(안전 마진 확보를 위해 3배 차이)로 잡는다.
    warmup_returns = pd.Series(_WARMUP).pct_change(fill_method=None)
    pre_crash_vol = warmup_returns.ewm(halflife=_HALF_LIFE_BARS).std().iloc[-1]
    assert pre_crash_vol > 0
    narrow_barrier = _K * pre_crash_vol
    further_decline_pct = narrow_barrier * 1.5  # 좁은 barrier는 확실히 넘는 크기

    future_after_crash = [
        crashed_close * (1 - further_decline_pct * (i / 9)) for i in range(1, 10)
    ]  # 급락 이후 9봉에 걸쳐 완만하게 further_decline_pct까지 추가 하락(더 이상 급락 아님)
    closes = (
        _WARMUP + [_BASE] * 9 + [crashed_close] + future_after_crash + [future_after_crash[-1]] * 5
    )

    labels = compute_triple_barrier_labels(_make_close_df(closes), _HALF_LIFE_BARS, _N_BARS, _K)

    assert labels.iloc[crash_index] == "하락"


def test_compute_sample_uniqueness_weights_isolated_label_gets_weight_one():
    """다른 라벨과 활성구간이 전혀 안 겹치는 라벨은 c_t가 항상 1이라
    uniqueness weight도 정확히 1.0이어야 한다."""
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    # 라벨 2개, n_bars=2라 활성구간은 [0,2]와 [10,12] — 전혀 안 겹침.
    labels = pd.Series([float("nan")] * 13)
    labels.iloc[0] = "하락"
    labels.iloc[10] = "하락아님"

    weights = compute_sample_uniqueness_weights(labels, n_bars=2)

    assert weights.iloc[0] == pytest.approx(1.0)
    assert weights.iloc[10] == pytest.approx(1.0)


def test_compute_sample_uniqueness_weights_fully_overlapping_labels_get_weight_half():
    """두 라벨이 활성구간을 완전히 공유하면(t=0, t=1, n_bars=1 -> 둘 다 [t,t+1]이
    [0,1]과 [1,2]로 한 시점(t=1)을 공유) 그 겹치는 시점에서는 c_t=2가 되어
    각 라벨의 평균 uniqueness가 1보다 작아져야 한다."""
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    labels = pd.Series(["하락", "하락아님", float("nan")])  # n_bars=1

    weights = compute_sample_uniqueness_weights(labels, n_bars=1)

    # 라벨0 활성구간=[0,1], 라벨1 활성구간=[1,2] -> t=1에서 c_t=2, 나머지는 c_t=1.
    # 라벨0 weight = mean(1/c_0, 1/c_1) = mean(1/1, 1/2) = 0.75
    assert weights.iloc[0] == pytest.approx(0.75)


def test_compute_sample_uniqueness_weights_nan_labels_stay_nan_and_are_excluded_from_concurrency():
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    labels = pd.Series(["하락", float("nan"), "하락아님"])

    weights = compute_sample_uniqueness_weights(labels, n_bars=1)

    assert pd.isna(weights.iloc[1])
    assert weights.iloc[0] == pytest.approx(1.0)  # 이웃이 NaN이라 안 겹침


def test_compute_sample_uniqueness_weights_preserves_length_and_index():
    from engine.regime_ml_labels import compute_sample_uniqueness_weights

    labels = pd.Series(["하락"] * 5, index=range(100, 105))
    weights = compute_sample_uniqueness_weights(labels, n_bars=2)

    assert len(weights) == len(labels)
    assert list(weights.index) == list(labels.index)
