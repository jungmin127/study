# 장세 판별 — HMM 비지도 클러스터링 대안 검증 (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-09-05-regime-hmm-unsupervised-clustering-design.md`에서 승인된 설계대로, 지도학습(Triple Barrier + LightGBM) 없이 HMM 비지도 클러스터링만으로 뽑아낸 잠재 상태가 실전 매매 성과와 상관이 있는지 사후(ex-post) 분석으로 확인한다.

**Architecture:** `engine/regime_ml_hmm.py`(2026-08-30에 이미 작성됐지만 한 번도 실행되지 않은 채 방치된 순수함수 `build_hmm_observations`/`fit_hmm`/`score_hmm_state_probabilities`)에 신규 함수 `compute_dominant_state()`를 추가해 바별 지배적 상태(argmax)를 뽑아낸다. `scripts/analyze_regime_fact_performance.py`(2026-08-30 fact 라벨 백테스트 분석)의 라벨-소스에 무관한 순수 함수(`label_for_entry`/`load_labeled_trades`/`print_pooled_comparison`/`print_run_ranking`)를 그대로 재사용하고, 라벨 소스만 Triple Barrier → HMM 상태로 바꾼 신규 스크립트 `scripts/analyze_regime_hmm_fact_performance.py`를 추가한다.

**Tech Stack:** Python, pandas, numpy, hmmlearn(기존 의존성, 신규 추가 없음), pytest.

## Global Constraints

- HMM 입력 변수는 로그수익률+EWM 변동성 2개(기존 `build_hmm_observations` 그대로) — 거래량 추가는 비범위.
- `n_states=3`(기존 `engine.regime_ml_hmm.N_STATES` 기본값) 고정 — 그리드서치 비범위.
- 마켓별 개별 HMM, **전체 기간(2024-01-01~현재) 한 번에 fit** — 워크포워드 fold 분리 없음(사후 분석 목적, 설계 문서 "A. HMM 모델링" 참고).
- 대상 마켓은 `scripts/analyze_regime_fact_performance.py`의 `MARKETS`(KRW-BTC, KRW-XLM)와 동일 — 저장된 백테스트 결과가 있는 마켓만 가능.
- 프로덕션 코드(`engine/regime_ml_features.py`, `scripts/train_regime_ml.py`, `backend/regime_ml_service.py`)는 전혀 건드리지 않는다. `engine/regime_ml_hmm.py`에 함수 하나만 추가하고 기존 3개 함수는 수정하지 않는다.
- 신규 검증 스크립트(`scripts/analyze_regime_hmm_fact_performance.py`)는 `scripts/tune_regime_ml_horizon.py` 등 기존 1회성 진단 스크립트와 같은 성격이라 전용 pytest를 작성하지 않는다 — synthetic 데이터로 손으로 완주 확인한다. 단, 엔진 순수함수(`compute_dominant_state`)는 TDD로 작성한다.
- 성공/미채택 판단은 사전에 숫자 문턱을 고정하지 않고, 기존 지도학습 기준값(하락 진입 승률 41.6%/총기여 -17.7% vs 하락아님 진입 승률 73.2%/총기여 +1767.1%, `docs/regime-ml-backlog.md`)과 정성적으로 비교한다.
- 각 태스크 끝에서 `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`가 전부 통과해야 한다(단, `tests/test_import_backtest_results.py::test_script_runs_as_real_subprocess_entry_point`는 이 작업과 무관한 기존 Windows subprocess 인코딩 flake로 알려져 있음 — 실패해도 회귀 아님).
- 한국어 docstring/주석 관례 유지("왜"를 설명).

---

## Task 1: `engine/regime_ml_hmm.py`에 `compute_dominant_state()` 추가

**Files:**
- Modify: `engine/regime_ml_hmm.py`
- Modify: `tests/test_regime_ml_hmm.py`

**Interfaces:**
- Consumes: 기존 `build_hmm_observations(df, half_life_bars) -> pd.DataFrame`, `fit_hmm(observations, n_states, random_state) -> GaussianHMM`, `score_hmm_state_probabilities(model, observations) -> pd.DataFrame`(모두 같은 파일에 이미 존재, 이번 태스크에서 수정하지 않음).
- Produces: `compute_dominant_state(df: pd.DataFrame, half_life_bars: float, n_states: int = N_STATES, random_state: int = _RANDOM_STATE) -> pd.Series` — df와 같은 인덱스, 유효한 바는 `0.0`~`n_states-1.0`의 float, 워밍업 구간은 `NaN`. Task 2가 이 함수를 사용한다.

### Step 1: 실패하는 테스트 작성

`tests/test_regime_ml_hmm.py` 파일 끝(기존 마지막 테스트
`test_score_hmm_state_probabilities_preserves_index` 다음)에 아래 3개
테스트를 추가한다. import 줄도 갱신한다.

교체 전(파일 13~19번째 줄):
```python
from engine.regime_ml_hmm import (
    HMM_STATE_COLUMNS,
    N_STATES,
    build_hmm_observations,
    fit_hmm,
    score_hmm_state_probabilities,
)
```

교체 후:
```python
from engine.regime_ml_hmm import (
    HMM_STATE_COLUMNS,
    N_STATES,
    build_hmm_observations,
    compute_dominant_state,
    fit_hmm,
    score_hmm_state_probabilities,
)
```

파일 끝에 추가:
```python
def test_compute_dominant_state_matches_df_length_with_warmup_nan():
    df = _make_close_df(500, seed=5)
    result = compute_dominant_state(df, _HALF_LIFE_BARS)

    assert len(result) == len(df)
    assert pd.isna(result.iloc[0])


def test_compute_dominant_state_values_are_valid_state_indices():
    df = _make_close_df(500, seed=6)
    result = compute_dominant_state(df, _HALF_LIFE_BARS, n_states=N_STATES)

    valid = result.dropna()
    assert len(valid) > 0
    assert set(valid.unique()).issubset({float(i) for i in range(N_STATES)})


def test_compute_dominant_state_is_deterministic_with_same_random_state():
    df = _make_close_df(500, seed=7)
    first = compute_dominant_state(df, _HALF_LIFE_BARS, random_state=42)
    second = compute_dominant_state(df, _HALF_LIFE_BARS, random_state=42)

    pd.testing.assert_series_equal(first, second)
```

### Step 2: 테스트 실행해서 실패 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_hmm.py -k compute_dominant_state -v`
Expected: 3개 테스트 전부 FAIL(`ImportError: cannot import name 'compute_dominant_state'`).

### Step 3: `engine/regime_ml_hmm.py` 구현

파일 끝(56번째 줄, `score_hmm_state_probabilities` 함수 뒤)에 추가:

```python


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
```

### Step 4: 테스트 실행해서 통과 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/test_regime_ml_hmm.py -v`
Expected: 7 passed(기존 4개 + 신규 3개).

### Step 5: 전체 테스트 스위트 통과 확인

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -m pytest tests/ -q`
Expected: 기존 실패 0건 유지(Global Constraints의 알려진 flake 1건 제외).

### Step 6: 커밋

```bash
git add engine/regime_ml_hmm.py tests/test_regime_ml_hmm.py
git commit -m "feat: HMM 지배적 상태(argmax) 계산 함수 추가"
```

---

## Task 2: `scripts/analyze_regime_hmm_fact_performance.py` 작성

**Files:**
- Create: `scripts/analyze_regime_hmm_fact_performance.py`

**Interfaces:**
- Consumes: Task 1의 `compute_dominant_state()`, `engine.regime_ml_hmm.build_hmm_observations`/`N_STATES`, `engine.regime_math.half_life_bars_for_timeframe`, `upbit_data_service.get_candles`, 그리고 `scripts.analyze_regime_fact_performance`의 `MARKETS`/`TIMEFRAME`/`START`/`END`/`label_for_entry`/`load_labeled_trades`/`print_pooled_comparison`/`print_run_ranking`(모두 기존 코드, 수정 없음).

`scripts/tune_regime_ml_horizon.py`와 같은 성격의 1회성 진단 스크립트라 전용
테스트를 작성하지 않는다(Global Constraints 참고). Step 2에서 synthetic
데이터로 손으로 완주 확인한다.

### Step 1: 스크립트 작성

```python
"""
scripts/analyze_regime_hmm_fact_performance.py

HMM 비지도 클러스터링으로 뽑아낸 잠재 상태가 "장세별 실전 매매 성과 차이"와
상관이 있는지 확인한다. scripts/analyze_regime_fact_performance.py(Triple
Barrier fact 라벨 버전)와 동일한 방법론(저장된 백테스트 거래를 진입 시점
라벨로 재분류)을 그대로 재사용하되, 라벨 소스만 HMM 상태로 바꾼다. 새
백테스트는 돌리지 않고 engine.cache에 이미 저장된 결과만 재분석한다. 설계
문서: docs/superpowers/specs_v1/2026-09-05-regime-hmm-unsupervised-clustering-design.md

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
```

### Step 2: synthetic 데이터로 손으로 동작 확인

`load_labeled_trades`(재사용 함수, `scripts/analyze_regime_fact_performance`
모듈에 정의됨)는 그 정의부 모듈의 전역 `MARKETS`/`TIMEFRAME`/`list_backtest_runs`를
참조한다(파이썬 클로저 규칙상 호출자가 아니라 정의부 모듈의 전역을 본다) —
synthetic 테스트에서 마켓 목록을 줄이려면 **두 모듈 모두** 패치해야 한다.
아래 명령을 실행한다:

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta

import scripts.analyze_regime_hmm_fact_performance as hmm_fact
import scripts.analyze_regime_fact_performance as fact

rng = np.random.default_rng(1)
n = 600
start = datetime(2024, 1, 1, tzinfo=timezone.utc)
candle_time = [start + timedelta(hours=i) for i in range(n)]
close = 100.0 + np.cumsum(rng.normal(0, 1.0, n))
synthetic_df = pd.DataFrame({'candle_time': candle_time, 'close': close})

hmm_fact.get_candles = lambda market, timeframe, start, end: synthetic_df
hmm_fact.MARKETS = ['KRW-BTC']
fact.MARKETS = ['KRW-BTC']

trades = [
    {'entryTime': candle_time[100].replace(tzinfo=None).isoformat(), 'returnRate': 1.5},
    {'entryTime': candle_time[300].replace(tzinfo=None).isoformat(), 'returnRate': -2.0},
    {'entryTime': candle_time[500].replace(tzinfo=None).isoformat(), 'returnRate': 0.5},
]
fact.list_backtest_runs = lambda market=None: [
    {'run_id': 'synthetic-run', 'title': '테스트', 'timeframe': 'minutes60', 'trades': trades}
]

hmm_fact.main()
"
```

Expected: 에러 없이 완주. 출력에 다음이 순서대로 나타나야 한다 —
"=== 마켓별 HMM 상태 프로파일 ===" 섹션(state 0/1/2 각각의 평균
수익률/변동성), "라벨 없음으로 제외된 거래: 0건", "=== 전체 풀링 비교 ==="
표(3건의 synthetic 거래가 상태별로 나뉘어 표시), "=== 'N' 진입 거래 평균수익률
상위 10 ===" 섹션들(표본이 `MIN_TRADES_FOR_RANKING=5` 미만이라 "표본 부족으로
랭킹 제외된 run: 1개"로 나올 수 있음 — 정상, synthetic 데이터는 완주 여부만
확인하는 목적).

### Step 3: 커밋

```bash
git add scripts/analyze_regime_hmm_fact_performance.py
git commit -m "feat: HMM 비지도 클러스터링 fact 성과 검증 스크립트 추가"
```

---

## Task 3: 실데이터 실행 + 백로그 문서 반영

**Files:**
- Modify: `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: Task 2 스크립트의 실제 콘솔 출력(KRW-BTC/KRW-XLM 실데이터).

### Step 1: 실데이터 실행

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/analyze_regime_hmm_fact_performance.py`

마켓 2개·전체 기간 1회 fit(20마켓 walk-forward 재학습과 달리 가볍다 — 데이터는
2026-08-30 fact 라벨 백테스트 세션이 이미 같은 `MARKETS`/`TIMEFRAME`/`START`/`END`로
캐시해뒀을 가능성이 높아 네트워크 재조회가 적을 것으로 예상). 수 분 내 완료가
예상되나, 캐시 미스로 오래 걸리면 `run_in_background: true`로 전환해 완료까지
기다린다. 콘솔에 출력된 마켓별 상태 프로파일 + 풀링 비교 표(상태별 승률/평균
수익률/총수익기여) + run 랭킹을 그대로 기록해둔다.

### Step 2: `docs/regime-ml-backlog.md`에 결과 반영

파일 최상단 도입부 단락(1~17번째 줄, "# 장세 판별 ML — 잔여 작업 백로그
(2026-09-01 갱신 10)"로 시작하는 부분)의 제목을 `(2026-09-05 갱신 11)`로
바꾸고, 마지막 문장 뒤에 이번 라운드 한 줄 요약을 이어붙인다(기존 관례 —
각 갱신이 이전 갱신 서술 뒤에 이어붙는 형식).

그 다음, "## 메타 레이블링(c-3)..." 절 **바로 위**에 새 절을 추가한다(가장
최근 라운드가 파일 맨 위에 쌓이는 기존 관례). 정확한 숫자는 Step 1 실행
결과로 채운다 — 아래는 형식 예시이며 실제 숫자로 교체할 것:

```markdown
## HMM 비지도 클러스터링 대안 검증 (2026-09-05, 메타 레이블링 미채택 직후) — 완료

설계: `docs/superpowers/specs_v1/2026-09-05-regime-hmm-unsupervised-clustering-design.md`.
계획: `docs/superpowers/plans_v1/2026-09-05-regime-hmm-unsupervised-clustering.md`. 구현:
`engine/regime_ml_hmm.py`(`compute_dominant_state` 추가),
`scripts/analyze_regime_hmm_fact_performance.py`.

지도학습(Triple Barrier 라벨 + LightGBM) 4개 방향이 전부 실패한 뒤, 라벨링
자체를 버리는 구조적 대안(HMM 비지도 클러스터링)이 매매 성과와 상관이 있는지
사후 분석으로 확인했다. 2026-08-30에 방치돼 있던 HMM 순수함수(`engine/
regime_ml_hmm.py`)를 재사용, 마켓별(KRW-BTC/KRW-XLM) 로그수익률+변동성 2변수로
Gaussian HMM(n_states=3)을 전체 기간 한 번에 fit해 바별 지배적 상태를 뽑고,
2026-08-30 fact 라벨 백테스트와 동일한 방법론으로 저장된 거래를 상태별로
재분류했다.

**상태 프로파일(평균 수익률/변동성)**:

| 마켓 | state | 평균 수익률 | 평균 변동성 |
|---|---|---|---|
| KRW-BTC | 0 | <실측값> | <실측값> |
| KRW-BTC | 1 | <실측값> | <실측값> |
| KRW-BTC | 2 | <실측값> | <실측값> |
| KRW-XLM | 0 | <실측값> | <실측값> |
| KRW-XLM | 1 | <실측값> | <실측값> |
| KRW-XLM | 2 | <실측값> | <실측값> |

**상태별 실전 매매 성과(풀링, 저장된 백테스트 거래 재분류)**:

| state | 거래수 | 승률 | 평균수익률 | 총수익기여 |
|---|---|---|---|---|
| 0 | <실측값> | <실측값> | <실측값> | <실측값> |
| 1 | <실측값> | <실측값> | <실측값> | <실측값> |
| 2 | <실측값> | <실측값> | <실측값> | <실측값> |

**결론**: <기존 지도학습 기준값(하락 41.6%/총기여 -17.7% vs 하락아님 73.2%/
총기여 +1767.1%)과 비교해 상태 간 격차가 이에 견줄 만큼 뚜렷한지 서술 —
뚜렷하면 "유의미, Phase 2(프로덕션 반영 여부 브레인스토밍) 검토 필요", 애매하면
"미채택 — 5연속 실패, 다음은 AWS 배포·실전 활용 전환(백로그 (b) 후보)만 남음">.
```

### Step 3: 커밋

```bash
git add docs/regime-ml-backlog.md
git commit -m "docs: HMM 비지도 클러스터링 대안 검증 결과 반영"
```

---

## 범위 밖

- 프로덕션 반영(`backend/regime_ml_service.py`를 HMM 기반으로 교체) — 이번
  결과가 뚜렷하게 좋을 때만 별도 세션에서 브레인스토밍.
- 상태별 최적 전략 자동 매핑, 실시간 자동 전략전환(백로그 우선순위2 "③").
- 워크포워드(fold별 fit) 방식의 "라이브 예측용" HMM.
- HMM 입력에 거래량 추가, `n_states` 그리드서치(3/4/5 비교).
- KRW-BTC/KRW-XLM 외 마켓 확장(저장된 백테스트 결과가 없어 거래 데이터 자체가
  없음).
