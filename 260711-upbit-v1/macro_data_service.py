"""
macro_data_service.py

업비트/바이낸스가 아닌 거시경제 지표(미국 기준금리, 미국 장단기 국채금리차, 한국
콜금리, 원/달러 공식환율, S&P500/다우존스/나스닥종합 일간 종가)를 무료 공개
API에서 조회·캐싱한다. 재시도/캐싱 패턴은 external_data_service.py(공포탐욕지수)를
그대로 따른다. 두 provider(FRED, Frankfurter) 모두 API 키가 필요 없다. 설계 문서:
docs/superpowers/specs/2026-08-31-regime-ml-macro-calendar-features-design.md,
docs/superpowers/specs/2026-08-31-regime-ml-stock-index-features-design.md

한국은행 기준금리 자체를 제공하는 무료·키불필요 API가 없어, FRED의
IRSTCI01KRM156N(한국 콜금리/은행간금리, OECD 경유)을 대리지표로 쓴다. 2008년
한국은행이 콜금리를 기준금리의 운영목표로 직접 관리하기 시작한 이후로는 두 값이
사실상 동일하게 움직이지만, 완전히 같은 수치는 아니다.
"""
from __future__ import annotations

import io
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FRANKFURTER_URL = "https://api.frankfurter.dev/v1"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0

CACHE_DIR = Path(__file__).parent / "data" / "cache" / "external"

_CACHE_TTL_HOURS = 24

# 학습 구간(TRAIN_START=2024-01-01)보다 충분히 이전이면 되므로, 전체 히스토리를
# 받을 필요 없이 이 시점부터만 받는다(응답 크기/캐시 갱신 비용 절감).
_HISTORY_START = datetime(2020, 1, 1, tzinfo=timezone.utc)

FED_FUNDS_SERIES_ID = "FEDFUNDS"
YIELD_CURVE_SERIES_ID = "T10Y2Y"
KR_CALL_RATE_SERIES_ID = "IRSTCI01KRM156N"
SP500_SERIES_ID = "SP500"
DJIA_SERIES_ID = "DJIA"
NASDAQ_SERIES_ID = "NASDAQCOM"


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


def _cache_is_stale(name: str) -> bool:
    """캐시 파일이 24시간 이상 지났으면 stale로 본다. 데이터 내용(예: "오늘 날짜
    데이터가 있는가")이 아니라 파일 자체의 최종 수정 시각을 기준으로 삼는다 —
    FRED의 FEDFUNDS/IRSTCI01KRM156N은 월간 시리즈라 "오늘 날짜 데이터"가 사실상
    영원히 나타나지 않고(external_data_service.get_fear_greed_cmc의 "오늘 날짜
    포함 여부" 체크를 그대로 썼다가는 매 호출마다 무조건 재요청하게 됨), T10Y2Y도
    발표 지연이 있어 마찬가지 문제가 있다."""
    path = _cache_path(name)
    if not path.exists():
        return True
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds > _CACHE_TTL_HOURS * 3600


def _get_fred_series(name: str, series_id: str, value_col: str, start: datetime, end: datetime) -> pd.DataFrame:
    """캐시 파일이 stale(24시간 초과)하면 히스토리 전체를 재조회해 덮어쓴다.
    "오늘 날짜 데이터 포함 여부"가 아니라 파일 mtime을 기준으로 삼는 이유는
    _cache_is_stale() docstring 참고."""
    cached = _load_cache(name, value_col)

    if cached.empty or _cache_is_stale(name):
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


# S&P500/다우존스/나스닥종합 일간 종가(FRED SP500/DJIA/NASDAQCOM). 2026-08-31
# 주가지수 수익률 피처 추가 라운드 — eta² 사전측정에서 pct_change() 형태가
# USDKRW_RETURN과 동급으로 안전 확인됨(docs/regime-ml-backlog.md 우선순위0
# 액션아이템 2번 참고). 레벨 그대로는 피처로 쓰지 않는다 — build_feature_matrix에서
# pct_change만 계산.
def get_sp500_index(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_sp500", SP500_SERIES_ID, "sp500_close_value", start, end)


def get_djia_index(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_djia", DJIA_SERIES_ID, "djia_close_value", start, end)


def get_nasdaq_index(start: datetime, end: datetime) -> pd.DataFrame:
    return _get_fred_series("fred_nasdaq", NASDAQ_SERIES_ID, "nasdaq_close_value", start, end)


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

    records = [{"date": date_str, "usdkrw_rate_value": values.get("KRW")} for date_str, values in rates.items()]
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.as_unit("us")
    return df.sort_values("date").dropna(subset=["usdkrw_rate_value"]).reset_index(drop=True)


def get_usdkrw_rate(start: datetime, end: datetime) -> pd.DataFrame:
    """캐시 파일이 stale(24시간 초과)하면 히스토리 전체를 재조회해 덮어쓴다 —
    _get_fred_series/_cache_is_stale와 동일한 mtime 기반 패턴(provider가 달라도
    _cache_is_stale은 파일명만 받으므로 그대로 재사용 가능하다)."""
    cached = _load_cache("frankfurter_usdkrw", "usdkrw_rate_value")

    if cached.empty or _cache_is_stale("frankfurter_usdkrw"):
        with httpx.Client(timeout=15) as client:
            payload = _fetch_frankfurter_json(client, _HISTORY_START, datetime.now(timezone.utc))
        cached = _parse_frankfurter_json(payload)
        _save_cache("frankfurter_usdkrw", cached)

    mask = (cached["date"].dt.date >= start.date()) & (cached["date"].dt.date <= end.date())
    return cached[mask].reset_index(drop=True)


def merge_usdkrw_rate(df: pd.DataFrame, rate_df: pd.DataFrame) -> pd.DataFrame:
    return merge_fred_series(df, rate_df, "usdkrw_rate_value")


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
