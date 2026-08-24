# 장세 판별기 반전 게이팅 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 장세 판별기(`engine/regime_detector.py`)의 스코어에 거래량 확인·VPIN 매수매도
불균형·지지저항 근접도를 반영해, 극값(급상승/급하락) 근처에서 발생하는 정반대 오분류를
줄인다.

**Architecture:** 신규 모듈 `engine/regime_features.py`(순수 pandas 함수, backtrader
의존 없음)에 보조 신호 계산 함수 5개를 추가하고, `engine/regime_detector.py`의
`compute_regime_probs_series()`가 기존 `raw_score`에 이 신호들로 만든 조정 계수를 곱해
`adjusted_score`를 만든 뒤 기존 `_softmax_categorize()`에 그대로 넘긴다. `evaluate_market()`,
API, UI 컴포넌트는 무수정.

**Tech Stack:** Python, pandas, numpy, pytest

## Global Constraints

- 스펙 문서: `docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md`
- `engine/regime_features.py`는 `engine/regime_detector.py`와 마찬가지로 순수 함수만 담는다
  (I/O 없음, backtrader 의존 없음) — 이후 백테스트/그리드서치/라이브 데몬 어디서든
  재사용 가능해야 한다는 기존 설계 철학(`docs/superpowers/specs/2026-08-23-realtime-regime-detector-design.md:41-44`)을 따른다.
- `df`에 `volume`/`trade_value`/`high`/`low` 컬럼이 없으면(기존 호출자·테스트가 `close`만
  제공하는 경우) 조정을 전부 중립(원래 `raw_score` 그대로)으로 처리한다 — 기존 API 계약과
  `tests/test_regime_detector.py`의 기존 테스트를 깨지 않기 위한 하위호환 요구사항.
- 순환 참조 방지를 위해 `_MIN_VOLATILITY_FLOOR` 상수는 `engine/regime_features.py`에
  동일 값(`1e-6`)으로 별도 정의한다(`backend/regime_service.py:30-38`의 `_to_utc_iso`
  중복 정의와 같은 이유 — `regime_detector.py`가 `regime_features.py`를 import하므로
  반대 방향 import는 순환참조가 된다).
- 커밋 메시지는 이 저장소의 기존 스타일(`feat:`, `fix:`, `docs:` 등 conventional 접두사 +
  한국어 설명)을 따른다.

---

## Task 0: 베이스라인 캡처 (코드 변경 없음)

변경 전 성능을 기록해 마지막 Task에서 비교할 수 있게 한다.

**Files:**
- Create: `docs/bugfix/regime-backtest-baseline-before.txt` (스크립트 출력 저장, 커밋 대상 아님 — `.gitignore` 확인 불필요, 임시 비교용 파일로 두되 최종 Task에서 결과를 스펙 문서에 기록한 뒤 삭제)

- [ ] **Step 1: 변경 전 상태에서 검증 스크립트 실행**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py > docs/bugfix/regime-backtest-baseline-before.txt 2>&1`

Expected: 3개 마켓(KRW-BTC/ETH/XRP)에 대해 hit-rate·상관계수·confusion matrix가 출력되고 파일에 저장됨. (네트워크로 캔들을 받아오므로 최초 실행은 다소 걸릴 수 있다 — 이미 캐시돼 있다면 빠르게 끝난다.)

- [ ] **Step 2: 파일 내용 확인**

`docs/bugfix/regime-backtest-baseline-before.txt`를 열어 3개 마켓 모두 정상 출력됐는지(에러 트레이스백이 없는지) 확인한다. Task 7에서 이 파일과 변경 후 출력을 비교한다.

---

## Task 1: `engine/regime_features.py` 신설 — `volume_confirm()`

**Files:**
- Create: `engine/regime_features.py`
- Create: `tests/test_regime_features.py`

**Interfaces:**
- Produces: `volume_confirm(trade_value: pd.Series, period: int = 20) -> pd.Series` — 반환값 범위 `[0.7, 1.3]`, `trade_value`와 동일한 index.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_features.py` 신규 생성:

```python
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.regime_features import volume_confirm


def test_volume_confirm_neutral_when_constant():
    trade_value = pd.Series([100.0] * 30)
    result = volume_confirm(trade_value)
    assert result.iloc[-1] == pytest.approx(1.0)


def test_volume_confirm_above_one_when_volume_spikes_above_average():
    trade_value = pd.Series([100.0] * 20 + [500.0])
    result = volume_confirm(trade_value)
    assert result.iloc[-1] > 1.0


def test_volume_confirm_below_one_when_volume_drops_below_average():
    trade_value = pd.Series([100.0] * 20 + [10.0])
    result = volume_confirm(trade_value)
    assert result.iloc[-1] < 1.0


def test_volume_confirm_clipped_to_range():
    trade_value = pd.Series([100.0] * 20 + [100000.0])
    result = volume_confirm(trade_value)
    assert result.iloc[-1] == pytest.approx(1.3)


def test_volume_confirm_neutral_during_warmup():
    trade_value = pd.Series([100.0, 200.0, 50.0])
    result = volume_confirm(trade_value, period=20)
    assert result.iloc[0] == pytest.approx(1.0)
    assert result.iloc[-1] == pytest.approx(1.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'engine.regime_features'`

- [ ] **Step 3: 최소 구현**

`engine/regime_features.py` 신규 생성:

```python
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

import numpy as np
import pandas as pd

# regime_detector.py의 동명 상수와 값이 같아야 한다. regime_detector가 이 모듈을
# import하므로(반대 방향은 순환참조), backend/regime_service.py의 _to_utc_iso와
# 같은 이유로 별도 정의한다.
_MIN_VOLATILITY_FLOOR = 1e-6


def volume_confirm(trade_value: pd.Series, period: int = 20) -> pd.Series:
    """거래대금이 자체 이동평균(period봉) 대비 얼마나 실렸는지를 [0.7, 1.3] 배율로
    변환한다. engine/indicators/volume.py:111-124(TradeValueRatio)와 동일한 정의를
    pandas로 재구현. 방향(상승/하락) 무관 — 평균보다 거래대금이 실린 봉이면 모멘텀
    점수를 증폭, 안 실렸으면 감쇠시키는 용도."""
    sma = trade_value.rolling(period).mean()
    ratio = (trade_value - sma) / sma.replace(0.0, np.nan)
    ratio = ratio.fillna(0.0)
    return 1.0 + ratio.clip(-0.3, 0.3)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_features.py tests/test_regime_features.py
git commit -m "feat: regime_features.volume_confirm 거래량 확인 배율 추가"
```

---

## Task 2: `pivot_levels()` 추가

**Files:**
- Modify: `engine/regime_features.py`
- Modify: `tests/test_regime_features.py`

**Interfaces:**
- Consumes: 없음(Task 1과 독립)
- Produces: `pivot_levels(high: pd.Series, low: pd.Series, close: pd.Series) -> tuple[pd.Series, pd.Series]` — `(r1, s1)` 반환, 첫 행은 `NaN`(직전 봉 없음).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_features.py`에 추가:

```python
from engine.regime_features import pivot_levels


def test_pivot_levels_first_row_is_nan():
    high = pd.Series([110.0, 112.0, 111.0])
    low = pd.Series([90.0, 95.0, 94.0])
    close = pd.Series([100.0, 105.0, 103.0])
    r1, s1 = pivot_levels(high, low, close)
    assert pd.isna(r1.iloc[0])
    assert pd.isna(s1.iloc[0])


def test_pivot_levels_uses_previous_bar_only():
    # 2번째 행(index=1)의 R1/S1은 index=0의 high/low/close로만 계산돼야 한다.
    high = pd.Series([110.0, 999.0])
    low = pd.Series([90.0, 999.0])
    close = pd.Series([100.0, 999.0])
    r1, s1 = pivot_levels(high, low, close)
    pivot = (110.0 + 90.0 + 100.0) / 3.0
    assert r1.iloc[1] == pytest.approx(pivot * 2 - 90.0)
    assert s1.iloc[1] == pytest.approx(pivot * 2 - 110.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: FAIL — `ImportError: cannot import name 'pivot_levels'`

- [ ] **Step 3: 구현 추가**

`engine/regime_features.py`에 추가:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_features.py tests/test_regime_features.py
git commit -m "feat: regime_features.pivot_levels 지지저항 산출 추가"
```

---

## Task 3: `vpin_score()` 추가

**Files:**
- Modify: `engine/regime_features.py`
- Modify: `tests/test_regime_features.py`

**Interfaces:**
- Consumes: 없음(Task 1/2와 독립)
- Produces: `vpin_score(volume: pd.Series, close: pd.Series, period: int = 20) -> pd.Series` — `[0, 1]` 범위, 워밍업(버킷 `period`개 미달) 구간은 `NaN`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_features.py`에 추가:

```python
from engine.regime_features import vpin_score


def test_vpin_score_nan_during_warmup():
    volume = pd.Series([10.0] * 5)
    close = pd.Series([100.0, 101.0, 99.0, 102.0, 98.0])
    result = vpin_score(volume, close, period=20)
    assert pd.isna(result.iloc[-1])


def test_vpin_score_high_when_one_sided_trend():
    # 거래량 일정, 종가가 매 봉 꾸준히 상승 — 매수 쏠림이 강해야 한다.
    n = 60
    volume = pd.Series([10.0] * n)
    close = pd.Series([100.0 * (1.01 ** i) for i in range(n)])
    result = vpin_score(volume, close, period=10)
    assert result.iloc[-1] > 0.5


def test_vpin_score_bounded_between_zero_and_one():
    n = 60
    rng = np.random.default_rng(seed=1)
    volume = pd.Series(rng.uniform(5.0, 15.0, size=n))
    close = pd.Series(100.0 + np.cumsum(rng.normal(0.0, 1.0, size=n)))
    result = vpin_score(volume, close, period=10)
    valid = result.dropna()
    assert len(valid) > 0
    assert valid.between(0.0, 1.0).all()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: FAIL — `ImportError: cannot import name 'vpin_score'`

- [ ] **Step 3: 구현 추가**

`engine/regime_features.py` 상단 import에 `import statistics`, `from collections import deque` 추가 후:

```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: PASS (10 tests). `test_vpin_score_high_when_one_sided_trend`이 실패하면 `period` 또는 트렌드 강도(1.01 → 더 큰 값)를 조정 — 목표는 "일관된 한쪽 방향 추세는 VPIN이 0.5보다 커야 한다"는 정성적 성질이지 정확한 수치가 아니다.

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_features.py tests/test_regime_features.py
git commit -m "feat: regime_features.vpin_score 매수매도 불균형 지표 추가"
```

---

## Task 4: `level_proximity()` 추가

**Files:**
- Modify: `engine/regime_features.py`
- Modify: `tests/test_regime_features.py`

**Interfaces:**
- Consumes: `pivot_levels()`(Task 2)의 반환 타입(`pd.Series` 튜플)과 동일한 형태의 `r1`/`s1` 인자
- Produces: `level_proximity(close: pd.Series, raw_score: pd.Series, r1: pd.Series, s1: pd.Series, volatility: pd.Series) -> pd.Series` — `[0, 1]` 범위.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_features.py`에 추가:

```python
from engine.regime_features import level_proximity


def test_level_proximity_high_when_uptrend_close_to_resistance():
    close = pd.Series([100.0])
    raw_score = pd.Series([1.0])       # 상승 방향
    r1 = pd.Series([100.5])            # 저항선이 바로 위
    s1 = pd.Series([90.0])
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] > 0.5


def test_level_proximity_low_when_uptrend_far_from_resistance():
    close = pd.Series([100.0])
    raw_score = pd.Series([1.0])
    r1 = pd.Series([200.0])            # 저항선이 훨씬 위
    s1 = pd.Series([90.0])
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] == pytest.approx(0.0)


def test_level_proximity_ignores_opposite_direction_level():
    # 하락 중(raw_score<0)에는 저항선(R1) 근접은 무시하고 지지선(S1)만 본다.
    close = pd.Series([100.0])
    raw_score = pd.Series([-1.0])
    r1 = pd.Series([100.5])            # 저항선이 바로 위지만 하락 중이라 무시돼야 함
    s1 = pd.Series([200.0])            # 지지선은 훨씬 아래
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] == pytest.approx(0.0)


def test_level_proximity_zero_when_sideways():
    close = pd.Series([100.0])
    raw_score = pd.Series([0.0])
    r1 = pd.Series([100.1])
    s1 = pd.Series([99.9])
    volatility = pd.Series([1.0])
    result = level_proximity(close, raw_score, r1, s1, volatility)
    assert result.iloc[0] == pytest.approx(0.0)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: FAIL — `ImportError: cannot import name 'level_proximity'`

- [ ] **Step 3: 구현 추가**

`engine/regime_features.py` 파일 상단(마지막 import 문 다음, `def volume_confirm` 이전)에
`_MIN_VOLATILITY_FLOOR` 상수를 추가한다(Task 1에서는 이 상수가 쓰이지 않아 제외됐고,
`level_proximity`가 이를 쓰는 첫 함수이므로 여기서 정의한다):

```python
# regime_detector.py의 동명 상수와 값이 같아야 한다. regime_detector가 이 모듈을
# import하므로(반대 방향은 순환참조), backend/regime_service.py의 _to_utc_iso와
# 같은 이유로 별도 정의한다.
_MIN_VOLATILITY_FLOOR = 1e-6
```

그리고 `engine/regime_features.py`에 추가:

```python
def level_proximity(
    close: pd.Series,
    raw_score: pd.Series,
    r1: pd.Series,
    s1: pd.Series,
    volatility: pd.Series,
) -> pd.Series:
    """추세 방향의 저항/지지선 근접도를 [0, 1]로 나타낸다(1=바로 위/아래에 위치).
    raw_score > 0(상승 중)이면 저항선(R1)과의 거리만, raw_score < 0(하락 중)이면
    지지선(S1)과의 거리만 본다 — 추세와 무관한 반대편 레벨 근접까지 반전 신호로 잡으면
    오탐이 늘어난다(설계 문서 참고). raw_score == 0(횡보)이면 항상 0."""
    safe_vol = volatility.clip(lower=_MIN_VOLATILITY_FLOOR)
    dist_to_r1 = (close - r1).abs() / safe_vol
    dist_to_s1 = (close - s1).abs() / safe_vol
    nearest_dist = np.where(
        raw_score > 0, dist_to_r1, np.where(raw_score < 0, dist_to_s1, np.inf)
    )
    proximity = 1.0 - np.clip(nearest_dist, 0.0, 1.0)
    return pd.Series(proximity, index=close.index).fillna(0.0)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_features.py tests/test_regime_features.py
git commit -m "feat: regime_features.level_proximity 지지저항 근접도 추가"
```

---

## Task 5: `reversal_gate()` 추가

**Files:**
- Modify: `engine/regime_features.py`
- Modify: `tests/test_regime_features.py`

**Interfaces:**
- Consumes: `vpin_score()`(Task 3), `level_proximity()`(Task 4)의 반환값(둘 다 `pd.Series`, `[0,1]` 범위 — `vpin_score`는 `NaN` 가능)
- Produces: `reversal_gate(vpin: pd.Series, proximity: pd.Series) -> pd.Series` — `[0.3, 1.0]` 범위.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_features.py`에 추가:

```python
from engine.regime_features import reversal_gate


def test_reversal_gate_neutral_when_no_risk():
    vpin = pd.Series([0.0])
    proximity = pd.Series([0.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(1.0)


def test_reversal_gate_dampens_when_both_high():
    vpin = pd.Series([1.0])
    proximity = pd.Series([1.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(0.3)


def test_reversal_gate_neutral_when_only_one_high():
    # VPIN만 높고 레벨 근접이 0이면 감쇠하지 않는다(둘 다 성립해야 반전위험으로 인정).
    vpin = pd.Series([1.0])
    proximity = pd.Series([0.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(1.0)


def test_reversal_gate_treats_nan_vpin_as_neutral():
    vpin = pd.Series([float("nan")])
    proximity = pd.Series([1.0])
    result = reversal_gate(vpin, proximity)
    assert result.iloc[0] == pytest.approx(1.0)


def test_reversal_gate_never_below_floor():
    vpin = pd.Series([1.0, 1.0])
    proximity = pd.Series([1.0, 1.0])
    result = reversal_gate(vpin, proximity)
    assert (result >= 0.3).all()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: FAIL — `ImportError: cannot import name 'reversal_gate'`

- [ ] **Step 3: 구현 추가**

`engine/regime_features.py`에 추가:

```python
def reversal_gate(vpin: pd.Series, proximity: pd.Series) -> pd.Series:
    """VPIN 매수/매도 쏠림과 추세방향 저항/지지 근접이 동시에 나타나면 모멘텀 점수를
    감쇠시키는 배율. 둘 중 하나만 높으면(단독으론 반전 신호로 부족) 감쇠하지 않는다.
    NaN(워밍업 미달)은 '위험 없음'으로 취급 — 판단불가를 억지로 강한 신호로 포장하지
    않는다는 기존 정책(regime_detector.py의 워밍업 None 정책)과 같은 방향."""
    risk = (vpin.fillna(0.0) * proximity.fillna(0.0)).clip(0.0, 1.0)
    return 1.0 - 0.7 * risk
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_features.py -v`
Expected: PASS (19 tests)

- [ ] **Step 5: 커밋**

```bash
git add engine/regime_features.py tests/test_regime_features.py
git commit -m "feat: regime_features.reversal_gate 반전 게이트 배율 추가"
```

---

## Task 6: `regime_detector.py` 통합 — `adjusted_score` 적용

**Files:**
- Modify: `engine/regime_detector.py`
- Modify: `tests/test_regime_detector.py`

**Interfaces:**
- Consumes: `engine.regime_features`의 `volume_confirm`, `pivot_levels`, `vpin_score`, `level_proximity`, `reversal_gate`(Task 1~5에서 확정된 시그니처 그대로)
- Produces: `compute_regime_probs_series()`, `compute_regime_probs()`의 **공개 시그니처와 반환 타입은 무수정** — 내부 스코어 계산만 바뀐다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_detector.py` 맨 아래에 추가(파일 상단 import에 `from engine.regime_detector import ...`는 이미 있으므로 추가 import 불필요):

```python
def _make_full_price_df(
    closes: list[float], highs: list[float], lows: list[float],
    volumes: list[float], trade_values: list[float],
) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({
        "candle_time": dates, "close": closes, "high": highs, "low": lows,
        "volume": volumes, "trade_value": trade_values,
    })


def test_compute_regime_probs_series_falls_back_to_raw_score_without_volume_columns():
    """volume/trade_value/high/low 컬럼이 없으면(기존 호출자·close만 있는 df) adjusted_score는
    raw_score와 완전히 동일해야 한다 — 하위호환 요구사항을 고정한다."""
    closes = [100.0 * (1.01 ** i) for i in range(60)]
    df_close_only = _make_price_df(closes)
    df_full = _make_full_price_df(
        closes,
        highs=closes, lows=closes,
        volumes=[1.0] * 60, trade_values=[1.0] * 60,
    )

    probs_close_only = compute_regime_probs(df_close_only, half_life_bars=3.0)
    probs_full_neutral = compute_regime_probs(df_full, half_life_bars=3.0)

    assert probs_close_only is not None and probs_full_neutral is not None
    # high=low=close, volume/trade_value 상수 조건에서는 조정 배율이 전부 중립(1.0)이어야
    # 두 결과가 사실상 동일해야 한다.
    for label in probs_close_only:
        assert probs_close_only[label] == pytest.approx(probs_full_neutral[label], abs=1e-6)


def test_compute_regime_probs_series_reversal_gate_lowers_confidence_near_resistance():
    """거래량이 실린 급등 랠리가 저항선 근처까지 이어질 때, VPIN+지지저항 조정을 반영한
    adjusted_score 기준 확신도(최댓값 확률)가 조정 없는(raw_score만 쓰는, volume/trade_value
    컬럼을 뺀) 경우보다 낮아야 한다 — 극값 근처 정반대 오분류를 줄이는 이번 스펙의 핵심
    목표를 코드로 고정한다."""
    rng = np.random.default_rng(seed=11)
    n = 60
    # 완만한 등락 20봉(지지/저항 구조를 만들기 위한 베이스) + 거래량 실린 강한 랠리 20봉
    base_returns = rng.normal(0.0, 0.01, size=20)
    rally_returns = np.full(20, 0.03) + rng.normal(0.0, 0.003, size=20)
    tail_returns = rng.normal(0.0, 0.01, size=20)
    returns = np.concatenate([base_returns, rally_returns, tail_returns])
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1.0 + r))
    closes = closes[1:]
    highs = [c * 1.01 for c in closes]
    lows = [c * 0.99 for c in closes]
    volumes = [10.0] * 20 + [50.0] * 20 + [10.0] * 20   # 랠리 구간 거래량 급증
    trade_values = volumes

    df_full = _make_full_price_df(closes, highs, lows, volumes, trade_values)
    df_raw_only = df_full.drop(columns=["volume", "trade_value"])

    peak_idx = 39  # 랠리 마지막 봉

    series_adjusted = compute_regime_probs_series(df_full, half_life_bars=3.0)
    series_raw = compute_regime_probs_series(df_raw_only, half_life_bars=3.0)

    probs_adjusted = series_adjusted[peak_idx]
    probs_raw = series_raw[peak_idx]
    assert probs_adjusted is not None and probs_raw is not None
    assert max(probs_adjusted.values()) < max(probs_raw.values())
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v -k "fallback or reversal_gate_lowers"`
Expected: `test_compute_regime_probs_series_falls_back_to_raw_score_without_volume_columns`는 이미 PASS(현재 구현도 volume 컬럼을 안 쓰므로), `test_compute_regime_probs_series_reversal_gate_lowers_confidence_near_resistance`는 FAIL(아직 조정 로직이 없어 두 결과가 완전히 동일 → `<` 비교 실패).

- [ ] **Step 3: `compute_regime_probs_series()` 수정**

`engine/regime_detector.py` 상단 import에 추가:

```python
from engine.regime_features import (
    level_proximity,
    pivot_levels,
    reversal_gate,
    vpin_score,
    volume_confirm,
)
```

`compute_regime_probs_series()`(현재 85~112행)를 다음으로 교체:

```python
_ADJUSTMENT_COLUMNS = {"volume", "trade_value", "high", "low"}


def compute_regime_probs_series(
    df: pd.DataFrame, half_life_bars: float
) -> list[dict[str, float] | None]:
    """df 전체에 대해 매 봉마다의 regime_probs를 O(n)에 한 번에 계산한다(검증스크립트용
    벡터화 버전 — compute_regime_probs(df.iloc[:t+1], ...)를 매 t마다 반복호출하면
    O(n^2)라 느림). 두 방식이 동일한 결과를 내는지는
    test_compute_regime_probs_series_matches_pointwise_calls로 고정한다.

    df에 volume/trade_value/high/low 컬럼이 전부 있으면 거래량 확인·VPIN 불균형·
    지지저항 근접도로 raw_score를 조정한 adjusted_score를 쓴다(설계 문서:
    docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md).
    컬럼이 없으면(기존 호출자 하위호환) 조정 없이 raw_score를 그대로 쓴다."""
    if df.empty or "close" not in df.columns:
        return []
    min_bars = int(half_life_bars * WARMUP_MULTIPLIER)
    returns = df["close"].pct_change(fill_method=None)
    valid_counts = returns.notna().cumsum()
    momentum_series = _ewm_series(returns, half_life_bars)
    volatility_series = _ewm_std_series(returns, half_life_bars)
    raw_score_series = momentum_series / volatility_series.clip(lower=_MIN_VOLATILITY_FLOOR)

    if _ADJUSTMENT_COLUMNS.issubset(df.columns):
        confirm_series = volume_confirm(df["trade_value"])
        r1_series, s1_series = pivot_levels(df["high"], df["low"], df["close"])
        vpin_series_values = vpin_score(df["volume"], df["close"])
        proximity_series = level_proximity(
            df["close"], raw_score_series, r1_series, s1_series, volatility_series
        )
        gate_series = reversal_gate(vpin_series_values, proximity_series)
        adjusted_score_series = raw_score_series * confirm_series * gate_series
    else:
        adjusted_score_series = raw_score_series

    results: list[dict[str, float] | None] = []
    for i in range(len(df)):
        if int(valid_counts.iloc[i]) < min_bars:
            results.append(None)
            continue
        momentum = momentum_series.iloc[i]
        volatility = volatility_series.iloc[i]
        if pd.isna(momentum) or pd.isna(volatility):
            results.append(None)
            continue
        results.append(_softmax_categorize(adjusted_score_series.iloc[i]))
    return results
```

`compute_regime_probs()` 함수는 무수정(내부적으로 `compute_regime_probs_series`를 호출하므로 자동으로 반영됨).

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_detector.py -v`
Expected: PASS (전체, 새로 추가한 2개 포함). `test_compute_regime_probs_series_reversal_gate_lowers_confidence_near_resistance`가 여전히 실패하면 `rally_returns`의 크기(0.03)나 `volumes`의 배율(50.0)을 키워 VPIN·근접도가 실제로 유의미하게 커지도록 조정한다 — 목표는 정성적 부등식이지 정확한 수치가 아니다.

- [ ] **Step 5: 전체 회귀 스위트 실행**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. python -m pytest tests/test_regime_detector.py tests/test_regime_service.py tests/test_regime_features.py -v`
Expected: 전체 PASS. `test_regime_service.py`의 기존 테스트(합성 데이터가 open=high=low=close, volume=trade_value=1.0 상수라 조정 배율이 전부 중립이 되는 케이스)가 깨지면, `_make_candle_df`가 만드는 데이터의 변동성이 `_MIN_VOLATILITY_FLOOR`에 걸려 `level_proximity`가 예상과 다르게 나오는지 직접 확인 — 필요시 `level_proximity`나 `reversal_gate`의 클립 범위를 재점검한다(스펙 문서의 상수는 튜닝 가능하다고 명시돼 있음).

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_detector.py tests/test_regime_detector.py
git commit -m "feat: regime_detector에 반전 게이팅(adjusted_score) 통합"
```

---

## Task 7: 실측 검증 및 결과 기록

**Files:**
- Modify: `docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md` (검증 결과 절 추가)
- Delete: `docs/bugfix/regime-backtest-baseline-before.txt` (Task 0의 임시 캡처 파일 — 결과를 스펙 문서에 기록한 뒤 삭제)

- [ ] **Step 1: 변경 후 상태에서 검증 스크립트 실행**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/regime_backtest.py > docs/bugfix/regime-backtest-baseline-after.txt 2>&1`

- [ ] **Step 2: 전/후 비교**

`docs/bugfix/regime-backtest-baseline-before.txt`와 `docs/bugfix/regime-backtest-baseline-after.txt`를 나란히 읽고, 3개 마켓 각각에 대해:
- 확률벡터-실현수익률 상관계수가 개선됐는지(절댓값이 커졌는지)
- confusion matrix에서 "정반대 오분류" 셀(급상승 행×급하락 열, 급하락 행×급상승 열)의 건수가 줄었는지
- 급상승/급하락 카테고리가 여전히 통계적으로 도달 가능한지(샘플 0건으로 사라지지 않았는지 — 과도한 게이팅으로 모든 스코어가 횡보로 수렴하는 회귀가 없는지)

- [ ] **Step 3: 결과를 스펙 문서에 기록**

`docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md` 끝에 `## 검증 결과(YYYY-MM-DD)` 절을 추가해 Step 2에서 확인한 수치(마켓별 상관계수 전/후, 정반대 오분류 건수 전/후)를 기록한다.

- [ ] **Step 4: 임시 파일 정리 및 커밋**

```bash
git rm docs/bugfix/regime-backtest-baseline-before.txt docs/bugfix/regime-backtest-baseline-after.txt
git add docs/superpowers/specs/2026-08-24-regime-detector-reversal-gating-design.md
git commit -m "docs: 반전 게이팅 실측 검증 결과 기록"
```

- [ ] **Step 5: 사용자 보고**

상관계수·confusion matrix 개선 여부를 사용자에게 요약 보고한다. 만약 개선이 미미하거나 없으면(가중치 튜닝 필요, 또는 규칙기반 확장의 한계) 2단계(하이브리드 ML) 브레인스토밍으로 넘어갈지 여부를 사용자와 논의한다.
