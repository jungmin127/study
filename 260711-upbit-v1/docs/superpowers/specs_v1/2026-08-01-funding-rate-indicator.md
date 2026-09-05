# 펀딩비(Funding Rate) 지표 추가 설계

- 작성일: 2026-08-01
- 상태: 승인 대기 (사용자 리뷰 전)

## 목적

`docs/superpowers/specs_v1/2026-07-27-strategy-source-classification.md`의 C 레이어 후보 중
"미결제약정&펀딩비" 항목을 부분 구현한다. 미결제약정/롱숏비율은 무료 API가 최근 30일치만
제공해 백테스트에 못 쓰지만, 바이낸스 선물 펀딩비는 긴 히스토리를 무료로 제공하므로 이것만
구현한다. 기존 `KOREA_PREMIUM`/`FEAR_GREED_CMC`(C 레이어, "시장 심리" 카테고리)와 동일한
패턴(외부 API 조회 → parquet 캐싱 → `backend/main.py`에서 캔들에 병합)을 재사용한다.

`backtesting_1`(이전 버전 앱) 전체를 검색했으나 펀딩비 관련 코드가 전혀 없어 참고할 선례가
없다 — 이 스펙이 처음부터 새로 설계한 것이다.

## 결정된 사항 (사용자 승인)

- **조건 구조**: 원시 펀딩비값을 threshold와 비교하는 단순 구조. `KOREA_PREMIUM`/
  `FEAR_GREED_CMC`와 동일 — 파생값(누적/이동평균 등) 계산 없음.
- **카탈로그 카테고리**: 기존 "시장 심리"에 포함(신규 카테고리 안 만듦).
- **단위**: 퍼센트로 저장(`funding_rate × 100`) — `KOREA_PREMIUM`의 "+5%" 같은 표기 컨벤션과
  일관되게. 바이낸스 API는 소수 비율(`0.00005729`)로 주는 걸 변환한다.
- **심볼 매핑**: 기존 `binance_data_service.binance_symbol()`(`KRW-ETH`→`ETHUSDT`) 그대로
  재사용 — 바이낸스 선물 심볼 표기가 현물과 동일.
- **API 검증 (실측, 2026-08-01)**: `GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=ETHUSDT`
  로 직접 호출해 확인함 — `fundingRate`(문자열, 소수 비율), `fundingTime`(ms epoch) 필드,
  약 8시간 간격. **존재하지 않는 심볼을 넣어도 HTTP 400이 아니라 200 + 빈 배열을 반환**한다
  (spot klines의 `-1121` 에러 코드 패턴과 다름) — 그래서 "심볼 없음"과 "이 구간에 데이터
  없음"을 구분하지 않고, 결과가 비었으면 동일하게 처리한다.

## 설계

### 1. `binance_data_service.py` — 펀딩비 조회 + 캐싱

`get_binance_close()`와 동일한 재시도(`RETRY_ATTEMPTS`/`RETRY_BASE_DELAY_SECONDS`/
`RATE_LIMIT_BACKOFF_SECONDS`)·페이지네이션·parquet 캐싱 패턴을 재사용하되, base URL만
선물 API로 바꾼다.

```python
FUNDING_BASE_URL = "https://fapi.binance.com/fapi/v1"

_FUNDING_COLUMNS = ["funding_time", "funding_rate"]

FUNDING_CACHE_DIR = Path(__file__).parent / "data" / "cache" / "binance_funding"


def _fetch_funding_page(
    client: httpx.Client,
    symbol: str,
    start_time: datetime,
    end_time: datetime,
    limit: int = 1000,
) -> list[dict]:
    params = {
        "symbol": symbol,
        "startTime": int(start_time.timestamp() * 1000),
        "endTime": int(end_time.timestamp() * 1000),
        "limit": limit,
    }
    last_exc: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = client.get(f"{FUNDING_BASE_URL}/fundingRate", params=params)
            if resp.status_code == 429:
                time.sleep(RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            last_exc = exc
            time.sleep(RETRY_BASE_DELAY_SECONDS * (2**attempt))

    raise RuntimeError(f"바이낸스 펀딩비 API 호출 실패 (symbol={symbol}): {last_exc}")


def _parse_funding(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=_FUNDING_COLUMNS)
    df = pd.DataFrame(raw)
    df["funding_time"] = pd.to_datetime(df["fundingTime"].astype("int64"), unit="ms", utc=True)
    df["funding_rate"] = df["fundingRate"].astype(float) * 100
    return df[_FUNDING_COLUMNS].drop_duplicates(subset="funding_time").sort_values("funding_time").reset_index(drop=True)


def _fetch_funding_range(
    symbol: str, start: datetime, end: datetime, client: httpx.Client | None = None
) -> pd.DataFrame:
    close_client = client is None
    client = client or httpx.Client(timeout=10)
    try:
        frames: list[pd.DataFrame] = []
        cursor = start
        while cursor <= end:
            raw = _fetch_funding_page(client, symbol, cursor, end)
            if not raw:
                break
            page_df = _parse_funding(raw)
            frames.append(page_df)
            newest = page_df["funding_time"].max()
            if len(raw) < 1000 or newest >= end:
                break
            cursor = newest + timedelta(milliseconds=1)
            time.sleep(REQUEST_DELAY_SECONDS)

        if not frames:
            return pd.DataFrame(columns=_FUNDING_COLUMNS)

        merged = pd.concat(frames).drop_duplicates(subset="funding_time").sort_values("funding_time").reset_index(drop=True)
        return merged[(merged["funding_time"] >= start) & (merged["funding_time"] <= end)].reset_index(drop=True)
    finally:
        if close_client:
            client.close()


def _funding_cache_path(symbol: str) -> Path:
    return FUNDING_CACHE_DIR / f"{symbol}.parquet"


def _load_funding_cache(symbol: str) -> pd.DataFrame:
    path = _funding_cache_path(symbol)
    if not path.exists():
        return pd.DataFrame(columns=_FUNDING_COLUMNS)
    return pd.read_parquet(path)


def _save_funding_cache(symbol: str, df: pd.DataFrame) -> None:
    FUNDING_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(_funding_cache_path(symbol), index=False)


def get_binance_funding_rate(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """바이낸스 선물 펀딩비 히스토리를 조회한다(퍼센트 단위, funding_rate = 원시값×100).
    심볼이 선물에 없거나 이 구간에 데이터가 없으면 빈 DataFrame을 반환한다 — futures
    fundingRate 엔드포인트는 spot klines와 달리 잘못된 심볼도 200 + 빈 배열을 반환하므로
    (실측 확인, 2026-08-01), "심볼 없음"과 "데이터 없음"을 구분하지 않는다."""
    cached = _load_funding_cache(symbol)
    gaps = _compute_gaps(cached, start, end)  # candle_time 대신 funding_time 기준으로 오버로드 필요(구현 시 확인)

    if gaps:
        fetched = [_fetch_funding_range(symbol, g_start, g_end) for g_start, g_end in gaps]
        to_concat = [df for df in [cached, *fetched] if not df.empty]
        cached = (
            pd.concat(to_concat).drop_duplicates(subset="funding_time").sort_values("funding_time").reset_index(drop=True)
            if to_concat else pd.DataFrame(columns=_FUNDING_COLUMNS)
        )
        _save_funding_cache(symbol, cached)

    result = cached[(cached["funding_time"] >= start) & (cached["funding_time"] <= end)]
    return result.reset_index(drop=True)


def merge_funding_rate(df: pd.DataFrame, funding_df: pd.DataFrame) -> pd.DataFrame:
    """대상 코인 캔들(df, candle_time 컬럼)에 펀딩비(funding_df, funding_time 컬럼)를
    merge_asof(direction="backward")로 병합한다 — 각 캔들 시각 기준 그 시각 이전(또는 동시)
    가장 최근 펀딩비를 채운다(look-ahead bias 방지). funding_df가 비어있으면 전체 NaN —
    호출부(backend/main.py)가 이 NaN을 보고 400 에러를 낸다. external_data_service.py의
    merge_fear_greed()와 동일한 기법."""
    if funding_df.empty:
        return df.assign(funding_rate_value=float("nan"))

    merged = pd.merge_asof(
        df.sort_values("candle_time").reset_index(drop=True),
        funding_df.sort_values("funding_time").reset_index(drop=True).rename(columns={"funding_rate": "funding_rate_value"}),
        left_on="candle_time",
        right_on="funding_time",
        direction="backward",
    )
    return merged.drop(columns="funding_time")
```

**주의**: 기존 `_compute_gaps(cached, start, end)`는 `candle_time` 컬럼명을 전제로 만들어져 있다
(`binance_data_service.py`의 기존 함수, `get_binance_close`가 씀). 펀딩비 캐시는 컬럼명이
`funding_time`이라 그대로 재사용하면 `KeyError`가 난다 — 구현 시 `_compute_gaps`를 컬럼명
파라미터를 받도록 일반화하거나, 펀딩비 전용 `_compute_funding_gaps`를 별도로 만들어야 한다.
(이 스펙의 self-review에서 발견 — 플랜 작성 시 반드시 반영할 것.)

### 2. 지표 등록

`engine/indicators/sentiment.py`에 추가 (기존 `create_fear_greed_cmc`/`create_korea_premium`과
완전히 동일한 패턴 — 실제 계산은 병합 단계에서 끝나므로 그냥 병합된 라인을 반환):

```python
def create_funding_rate(data: bt.feeds.PandasData, **params) -> bt.LineBuffer:
    return data.funding_rate_value
```

`engine/indicators/__init__.py`의 `INDICATOR_FACTORY`에 `"FUNDING_RATE": create_funding_rate`
등록. `engine/runner.py`의 `_OPTIONAL_LINE_CANDIDATES` 튜플에 `"funding_rate_value"` 추가
(안 하면 병합된 컬럼이 backtrader data feed에 안 실린다).

`get_indicator_value()`(`engine/condition_tree.py`)에 별도 분기는 필요 없다 — `create_funding_rate`가
반환하는 `data.funding_rate_value`는 일반 라인이라 기존 `else: return float(obj[0])` 폴백으로
충분하다(FEAR_GREED_CMC/KOREA_PREMIUM도 별도 분기 없이 이 폴백을 씀, 확인 완료).

### 3. `backend/main.py` — 카탈로그 + 병합

카탈로그 항목(`INDICATOR_CATALOG`, `KOREA_PREMIUM` 항목 바로 다음에 추가):

```python
{
    "value": "FUNDING_RATE", "label": "펀딩비(바이낸스 선물)", "category": "시장 심리",
    "params": [],
    "description": "대상 코인의 바이낸스 무기한 선물 펀딩비를 퍼센트로 나타냅니다. 양수면 롱이 숏에게 수수료를 지불(롱 우세/과열), 음수면 그 반대(숏 우세)입니다.",
    "example": "펀딩비 > 0.05%면 롱 포지션이 과열된 구간으로, < -0.03%면 숏 포지션이 과열된 구간으로 흔히 해석합니다.",
},
```

`_fetch_backtest_dataframe()`에 `KOREA_PREMIUM` 블록 바로 다음, `used_indicators` 체크 블록으로 추가:

```python
if "FUNDING_RATE" in used_indicators:
    symbol = binance_symbol(market)
    try:
        funding_df = get_binance_funding_rate(symbol, start_dt, end_dt)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    df = merge_funding_rate(df, funding_df)
    if df["funding_rate_value"].isna().all():
        raise HTTPException(
            status_code=400,
            detail=f"{symbol}의 바이낸스 선물 펀딩비 데이터가 해당 기간에 없습니다(선물 미상장 또는 기간 밖일 수 있습니다)",
        )
```

`isna().all()`을 쓴다(KOREA_PREMIUM처럼 `isna().any()`가 아님) — 펀딩비는 8시간마다 한 번씩만
찍히고 `merge_asof`가 각 캔들에 "그 이전 가장 최근 값"을 채우는 구조라, **캔들 구간의 맨
앞부분**(첫 펀딩비 이벤트 이전)은 정상적으로 NaN이 남을 수 있다(정상 동작, 에러 아님) — 반면
`isna().all()`(전부 NaN)은 애초에 그 심볼의 펀딩비 데이터를 하나도 못 찾았다는 뜻이라 에러로
처리하는 게 맞다. 앞부분 NaN은 `ffill()`/`bfill()` 없이 그대로 두면 조건 평가 시(get_indicator_value)
NaN과 threshold 비교가 항상 False가 되어 자연스럽게 "그 구간은 조건 불충족"으로 처리된다
(KOREA_PREMIUM처럼 `ffill().bfill()`을 걸지 않는 이유 — KOREA_PREMIUM은 보조 마켓 캔들 병합이라
매 캔들마다 값이 있어야 정상이라 다르다).

`backend/main.py` 상단 import에 `binance_data_service`에서 `get_binance_funding_rate`,
`merge_funding_rate` 추가.

### 4. 조건식 빌더 threshold 추천 (`frontend/components/StrategyConditionBuilder.tsx`)

`OSCILLATOR_BOUNDS`(이름과 무관하게 "시장 심리" 지표도 이미 포함 — `FEAR_GREED_CMC` 참고)에 추가:

```ts
FUNDING_RATE: { low: -0.03, high: 0.05 },
```

롱 과열(양수 극단)/숏 과열(음수 극단)의 통상적인 경계값을 초기 추천으로 둔다. 다른 지표들과
마찬가지로 사용자가 직접 조정하는 것을 전제로 한 시작값이다.

### 5. 지표 가이드 (`frontend/lib/indicator-guide.ts`)

`KOREA_PREMIUM` 항목 바로 다음에 추가(기존 `FEAR_GREED_CMC`/`KOREA_PREMIUM` 항목과 같은 톤):

```ts
FUNDING_RATE: {
  meaning:
    '대상 코인의 바이낸스 무기한 선물(perpetual futures) 펀딩비를 퍼센트로 나타낸 값입니다. 양수면 롱 포지션이 숏 포지션에게 수수료를 지불하는 상태(롱 우세/과열), 음수면 그 반대(숏 우세/과열)입니다. 8시간마다 갱신되며, 각 캔들 시각 기준 가장 최근 값을 그대로 씁니다.',
  params: [],
  formula: '바이낸스 선물 API가 산출하는 값을 그대로 가져와 퍼센트로 변환합니다(원시값 × 100). 이 앱이 직접 계산하지 않습니다. 대상 코인이 바이낸스 선물에 상장돼 있지 않으면 계산할 수 없습니다.',
  thresholdExample: '값은 부호 있는 퍼센트입니다. 예: 임계값 0.05, 연산자 ">"면 롱 과열 구간을, 임계값 -0.03, 연산자 "<"면 숏 과열 구간을 포착합니다.',
  usage: '펀딩비가 과도하게 양수면 롱 포지션이 몰려 과열됐다고 보고 역발상 매도(숏) 필터로, 과도하게 음수면 숏이 몰렸다고 보고 역발상 매수(롱) 필터로 흔히 씁니다. KOREA_PREMIUM과 마찬가지로 대상 코인마다 값이 다릅니다.',
},
```

## 검증 절차 (구현 완료 후)

1. `pytest tests/test_binance_data_service.py tests/test_indicators.py tests/test_backend.py -v` — 신규 테스트 포함 통과 확인.
2. 백엔드/프론트 기동 후, 조건식 빌더에서 `FUNDING_RATE` 선택 → threshold 추천값(`-0.03`/`0.05`) 확인.
3. 실제 백테스트 1회 실행(예: `KRW-ETH`, `FUNDING_RATE > 0.05` 매도 조건 포함)해서 에러 없이 완주하는지, 지표 가이드 페이지에서 빈 화면 아닌지 확인.
4. 선물 미상장 코인(또는 존재하지 않는 심볼) 조합으로 실행해 400 에러가 명확한 메시지로 나오는지 확인.

## 범위 밖

- 미결제약정(open interest)/롱숏비율 — 무료 API가 최근 30일치만 제공해 백테스트 불가, 구현 안 함.
- 펀딩비 파생값(누적/이동평균/변화율 등) — 원시값 threshold 비교만 구현.
- 바이낸스 외 다른 거래소(예: OKX, Bybit) 펀딩비 — 이번 범위 아님.
- grid search 스킬에 `FUNDING_RATE` 편입 — 이번 범위 아님(오실레이터 계열이 아니라 "시장 심리" 카테고리라 애초에 grid search 대상 밖).

## Self-Review 결과

- **스펙 커버리지**: 사용자 승인 사항(원시값 threshold, 시장심리 카테고리, % 단위) 전부 반영.
- **내부 정합성 점검 중 실제 버그 발견**: `_compute_gaps()`가 `candle_time` 컬럼명을 전제로
  한 기존 함수라, 펀딩비 캐시(`funding_time` 컬럼)에 그대로 재사용하면 `KeyError`가 난다 —
  "설계" 섹션에 주의사항으로 명시하고 플랜에서 컬럼명 파라미터화 또는 전용 함수 분리로
  해결하도록 함(구현 전에 미리 잡음 — STOCH_K/STOCH_D k_period 버그, MACD dedup 버그와 같은
  패턴의 사전 발견).
- **`isna().any()` vs `isna().all()` 구분**: KOREA_PREMIUM(매 캔들 값 있어야 정상)과
  FUNDING_RATE(구간 앞부분 NaN이 정상일 수 있음)의 차이를 명시적으로 구분해 반영.
- **대상 파일 목록**: `binance_data_service.py`, `engine/indicators/sentiment.py`,
  `engine/indicators/__init__.py`, `engine/runner.py`, `backend/main.py`,
  `frontend/components/StrategyConditionBuilder.tsx`, `frontend/lib/indicator-guide.ts`,
  테스트(`tests/test_binance_data_service.py`, `tests/test_indicators.py`, `tests/test_backend.py`).
