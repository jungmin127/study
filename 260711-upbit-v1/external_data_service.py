"""
external_data_service.py

업비트 API가 아닌 외부 API에서 가져오는 시장 데이터(공포/탐욕 지수 등 C 레이어)를 조회·캐싱한다.
재시도/캐싱 패턴은 upbit_data_service.py를 따른다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

CMC_FNG_URL = "https://api.alternative.me/fng/"

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 1.0
RATE_LIMIT_BACKOFF_SECONDS = 5.0

CACHE_DIR = Path(__file__).parent / "data" / "cache" / "external"

_FNG_COLUMNS = ["date", "fear_greed_value"]

# alternative.me 히스토리엔 실제로 결측일이 간간이 있다(예: 2018-04-14~16, 2024-10-26). 요청
# 시작일 자체가 결측일이면 merge_fear_greed()의 merge_asof(direction="backward")가 참조할
# 이전 값이 없어 정상 구간인데도 400 에러가 나므로, 조회 시작일보다 이만큼 앞선 날짜부터
# 여유 있게 포함해 반환한다(2018-02-01보다 이른 구간은 이 여분을 포함해도 여전히 데이터가
# 없어 그대로 400으로 이어진다 — 진짜 범위 밖 요청까지 통과시키지는 않는다).
_LOOKBACK_MARGIN_DAYS = 7


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
        except (KeyError, ValueError) as exc:
            # 200 OK인데 예상 스키마({"data": [...]}) 형태가 아닌 경우 — HTTPError로는
            # 안 잡히므로 별도로 잡아 같은 재시도/에러 통일 경로를 태운다.
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    raise RuntimeError(f"alternative.me 공포탐욕지수 API 호출 실패: {last_exc}")


def _parse_fear_greed(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_FNG_COLUMNS)

    df = pd.DataFrame(raw)
    # us로 맞춘다 — upbit_data_service.get_candles()가 candle_time을 us로 정규화해서 반환하는데
    # (upbit_data_service._CANDLE_TIME_UNIT 참고), 여기서 ns 그대로 두면 merge_fear_greed()의
    # merge_asof가 "incompatible merge keys ... datetime64[us, UTC] and datetime64[ns, UTC]"로
    # 죽는다.
    df["date"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True).dt.normalize().dt.as_unit("us")
    df["fear_greed_value"] = df["value"].astype(float)
    return df[_FNG_COLUMNS].drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)


def _cache_path() -> Path:
    return CACHE_DIR / "fear_greed_cmc.parquet"


def _load_cache() -> pd.DataFrame:
    path = _cache_path()
    if not path.exists():
        return pd.DataFrame(columns=_FNG_COLUMNS)
    df = pd.read_parquet(path)
    # us로 맞춘다 — _parse_fear_greed와 동일한 이유(merge_fear_greed의 merge_asof가
    # 업비트 캔들의 us 해상도와 비교하므로, 과거에 ns로 저장된 캐시 파일이 그대로 반환되면
    # MergeError를 던진다).
    if not df.empty and df["date"].dt.unit != "us":
        df = df.assign(date=df["date"].dt.as_unit("us"))
    return df


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

    lookback_start_date = start.date() - timedelta(days=_LOOKBACK_MARGIN_DAYS)
    end_date = end.date()
    mask = (cached["date"].dt.date >= lookback_start_date) & (cached["date"].dt.date <= end_date)
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
