# 장세 판별 ML 캘린더/환율/금리 피처 추가 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `docs/superpowers/specs_v1/2026-08-31-regime-ml-macro-calendar-features-design.md`에 따라 캘린더(시간대/요일/월/월중), 환율(원/달러), 금리(미국-한국 정책금리 스프레드, 미국 장단기 금리차, 금리결정 경과시간) 3개 그룹 14개 피처를 장세 판별 ML에 추가하고, 각 그룹을 실데이터 walk-forward ablation으로 채택/폐기한다.

**Architecture:** 신규 `macro_data_service.py`가 FRED(무료 CSV, 키불필요)와 Frankfurter(무료 JSON, 키불필요) 두 provider에서 시계열을 조회·캐싱한다. `engine/regime_ml_data.py::load_market_training_data`가 이 원시 컬럼들을 merge_asof(backward)로 병합하고, `engine/regime_ml_features.py::build_feature_matrix`가 그 원시 컬럼 + `candle_time`으로 파생 피처를 계산한다(순수 함수, 기존 구조 그대로). `scripts/train_regime_ml.py`는 코드 변경 없이 그대로 새 컬럼들을 자동으로 학습에 반영한다(features_df의 모든 컬럼이 이미 모델 입력이므로).

**Tech Stack:** Python, pandas, numpy, httpx, LightGBM(`lightgbm.LGBMClassifier`), pytest.

## Global Constraints

- 평가지표: `scripts/train_regime_ml.py`가 리포트하는 **pooled weighted kappa**를 1순위, **macro F1**을 2순위로 판단한다. 현재 baseline: pooled weighted kappa **0.096**, macro F1 **0.534**(`docs/regime-ml-backlog.md`, 20마켓/이진분류/②모델 성능 개선 라운드 종료 시점).
- 실데이터 재학습 커맨드(모든 ablation 태스크 공통): `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py` (저장소 루트에서 실행, 네트워크로 20개 마켓 캔들 + 외부데이터를 불러오므로 수 분~수십 분 소요될 수 있음).
- 각 피처 그룹(캘린더/환율/금리)은 **자체 git 커밋**으로 만든다. 그룹 도입 후 실데이터 ablation에서 kappa가 그 시점 baseline 미만으로 악화되면 `git revert`로 그 그룹의 커밋만 되돌리고 baseline은 그대로 유지한 채 다음 그룹으로 진행한다(재확인 질문 없이 자동 진행 — 스펙에서 이미 승인된 정책, `docs/regime-ml-backlog.md`의 기존 라운드들과 동일). 개선되면 그 kappa가 다음 그룹의 새 baseline이 된다.
- 그룹이 **개선으로 채택**되면, 그 그룹 안의 "신호" 단위로 leave-one-out ablation을 추가로 돌린다. 신호 단위는 **sin/cos 쌍을 하나로 묶은 것**이다 — 캘린더는 시간대/요일/월/월중 4개 신호, 환율은 변동률/변동성/스프레드 3개 신호, 금리는 미한스프레드/장단기금리차/결정경과시간 3개 신호. 개별 sin 또는 cos 컬럼만 따로 빼는 것은 의미가 없다(둘이 합쳐 하나의 주기 신호를 인코딩하므로). leave-one-out에서 특정 신호를 뺐을 때 kappa가 "신호 포함" 대비 개선되면 그 신호는 코드에서 제거, 그 외에는 그대로 유지.
- `macro_data_service.py`는 **API 키가 필요 없다**(FRED `fredgraph.csv`, Frankfurter `api.frankfurter.dev` 둘 다 공개 무료 엔드포인트) — `.env`/`.env.example` 변경 없음.
- 캘린더 피처는 **KST(Asia/Seoul)** 기준으로 계산한다(`candle_time`은 UTC로 저장되어 있으므로 `.dt.tz_convert("Asia/Seoul")` 필요).
- 기존 테스트(`PYTHONPATH=. python -m pytest tests/ -q`, 저장소 루트에서 실행)는 각 태스크 끝에서 항상 전부 통과해야 한다(회귀 없음).
- 한국어 docstring/주석 관례를 그대로 따른다("왜"를 설명, "무엇"은 설명 안 함).
- FRED CSV 실제 응답 포맷(2026-08-31 직접 조회로 확인): 헤더 `observation_date,{SERIES_ID}`, 행 `YYYY-MM-DD,value`(결측/주말은 행 자체가 없음, "." 마커 없음). Frankfurter JSON 응답: `{"amount":1.0,"base":"USD","start_date":"...","end_date":"...","rates":{"YYYY-MM-DD":{"KRW":1234.56}, ...}}`.

---

## Task 1: `macro_data_service.py` — FRED 3개 시리즈 fetch/parse/cache

**Files:**
- Create: `macro_data_service.py`
- Test: `tests/test_macro_data_service.py`

**Interfaces:**
- Produces: `get_fed_funds_rate(start: datetime, end: datetime) -> pd.DataFrame` (columns: `date`, `fed_funds_rate_value`)
- Produces: `get_us_yield_curve_spread(start: datetime, end: datetime) -> pd.DataFrame` (columns: `date`, `treasury_yield_spread_value`)
- Produces: `get_kr_call_rate(start: datetime, end: datetime) -> pd.DataFrame` (columns: `date`, `kr_call_rate_value`)
- Produces: `merge_fred_series(df: pd.DataFrame, series_df: pd.DataFrame, value_col: str) -> pd.DataFrame`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
"""
tests/test_macro_data_service.py

macro_data_service의 FRED(미국 기준금리/장단기금리차/한국 콜금리) fetch/parse/cache/merge를
검증한다. 캐싱/재시도 패턴은 tests/test_external_data_service.py를 그대로 따른다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

import macro_data_service as mds
from macro_data_service import (
    _fetch_fred_csv,
    _parse_fred_csv,
    get_fed_funds_rate,
    get_kr_call_rate,
    get_us_yield_curve_spread,
    merge_fred_series,
)


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _fail_fetch(*args, **kwargs):
    raise AssertionError("캐시가 fresh하면 API를 호출하면 안 됨")


def test_fetch_fred_csv_sends_correct_params_and_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "FEDFUNDS"
        assert request.url.params["cosd"] == "2024-01-01"
        assert request.url.params["coed"] == "2024-03-01"
        return httpx.Response(200, text="observation_date,FEDFUNDS\n2024-01-01,5.33\n2024-02-01,5.33\n")

    with _mock_client(handler) as client:
        text = _fetch_fred_csv(
            client, "FEDFUNDS",
            datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 3, 1, tzinfo=timezone.utc),
        )

    assert "FEDFUNDS" in text


def test_fetch_fred_csv_retries_on_error_then_succeeds(monkeypatch):
    monkeypatch.setattr(mds, "RETRY_BASE_DELAY_SECONDS", 0.0)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(500)
        return httpx.Response(200, text="observation_date,FEDFUNDS\n2024-01-01,5.33\n")

    with _mock_client(handler) as client:
        text = _fetch_fred_csv(client, "FEDFUNDS", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))

    assert calls["count"] == 2
    assert "5.33" in text


def test_fetch_fred_csv_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(mds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            _fetch_fred_csv(client, "FEDFUNDS", datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))


def test_parse_fred_csv_renames_value_column_and_normalizes_date():
    text = "observation_date,FEDFUNDS\n2024-01-01,5.33\n2024-02-01,5.33\n"

    df = _parse_fred_csv(text, "fed_funds_rate_value")

    assert list(df.columns) == ["date", "fed_funds_rate_value"]
    assert df.iloc[0]["fed_funds_rate_value"] == 5.33
    assert df.iloc[0]["date"] == pd.Timestamp("2024-01-01", tz="UTC")
    assert df["date"].dt.unit == "us"


def test_parse_fred_csv_drops_non_numeric_rows():
    text = "observation_date,T10Y2Y\n2024-01-01,.\n2024-01-02,-0.38\n"

    df = _parse_fred_csv(text, "treasury_yield_spread_value")

    assert len(df) == 1
    assert df.iloc[0]["treasury_yield_spread_value"] == -0.38


def test_get_fed_funds_rate_skips_fetch_when_cache_is_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame({"date": pd.to_datetime([today.date()], utc=True), "fed_funds_rate_value": [5.33]})
    cached.to_parquet(tmp_path / "fred_fedfunds.parquet", index=False)
    monkeypatch.setattr(mds, "_fetch_fred_csv", _fail_fetch)

    result = get_fed_funds_rate(today - timedelta(days=1), today)

    assert result.iloc[-1]["fed_funds_rate_value"] == 5.33


def test_get_fed_funds_rate_refetches_when_cache_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    stale_date = datetime.now(timezone.utc) - timedelta(days=40)
    cached = pd.DataFrame({"date": pd.to_datetime([stale_date.date()], utc=True), "fed_funds_rate_value": [1.0]})
    cached.to_parquet(tmp_path / "fred_fedfunds.parquet", index=False)

    def fake_fetch(client, series_id, start, end):
        return "observation_date,FEDFUNDS\n" + f"{datetime.now(timezone.utc).date().isoformat()},9.99\n"

    monkeypatch.setattr(mds, "_fetch_fred_csv", fake_fetch)

    result = get_fed_funds_rate(datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc))

    assert result.iloc[-1]["fed_funds_rate_value"] == 9.99


def test_get_fed_funds_rate_filters_to_requested_range(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-02-01", today.date()], utc=True),
        "fed_funds_rate_value": [5.33, 5.25, 4.50],
    })
    cached.to_parquet(tmp_path / "fred_fedfunds.parquet", index=False)
    monkeypatch.setattr(mds, "_fetch_fred_csv", _fail_fetch)

    result = get_fed_funds_rate(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 2, 1, tzinfo=timezone.utc))

    assert result["fed_funds_rate_value"].tolist() == [5.33, 5.25]


def test_get_us_yield_curve_spread_and_kr_call_rate_use_distinct_cache_files(monkeypatch, tmp_path):
    """세 시리즈가 같은 헬퍼(_get_fred_series)를 공유하므로, 캐시 파일이 서로 다른 이름을
    써서 충돌하지 않는지 확인한다."""
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    captured_ids = []

    def fake_fetch(client, series_id, start, end):
        captured_ids.append(series_id)
        col = "TEMP"
        return f"observation_date,{col}\n2024-01-01,1.0\n"

    monkeypatch.setattr(mds, "_fetch_fred_csv", fake_fetch)

    get_us_yield_curve_spread(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))
    get_kr_call_rate(datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))

    assert captured_ids == ["T10Y2Y", "IRSTCI01KRM156N"]
    assert (tmp_path / "fred_t10y2y.parquet").exists()
    assert (tmp_path / "fred_kr_call_rate.parquet").exists()


def test_merge_fred_series_backward_fills():
    df = pd.DataFrame({
        "candle_time": pd.to_datetime(["2024-01-01 00:00", "2024-01-15 00:00", "2024-02-05 00:00"], utc=True),
        "close": [100.0, 101.0, 102.0],
    })
    series_df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-02-01"], utc=True),
        "fed_funds_rate_value": [5.33, 5.25],
    })

    merged = merge_fred_series(df, series_df, "fed_funds_rate_value")

    assert merged["fed_funds_rate_value"].tolist() == [5.33, 5.33, 5.25]


def test_merge_fred_series_returns_nan_when_series_df_is_empty():
    df = pd.DataFrame({"candle_time": pd.to_datetime(["2024-01-01 00:00"], utc=True), "close": [100.0]})
    series_df = pd.DataFrame(columns=["date", "fed_funds_rate_value"])

    merged = merge_fred_series(df, series_df, "fed_funds_rate_value")

    assert merged["fed_funds_rate_value"].isna().all()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_macro_data_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'macro_data_service'`

- [ ] **Step 3: `macro_data_service.py` 구현**

```python
"""
macro_data_service.py

업비트/바이낸스가 아닌 거시경제 지표(미국 기준금리, 미국 장단기 국채금리차, 한국
콜금리, 원/달러 공식환율)를 무료 공개 API에서 조회·캐싱한다. 재시도/캐싱 패턴은
external_data_service.py(공포탐욕지수)를 그대로 따른다. 두 provider(FRED,
Frankfurter) 모두 API 키가 필요 없다. 설계 문서:
docs/superpowers/specs_v1/2026-08-31-regime-ml-macro-calendar-features-design.md

한국은행 기준금리 자체를 제공하는 무료·키불필요 API가 없어, FRED의
IRSTCI01KRM156N(한국 콜금리/은행간금리, OECD 경유)을 대리지표로 쓴다. 2008년
한국은행이 콜금리를 기준금리의 운영목표로 직접 관리하기 시작한 이후로는 두 값이
사실상 동일하게 움직이지만, 완전히 같은 수치는 아니다.
"""
from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0

CACHE_DIR = Path(__file__).parent / "data" / "cache" / "external"

# 학습 구간(TRAIN_START=2024-01-01)보다 충분히 이전이면 되므로, 전체 히스토리를
# 받을 필요 없이 이 시점부터만 받는다(응답 크기/캐시 갱신 비용 절감).
_HISTORY_START = datetime(2020, 1, 1, tzinfo=timezone.utc)

FED_FUNDS_SERIES_ID = "FEDFUNDS"
YIELD_CURVE_SERIES_ID = "T10Y2Y"
KR_CALL_RATE_SERIES_ID = "IRSTCI01KRM156N"


def _fetch_fred_csv(client: httpx.Client, series_id: str, start: datetime, end: datetime) -> str:
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(
                FRED_CSV_URL,
                params={"id": series_id, "cosd": start.date().isoformat(), "coed": end.date().isoformat()},
            )
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))
    raise RuntimeError(f"FRED {series_id} CSV 호출 실패: {last_exc}")


def _parse_fred_csv(text: str, value_col: str) -> pd.DataFrame:
    """FRED fredgraph.csv 응답(헤더 `observation_date,{SERIES_ID}`)을 파싱한다.
    값 컬럼을 value_col로 통일해 시리즈마다 다른 원래 컬럼명(FEDFUNDS/T10Y2Y/...)을
    가려준다. 결측 마커(".")가 섞여 있어도 숫자 변환 실패로 자연스럽게 NaN이 되고
    dropna로 제거된다(us 캔들 해상도와 맞추는 이유는 external_data_service.py의
    같은 처리와 동일 — merge_asof가 해상도 불일치에서 죽는 걸 방지)."""
    df = pd.read_csv(io.StringIO(text))
    df.columns = ["date", value_col]
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.as_unit("us")
    df[value_col] = pd.to_numeric(df[value_col], errors="coerce")
    return df.dropna(subset=[value_col]).reset_index(drop=True)


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.parquet"


def _load_cache(name: str, value_col: str) -> pd.DataFrame:
    path = _cache_path(name)
    if not path.exists():
        return pd.DataFrame(columns=["date", value_col])
    df = pd.read_parquet(path)
    if not df.empty and df["date"].dt.unit != "us":
        df = df.assign(date=df["date"].dt.as_unit("us"))
    return df


def _save_cache(name: str, df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(name), index=False)


def _get_fred_series(name: str, series_id: str, value_col: str, start: datetime, end: datetime) -> pd.DataFrame:
    """캐시가 오늘(UTC)을 포함하지 않으면(=하루 지났으면) 히스토리 전체를 재조회해
    덮어쓴다 — external_data_service.get_fear_greed_cmc와 동일한 패턴."""
    cached = _load_cache(name, value_col)
    today = datetime.now(timezone.utc).date()

    if cached.empty or cached["date"].max().date() < today:
        with httpx.Client(timeout=15) as client:
            text = _fetch_fred_csv(client, series_id, _HISTORY_START, datetime.now(timezone.utc))
        cached = _parse_fred_csv(text, value_col)
        _save_cache(name, cached)

    mask = (cached["date"].dt.date >= start.date()) & (cached["date"].dt.date <= end.date())
    return cached[mask].reset_index(drop=True)


def get_fed_funds_rate(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_fedfunds", FED_FUNDS_SERIES_ID, "fed_funds_rate_value", start, end)


def get_us_yield_curve_spread(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_t10y2y", YIELD_CURVE_SERIES_ID, "treasury_yield_spread_value", start, end)


def get_kr_call_rate(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_kr_call_rate", KR_CALL_RATE_SERIES_ID, "kr_call_rate_value", start, end)


def merge_fred_series(df: pd.DataFrame, series_df: pd.DataFrame, value_col: str) -> pd.DataFrame:
    """대상 코인 캔들(df, candle_time 컬럼 필요)에 FRED 시계열(series_df, date 컬럼)을
    merge_asof(backward)로 병합한다 — external_data_service.merge_fear_greed와 동일한
    look-ahead bias 방지 패턴. series_df가 비어있으면 value_col을 NaN으로 채운다."""
    if series_df.empty:
        return df.assign(**{value_col: float("nan")})

    merged = pd.merge_asof(
        df.sort_values("candle_time").reset_index(drop=True),
        series_df.sort_values("date").reset_index(drop=True),
        left_on="candle_time",
        right_on="date",
        direction="backward",
    )
    return merged.drop(columns="date")


__all__ = [
    "get_fed_funds_rate",
    "get_us_yield_curve_spread",
    "get_kr_call_rate",
    "merge_fred_series",
]
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_macro_data_service.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: 커밋**

```bash
git add macro_data_service.py tests/test_macro_data_service.py
git commit -m "feat: FRED 기반 미국금리/장단기금리차/한국콜금리 조회 함수 추가"
```

---

## Task 2: `macro_data_service.py` — Frankfurter 원/달러 환율 fetch/parse/cache

**Files:**
- Modify: `macro_data_service.py`
- Test: `tests/test_macro_data_service.py`

**Interfaces:**
- Consumes: `merge_fred_series`(Task 1, 재사용)
- Produces: `get_usdkrw_rate(start: datetime, end: datetime) -> pd.DataFrame` (columns: `date`, `usdkrw_rate_value`)
- Produces: `merge_usdkrw_rate(df: pd.DataFrame, rate_df: pd.DataFrame) -> pd.DataFrame`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_macro_data_service.py`에 추가(파일 상단 import에 `get_usdkrw_rate`, `merge_usdkrw_rate`, `_fetch_frankfurter_json`, `_parse_frankfurter_json` 추가):

```python
def test_fetch_frankfurter_json_sends_correct_range_and_currencies():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/2024-01-01..2024-01-10"
        assert request.url.params["from"] == "USD"
        assert request.url.params["to"] == "KRW"
        return httpx.Response(200, json={
            "amount": 1.0, "base": "USD", "start_date": "2024-01-01", "end_date": "2024-01-10",
            "rates": {"2024-01-02": {"KRW": 1313.23}},
        })

    with _mock_client(handler) as client:
        payload = mds._fetch_frankfurter_json(
            client, datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 10, tzinfo=timezone.utc)
        )

    assert payload["rates"]["2024-01-02"]["KRW"] == 1313.23


def test_fetch_frankfurter_json_raises_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(mds, "RETRY_BASE_DELAY_SECONDS", 0.0)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with _mock_client(handler) as client:
        with pytest.raises(RuntimeError):
            mds._fetch_frankfurter_json(client, datetime(2024, 1, 1, tzinfo=timezone.utc), datetime(2024, 1, 2, tzinfo=timezone.utc))


def test_parse_frankfurter_json_flattens_rates_to_dataframe():
    payload = {
        "amount": 1.0, "base": "USD", "start_date": "2023-12-29", "end_date": "2024-01-10",
        "rates": {"2023-12-29": {"KRW": 1297.43}, "2024-01-02": {"KRW": 1313.23}},
    }

    df = mds._parse_frankfurter_json(payload)

    assert list(df.columns) == ["date", "usdkrw_rate_value"]
    assert df["date"].dt.unit == "us"
    assert df.iloc[0]["usdkrw_rate_value"] == 1297.43
    assert df.iloc[1]["usdkrw_rate_value"] == 1313.23


def test_parse_frankfurter_json_handles_empty_rates():
    df = mds._parse_frankfurter_json({"rates": {}})
    assert list(df.columns) == ["date", "usdkrw_rate_value"]
    assert df.empty


def test_get_usdkrw_rate_skips_fetch_when_cache_is_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    today = datetime.now(timezone.utc)
    cached = pd.DataFrame({"date": pd.to_datetime([today.date()], utc=True), "usdkrw_rate_value": [1350.0]})
    cached.to_parquet(tmp_path / "frankfurter_usdkrw.parquet", index=False)
    monkeypatch.setattr(mds, "_fetch_frankfurter_json", _fail_fetch)

    result = mds.get_usdkrw_rate(today - timedelta(days=1), today)

    assert result.iloc[-1]["usdkrw_rate_value"] == 1350.0


def test_get_usdkrw_rate_refetches_when_cache_is_stale(monkeypatch, tmp_path):
    monkeypatch.setattr(mds, "CACHE_DIR", tmp_path)
    stale_date = datetime.now(timezone.utc) - timedelta(days=3)
    cached = pd.DataFrame({"date": pd.to_datetime([stale_date.date()], utc=True), "usdkrw_rate_value": [1300.0]})
    cached.to_parquet(tmp_path / "frankfurter_usdkrw.parquet", index=False)

    def fake_fetch(client, start, end):
        today_str = datetime.now(timezone.utc).date().isoformat()
        return {"rates": {today_str: {"KRW": 1400.0}}}

    monkeypatch.setattr(mds, "_fetch_frankfurter_json", fake_fetch)

    result = mds.get_usdkrw_rate(datetime.now(timezone.utc) - timedelta(days=1), datetime.now(timezone.utc))

    assert result.iloc[-1]["usdkrw_rate_value"] == 1400.0


def test_merge_usdkrw_rate_backward_fills():
    df = pd.DataFrame({
        "candle_time": pd.to_datetime(["2024-01-02 05:00", "2024-01-03 05:00"], utc=True),
        "close": [100.0, 101.0],
    })
    rate_df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-02"], utc=True),
        "usdkrw_rate_value": [1313.23],
    })

    merged = mds.merge_usdkrw_rate(df, rate_df)

    assert merged["usdkrw_rate_value"].tolist() == [1313.23, 1313.23]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_macro_data_service.py -k frankfurter -v`
Expected: FAIL with `AttributeError: module 'macro_data_service' has no attribute '_fetch_frankfurter_json'`

- [ ] **Step 3: `macro_data_service.py`에 추가**

`FRED_CSV_URL` 아래에 상수 추가:

```python
FRANKFURTER_URL = "https://api.frankfurter.dev/v1"
```

파일 끝(`__all__` 정의 위)에 함수 추가:

```python
def _fetch_frankfurter_json(client: httpx.Client, start: datetime, end: datetime) -> dict:
    last_exc: Exception | None = None
    url = f"{FRANKFURTER_URL}/{start.date().isoformat()}..{end.date().isoformat()}"
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(url, params={"from": "USD", "to": "KRW"})
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))
    raise RuntimeError(f"Frankfurter USD/KRW 환율 호출 실패: {last_exc}")


def _parse_frankfurter_json(payload: dict) -> pd.DataFrame:
    """Frankfurter 응답의 rates 딕셔너리({"YYYY-MM-DD": {"KRW": value}, ...})를
    평평한 DataFrame으로 변환한다. 영업일 기준 갱신이라 주말/공휴일은 키 자체가
    없다(merge_asof가 자연스럽게 직전 영업일 값으로 backward-fill)."""
    rates = payload.get("rates", {})
    if not rates:
        return pd.DataFrame(columns=["date", "usdkrw_rate_value"])

    records = [{"date": date_str, "usdkrw_rate_value": values["KRW"]} for date_str, values in rates.items()]
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.as_unit("us")
    return df.sort_values("date").reset_index(drop=True)


def get_usdkrw_rate(start: datetime, end: datetime) -> pd.DataFrame:
    """캐시가 오늘(UTC)을 포함하지 않으면 히스토리 전체를 재조회해 덮어쓴다 —
    _get_fred_series와 동일한 패턴(provider가 달라 공용 헬퍼로 묶지는 않음)."""
    cached = _load_cache("frankfurter_usdkrw", "usdkrw_rate_value")
    today = datetime.now(timezone.utc).date()

    if cached.empty or cached["date"].max().date() < today:
        with httpx.Client(timeout=15) as client:
            payload = _fetch_frankfurter_json(client, _HISTORY_START, datetime.now(timezone.utc))
        cached = _parse_frankfurter_json(payload)
        _save_cache("frankfurter_usdkrw", cached)

    mask = (cached["date"].dt.date >= start.date()) & (cached["date"].dt.date <= end.date())
    return cached[mask].reset_index(drop=True)


def merge_usdkrw_rate(df: pd.DataFrame, rate_df: pd.DataFrame) -> pd.DataFrame:
    return merge_fred_series(df, rate_df, "usdkrw_rate_value")
```

`__all__` 리스트에 `"get_usdkrw_rate"`, `"merge_usdkrw_rate"` 추가.

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_macro_data_service.py -v`
Expected: PASS (18 passed)

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS

- [ ] **Step 6: 커밋**

```bash
git add macro_data_service.py tests/test_macro_data_service.py
git commit -m "feat: Frankfurter 기반 원/달러 공식환율 조회 함수 추가"
```

---

## Task 3: `engine/regime_ml_data.py` 배선 — 4개 원시 컬럼 병합

**Files:**
- Modify: `engine/regime_ml_data.py`
- Test: `tests/test_regime_ml_data.py`

**Interfaces:**
- Consumes: `macro_data_service.get_fed_funds_rate/get_us_yield_curve_spread/get_kr_call_rate/get_usdkrw_rate/merge_fred_series/merge_usdkrw_rate`(Task 1, 2)
- Produces: `load_market_training_data()`의 반환 df에 `fed_funds_rate_value`, `treasury_yield_spread_value`, `kr_call_rate_value`, `usdkrw_rate_value` 컬럼 추가

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_data.py`의 `_patch_common` 함수(파일 36번째 줄 부근)를 아래로 교체:

```python
def _patch_common(monkeypatch, *, symbol_found: bool = True):
    monkeypatch.setattr(regime_ml_data, "get_fear_greed_cmc", lambda *a, **k: pd.DataFrame(columns=["date", "fear_greed_value"]))
    monkeypatch.setattr(
        regime_ml_data, "merge_fear_greed",
        lambda df, fng_df: df.assign(fear_greed_value=float("nan")),
    )
    monkeypatch.setattr(regime_ml_data, "binance_symbol", lambda market: "BTCUSDT")  # Always return a string
    if symbol_found:
        monkeypatch.setattr(regime_ml_data, "get_binance_close", lambda *a, **k: pd.DataFrame(columns=["candle_time", "close"]))
        monkeypatch.setattr(regime_ml_data, "get_binance_funding_rate", lambda *a, **k: pd.DataFrame(columns=["funding_time", "funding_rate_value"]))
        monkeypatch.setattr(
            regime_ml_data, "merge_funding_rate",
            lambda df, funding_df: df.assign(funding_rate_value=float("nan")),
        )
    else:
        # Patch get_binance_close to raise the exception (the actual failure mode in production)
        def _raise_symbol_not_found(*args, **kwargs):
            raise BinanceSymbolNotFoundError("BTCUSDT")
        monkeypatch.setattr(regime_ml_data, "get_binance_close", _raise_symbol_not_found)
    monkeypatch.setattr(regime_ml_data, "compute_korea_premium_value", lambda df: pd.Series([float("nan")] * len(df), index=df.index))
    monkeypatch.setattr(regime_ml_data, "get_fed_funds_rate", lambda *a, **k: pd.DataFrame(columns=["date", "fed_funds_rate_value"]))
    monkeypatch.setattr(regime_ml_data, "get_us_yield_curve_spread", lambda *a, **k: pd.DataFrame(columns=["date", "treasury_yield_spread_value"]))
    monkeypatch.setattr(regime_ml_data, "get_kr_call_rate", lambda *a, **k: pd.DataFrame(columns=["date", "kr_call_rate_value"]))
    monkeypatch.setattr(regime_ml_data, "get_usdkrw_rate", lambda *a, **k: pd.DataFrame(columns=["date", "usdkrw_rate_value"]))
```

`test_load_market_training_data_has_all_required_columns`의 `required` 집합을 교체:

```python
    required = {
        "close", "high", "low", "volume", "trade_value",
        "btc_close", "usdt_close", "binance_close",
        "fear_greed_value", "funding_rate_value", "korea_premium_value",
        "fed_funds_rate_value", "treasury_yield_spread_value", "kr_call_rate_value", "usdkrw_rate_value",
    }
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_data.py::test_load_market_training_data_has_all_required_columns -v`
Expected: FAIL — `AttributeError: <module 'engine.regime_ml_data'> does not have the attribute 'get_fed_funds_rate'`

- [ ] **Step 3: `engine/regime_ml_data.py` 수정**

임포트 블록(`from upbit_data_service import get_candles` 다음)에 추가:

```python
from macro_data_service import (
    get_fed_funds_rate,
    get_kr_call_rate,
    get_us_yield_curve_spread,
    get_usdkrw_rate,
    merge_fred_series,
    merge_usdkrw_rate,
)
```

`load_market_training_data` 함수 끝, `df["korea_premium_value"] = compute_korea_premium_value(df)` 다음(`return df` 이전)에 추가:

```python
    fed_funds_df = get_fed_funds_rate(start, end)
    df = merge_fred_series(df, fed_funds_df, "fed_funds_rate_value")

    yield_curve_df = get_us_yield_curve_spread(start, end)
    df = merge_fred_series(df, yield_curve_df, "treasury_yield_spread_value")

    kr_call_rate_df = get_kr_call_rate(start, end)
    df = merge_fred_series(df, kr_call_rate_df, "kr_call_rate_value")

    usdkrw_df = get_usdkrw_rate(start, end)
    df = merge_usdkrw_rate(df, usdkrw_df)

    return df
```

함수 docstring(파일 38~41줄)의 "market 자체의 캔들이 비어있으면..." 문단 뒤에 이어서 추가:

```
    2026-08-31 캘린더/거시경제 피처 추가 라운드에서 미국 기준금리/미국 장단기
    국채금리차/한국 콜금리(기준금리 대리지표)/원-달러 공식환율 4개 원시 컬럼도
    함께 병합한다(macro_data_service.py, FRED+Frankfurter, 둘 다 API 키 불필요).
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
git commit -m "feat: 학습 데이터 로더에 거시경제 원시 컬럼 4개 배선"
```

---

## Task 4: `engine/regime_ml_features.py` — 캘린더 피처 8개(KST) + 실데이터 ablation

**Files:**
- Modify: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`
- Modify (ablation 결과 기록): `docs/regime-ml-backlog.md`

**Interfaces:**
- Produces: `build_feature_matrix()` 반환값에 `HOUR_SIN`, `HOUR_COS`, `DOW_SIN`, `DOW_COS`, `MONTH_SIN`, `MONTH_COS`, `DAY_OF_MONTH_SIN`, `DAY_OF_MONTH_COS` 컬럼 추가

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py` 상단 import에 `numpy as np`는 이미 있음(2번째 줄). `test_build_feature_matrix_has_one_column_per_registered_indicator_except_obv_plus_regime_features`의 `expected_columns`를 교체:

```python
    expected_columns = (
        (set(LIVE_INDICATOR_FACTORY.keys()) - {"OBV", "FEAR_GREED_CMC"})
        | {
            "RAW_SCORE", "VOLUME_CONFIRM", "VPIN_SCORE", "LEVEL_PROXIMITY", "REVERSAL_GATE",
            "VOLATILITY_PERCENTILE", "LIQUIDITY_PERCENTILE", "market",
            "HOUR_SIN", "HOUR_COS", "DOW_SIN", "DOW_COS",
            "MONTH_SIN", "MONTH_COS", "DAY_OF_MONTH_SIN", "DAY_OF_MONTH_COS",
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
    expected_month_sin = np.sin(2 * np.pi * (kst_time.dt.month - 1) / 12)
    expected_day_cos = np.cos(2 * np.pi * (kst_time.dt.day - 1) / 31)

    pd.testing.assert_series_equal(
        result["HOUR_SIN"].reset_index(drop=True), expected_hour_sin.reset_index(drop=True), check_names=False
    )
    pd.testing.assert_series_equal(
        result["DOW_COS"].reset_index(drop=True), expected_dow_cos.reset_index(drop=True), check_names=False
    )
    pd.testing.assert_series_equal(
        result["MONTH_SIN"].reset_index(drop=True), expected_month_sin.reset_index(drop=True), check_names=False
    )
    pd.testing.assert_series_equal(
        result["DAY_OF_MONTH_COS"].reset_index(drop=True), expected_day_cos.reset_index(drop=True), check_names=False
    )


def test_build_feature_matrix_hour_sin_is_continuous_across_kst_midnight():
    """KST 23시->0시 전환처럼 raw hour 값은 23->0으로 불연속이지만, sin 인코딩은
    실제 1시간 차이만큼만 작게 움직여야 한다(정오~자정 같은 먼 시간대 차이보다
    훨씬 작아야 함)."""
    dates = pd.to_datetime(["2024-01-01 14:00", "2024-01-01 15:00"], utc=True)  # UTC 14/15시 -> KST 23시/(다음날)0시
    df = _make_full_df().iloc[:2].copy()
    df["candle_time"] = dates

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    diff_across_midnight = abs(result["HOUR_SIN"].iloc[0] - result["HOUR_SIN"].iloc[1])
    assert diff_across_midnight < 0.3  # sin(2π*23/24)≈-0.259, sin(0)=0 -> 실제 차이≈0.259
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — 기존 exact-match 테스트가 `KeyError` 또는 컬럼 집합 불일치로 실패, 신규 테스트는 `KeyError: 'HOUR_SIN'`

- [ ] **Step 3: `engine/regime_ml_features.py` 수정**

파일 상단 import에 `numpy`를 추가:

```python
from __future__ import annotations

import numpy as np
import pandas as pd
```

`build_feature_matrix` 함수 끝, `result = pd.DataFrame(features, index=df.index)` 이전에 추가:

```python
    # 캘린더 피처(2026-08-31 추가) — 업비트가 한국 거래소라 KST 기준 시간대/요일/
    # 월/월중 패턴을 잡으려는 목적. sin/cos 주기인코딩으로 23시->0시, 12월->1월
    # 같은 경계에서 불연속이 생기지 않게 한다. LISTING_AGE_BARS처럼 "단조증가"가
    # 아니라 "주기적으로 반복"되는 값이라, 워크포워드 fold 위치를 암묵적으로
    # 알려주는 캘린더 프록시 위험은 낮지만, 실데이터 ablation으로 검증 후 채택한다
    # (docs/superpowers/specs_v1/2026-08-31-regime-ml-macro-calendar-features-design.md).
    kst_time = df["candle_time"].dt.tz_convert("Asia/Seoul")
    features["HOUR_SIN"] = np.sin(2 * np.pi * kst_time.dt.hour / 24)
    features["HOUR_COS"] = np.cos(2 * np.pi * kst_time.dt.hour / 24)
    features["DOW_SIN"] = np.sin(2 * np.pi * kst_time.dt.dayofweek / 7)
    features["DOW_COS"] = np.cos(2 * np.pi * kst_time.dt.dayofweek / 7)
    features["MONTH_SIN"] = np.sin(2 * np.pi * (kst_time.dt.month - 1) / 12)
    features["MONTH_COS"] = np.cos(2 * np.pi * (kst_time.dt.month - 1) / 12)
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
git commit -m "feat: KST 기준 캘린더 피처(시간대/요일/월/월중) 추가"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 그룹 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

출력의 "=== 전체 fold 풀링 ===" 블록에서 weighted kappa를 확인한다.

- **kappa >= 0.096(현재 baseline)이면 그룹 채택** — Step 8(leave-one-out)로 진행. 이 kappa를 이후 그룹(환율, Task 5)의 새 baseline으로 기록.
- **kappa < 0.096이면 그룹 폐기** — `git revert HEAD~1`(방금 만든 커밋만 되돌림)을 실행하고, `PYTHONPATH=. python -m pytest tests/ -q`로 전체 테스트가 다시 통과하는지 확인한 뒤 Task 5로 진행(baseline은 0.096 그대로 유지, Step 8 생략).

- [ ] **Step 8 (그룹이 채택된 경우에만): 신호 단위 leave-one-out**

캘린더 그룹의 4개 신호(시간대=HOUR_SIN+HOUR_COS, 요일=DOW_SIN+DOW_COS, 월=MONTH_SIN+MONTH_COS, 월중=DAY_OF_MONTH_SIN+DAY_OF_MONTH_COS) 각각에 대해:

1. `engine/regime_ml_features.py`에서 그 신호의 sin/cos 두 줄을 임시로 주석 처리.
2. `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py` 실행, kappa 기록.
3. 원상복구(주석 해제).

4개 신호를 전부 시도한 뒤:
- 어떤 신호를 뺐을 때 kappa가 Step 7에서 채택된 값보다 **개선**되면, 그 신호는 해로운 것으로 판단해 코드에서 완전히 제거(주석이 아니라 삭제)하고 관련 테스트도 함께 수정한 뒤 새 커밋(`fix: 캘린더 X 신호 제거 — ablation상 해로움 확인`)을 만든다.
- 모든 신호가 "빼면 악화되거나 거의 동일"이면 아무것도 제거하지 않고 그대로 둔다.

- [ ] **Step 9: `docs/regime-ml-backlog.md`에 결과 기록**

"완료된 것" 섹션에 캘린더 그룹 ablation 결과(채택/폐기 여부, kappa 변화, leave-one-out 결과)를 한 문단으로 추가한다. 이 파일은 git 추적 대상이 아니므로(기존 관례, `docs/regime-ml-backlog.md` 파일 최상단 참고) 별도 커밋 불필요.

---

## Task 5: `engine/regime_ml_features.py` — 환율 피처 3개 + 실데이터 ablation

**Files:**
- Modify: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`
- Modify (ablation 결과 기록): `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: `usdt_close`, `usdkrw_rate_value` 원시 컬럼(Task 3)
- Produces: `build_feature_matrix()` 반환값에 `USDKRW_RETURN`, `USDKRW_VOLATILITY`, `UPBIT_FX_SPREAD` 컬럼 추가

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`의 `_make_full_df()`(21~37줄)에 컬럼 추가:

```python
def _make_full_df() -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.date_range("2024-01-01", periods=_N, freq="h", tz="UTC")
    close = 100.0 + np.cumsum(rng.normal(0, 1.0, _N))
    high = close + rng.uniform(0.1, 1.0, _N)
    low = close - rng.uniform(0.1, 1.0, _N)
    volume = rng.uniform(10, 100, _N)
    return pd.DataFrame({
        "candle_time": dates,
        "close": close, "high": high, "low": low,
        "volume": volume, "trade_value": volume * close,
        "btc_close": close * 1.1, "usdt_close": np.full(_N, 1350.0),
        "binance_close": close / 1350.0,
        "fear_greed_value": rng.uniform(0, 100, _N),
        "funding_rate_value": rng.uniform(-0.05, 0.05, _N),
        "korea_premium_value": rng.uniform(-2, 2, _N),
        "usdkrw_rate_value": 1300.0 + np.cumsum(rng.normal(0, 1.0, _N)),
    })
```

`expected_columns`에 3개 키 추가(Task 4에서 만든 집합에 이어서):

```python
            "USDKRW_RETURN", "USDKRW_VOLATILITY", "UPBIT_FX_SPREAD",
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


def test_build_feature_matrix_upbit_fx_spread_is_zero_when_rates_match():
    df = _make_full_df()
    df["usdkrw_rate_value"] = df["usdt_close"]  # 업비트 암묵환율과 공식환율이 완전히 같다고 가정

    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert (result["UPBIT_FX_SPREAD"] == 0.0).all()


def test_build_feature_matrix_usdkrw_volatility_is_nonnegative_after_warmup():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    assert (result["USDKRW_VOLATILITY"].iloc[2:] >= 0).all()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — `KeyError: 'USDKRW_RETURN'` 등

- [ ] **Step 3: `engine/regime_ml_features.py` 수정**

Task 4에서 추가한 캘린더 피처 블록 다음(같은 `build_feature_matrix` 함수 안, `result = pd.DataFrame(features, index=df.index)` 이전)에 추가:

```python
    # 환율 피처(2026-08-31 추가) — Frankfurter 공식 USD/KRW 환율의 변동률/변동성과,
    # 업비트 암묵환율(usdt_close) 대비 괴리(자본유출입/크립토 유동성 프리미엄 신호
    # 후보). 원시 레벨 자체는 넣지 않는다 — 2024~2026 구간에 추세적으로 움직이면
    # LISTING_AGE_BARS처럼 fold 위치 프록시가 될 위험이 있어 변동률/스프레드처럼
    # 상대적으로 정상성(stationary)이 높은 형태만 쓴다.
    fx_return = df["usdkrw_rate_value"].pct_change(fill_method=None)
    features["USDKRW_RETURN"] = fx_return
    features["USDKRW_VOLATILITY"] = fx_return.ewm(halflife=half_life_bars).std()
    features["UPBIT_FX_SPREAD"] = (df["usdt_close"] / df["usdkrw_rate_value"] - 1) * 100
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: 전부 PASS

- [ ] **Step 5: 전체 회귀 테스트**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: 전부 PASS (`tests/test_regime_ml_data.py`는 Task 3에서 이미 `usdkrw_rate_value`를 NaN으로 채우는 목업이 있으므로, `build_feature_matrix`가 그 NaN을 그대로 통과시켜도(pct_change of NaN -> NaN) 에러 없이 통과해야 한다)

- [ ] **Step 6: 커밋**

```bash
git add engine/regime_ml_features.py tests/test_regime_ml_features.py
git commit -m "feat: 원/달러 환율 변동률/변동성/업비트스프레드 피처 추가"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 그룹 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

Task 4 종료 시점 baseline(캘린더 채택 시 그 kappa, 폐기 시 0.096)과 비교한다.

- **개선되면 채택** — Step 8(leave-one-out)로 진행. 이 kappa를 Task 6(금리)의 새 baseline으로 기록.
- **악화되면 폐기** — `git revert HEAD~1`을 실행하고 테스트 재확인 후 Task 6로 진행(baseline은 Task 4 종료 시점 값 유지, Step 8 생략).

- [ ] **Step 8 (그룹이 채택된 경우에만): 신호 단위 leave-one-out**

환율 그룹의 3개 신호(`USDKRW_RETURN`, `USDKRW_VOLATILITY`, `UPBIT_FX_SPREAD`) 각각에 대해 Task 4 Step 8과 동일한 절차(주석 처리 -> 재학습 -> 기록 -> 원복)를 반복한다. 어떤 신호를 뺐을 때 kappa가 개선되면 그 신호를 코드에서 삭제하고 새 커밋을 만든다.

- [ ] **Step 9: `docs/regime-ml-backlog.md`에 결과 기록**

Task 4 Step 9와 동일한 방식으로 환율 그룹 결과를 추가한다.

---

## Task 6: `engine/regime_ml_features.py` — 금리 피처 3개 + 실데이터 ablation

**Files:**
- Modify: `engine/regime_ml_features.py`
- Test: `tests/test_regime_ml_features.py`
- Modify (ablation 결과 기록): `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: `fed_funds_rate_value`, `kr_call_rate_value`, `treasury_yield_spread_value` 원시 컬럼(Task 3)
- Produces: `build_feature_matrix()` 반환값에 `US_KR_RATE_SPREAD`, `YIELD_CURVE_SPREAD`, `HOURS_SINCE_RATE_DECISION` 컬럼 추가
- Produces: `_hours_since_last_change(series: pd.Series, candle_time: pd.Series) -> pd.Series` (모듈 내부 헬퍼)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_regime_ml_features.py`의 `_make_full_df()`에 컬럼 3개 추가(Task 5에서 만든 딕셔너리 리터럴 끝에 이어서):

```python
        "fed_funds_rate_value": np.where(np.arange(_N) < _N // 2, 5.33, 5.25),
        "kr_call_rate_value": np.where(np.arange(_N) < _N // 3, 3.50, 3.25),
        "treasury_yield_spread_value": rng.uniform(-0.5, 0.5, _N),
```

`expected_columns`에 3개 키 추가(Task 5에서 만든 집합에 이어서):

```python
            "US_KR_RATE_SPREAD", "YIELD_CURVE_SPREAD", "HOURS_SINCE_RATE_DECISION",
```

파일 끝에 신규 테스트 추가:

```python
def test_build_feature_matrix_us_kr_rate_spread_is_difference():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    expected = df["fed_funds_rate_value"] - df["kr_call_rate_value"]
    pd.testing.assert_series_equal(
        result["US_KR_RATE_SPREAD"].reset_index(drop=True), expected.reset_index(drop=True), check_names=False
    )


def test_build_feature_matrix_yield_curve_spread_passes_through():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    pd.testing.assert_series_equal(
        result["YIELD_CURVE_SPREAD"].reset_index(drop=True),
        df["treasury_yield_spread_value"].reset_index(drop=True),
        check_names=False,
    )


def test_build_feature_matrix_hours_since_rate_decision_resets_to_zero_at_change_point():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    # fed_funds_rate_value는 index _N//2에서 5.33->5.25로 바뀐다(_make_full_df 정의).
    change_index = _N // 2
    assert result["HOURS_SINCE_RATE_DECISION"].iloc[change_index] == 0.0
    assert result["HOURS_SINCE_RATE_DECISION"].iloc[change_index + 5] == 5.0


def test_build_feature_matrix_hours_since_rate_decision_takes_more_recent_of_two_series():
    df = _make_full_df()
    result = build_feature_matrix(df, market="KRW-BTC", half_life_bars=24.0)

    # kr_call_rate_value는 index _N//3에서 바뀌고, fed_funds_rate_value는 index _N//2에서
    # 바뀐다(_make_full_df 정의) -> _N//3 < _N//2이므로, _N//2 시점에는 fed 변경이
    # 더 최근이라 그 값(0시간)이 선택돼야 한다.
    assert result["HOURS_SINCE_RATE_DECISION"].iloc[_N // 2] == 0.0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `PYTHONPATH=. python -m pytest tests/test_regime_ml_features.py -v`
Expected: FAIL — `KeyError: 'US_KR_RATE_SPREAD'` 등

- [ ] **Step 3: `engine/regime_ml_features.py` 수정**

`build_feature_matrix` 함수 정의 위에 모듈 레벨 헬퍼 추가:

```python
def _hours_since_last_change(series: pd.Series, candle_time: pd.Series) -> pd.Series:
    """series 값이 이전 행 대비 바뀐 가장 최근 시점(candle_time 기준)으로부터
    경과한 시간(시간 단위)을 반환한다. series가 NaN인 구간(워밍업 등)은 변화
    시점으로 치지 않는다 — series.notna()가 False인 행은 변화 판정에서 제외한다.
    아직 어떤 실제 값도 나오지 않은 구간(첫 유효값 이전)은 NaN을 반환한다."""
    changed = series.ne(series.shift(1)) & series.notna()
    change_times = candle_time.where(changed)
    last_change_time = change_times.ffill()
    return (candle_time - last_change_time).dt.total_seconds() / 3600.0
```

`build_feature_matrix` 함수 끝, Task 5에서 추가한 환율 피처 블록 다음(`result = pd.DataFrame(features, index=df.index)` 이전)에 추가:

```python
    # 금리 피처(2026-08-31 추가) — 미국-한국 정책금리 스프레드(캐리트레이드/자본
    # 유출입 유인)와 미국 장단기 국채금리차(매크로 리스크온/오프 선행지표),
    # 금리결정 경과시간(변동성 급등 구간 포착 후보). US_KR_RATE_SPREAD/
    # YIELD_CURVE_SPREAD는 스프레드라 원시 레벨보다 정상성이 높다.
    # HOURS_SINCE_RATE_DECISION은 변경 시점마다 0으로 리셋되므로(단조증가 아님)
    # LISTING_AGE_BARS류 fold 위치 프록시 위험이 낮다.
    features["US_KR_RATE_SPREAD"] = df["fed_funds_rate_value"] - df["kr_call_rate_value"]
    features["YIELD_CURVE_SPREAD"] = df["treasury_yield_spread_value"]
    fed_hours_since = _hours_since_last_change(df["fed_funds_rate_value"], df["candle_time"])
    kr_hours_since = _hours_since_last_change(df["kr_call_rate_value"], df["candle_time"])
    features["HOURS_SINCE_RATE_DECISION"] = pd.concat([fed_hours_since, kr_hours_since], axis=1).min(axis=1, skipna=True)
```

`build_feature_matrix`의 docstring(파일 54~58줄)에 필요 컬럼 목록을 갱신:

```
    """df: close/high/low/volume/trade_value + btc_close/usdt_close/binance_close/
    fear_greed_value/funding_rate_value/korea_premium_value/fed_funds_rate_value/
    treasury_yield_spread_value/kr_call_rate_value/usdkrw_rate_value를 전부
    포함해야 한다(engine.regime_ml_data.load_market_training_data()가 반환하는
    형태). 반환 DataFrame은 df와 같은 행 수/인덱스를 유지하며(워밍업 구간은 NaN),
    원본 OHLCV 컬럼은 포함하지 않는다(피처 전용) — market 범주형 컬럼만 추가한다."""
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
git commit -m "feat: 미한 정책금리스프레드/장단기금리차/금리결정경과시간 피처 추가"
```

- [ ] **Step 7: 실데이터 ablation 실행 및 그룹 채택 여부 결정**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`

Task 5 종료 시점 baseline과 비교한다.

- **개선되면 채택** — Step 8(leave-one-out)로 진행.
- **악화되면 폐기** — `git revert HEAD~1`을 실행하고 테스트 재확인.

- [ ] **Step 8 (그룹이 채택된 경우에만): 신호 단위 leave-one-out**

금리 그룹의 3개 신호(`US_KR_RATE_SPREAD`, `YIELD_CURVE_SPREAD`, `HOURS_SINCE_RATE_DECISION`) 각각에 대해 Task 4 Step 8과 동일한 절차를 반복한다.

- [ ] **Step 9: `docs/regime-ml-backlog.md`에 결과 기록**

Task 4 Step 9와 동일한 방식으로 금리 그룹 결과를 추가한다.

---

## Task 7: 최종 요약 — 백로그 갱신

**Files:**
- Modify: `docs/regime-ml-backlog.md`

**Interfaces:**
- Consumes: Task 4~6의 채택/폐기 결과와 최종 kappa

- [ ] **Step 1: 백로그 "완료된 것" 섹션에 이번 라운드 종합 요약 추가**

3개 그룹(캘린더/환율/금리) 각각의 채택 여부, leave-one-out으로 제거된 신호(있다면), 세션 시작 baseline(0.096) 대비 최종 kappa를 한 문단으로 정리한다. `docs/regime-ml-backlog.md`는 git 추적 대상이 아니므로 커밋 불필요.

- [ ] **Step 2: "다음 세션 작업 후보" 섹션 갱신**

이번 라운드 결과에 따라 c-2(로지스틱회귀 baseline + LightGBM 하이퍼파라미터 튜닝) 착수를 다음 세션 1순위로 명시한다. 최종 채택된 피처 세트의 AWS 배포 여부는 이 태스크에서 결정하지 않는다 — 전체 라운드 종료 후 사용자에게 별도로 확인한다(`[[upbit-v1-dont-push-on-empirical-regression]]` 원칙).

- [ ] **Step 3: 사용자에게 최종 결과 보고**

채택된 피처 목록, 각 그룹의 kappa 변화, 최종 kappa(baseline 0.096 대비)를 요약해 사용자에게 보고하고, AWS 배포 여부를 확인한다.

---

## 범위 밖

- c-2(로지스틱회귀 baseline 비교 + LightGBM 하이퍼파라미터 튜닝) — 이 플랜 완료 후 별도 진행.
- c-1(CUSUM 이벤트 샘플링), c-3(메타 레이블링), c-4(threshold 튜닝 실효성 개선) — 기존 백로그 순서 유지.
- ECOS API 연동 — FRED 콜금리 대리지표로 대체 확정.
- 최종 채택 피처 세트의 AWS 라이브 배포 실행 — Task 7 Step 3에서 별도 확인 후 진행(이 플랜 범위 밖).
