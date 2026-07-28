"""
external_data_service.py

업비트 API가 아닌 외부 API에서 가져오는 시장 데이터(공포/탐욕 지수 등 C 레이어)를 조회·캐싱한다.
재시도/캐싱 패턴은 upbit_data_service.py를 따른다.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

CMC_FNG_URL = "https://api.alternative.me/fng/"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0

CACHE_DIR = Path(__file__).parent / "data" / "cache" / "external"

_FNG_COLUMNS = ["date", "fear_greed_value"]


def _fetch_fear_greed_all(client: httpx.Client) -> list[dict]:
    """alternative.me에서 전체 히스토리를 한 번에 받아온다(limit=0)."""
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(CMC_FNG_URL, params={"limit": 0})
            if resp.status_code == 429:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()["data"]
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    raise RuntimeError(f"alternative.me 공포탐욕지수 API 호출 실패: {last_exc}")


def _parse_fear_greed(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_FNG_COLUMNS)

    df = pd.DataFrame(raw)
    df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True).dt.normalize()
    df["fear_greed_value"] = df["value"].astype(float)
    return df[_FNG_COLUMNS].drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def _cache_path() -> Path:
    return CACHE_DIR / "fear_greed_cmc.parquet"


def _load_cache() -> pd.DataFrame:
    path = _cache_path()
    if not path.exists():
        return pd.DataFrame(columns=_FNG_COLUMNS)
    return pd.read_parquet(path)


def _save_cache(df: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_cache_path(), index=False)


def get_fear_greed_cmc(start: datetime, end: datetime) -> pd.DataFrame:
    """캐시가 오늘(UTC) 날짜를 포함하지 않으면(=하루 지났으면) 전체를 재조회해 덮어쓴다.
    이 API는 하루 1회 갱신되고 limit=0으로 전체 히스토리를 한 번에 받아오는 방식이라,
    upbit_data_service.py의 gap-fill 로직과 달리 "통째로 다시 받아서 덮어쓰기"가 더
    단순하고 안전하다."""
    cached = _load_cache()
    today = datetime.now(timezone.utc).date()

    if cached.empty or cached["date"].max().date() < today:
        with httpx.Client(timeout=15) as client:
            raw = _fetch_fear_greed_all(client)
        cached = _parse_fear_greed(raw)
        _save_cache(cached)

    start_date = start.date()
    end_date = end.date()
    mask = (cached["date"].dt.date >= start_date) & (cached["date"].dt.date <= end_date)
    return cached[mask].reset_index(drop=True)


def merge_fear_greed(df: pd.DataFrame, fng_df: pd.DataFrame) -> pd.DataFrame:
    """대상 코인 캔들(df, candle_time 컬럼 필요)에 공포탐욕지수(fng_df, date 컬럼)를
    merge_asof(direction="backward")로 병합한다 — 각 캔들 시각 기준 그 시각 이전(또는 당일)의
    가장 최근 지수값을 채워, 미래 데이터가 과거 캔들에 섞여드는 것(look-ahead bias)을 막는다.
    fng_df가 비어있거나 df의 가장 이른 캔들보다 늦게 시작하면 그 구간은 NaN으로 남는다 —
    호출부(backend/main.py)가 이 NaN을 보고 400 에러를 낸다."""
    if fng_df.empty:
        return df.assign(fear_greed_value=float("nan"))

    merged = pd.merge_asof(
        df.sort_values("candle_time").reset_index(drop=True),
        fng_df.sort_values("date").reset_index(drop=True),
        left_on="candle_time",
        right_on="date",
        direction="backward",
    )
    return merged.drop(columns="date")


__all__ = ["get_fear_greed_cmc", "merge_fear_greed"]
