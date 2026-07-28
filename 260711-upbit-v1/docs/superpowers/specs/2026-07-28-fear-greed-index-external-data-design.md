# 공포/탐욕 지수(CMC) 외부 데이터 연동 설계

## 목적

`docs/superpowers/specs/2026-07-27-strategy-source-classification.md`에서 **C 레이어**(외부 API/MCP 필수)로
분류된 후보 중 하나인 "공포탐욕지수"를 조건 빌더에 새 지표로 추가한다. C 레이어 전체(시가총액/김치프리미엄/
시장심리/공포탐욕지수, 나중엔 온체인/선물/구글트렌드까지)는 데이터 소스·심볼 매핑·캐싱 정책이 제각각이라
사실상 독립된 서브프로젝트들이다 — 한 플랜에 욱여넣지 않고 하나씩 스펙→플랜→구현 사이클을 돈다.
**이 스펙은 그 첫 번째로, 공포탐욕지수(CMC/alternative.me) 하나만 다룬다.** 나머지는 이 스펙의 범위 밖이며
문서 끝의 "이 스펙에 포함하지 않은 것"에 이유를 남긴다.

## 배경 리서치

### 참고 프로젝트: `C:\Users\jungm\project\backtesting_1`

이미 유사 기능을 구현한 더 성숙한 프로젝트를 직접 조사했다.

- **BTC 도미넌스**: CoinGecko 무료 API로 BTC/ETH/USDT 시가총액만 받아 "상위 3종 합산 ÷ 0.72(경험적 커버리지
  보정)"로 전체 시장을 근사. `backend/app/services/external_data_service.py`.
- **김치프리미엄**: `(업비트 KRW-BTC / (바이낸스 USDT-BTC × 업비트 KRW-USDT)) - 1`. 환율은 업비트 KRW-USDT
  시세를 그대로 쓰고(별도 forex API 불필요), 그 마켓이 없던 과거 구간만 Frankfurter.app(무료 환율 API)으로
  폴백. **다만 BTC로 하드코딩**돼 있어 다른 코인엔 못 씀 — 일반화되지 않은 구현.
- **공포탐욕지수**: `backend/app/services/sentiment_service.py`에 alternative.me(CMC 동일 지수, 무료, 키
  불필요) 연동이 이미 구현돼 있다. 캐싱은 parquet 파일(`data/cache/sentiment/cmc_fng.parquet`), 캐시 히트
  판정은 "요청 구간이 캐시 범위에 완전히 포함되는지"로 단순 검사.
- **아키텍처**: 지표 하나당 외부 데이터 컬럼을 "하나"만 지정하는 단순 구조(`extra_column` 하나짜리 커스텀
  `PandasData`, `backend/app/api/v1/backtests.py`). 이 프로젝트(upbit-v1)는 이번 세션(B 레이어 플랜)에서
  이미 이보다 발전된 **N개 보조 라인 동시 지원 구조**(`engine/runner.py::build_data_feed_class`)를 만들어놔서
  backtesting_1보다 나은 시작점이다.
- `docs/ROADMAP_v2.md`(backtesting_1)에 "업비트 데이터랩 공포탐욕지수"(`data.upbit.com/api/public/fear_and_greed`)
  라는 메모가 있었으나 **미구현 상태였고, 실제로 그 URL은 DNS조차 안 잡히는 존재하지 않는 도메인이었다**
  (검증 결과는 아래 참고).

### 업비트 데이터랩 지수는 이번 스펙에서 제외

웹서치로 실제 도메인은 `datalab.upbit.com`(예: `datalab.upbit.com/assets/BTC/fear-greed-index`)임을 확인했다.
다만 두 가지 이유로 이번 스펙에서는 제외하고 CMC만 다룬다:

1. **코인별로 다른 지수다** — CMC/alternative.me는 시장 전체 공통값 하나인데, 업비트 데이터랩은 "업비트 내부
   기준을 충족하는 자산 대상"으로 코인마다 따로 계산된다. 없는 코인도 있을 수 있어 원래 요청하신 "특정 코인만
   사용 불가 노티" 요건이 이 지수엔 그대로 적용된다.
2. **실제 JSON API 엔드포인트가 미확인 상태다** — 페이지가 React로 클라이언트 렌더링돼 있어 curl로는 API를
   못 찾았고, 브라우저 개발자도구로 네트워크 탭을 리버스엔지니어링해야 확인 가능하다.

업비트 데이터랩 지수는 리버스엔지니어링이 끝난 뒤 별도 스펙(`FEAR_GREED_UPBIT`)으로 다룬다.

### CMC(alternative.me) API 실측 확인

```
GET https://api.alternative.me/fng/?limit=0
```

- API 키 불필요, 무료, 요청 제한 명시 없음.
- 응답 예시:
  ```json
  {
    "data": [
      {"value": "29", "value_classification": "Fear", "timestamp": "1785196800", "time_until_update": "43954"},
      {"value": "30", "value_classification": "Fear", "timestamp": "1785110400"}
    ],
    "metadata": {"error": null}
  }
  ```
- `limit=0`이면 전체 히스토리를 **한 번의 호출**로 반환한다(현재 3,096건).
- 가장 오래된 데이터: `timestamp=1517443200` = **2018-02-01 00:00:00 UTC**. 이보다 이른 구간은 데이터가
  없다.
- `value`는 문자열로 온 숫자(0~100), `timestamp`는 UTC 유닉스초 문자열, 정오가 아니라 00:00:00 UTC로
  정규화된 일봉 기준값이다 — 업비트 일봉 마감(09:00 KST = 00:00 UTC)과 시각이 정확히 일치해 별도 시간대
  보정이 필요 없다.
- `time_until_update`(최신 레코드에만 존재)로 다음 갱신까지 남은 초를 알 수 있으나, 캐시 갱신 판단에는
  쓰지 않는다(아래 캐싱 설계 참고 — 더 단순한 기준을 쓴다).

## 아키텍처

### 검토한 접근 3가지

- **A. 최소 통합 (채택)** — 이번 지표 전용으로 필요한 만큼만 만든다. 기존 `AUX_MARKET_INDICATORS`/
  `AUX_MARKET_LINE_NAME`(마켓 캔들 병합용, B 레이어에서 일반화)는 재사용하지 않고, 이 지표 전용의 별도
  병합 분기를 하나 추가한다. 이유: 기존 aux-market 메커니즘은 "업비트의 다른 마켓 OHLC 캔들"을 병합하는
  용도로 설계돼 있어 캔들 구조를 가정한다. 공포탐욕지수는 값 하나짜리 외부 API 데이터라 모양이 다르다.
  이번이 C 레이어의 첫 사례라, 지금 딱 맞는 추상화를 억지로 예측하기보다 김치프리미엄(2번째 사례)까지
  만들어본 뒤 공통 패턴이 보이면 그때 일반화한다 — B 레이어에서 BTC/테더 상관계수 두 사례가 나온 뒤에야
  보조마켓 메커니즘을 일반화했던 것과 같은 순서.
- **B. 지금 바로 일반화** — `required_aux_markets`를 마켓 캔들이든 외부 API든 같은 인터페이스로 다루게
  확장. 사례가 1개뿐인 지금 일반화하면 잘못된 모양으로 굳어질 위험이 있어 기각.
- **C. backtesting_1 방식 그대로** — 지표당 "extra 컬럼 1개"만 지원. upbit-v1은 이미 N개 동시 지원 구조가
  있어 퇴보시킬 이유가 없어 기각.

### 데이터 흐름

```
alternative.me API
  → external_data_service.get_fear_greed_cmc(start, end)   (신규, fetch + parquet 캐시)
  → backend/main.py: 조건 트리에 FEAR_GREED_CMC 있으면 병합
  → build_data_feed_class(...)로 동적 PandasData 피드에 fear_greed_value 라인 추가
  → engine/indicators: create_fear_greed_cmc(data) → data.fear_greed_value pass-through
  → 조건 엔진에서 SMA/RSI 등과 동일하게 threshold 비교
```

## 상세 설계

### 1. 데이터 수집 & 캐싱 — `external_data_service.py` (신규)

```python
def get_fear_greed_cmc(start: datetime, end: datetime) -> pd.DataFrame:
    """캐시(data/cache/external/fear_greed_cmc.parquet) 확인 후, 최신 캐시 날짜가
    오늘(UTC)보다 이전이면 limit=0으로 전체 재조회 후 덮어쓴다. start~end 구간으로
    필터링해 [date, fear_greed_value] 컬럼의 DataFrame을 반환한다."""
```

- **캐시 갱신 조건**: "캐시에 저장된 가장 최신 날짜가 오늘(UTC) 날짜보다 이전이면 재조회". 이 API는
  하루 1회 갱신되고 전체 히스토리를 한 번에 받아오는 방식이라, `upbit_data_service.py`의 gap-fill
  로직(`_compute_gaps`, 부분 구간만 추가 조회)은 필요 없다 — "통째로 다시 받아서 덮어쓰기"가 더 단순하고
  안전하다(API 호출 자체가 가벼워 비용 문제 없음).
- **재시도/에러 처리**: `upbit_data_service.py`의 기존 패턴(429 시 지수 백오프 재시도, 실패 시
  `RuntimeError`로 통일된 메시지)을 그대로 따른다.
- **날짜 정규화**: `timestamp`를 UTC 00:00:00 기준으로 정규화해 `date` 컬럼에 저장.

### 2. 백테스트 피드 병합 — `backend/main.py`

조건 트리에 `FEAR_GREED_CMC`가 있으면:

```python
if "FEAR_GREED_CMC" in condition_indicators:
    fng_df = get_fear_greed_cmc(start_dt, end_dt)
    if fng_df.empty:
        raise HTTPException(400, "이 조건에 필요한 공포탐욕지수 데이터가 해당 기간에 없습니다")
    df = _merge_daily_external_series(df, fng_df, "fear_greed_value")  # merge_asof, direction="backward"
```

- **분봉/시간봉 forward-fill**: `pd.merge_asof(df, fng_df, left_on="candle_time", right_on="date",
  direction="backward")`로, 각 캔들 시각 기준 "그 시각 이전(또는 당일)의 가장 최근 지수값"을 채운다.
  예: 15분봉이면 하루 96개 캔들이 그날의 `fear_greed_value`를 그대로 공유한다. `direction="backward"`를
  쓰는 이유는 미래 시점의 지수값이 과거 캔들에 새어 들어가는 것(look-ahead bias)을 막기 위함이다.
- **날짜 구간 미보유 처리**: 이 지표는 코인이 아니라 시장 전체 공통값이므로, "특정 코인만 사용 불가"라는
  원래 요건은 이 지표엔 해당하지 않는다. 대신 **"백테스트 시작일이 2018-02-01보다 이른 경우"**가 실질적인
  사용 불가 케이스다 — 이 경우 기존 aux-market 패턴과 동일하게 400 에러로 명확히 알리고, 부분 데이터로
  조용히 진행하지 않는다.
- 이 병합 분기는 기존 `required_aux_markets`(마켓 캔들 병합) 로직과 나란히 두되, 섞지 않는다(아키텍처
  결정 A 참고).

### 3. 조건 빌더 / 카탈로그 / 가이드 탭 연동

B 레이어에서 확립된 컨벤션(카탈로그 ↔ 가이드 탭 ↔ 조건 빌더 카테고리 상수를 항상 같이 갱신)을 그대로
따른다.

- **`engine/indicators/`**: `create_fear_greed_cmc(data, **params) -> data.fear_greed_value` — `TRADE_VALUE`와
  동일하게 파라미터 없는 pass-through. `get_indicator_value()`에 새 분기 불필요(단일 라인이라 기존
  fallback이 처리).
- **`backend/main.py`의 `INDICATOR_CATALOG`**: `BTC_CORRELATION`/`USDT_CORRELATION` 옆에 추가, 카테고리는
  기존 **"시장 심리"**를 그대로 재사용(신규 카테고리 불필요 — `frontend/lib/indicator-categories.ts`도
  이미 등록돼 있어 프론트 카테고리 상수 수정 없이 자동으로 반영됨, Task 10에서 확인된 패턴과 동일).
  description에 "0~100, 시장 전체 공통값(코인과 무관), 2018-02-01 이전 구간은 데이터 없음"을 명시한다.
- **`StrategyConditionBuilder.tsx`의 `recommendedThreshold()`**: `<`/`<=` 연산자면 20(극단적 공포 기준),
  `>`/`>=`면 80(극단적 탐욕 기준) 추천.
- **가이드 탭**(`indicator-guide.ts` + `indicator-example-builder.ts`): 기존 패턴대로 공식/의미/사용법 +
  표·차트 추가. 이 지표는 대상 코인 캔들과 무관한 고정 시계열이라, 예시용 합성 데이터를
  `guide-sample-data.ts`에 `SAMPLE_FEAR_GREED`로 하나 추가한다.

### 4. 테스트 전략

기존 컨벤션(pytest 유닛/통합 테스트 + 프론트는 tsc + Playwright/수동 확인) 그대로.

- **`external_data_service.py`**: httpx 호출을 monkeypatch해서 (1) 캐시가 오늘 날짜 포함 시 API 미호출,
  (2) 캐시가 stale하면 재조회 후 덮어쓰기, (3) alternative.me JSON 파싱(문자열 `value`→float, `timestamp`→
  UTC 날짜 정규화)이 정확한지 검증.
- **`engine/indicators`**: `create_fear_greed_cmc`가 입력 시리즈를 그대로 반환하는지 수기 비교(`TRADE_VALUE`
  테스트와 동일 패턴).
- **`backend/main.py`**: (1) 분봉 백테스트에서 하루치 캔들이 모두 같은 날짜의 지수값을 공유하는지
  (forward-fill 검증), (2) 2018-02-01 이전 구간 요청 시 400 에러, (3) 카탈로그 커버리지 테스트
  (`test_get_indicator_catalog_covers_all_registered_indicators`) 확장.
- **프론트**: `npx tsc --noEmit` 클린 + Playwright로 조건 빌더 드롭다운/가이드 탭 수동 확인(이 저장소는
  프론트 유닛테스트 인프라가 없다는 기존 컨벤션 그대로).

## 이 스펙에 포함하지 않은 것

C 레이어의 나머지 항목은 각각 별도 스펙이 필요하다 — 데이터 소스·심볼 매핑·에러 케이스가 서로 다르다.

- **업비트 데이터랩 공포탐욕지수(`FEAR_GREED_UPBIT`)**: 실제 JSON API 엔드포인트가 미확인 상태(브라우저
  리버스엔지니어링 필요) + 코인별로 다른 지수라 "특정 코인만 사용 불가" 처리 로직이 별도로 필요함.
- **김치프리미엄**: 사용자 요청에서 처음 언급된 항목. 코인마다 바이낸스 USDT 페어 존재 여부가 갈려(예:
  일부 알트코인은 바이낸스에 상장 안 됨) 심볼 매핑(`KRW-XXX → XXXUSDT`)과 "이 코인은 김프 계산 불가" skip/
  notify 로직이 핵심 설계 과제 — 별도 스펙에서 다룬다.
- **시가총액(코인별 raw market cap)**: CoinGecko `/coins/{id}/market_chart`로 코인별 조회 필요, 업비트
  티커→CoinGecko coin-id 매핑이 새로 필요(backtesting_1에도 이 형태는 구현돼 있지 않음 — 거기 있는 건
  BTC/ETH/USDT 합산 기반의 "도미넌스" 근사치뿐).
- **온체인 데이터(거래소 입출금량), 선물 미결제약정/펀딩비, 구글 트렌드 검색량**: 원래 요청에 언급됐으나
  이번 세션의 우선순위 밖 — `2026-07-27-strategy-source-classification.md`에 C 레이어로 이미 분류돼 있고,
  나중에 필요할 때 각각 별도 스펙으로 착수한다.
