# 장세 판별 ML — 주가지수(S&P500/다우/나스닥) 수익률 피처 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-08-31-regime-ml-stock-index-features-design.md`에서 승인된 설계대로 S&P500/다우존스/나스닥 3개 지수의 일간 수익률(`SP500_RETURN`/`DJIA_RETURN`/`NASDAQ_RETURN`)을 장세 판별 ML 피처로 추가하고, 실데이터 walk-forward 재학습으로 채택/폐기를 결정한다.

**Architecture:** `macro_data_service.py`(FRED CSV fetch, 기존 4개 시리즈와 동일 헬퍼 재사용)에 3개 시리즈를 추가 → `engine/regime_ml_data.py::load_market_training_data`가 원시 컬럼 3개를 병합 → `engine/regime_ml_features.py::build_feature_matrix`가 `pct_change()`만으로 3개 피처를 계산 → `scripts/train_regime_ml.py` 실데이터 재학습으로 pooled weighted kappa를 측정해 채택/폐기.

**Tech Stack:** Python, pandas, httpx, LightGBM, pytest.

## Global Constraints

- 평가지표: `scripts/train_regime_ml.py`가 리포트하는 **pooled weighted kappa** 1순위, **macro F1** 2순위. baseline: **0.106**(캘린더3+환율1 채택 상태, `docs/regime-ml-backlog.md` "안전 신호 재시도" 절).
- eta² 사전측정 완료(설계 문서 참고) — `SP500_RETURN`/`DJIA_RETURN`/`NASDAQ_RETURN` 전부 eta²=0.0002로 안전 판정. **leave-one-out은 생략**(개별 위험도 차이가 없어 그룹 내부에 숨은 위험 신호가 섞여 있을 가능성이 낮다는 판단 — 안전 신호 재시도 라운드와 동일 기준).
- 실데이터 재학습 커맨드: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`(저장소 루트, 10~20분 소요 가능, 네트워크로 20개 마켓 캔들+외부데이터 조회). **백그라운드로 띄워놓고 응답을 끝내면 안 된다** — 전경 `Bash` 호출에 긴 timeout(예: 1800000ms)을 주거나, 백그라운드로 띄웠으면 그 안에서 직접 폴링하며 기다릴 것. 실제 kappa 숫자를 손에 넣기 전까지 태스크를 끝내지 말 것.
- 각 태스크는 자체 커밋. **Task 3의 실데이터 ablation에서 kappa가 baseline(0.106) 미만이면 Task 1~3의 커밋 3개를 전부 `git revert`로 되돌린다**(가장 최근 커밋부터 순서대로) — 재확인 질문 없이 자동 진행.
- 기존 테스트(`PYTHONPATH=. python -m pytest tests/ -q`)는 각 태스크 끝에서 항상 전부 통과해야 한다.
- 알려진 gotcha(과거 3라운드에서 반복 확인됨): `tests/test_train_regime_ml.py`/`tests/test_regime_ml_service.py`의 합성 fixture가 `build_feature_matrix`에 전달되는 전체 컬럼 집합을 갖고 있어야 한다 — Task 3에서 신규 raw 컬럼 3개를 이 두 파일의 fixture에도 미리 추가한다(과거처럼 "깨지면 그때 고치기"가 아니라 이번엔 처음부터 포함).
- 코스피/코스닥 지수는 이번 스코프 아님(FRED에 없음, 데이터 소스 미정) — 절대 시도하지 말 것.
- 한국어 docstring/주석 관례 유지("왜"를 설명).

---

## Task 1: `macro_data_service.py` — S&P500/다우/나스닥 FRED 시리즈 fetch 함수 추가

**Files:**
- Modify: `macro_data_service.py`
- Test: `tests/test_macro_data_service.py`

**Interfaces:**
- Consumes: 기존 `_get_fred_series(name, series_id, value_col, start, end)` 헬퍼(변경 없음)
- Produces: `get_sp500_index(start, end) -> pd.DataFrame`(columns: `date`, `sp500_close_value`), `get_djia_index(start, end) -> pd.DataFrame`(columns: `date`, `djia_close_value`), `get_nasdaq_index(start, end) -> pd.DataFrame`(columns: `date`, `nasdaq_close_value`) — 3개 함수 모두 기존 `merge_fred_series(df, series_df, value_col)`로 병합 가능한 형태.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_macro_data_service.py`의 import 블록(`from macro_data_service import (...)`)에 3개 함수 추가:

```python
from macro_data_service import (
    _fetch_fred_csv,
    _fetch_frankfurter_json,
    _parse_fred_csv,
    _parse_frankfurter_json,
    get_djia_index,
    get_fed_funds_rate,
    get_kr_call_rate,
    get_nasdaq_index,
    get_sp500_index,
    get_usdkrw_rate,
    get_us_yield_curve_spread,
    merge_fred_series,
    merge_usdkrw_rate,
)
```

파일 끝에 신규 테스트 추가:

```python
def test_get_stock_index_functions_use_distinct_cache_files_and_series_ids(monkeypatch, tmp_path):
    """S&P500/다우/나스닥 3개 함수가 서로 다른 FRED series id/캐시 파일을 쓰는지
    확인한다 - _get_fred_series 공용 헬퍼 재사용(get_us_yield_curve_spread 등과
    동일 패턴)."""
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    captured_ids = []

    def fake_fetch(client, series_id, start, end):
        captured_ids.append(series_id)
        return "observation_date,TEMP\n2024-01-01,1.0\n"

    monkeypatch.setattr(mds, "_fetch_fred_csv", fake_fetch)

    get_sp500_index(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
    get_djia_index(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
    get_nasdaq_index(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))

    assert captured_ids == ["SP500", "DJIA", "NASDAQCOM"]
    assert (tmp_path / "fred_sp500.parquet").exists()
    assert (tmp_path / "fred_djia.parquet").exists()
    assert (tmp_path / "fred_nasdaq.parquet").exists()


def test_get_sp500_index_filters_to_requested_range(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-02-01", today.date()], utc=True),
        "sp500_close_value": [4700.0, 4900.0, 5500.0],
    })
    cached.to_parquet(tmp_path / "fred_sp500.parquet", index=False)
    monkeypatch.setattr(mds, "_fetch_fred_csv", _fail_fetch)

    result = get_sp500_index(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 2, 1, tzinfo=timezone.utc))

    assert result["sp500_close_value"].tolist() == [4700.0, 4900.0]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_macro_data_service.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_sp500_index'`

- [ ] **Step 3: `macro_data_service.py` 수정**

시리즈 ID 상수 블록(`KR_CALL_RATE_SERIES_ID = "IRSTCI01KRM156N"` 다음 줄)에 추가:

```python
SP500_SERIES_ID = "SP500"
DJIA_SERIES_ID = "DJIA"
NASDAQ_SERIES_ID = "NASDAQCOM"
```

`get_kr_call_rate` 함수 다음(같은 파일, `merge_fred_series` 정의 이전)에 추가:

```python
def get_sp500_index(start: datetime, end: datetime) -> pd.DataFrame:
    """S&P500 일간 종가(FRED SP500). 2026-08-31 주가지수 수익률 피처 추가 라운드 —
    eta² 사전측정에서 pct_change() 형태가 USDKRW_RETURN과 동급으로 안전 확인됨
    (docs/regime-ml-backlog.md 우선순위0 액션아이템 2번 참고). 레벨 그대로는
    피처로 쓰지 않는다 — build_feature_matrix에서 pct_change만 계산."""
    return _get_fred_series("fred_sp500", SP500_SERIES_ID, "sp500_close_value", start, end)


def get_djia_index(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_djia", DJIA_SERIES_ID, "djia_close_value", start, end)


def get_nasdaq_index(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_nasdaq", NASDAQ_SERIES_ID, "nasdaq_close_value", start, end)
```

`__all__` 리스트에 3개 추가:

```python
__all__ = [
    "get_fed_funds_rate",
    "get_us_yield_curve_spread",
    "get_kr_call_rate",
    "merge_fred_series",
    "get_usdkrw_rate",
    "merge_usdkrw_rate",
    "get_sp500_index",
    "get_djia_index",
    "get_nasdaq_index",
]
```

파일 상단 모듈 docstring 첫 문단에 지수 3개를 언급하도록 갱신(선택 사항이 아님 — 다음 세션에서 이 파일을 다시 볼 사람을 위해 필수):

```
macro_data_service.py

업비트/바이낸스가 아닌 거시경제 지표(미국 기준금리, 미국 장단기 국채금리차, 한국
콜금리, 원/달러 공식환율, S&P500/다우존스/나스닥종합 일간 종가)를 무료 공개
API에서 조회·캐싱한다. 재시도/캐싱 패턴은 external_data_service.py(공포탐욕지수)를
그대로 따른다. 두 provider(FRED, Frankfurter) 모두 API 키가 필요 없다. 설계 문서:
docs/superpowers/specs_v1/2026-08-31-regime-ml-macro-calendar-features-design.md,
docs/superpowers/specs_v1/2026-08-31-regime-ml-stock-index-features-design.md
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_macro_data_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add macro_data_service.py tests/test_macro_data_service.py
git commit -m "feat: S&P500/다우/나스닥 FRED 종가 시리즈 fetch 함수 추가"
```

---

## Task 2: `engine/regime_ml_data.py` — 3개 원시 컬럼 학습 데이터 로더 배선

**Files:**
- Modify: `engine/regime_ml_data.py`
- Test: `tests/test_regime_ml_data.py`

**Interfaces:**
- Consumes: Task 1의 `get_sp500_index`/`get_djia_index`/`get_nasdaq_index`, 기존 `merge_fred_series`
- Produces: `load_market_training_data()` 반환 DataFrame에 `sp500_close_value`, `djia_close_value`, `nasdaq_close_value` 원시 컬럼 추가

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_data.py`의 `_patch_common()` 함수 끝(`get_usdkrw_rate` monkeypatch 다음 줄)에 추가:

```python
    monkeypatch.setattr(regime_ml_data, "get_sp500_index", lambda *a, **k: pd.DataFrame(columns=["date", "sp500_close_value"]))
    monkeypatch.setattr(regime_ml_data, "get_djia_index", lambda *a, **k: pd.DataFrame(columns=["date", "djia_close_value"]))
    monkeypatch.setattr(regime_ml_data, "get_nasdaq_index", lambda *a, **k: pd.DataFrame(columns=["date", "nasdaq_close_value"]))
```

`test_load_market_training_data_has_all_required_columns`의 `required` 집합에 3개 추가:

```python
    required = {
        "close", "high", "low", "volume", "trade_value",
        "btc_close", "usdt_close", "binance_close",
        "fear_greed_value", "funding_rate_value", "korea_premium_value",
        "fed_funds_rate_value", "treasury_yield_spread_value", "kr_call_rate_value", "usdkrw_rate_value",
        "sp500_close_value", "djia_close_value", "nasdaq_close_value",
    }
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_data.py -v`
Expected: FAIL — `AttributeError: <module 'engine.regime_ml_data'> does not have the attribute 'get_sp500_index'`(monkeypatch 대상 함수가 아직 없음)

- [ ] **Step 3: `engine/regime_ml_data.py` 수정**

`from macro_data_service import (...)` 블록을 교체:

```python
from macro_data_service import (
    get_djia_index,
    get_fed_funds_rate,
    get_kr_call_rate,
    get_nasdaq_index,
    get_sp500_index,
    get_usdkrw_rate,
    get_us_yield_curve_spread,
    merge_fred_series,
    merge_usdkrw_rate,
)
```

`df = merge_usdkrw_rate(df, usdkrw_df)` 다음 줄(함수 끝, `return df` 이전)에 추가:

```python
    sp500_df = get_sp500_index(start, end)
    df = merge_fred_series(df, sp500_df, "sp500_close_value")

    djia_df = get_djia_index(start, end)
    df = merge_fred_series(df, djia_df, "djia_close_value")

    nasdaq_df = get_nasdaq_index(start, end)
    df = merge_fred_series(df, nasdaq_df, "nasdaq_close_value")
```

`load_market_training_data`의 docstring 마지막 문단을 갱신:

```
    2026-08-31 캘린더/거시경제 피처 추가 라운드에서 미국 기준금리/미국 장단기
    국채금리차/한국 콜금리(기준금리 대리지표)/원-달러 공식환율 4개 원시 컬럼도
    함께 병합한다(macro_data_service.py, FRED+Frankfurter, 둘 다 API 키 불필요).
    같은 날 이어진 주가지수 라운드에서 S&P500/다우존스/나스닥종합 일간 종가
    3개 원시 컬럼도 추가로 병합한다(전부 FRED, 신규 provider 없음).
    """
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_data.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_ml_data.py tests/test_regime_ml_data.py
git commit -m "feat: S&P500/다우/나스닥 원시 종가 컬럼 학습 로더 배선"
```

---

## Task 3: `engine/regime_ml_features.py` — 3개 수익률 피처 추가 + 실데이터 ablation

**Files:**
- Modify: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`
- Modify(fixture 동기화, gotcha 선반영): `tests/test_train_regime_ml.py`, `tests/test_regime_ml_service.py`

**Interfaces:**
- Consumes: Task 2의 `sp500_close_value`/`djia_close_value`/`nasdaq_close_value` 원시 컬럼
- Produces: `build_feature_matrix()` 반환값에 `SP500_RETURN`, `DJIA_RETURN`, `NASDAQ_RETURN` 컬럼 추가

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`의 `_make_full_df()`(`"usdkrw_rate_value": ...` 다음 줄)에 3개 컬럼 추가:

```python
        "sp500_close_value": 4700.0 + np.cumsum(rng.normal(0, 10.0, _N)),
        "djia_close_value": 37000.0 + np.cumsum(rng.normal(0, 50.0, _N)),
        "nasdaq_close_value": 14500.0 + np.cumsum(rng.normal(0, 30.0, _N)),
```

`expected_columns`에 3개 키 추가(기존 `"USDKRW_RETURN",` 다음 줄):

```python
            "SP500_RETURN", "DJIA_RETURN", "NASDAQ_RETURN",
```

파일 끝에 신규 테스트 추가:

```python
def test_build_feature_matrix_stock_index_returns_match_pct_change():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    for column, source in [
        ("SP500_RETURN", "sp500_close_value"),
        ("DJIA_RETURN", "djia_close_value"),
        ("NASDAQ_RETURN", "nasdaq_close_value"),
    ]:
        expected = df[source].pct_change(fill_method=None)
        pd.testing.assert_series_equal(
            result[column].reset_index(drop=True), expected.reset_index(drop=True), check_names=False
        )
```

`tests/test_train_regime_ml.py`의 `_make_synthetic_market_df()`(`"usdkrw_rate_value": ...` 다음 줄)에 동일하게 추가:

```python
        "sp500_close_value": 4700.0 + np.cumsum(rng.normal(0, 10.0, _N)),
        "djia_close_value": 37000.0 + np.cumsum(rng.normal(0, 50.0, _N)),
        "nasdaq_close_value": 14500.0 + np.cumsum(rng.normal(0, 30.0, _N)),
```

`tests/test_regime_ml_service.py`의 `_make_synthetic_ohlcv_df()`(`"usdkrw_rate_value": ...` 다음 줄)에도 동일하게 추가(단 `_N` 대신 이 파일의 로컬 변수명 `n` 사용):

```python
        "sp500_close_value": 4700.0 + np.cumsum(rng.normal(0, 10.0, n)),
        "djia_close_value": 37000.0 + np.cumsum(rng.normal(0, 50.0, n)),
        "nasdaq_close_value": 14500.0 + np.cumsum(rng.normal(0, 30.0, n)),
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — 컬럼 집합 불일치 / `KeyError: 'sp500_close_value'`

- [ ] **Step 3: `engine/regime_ml_features.py` 수정**

`features["USDKRW_RETURN"] = df["usdkrw_rate_value"].pct_change(fill_method=None)` 다음 줄(`result = pd.DataFrame(features, index=df.index)` 이전)에 추가:

```python

    # 주가지수 수익률 피처(2026-08-31, 사용자 제안 + 우선순위0 eta² 사전측정
    # 완료) — KRW-ETH 1개 마켓 5-fold eta² 실측 결과 SP500/DJIA/NASDAQCOM
    # 종가의 pct_change() 전부 0.0002로 USDKRW_RETURN(0.0001)과 동급으로
    # 안전 판정됐다 — 백로그가 예상했던 "환율/금리 중간 위험도"보다 훨씬
    # 낮았다(순수 t 대 t-1 시간축 차분이라 우선순위0 결론3의 "진짜 효과
    # 있는 변환" 패턴이 그대로 적용됨). 코스피/코스닥은 FRED에 없어 이번
    # 라운드 제외(docs/superpowers/specs_v1/2026-08-31-regime-ml-stock-index-
    # features-design.md 참고).
    features["SP500_RETURN"] = df["sp500_close_value"].pct_change(fill_method=None)
    features["DJIA_RETURN"] = df["djia_close_value"].pct_change(fill_method=None)
    features["NASDAQ_RETURN"] = df["nasdaq_close_value"].pct_change(fill_method=None)
```

`build_feature_matrix`의 docstring(파일 상단, 필요 컬럼 목록)을 갱신:

```
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value/usdkrw_rate_value/
    sp500_close_value/djia_close_value/nasdaq_close_value를 전부 포함해야 한다
    (engine.regime_ml_data.load_market_training_data()가 반환하는 형태). 반환
    DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업 구간은 NaN), 원본
    OHLCV 컬럼은 포함하지 않는다(피처 전용) — market 범주형 컬럼만 추가한다."""
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py tests/test_train_regime_ml.py tests/test_regime_ml_service.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_ml_features.py tests/test_regime_ml_features.py tests/test_train_regime_ml.py tests/test_regime_ml_service.py
git commit -m "feat: S&P500/다우/나스닥 수익률(SP500_RETURN 등) 피처 추가"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`(전경 실행, 완료까지 대기 — Global Constraints 참고)

- **pooled weighted kappa >= 0.106(baseline)이면 채택** — 이 라운드는 여기서 끝. 채택된 kappa 값을 기록해둔다(Step 8에서 백로그에 반영).
- **kappa < 0.106이면 폐기** — 다음 순서로 되돌린다:
  ```bash
  git revert --no-edit HEAD
  git revert --no-edit HEAD~1
  git revert --no-edit HEAD~2
  ```
  (Task 3 커밋 → Task 2 커밋 → Task 1 커밋 순서로 되돌리는 것 — 3개 커밋 모두 이번 라운드 전용 신규 코드라 부분 채택은 의미 없다.) 이후 `PYTHONPATH=. python -m pytest tests/ -q`로 전체 테스트가 revert 이후에도 전부 통과하는지 재확인.

leave-one-out은 Global Constraints에 적힌 대로 생략한다(eta² 사전측정으로 3개 다 동급 안전 확인됨).

- [ ] **Step 8: 백로그 갱신 및 커밋**

`docs/regime-ml-backlog.md`에 이번 라운드 결과(채택/폐기 여부, 최종 kappa, 우선순위0 액션아이템 2번 완료 표시)를 반영한다. 형식은 기존 "안전 신호 재시도" 절과 동일하게 새 절 추가.

```bash
git add docs/regime-ml-backlog.md
git commit -m "docs: 주가지수(S&P500/다우/나스닥) 수익률 피처 결과 백로그 반영"
```

(폐기됐다면 커밋 메시지를 `"docs: 주가지수 수익률 피처 ablation 결과(폐기) 백로그 반영"`로 바꾼다.)

---

## 범위 밖

- 코스피/코스닥 지수 피처 — FRED에 없음, yfinance는 실제 동작 확인했으나 신규 pip 의존성이라 이번 라운드 보류. 데이터 소스 재조사부터 별도 세션.
- 룩어헤드(미국장 종가가 실제보다 최대 21시간 먼저 병합되는 문제) 수정 — 설계 문서에서 사용자가 기존 패턴 유지로 결정, 이번 스코프 아님.
- leave-one-out(개별 지수 단위 ablation) — eta² 사전측정으로 생략 결정됨.
- c-2(로지스틱회귀 baseline + LightGBM 하이퍼파라미터 튜닝) — 별도 진행.
- 최종 채택 피처 세트의 AWS 라이브 배포 실행 — 검증 종료 후 별도 확인(`[[upbit-v1-deploy-check-open-positions-first]]` 원칙, 배포 전 오픈포지션 확인 필수).
