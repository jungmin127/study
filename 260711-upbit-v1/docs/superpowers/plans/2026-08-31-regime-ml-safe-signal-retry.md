# 장세 판별 ML — 안전 신호 재시도(캘린더 3개 + 환율 1개) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/regime-ml-backlog.md` 우선순위0 조사(eta² 기반 fold-leakage 정량화)에서 안전하다고 확인된 신호만 재시도한다 — 캘린더 3개(시간대/요일/월중, `MONTH_SIN/COS` 제외) + 환율 1개(`USDKRW_RETURN`, `USDKRW_VOLATILITY`/`UPBIT_FX_SPREAD` 제외). 두 그룹 다 이전 라운드(2026-08-31)에서 다른 위험 신호와 묶여서 그룹째 폐기됐을 뿐, 개별로는 eta²≈0(fold와 거의 안 얽힘)이라 재시도 가치가 있다.

**Architecture:** `engine/regime_ml_features.py::build_feature_matrix()`에 신호를 다시 추가하고(이전 라운드와 동일한 삽입 위치/수식), `scripts/train_regime_ml.py` 실데이터 walk-forward 재학습으로 kappa를 측정해 채택/폐기를 결정한다. 코드 구조는 이전 라운드(`docs/superpowers/plans/2026-08-31-regime-ml-macro-calendar-features.md`)와 완전히 동일 — 이번엔 이미 위험하다고 확인된 신호(MONTH_SIN/COS, USDKRW_VOLATILITY, UPBIT_FX_SPREAD, 금리 3종)를 처음부터 제외하고 시작한다.

**Tech Stack:** Python, pandas, numpy, LightGBM, pytest.

## Global Constraints

- 평가지표: `scripts/train_regime_ml.py`가 리포트하는 **pooled weighted kappa** 1순위, **macro F1** 2순위. baseline: **0.096**(현재 라이브 배포 모델과 동일 — 지난 3라운드(캘린더/환율/금리)가 전부 폐기돼 baseline 변화 없음).
- 실데이터 재학습 커맨드: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py` (저장소 루트, 10~20분 소요 가능, 네트워크로 20개 마켓 캔들+외부데이터 조회).
- **각 태스크는 자체 커밋**. 실데이터 ablation에서 kappa가 baseline 미만이면 `git revert HEAD`로 되돌리고 다음 태스크로 진행(재확인 질문 없이 자동 진행 — 기존 라운드와 동일 정책).
- **어떻게 실행하고 기다릴지**: 백그라운드로 띄워놓고 응답을 끝내면 안 된다(이전 라운드 Task 4에서 실제로 발생한 사고 — subagent가 백그라운드 실행 후 응답을 끝내버려 컨트롤러가 수동으로 재개시켜야 했음). 전경(foreground) `Bash` 호출에 긴 timeout(예: 1800000ms)을 주거나, 백그라운드로 띄웠으면 그 안에서 직접 폴링하며 기다릴 것 — 실제 kappa 숫자를 손에 넣기 전까지 응답을 끝내지 말 것.
- 캘린더 피처는 KST(Asia/Seoul) 기준.
- 기존 테스트(`PYTHONPATH=. python -m pytest tests/ -q`)는 각 태스크 끝에서 항상 전부 통과해야 한다.
- 알려진 gotcha: `tests/test_train_regime_ml.py`/`tests/test_regime_ml_service.py`의 합성 fixture가 새 raw 컬럼(`usdkrw_rate_value` 등)을 안 갖고 있을 수 있다(이전 라운드에서 발견, revert로 매번 원복됨) — Task 2에서 전체 스위트가 이 이유로 깨지면 그 두 파일의 fixture에 한 줄씩만(값은 `1300.0 + np.cumsum(rng.normal(0, 1.0, n))`) 최소 침습으로 추가할 것.
- 한국어 docstring/주석 관례 유지("왜"를 설명).

---

## Task 1: 캘린더 신호 재시도 — HOUR/DOW/DAY_OF_MONTH(6개 컬럼, MONTH 제외)

**Files:**
- Modify: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`

**Interfaces:**
- Produces: `build_feature_matrix()` 반환값에 `HOUR_SIN`, `HOUR_COS`, `DOW_SIN`, `DOW_COS`, `DAY_OF_MONTH_SIN`, `DAY_OF_MONTH_COS` 컬럼 추가(`MONTH_SIN`/`MONTH_COS`는 이번에도 포함하지 않음 — eta²=0.61로 확인된 위험 신호)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`의 `test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features`의 `expected_columns`를 교체:

```python
    expected_columns = (
        (set(LIVE_INDICATOR_FACTORY.keys()) - {"OBV", "FEAR_GREED_CMC"})
        | {
            "RAW_SCORE", "VOLUME_CONFIRM", "VPIN_SCORE", "LEVEL_PROXIMITY", "REVERSAL_GATE",
            "VOLATILITY_PERCENTILE", "LIQUIDITY_PERCENTILE", "market",
            "HOUR_SIN", "HOUR_COS", "DOW_SIN", "DOW_COS", "DAY_OF_MONTH_SIN", "DAY_OF_MONTH_COS",
        }
    )
```

파일 끝에 신규 테스트 추가:

```python
def test_build_feature_matrix_calendar_features_match_kst_sin_cos_formula():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    kst_time = df["candle_time"].dt.tz_convert("Asia/Seoul")
    expected_hour_sin = np.sin(2 * np.pi * kst_time.dt.hour / 24)
    expected_dow_cos = np.cos(2 * np.pi * kst_time.dt.dayofweek / 7)
    expected_day_cos = np.cos(2 * np.pi * (kst_time.dt.day - 1) / 31)

    pd.testing.assert_series_equal(
        result["HOUR_SIN"].reset_index(drop=True), expected_hour_sin.reset_index(drop=True), check_names=False
    )
    pd.testing.assert_series_equal(
        result["DOW_COS"].reset_index(drop=True), expected_dow_cos.reset_index(drop=True), check_names=False
    )
    pd.testing.assert_series_equal(
        result["DAY_OF_MONTH_COS"].reset_index(drop=True), expected_day_cos.reset_index(drop=True), check_names=False
    )


def test_build_feature_matrix_hour_sin_is_continuous_across_kst_midnight():
    """KST 23시->0시 전환처럼 raw hour 값은 23->0으로 불연속이지만, sin 인코딩은
    실제 1시간 차이만큼만 작게 움직여야 한다."""
    dates = pd.to_datetime(["2024-01-01 14:00", "2024-01-01 15:00"], utc=True)  # UTC 14/15시 -> KST 23시/(다음날)0시
    df = _make_full_df().iloc[:2].copy()
    df["candle_time"] = dates

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    diff_across_midnight = abs(result["HOUR_SIN"].iloc[0] - result["HOUR_SIN"].iloc[1])
    assert diff_across_midnight < 0.3  # sin(2π*23/24)≈-0.259, sin(0)=0 -> 실제 차이≈0.259
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — 기존 exact-match 테스트가 컬럼 집합 불일치, 신규 테스트는 `KeyError: 'HOUR_SIN'`

- [ ] **Step 3: `engine/regime_ml_features.py` 수정**

파일 상단 import에 `numpy`를 추가:

```python
from __future__ import annotations

import numpy as np
import pandas as pd
```

`build_feature_matrix` 함수 끝, `result = pd.DataFrame(features, index=df.index)` 이전에 추가:

```python
    # 캘린더 피처 재시도(2026-08-31, 우선순위0 조사 결과 반영) — 2026-08-31 첫
    # 시도 때는 시간대/요일/월/월중 8개를 한 그룹으로 묶어 전부 폐기했었다.
    # eta²(fold간 분산 비율) 실측 결과 MONTH_SIN/COS만 0.61로 fold와 강하게
    # 얽혀 있고(학습 구간이 2.6년뿐이라 fold 하나(~5개월)가 12개월 주기를 다
    # 못 채워서 생기는 문제) HOUR/DOW/DAY_OF_MONTH는 전부 0.002 이하였다 —
    # MONTH_SIN/COS를 빼고 나머지 3개 신호만 재시도한다
    # (docs/regime-ml-backlog.md 우선순위0 결론 참고).
    kst_time = df["candle_time"].dt.tz_convert("Asia/Seoul")
    features["HOUR_SIN"] = np.sin(2 * np.pi * kst_time.dt.hour / 24)
    features["HOUR_COS"] = np.cos(2 * np.pi * kst_time.dt.hour / 24)
    features["DOW_SIN"] = np.sin(2 * np.pi * kst_time.dt.dayofweek / 7)
    features["DOW_COS"] = np.cos(2 * np.pi * kst_time.dt.dayofweek / 7)
    features["DAY_OF_MONTH_SIN"] = np.sin(2 * np.pi * (kst_time.dt.day - 1) / 31)
    features["DAY_OF_MONTH_COS"] = np.cos(2 * np.pi * (kst_time.dt.day - 1) / 31)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_ml_features.py tests/test_regime_ml_features.py
git commit -m "feat: KST 캘린더 신호 재시도(시간대/요일/월중, MONTH 제외)"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

- **kappa >= 0.096(baseline)이면 채택** — 이 kappa를 Task 2의 새 baseline으로 기록.
- **kappa < 0.096이면 폐기** — `git revert HEAD`, `PYTHONPATH=. python -m pytest tests/ -q`로 재확인. baseline은 0.096 유지.

이번엔 신호가 3개뿐이고 이미 개별 eta²로 사전검증됐으므로, 채택되더라도 추가 leave-one-out은 생략하고 바로 Task 2로 진행한다(위험도가 낮다고 이미 확인된 신호들이라 태스크당 학습 1회로 충분 — 재검토가 필요하다고 판단되면 컨트롤러가 별도로 지시).

---

## Task 2: 환율 신호 재시도 — USDKRW_RETURN만(1개 컬럼)

**Files:**
- Modify: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`
- Modify(gotcha 발생시만): `tests/test_train_regime_ml.py`, `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes: `usdkrw_rate_value` 원시 컬럼(Task 3 인프라, `engine/regime_ml_data.py`에 이미 배선되어 있음 — 별도 작업 불필요)
- Produces: `build_feature_matrix()` 반환값에 `USDKRW_RETURN` 컬럼 추가

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`의 `_make_full_df()`(Task 1 완료 후 버전)에 컬럼 추가:

```python
        "usdkrw_rate_value": 1300.0 + np.cumsum(rng.normal(0, 1.0, _N)),
```

(기존 딕셔너리 리터럴의 `"korea_premium_value": rng.uniform(-2, 2, _N),` 다음 줄에 추가)

`expected_columns`에 키 추가(Task 1에서 만든 집합에 이어서):

```python
            "USDKRW_RETURN",
```

파일 끝에 신규 테스트 추가:

```python
def test_build_feature_matrix_usdkrw_return_matches_pct_change():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected = df["usdkrw_rate_value"].pct_change(fill_method=None)
    pd.testing.assert_series_equal(
        result["USDKRW_RETURN"].reset_index(drop=True), expected.reset_index(drop=True), check_names=False
    )
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — `KeyError: 'USDKRW_RETURN'` 등

- [ ] **Step 3: `engine/regime_ml_features.py` 수정**

Task 1에서 추가한 캘린더 블록 다음(같은 함수 안, `result = pd.DataFrame(features, index=df.index)` 이전)에 추가:

```python
    # 환율 피처 재시도(2026-08-31, 우선순위0 조사 결과 반영) — 2026-08-31 첫
    # 시도 때는 USDKRW_RETURN/USDKRW_VOLATILITY/UPBIT_FX_SPREAD 3개를 한
    # 그룹으로 묶어 전부 폐기했었다. eta² 실측 결과 USDKRW_RETURN(진짜
    # t 대 t-1 시간축 차분)만 0.0001로 완전히 안전했고, USDKRW_VOLATILITY(0.06)와
    # UPBIT_FX_SPREAD(0.31, 사실상 레벨형 지표)는 fold와 얽혀 있었다 —
    # USDKRW_RETURN만 재시도한다(docs/regime-ml-backlog.md 우선순위0 결론 참고).
    features["USDKRW_RETURN"] = df["usdkrw_rate_value"].pct_change(fill_method=None)
```

`build_feature_matrix`의 docstring(파일 상단)에 `usdkrw_rate_value`를 필요 컬럼 목록에 추가:

```
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value/usdkrw_rate_value를
    전부 포함해야 한다(engine.regime_ml_data.load_market_training_data()가
    반환하는 형태). 반환 DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업
    구간은 NaN), 원본 OHLCV 컬럼은 포함하지 않는다(피처 전용) — market
    범주형 컬럼만 추가한다."""
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS. `usdkrw_rate_value`가 없는 합성 fixture(`tests/test_train_regime_ml.py`, `tests/test_regime_ml_service.py`)가 `KeyError`로 깨지면, Global Constraints에 적힌 대로 그 두 파일의 fixture에 `usdkrw_rate_value` 컬럼 한 줄씩만 최소 침습으로 추가한다.

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_ml_features.py tests/test_regime_ml_features.py
git commit -m "feat: 원/달러 환율 수익률(USDKRW_RETURN) 신호 재시도"
```

(gotcha로 다른 파일도 고쳤다면 `git add`에 그 파일들도 포함)

- [ ] **Step 7: 실데이터 ablation 실행 및 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

Task 1 종료 시점 baseline(채택 시 그 kappa, 폐기 시 0.096)과 비교한다.

- **개선/유지되면 채택**.
- **악화되면 폐기** — `git revert HEAD`, 테스트 재확인.

신호가 1개뿐이라 leave-one-out은 해당 없음(그룹=1개 신호이므로 폐기 여부 결정 자체가 곧 그 신호에 대한 결정).

- [ ] **Step 8: 최종 요약 보고 및 백로그 갱신**

이 태스크(또는 컨트롤러)가 아래 내용을 `docs/regime-ml-backlog.md`에 기록한다(이 파일은 git 추적 대상 아님, 커밋 불필요): Task 1(캘린더 3신호)/Task 2(USDKRW_RETURN) 각각의 채택/폐기 여부와 최종 kappa, 세션 시작 baseline(0.096) 대비 변화.

---

## 범위 밖

- `MONTH_SIN`/`MONTH_COS`, `USDKRW_VOLATILITY`, `UPBIT_FX_SPREAD`, 금리 3종(`US_KR_RATE_SPREAD`/`YIELD_CURVE_SPREAD`/`HOURS_SINCE_RATE_DECISION`) — eta² 조사로 위험 확인됨, 이번 재시도 대상 아님.
- 코스피/코스닥/S&P500/다우존스/나스닥 지수 피처 — 별도 브레인스토밍 필요(사용자가 이미 제안, 백로그에 다음 후보로 기록됨).
- c-2(로지스틱회귀 baseline + LightGBM 하이퍼파라미터 튜닝) — 이 재시도 완료 후 별도 진행.
