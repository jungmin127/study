# 한국프리미엄(KOREA_PREMIUM) 외부 데이터 연동 설계

## 목적

`docs/superpowers/specs/2026-07-28-fear-greed-index-external-data-design.md`의 "이 스펙에 포함하지 않은 것"에
남겨뒀던 C 레이어 항목 중 "김치프리미엄"을 다룬다. 이번 스펙에서는 **명칭을 "한국프리미엄"으로 정하고
(`KOREA_PREMIUM`)**, 조건 빌더에 새 지표로 추가한다. 시가총액/온체인 데이터는 사용자가 이번 라운드에서
명시적으로 제외했다(각각 CoinGecko coin-id 매핑 필요, 유료 벤더 없이는 구현 불가 — 별도 판단 사안이라
여기서 다시 다루지 않는다).

## 배경 리서치

### 참고 프로젝트 `backtesting_1`의 구현

- 공식: `(업비트 KRW-BTC / (바이낸스 USDT-BTC × 업비트 KRW-USDT)) - 1`. 환율은 업비트 KRW-USDT 시세를
  그대로 쓰고(별도 forex API 불필요), 그 마켓이 없던 과거 구간만 Frankfurter.app으로 폴백.
- **BTC로 하드코딩**돼 있어 다른 코인엔 못 씀 — 일반화되지 않은 구현이라 이번 설계는 여기서 심볼 매핑
  아이디어만 참고하고, 계산 로직은 코인별로 일반화한다(아래 "계산 대상" 절 참고).

### 이 프로젝트의 기존 인프라 재사용 가능성

- `engine/condition_tree.py`의 `AUX_MARKET_INDICATORS`가 이미 `"USDT_CORRELATION": "KRW-USDT"`로 업비트
  KRW-USDT 종가를 보조 라인(`usdt_close`)으로 끌어오고 있다 — 한국프리미엄 계산에 필요한 원/달러 환율
  쪽은 새로 만들 것 없이 그대로 재사용한다.
- 바이낸스 쪽 종가만 새로 필요하다.

### 바이낸스 공개 API 실측 확인

```
GET https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=1d&limit=3
```

- API 키 불필요, 무료. 캔들 배열 반환, `close`는 인덱스 4(`[open_time, open, high, low, close, volume, ...]`).
- `interval` 파라미터가 이 프로젝트가 지원하는 4개 timeframe과 1:1로 대응됨: `minutes15→15m`,
  `minutes30→30m`, `minutes60→1h`, `days→1d`.
- 페이지네이션: `startTime`/`endTime`/`limit`(최대 1000). `upbit_data_service.py`의 캐시+gap-fill 패턴을
  그대로 복제 가능한 형태.
- **존재하지 않는 심볼**을 요청하면:
  ```
  HTTP 400 {"code":-1121,"msg":"Invalid symbol."}
  ```
  실측 확인(`NOTAREALCOINUSDT` 요청). 코인마다 바이낸스 상장 여부가 갈리는 문제를, 이 명확한 400 응답으로
  구분할 수 있다 — 별도 심볼 존재 여부 사전 조회(`exchangeInfo`) 없이, 캔들 요청 자체가 존재 여부 확인을
  겸한다.
- 심볼이 존재하지만 요청 구간이 상장일 이전이면(업비트와 동일한 동작) 에러 없이 실제 상장일 이후 데이터만
  반환된다 — 이 경우는 기존 aux-market 패턴의 "병합 결과가 전부 NaN이면 400" 처리로 충분히 커버된다.

## 아키텍처

### 계산 대상: 코인별 (BTC 고정 아님)

사용자 확인: 항상 BTC 기준이 아니라, **현재 백테스트 중인 코인 기준**으로 계산한다. 선택한 마켓이
`KRW-ETH`면 ETH의 한국프리미엄(`업비트 KRW-ETH 종가 / (바이낸스 ETHUSDT 종가 × 업비트 KRW-USDT 종가) - 1`)을
계산한다. `BTC_CORRELATION`/`USDT_CORRELATION`과 같은 "대상 코인 기준" 패턴을 그대로 따른다.

바이낸스 심볼은 `KRW-XXX → XXXUSDT` 문자열 변환으로 도출한다(예: `KRW-ETH → ETHUSDT`). 별도 매핑 테이블
없음 — 이 변환이 틀렸거나(상장 안 됨) 존재하지 않으면 위에서 확인한 400 응답이 그대로 "계산 불가" 신호가
된다.

**미상장 코인 처리**: 사용자 확인 — UI에서 미리 막지 않고, 요청 시점에 400 에러로 명확히 알린다
(공포탐욕지수의 날짜범위 미보유 처리와 동일 패턴, 마켓 선택 단계에 바이낸스 상장 정보를 미리 노출하는
건 범위 밖).

**`KRW-USDT` 자체를 백테스트 대상으로 선택한 경우**: 별도 차단 로직을 넣지 않는다. 심볼 변환 결과가
`USDTUSDT`가 되어 바이낸스가 자동으로 "Invalid symbol" 400을 반환하므로, 위 미상장 코인 처리 경로가
그대로 이 케이스도 커버한다(사용자가 이 마켓에서 이 지표를 쓸 이유도 없어 실사용 영향 없음).

### 검토한 접근

- **A. 병합 단계에서 완성된 값 하나로 계산 (채택)** — `FEAR_GREED_CMC`와 동일한 모양. `backend/main.py`의
  병합 단계에서 `korea_premium_value = (df.close / (binance_close × usdt_close) - 1) × 100`을 pandas
  벡터 연산으로 직접 계산해 컬럼으로 병합하고, 지표 팩토리는 `data.korea_premium_value` pass-through로
  끝낸다.
- **B. backtrader 라인 수식으로 계산** — `BTC_CORRELATION`/`MARKET_TREND`처럼 원시 `close` 라인 여러 개를
  피드에 실어두고, 지표 팩토리 안에서 나눗셈을 backtrader 라인 연산으로 계산. 기각 이유: 3중
  곱셈/나눗셈을 pandas 벡터 연산으로 하는 편이 유닛 테스트하기 쉽고(입력 DataFrame 하나 주고 결과 컬럼
  검증), fear-greed 병합 함수(`merge_fear_greed`)와 동일한 테스트 패턴을 그대로 재사용할 수 있다. 굳이
  backtrader 라인 수식으로 표현할 이유가 없다(`MARKET_TREND`는 SMA처럼 backtrader 내장 지표와 조합해야
  해서 그쪽이 자연스러웠던 것과 다름).
- **C. `required_aux_markets` 메커니즘을 확장해 바이낸스도 "보조 마켓"으로 취급** — 기각. 기존
  메커니즘은 "업비트의 다른 마켓 캔들"을 가정한 코드(`get_candles(aux_market, ...)`를 그대로 호출)라
  바이낸스처럼 데이터 소스 자체가 다른 경우엔 안 맞는다. `KRW-USDT` 환율 부분만 이 메커니즘을 재사용하고
  (이미 존재하는 항목이라 조회 함수를 바꿀 필요 없음), 바이낸스 종가는 fear-greed와 같은 독립 병합
  분기로 추가한다 — fear-greed 스펙에서 "지금 억지로 일반화하지 않는다"고 정한 원칙을 그대로 따른다.

### 데이터 흐름

```
바이낸스 API (klines)
  → binance_data_service.get_binance_close(symbol, timeframe, start, end)  (신규, fetch + parquet 캐시)
    - symbol = req.market의 "KRW-" 접두어를 떼고 "USDT"를 붙인 값
    - 심볼 없음(400 Invalid symbol) → 빈 DataFrame 반환(재시도 없이 즉시)
    - 그 외 에러 → upbit_data_service.py와 동일한 재시도 후 RuntimeError

업비트 KRW-USDT 캔들 (기존 aux-market 메커니즘 재사용, 변경 없음)
  → engine/condition_tree.py AUX_MARKET_INDICATORS에 "KOREA_PREMIUM": "KRW-USDT" 추가
  → backend/main.py의 기존 aux_markets 루프가 자동으로 usdt_close를 df에 병합

backend/main.py (신규 KOREA_PREMIUM 병합 분기, FEAR_GREED_CMC 분기 바로 다음)
  → binance_df = get_binance_close(symbol, timeframe, start_dt, end_dt)
  → binance_df 비어있으면 400 ("이 코인은 바이낸스에 상장되어 있지 않아...")
  → candle_time 기준 left-join으로 binance_close 병합, ffill/bfill로 소규모 결측(거래소 다운타임 등) 보정
  → 병합 후 전부 NaN이면 400 (기존 aux-market 패턴과 동일)
  → korea_premium_value = (df.close / (df.binance_close × df.usdt_close) - 1) × 100

  → build_data_feed_class(...)로 동적 PandasData 피드에 korea_premium_value 라인 추가
  → engine/indicators/sentiment.py: create_korea_premium(data) → data.korea_premium_value pass-through
  → 조건 엔진에서 다른 지표와 동일하게 threshold 비교
```

## 상세 설계

### 1. 데이터 수집 & 캐싱 — `binance_data_service.py` (신규)

```python
def get_binance_close(symbol: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame:
    """바이낸스 klines API에서 종가를 조회한다. 컬럼: [candle_time, close].
    upbit_data_service.py의 get_candles와 동일한 구조(parquet 캐시 + gap 계산 + 미마감 캔들 제외)를
    복제하되, 캔들 전체(OHLCV)가 아니라 close 하나만 저장한다 — 한국프리미엄 계산에 다른 컬럼이
    필요 없다. 심볼이 존재하지 않으면(바이낸스가 400 Invalid symbol을 반환하면) 재시도 없이 빈
    DataFrame을 즉시 반환한다 — 재시도해도 결과가 달라지지 않는 에러이므로."""
```

- **심볼 변환**: `binance_symbol(market: str) -> str` — `market.removeprefix("KRW-") + "USDT"`.
- **timeframe → interval 매핑**: `{"minutes15": "15m", "minutes30": "30m", "minutes60": "1h", "days": "1d"}`.
- **캐싱**: `data/cache/binance_ohlcv/{symbol}_{timeframe}.parquet`, 업비트 캐시(`data/cache/ohlcv/`)와
  물리적으로 분리. `upbit_data_service.py`의 `_compute_gaps`/`_load_cache`/`_save_cache`/미마감 캔들 제외
  로직을 그대로 복제한다(공통 유틸로 추출하지 않음 — `external_data_service.py`도 재시도 패턴을
  `upbit_data_service.py`에서 추출하지 않고 복제한 것과 같은 이유: 두 서비스가 데이터 소스별로
  독립적으로 진화할 수 있게 결합도를 낮춘다).
- **재시도/에러 처리**: 429는 지수 백오프 재시도, 5xx/네트워크 에러는 `upbit_data_service.py`와 동일한
  패턴으로 재시도 후 `RuntimeError`. **400 "Invalid symbol"만 예외** — 이건 재시도 대상이 아니라 "이
  코인은 바이낸스에 없다"는 확정적 신호이므로, 별도 분기로 즉시 빈 리스트를 반환한다.

### 2. 백테스트 피드 병합 — `backend/main.py`

`engine/condition_tree.py`의 `AUX_MARKET_INDICATORS`에 한 줄 추가:

```python
AUX_MARKET_INDICATORS: dict[str, str] = {
    "MARKET_TREND": "KRW-BTC",
    "BTC_CORRELATION": "KRW-BTC",
    "USDT_CORRELATION": "KRW-USDT",
    "KOREA_PREMIUM": "KRW-USDT",
}
```

이러면 기존 aux-market 병합 루프가 `usdt_close`를 자동으로 채워준다(코드 변경 없음, 이미 있는 메커니즘이
그대로 이 새 지표를 인식).

`FEAR_GREED_CMC` 분기 바로 다음에 `KOREA_PREMIUM` 전용 분기 추가:

```python
korea_premium_indicators = {
    b["indicator"] for b in collect_blocks(buy_dict) + collect_blocks(sell_dict)
}
if "KOREA_PREMIUM" in korea_premium_indicators:
    symbol = binance_symbol(req.market)
    try:
        binance_df = get_binance_close(symbol, req.timeframe, start_dt, end_dt)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if binance_df.empty:
        raise HTTPException(
            status_code=400,
            detail=f"{req.market}에 대응하는 바이낸스 심볼({symbol})이 없어 한국프리미엄을 계산할 수 없습니다",
        )
    df = df.merge(
        binance_df.rename(columns={"close": "binance_close"}), on="candle_time", how="left"
    )
    if df["binance_close"].isna().all():
        raise HTTPException(status_code=400, detail=f"해당 기간에 {symbol} 캔들 데이터가 없습니다")
    df["binance_close"] = df["binance_close"].ffill().bfill()
    df["korea_premium_value"] = (df["close"] / (df["binance_close"] * df["usdt_close"]) - 1) * 100
```

- **병합 방식**: `candle_time` 기준 exact left-join(공포탐욕지수의 날짜 단위 `merge_asof`와 다름 — 이
  지표는 캔들 대 캔들 병합이라 기존 aux-market 루프와 같은 방식). 거래소별 캔들 마감 시각이 초 단위로
  어긋날 가능성에 대비해 `ffill().bfill()`로 소규모 결측을 보정한다(기존 aux-market 패턴과 동일).
- **날짜 구간 미보유 처리**: "특정 코인만 사용 불가"(바이낸스 미상장)와 "그 구간에 데이터 없음"(상장일
  이전 구간 요청) 두 케이스 모두 400으로 명확히 처리하고, 부분 데이터로 조용히 진행하지 않는다 — 이
  프로젝트의 기존 원칙(fear-greed 스펙의 "Global Constraints")을 그대로 따른다.

### 3. 조건 빌더 / 카탈로그 / 가이드 탭 연동

B 레이어/fear-greed에서 확립된 컨벤션(카탈로그 ↔ 가이드 탭 ↔ 조건 빌더 카테고리 상수를 항상 같이 갱신)을
그대로 따른다.

- **`engine/indicators/sentiment.py`**: `create_korea_premium(data, **params) -> data.korea_premium_value`
  — `create_fear_greed_cmc`와 동일한 모양의 pass-through. 파일 상단 docstring을 "외부 데이터 소스에서
  값을 가져오거나 여러 마켓 데이터를 조합해 계산한 값을 다룬다"로 살짝 확장(현재는 공포탐욕지수만 있어
  "외부 데이터 소스" 문구뿐).
- **`backend/main.py`의 `INDICATOR_CATALOG`**: `FEAR_GREED_CMC` 옆에 추가, 카테고리는 기존 **"시장 심리"**
  재사용(신규 카테고리 불필요). label "한국프리미엄", description에 "코인별로 계산되며(대상 코인 기준
  바이낸스 USDT 페어 대비), 바이낸스에 상장되지 않은 코인은 이 지표를 쓸 수 없습니다"를 명시.
- **`StrategyConditionBuilder.tsx`의 `recommendedThreshold()`**: 한국프리미엄은 0~100 범위 오실레이터가
  아니라 부호 있는 퍼센트 값(과거 실측상 대략 -5%~+10% 범위를 오가며, 역프리미엄일 땐 음수)이라 기존
  `OSCILLATOR_BOUNDS`(저/고 경계 방식)엔 안 맞는다. `MACD_line`/`MACD_signal`과 동일한 **제로크로스
  기본값(0)** 방식을 채택 — 연산자와 무관하게 `0`을 추천값으로 채운다.
- **가이드 탭**(`indicator-guide.ts` + `indicator-example-builder.ts`): 기존 패턴대로 공식/의미/사용법 +
  표·차트 추가. `guide-sample-data.ts`에 `SAMPLE_KOREA_PREMIUM`(양수/음수를 오가는 합성 시계열, 대략
  -3%~+8% 범위) 추가.

### 4. 테스트 전략

기존 컨벤션(pytest 유닛/통합 테스트 + 프론트는 tsc + Playwright/수동 확인) 그대로.

- **`binance_data_service.py`**: httpx 호출을 monkeypatch해서 (1) 정상 심볼 klines 파싱, (2) 429 재시도,
  (3) 5xx 재시도 후 RuntimeError, (4) **400 Invalid symbol → 재시도 없이 즉시 빈 DataFrame**(재시도
  카운트가 0인지 검증), (5) 캐시 gap-fill 동작(부분 구간만 재조회).
- **`engine/condition_tree.py`**: `AUX_MARKET_INDICATORS["KOREA_PREMIUM"] == "KRW-USDT"` 반영 확인,
  `required_aux_markets`가 `KOREA_PREMIUM` 블록에서 `KRW-USDT`를 올바르게 뽑아내는지.
- **`engine/indicators`**: `create_korea_premium`이 입력 라인을 그대로 반환하는지(`create_fear_greed_cmc`
  테스트와 동일 패턴).
- **`backend/main.py`**: (1) `close`/`binance_close`/`usdt_close`로부터 `korea_premium_value`가 공식대로
  정확히 계산되는지(고정된 세 값으로 수기 검증 가능한 케이스), (2) 바이낸스 미상장 심볼(mock 400) →
  400 에러, (3) 병합 후 전부 NaN(상장일 이전 구간) → 400 에러, (4) 카탈로그 커버리지 테스트 확장.
- **프론트**: `npx tsc --noEmit` 클린 + Playwright로 조건 빌더 드롭다운(음수 threshold 포함)/가이드 탭
  수동 확인.

## 이 스펙에 포함하지 않은 것

- **시가총액(코인별 raw market cap), 온체인 데이터(거래소 입출금량)**: 사용자가 이번 라운드에서
  명시적으로 제외(온체인은 무료 API로 사실상 불가, 시가총액은 CoinGecko 매핑+무료 티어 히스토리 제약이
  걸림돌 — 별도 판단 필요 시 재논의).
- **업비트 데이터랩 공포탐욕지수(`FEAR_GREED_UPBIT`)**: fear-greed 스펙에서 이미 범위 밖으로 남겨둔 항목,
  변경 없음.
- **바이낸스 선물 펀딩비(Funding Rate)**: 이번 스펙 작성 중 리서치로 새로 발견한 후보. 바이낸스 선물
  API(`fapi.binance.com/fapi/v1/fundingRate`)가 코인 상장 시점부터의 펀딩비 히스토리를 무료로 제공한다
  (미결제약정·롱숏비율은 최근 30일로 히스토리가 짧아 백테스트에 못 씀, 펀딩비만 장기 히스토리 보유).
  시장 심리 카테고리에 들어갈 새 지표 후보지만 이번 스펙 범위 밖 — 별도 스펙(`FUNDING_RATE` 가칭)으로
  다룬다.
