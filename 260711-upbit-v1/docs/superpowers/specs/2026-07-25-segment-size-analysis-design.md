# 세그먼트(규모) 분석: 대형주/중형주/잡주 분류

## 목적
`/analysis` 페이지의 `세그먼트(규모)` 섹션에 실제 분석 결과를 채운다. 백테스트 설정에서 다루는 모든 KRW 마켓 코인을 대형주/중형주/잡주로 분류하고, 서버 기동 시 배치로 최신화한 뒤 저장·조회할 수 있게 한다.

## 배경 / 리서치
업비트 API는 시가총액(유통량×가격)을 제공하지 않는다(유통량 데이터 자체가 없음). 대신 다음 지표를 조합한다.

- **24시간 누적 거래대금(`acc_trade_price_24h`)** — 규모/유동성의 대리지표. `get_krw_markets_with_ticker()`로 이미 조회 중.
- **변동성** — 최근 30일 일봉(`days` 타임프레임) 종가 기준 일별 수익률 표준편차. 기존 `get_candles()` 캐시 인프라 재사용. 상장 30일 미만 코인은 가용 기간으로 축소.
- **업비트 공식 유의종목 플래그(`market_event.warning`/`caution`)** — `GET /v1/market/all?isDetails=true`로 조회. 분류 점수에는 반영하지 않고 카드에 배지로만 표시(스냅샷 기준 271개 중 10~15개만 플래그가 붙어 있어 전체 분류축으로 쓰기엔 신호가 약함).

## 분류 로직
1. 전체 KRW 마켓 각각에 대해 `trade_value_24h`, `volatility_30d`를 계산.
2. 두 값을 각각 percentile(0~100, 상위일수록 큰 값)로 변환 → `trade_value_percentile`(P), `volatility_percentile`(V).
3. 세그먼트 결정(우선순위 순서로 평가):
   - `P >= 70 and V <= 50` → 대형주
   - `P < 30 and V > 50` → 잡주
   - 그 외 → 중형주
4. `is_caution` = `market_event.warning == true` 또는 `caution` 중 하나라도 true.

컷 값(70/30/50)은 코드 상수로 정의하고 추후 조정 가능하게 한다.

## 저장
`engine/cache.py`가 쓰는 기존 SQLite DB에 테이블 추가:

```sql
CREATE TABLE segment_classification (
  market TEXT PRIMARY KEY,
  korean_name TEXT,
  segment TEXT,               -- 'large' | 'mid' | 'junk'
  trade_value_24h REAL,
  volatility_30d REAL,
  trade_value_percentile REAL,
  volatility_percentile REAL,
  is_caution INTEGER,         -- 0/1
  computed_at TEXT
);
```

배치 실행마다 전체 재계산 후 테이블을 통째로 교체(delete + insert)한다. 별도 히스토리 보관은 하지 않는다(항상 최신 1회분만 유지).

## 배치 트리거
- `backend/main.py`의 FastAPI startup 이벤트에서 배치 함수를 **백그라운드 스레드**로 실행한다.
- 이유: 캔들 조회는 코인마다 개별 요청이 필요하고, 기존 rate-limit 딜레이(요청당 0.15초) 때문에 271개 마켓 기준 순차 실행 시 약 1~2분 소요된다. 서버 기동을 이 시간만큼 막지 않기 위해 백그라운드로 돌린다.
- 배치가 끝나기 전 조회 API가 호출되면, 테이블이 비어 있거나(최초 기동) 이전 배치 결과(재기동 시)를 그대로 반환한다. 최초 기동 직후 잠깐 빈 목록이 나올 수 있음을 프론트엔드 문구로 안내한다.

## API
- `GET /api/v1/analysis/segments/size`
  - 응답: 세그먼트별 코인 배열. 각 항목에 `market`, `korean_name`, `segment`, `trade_value_24h`, `volatility_30d`, `is_caution`, `computed_at` 포함.

## 프론트엔드
`app/analysis/page.tsx`의 `세그먼트(규모)` 카드를 서버 컴포넌트에서 API를 호출해 실데이터로 렌더링:
- 대형주/중형주/잡주 3개 그룹, 그룹별 코인 수를 제목에 표시(예: "대형주 (12)").
- 각 코인 행: 한글명, 거래대금(억 단위), 변동성(%), 유의종목이면 "⚠ 유의종목" 배지.
- 배치가 아직 안 돌아 데이터가 없으면 "배치 실행 중입니다. 잠시 후 새로고침해 주세요." 같은 안내 문구.

## 비범위
- 세그먼트(섹터) 분석은 포함하지 않는다(별도 작업).
- 컷 값 자동 튜닝/백테스트 검증은 하지 않는다.
- 배치 이력/추세 추적(과거 분류 변화 기록)은 하지 않는다.

## 검증
- 백엔드 기동 후 1~2분 내 `segment_classification` 테이블에 271개 행이 채워지는지 확인.
- `GET /api/v1/analysis/segments/size` 응답에서 세 세그먼트 합이 전체 마켓 수와 일치하는지 확인.
- `/analysis` 페이지에서 세 그룹 테이블이 렌더링되고, 유의종목 배지가 실제 유의 코인에만 붙는지 브라우저로 확인.
