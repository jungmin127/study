# 세그먼트(추세 기반) 분석 — 1단계: 구간 분류 + UI

## 목적
`/analysis` 페이지 `세그먼트` 탭에 `규모` / `섹터`와 나란히 **`추세 기반`** 섹션을 추가한다. 코인을 선택하면 상장일부터 현재까지 전체 일봉을 상승/하락/횡보 구간으로 자동 분류하고, 각 구간을 전반부/후반부로 다시 나눠 9가지 패턴 라벨을 붙여 차트+표로 보여준다.

이 결과는 다음 단계(코인별·추세패턴별 백테스트 매트릭스 자동 실행/적재)의 입력 자료로 쓰일 예정이다. **2단계는 이번 스펙 범위에 포함하지 않는다** — 구간 분류가 먼저 신뢰할 수 있게 나와야 그 위에 백테스트 매트릭스를 얹을 수 있어서, 별도 세션에서 다시 브레인스토밍부터 시작한다.

## 배경 / 문제의식
- 현재 백테스트 전략은 추세 국면을 구분하지 않고 전 구간에 동일 조건을 적용한다. 하락장에서 "떨어지면 매수, 떨어지면 매도"가 반복될 위험이 있고, 상승장에서는 초반에 조금 오르면 바로 매도해 큰 상승분을 놓치는 문제가 있다.
- 다만 "지금이 상승 추세인지"는 지나봐야 안다 — 그래서 장기 상승장에서 통하는 전략과 단기 상승장에서 통하는 전략을 구분해야 한다. 이 구분을 가능하게 하려면, 각 상승/하락 구간이 "지속됐는지" 아니면 "초반에 반전/둔화됐는지"를 사후에 라벨링할 수 있어야 한다 → 구간을 전반부/후반부로 나눠 재분류하는 9패턴 설계로 이어짐.

## 분류 로직 (`engine/trend_segments.py` 신설)

1. **적응형 임계값**: 코인의 최근 30일 일간수익률 표준편차(기존 `segment_analysis._compute_volatility()`와 동일 계산)를 기준으로 `threshold_pct = clamp(volatility_30d_std * 100 * THRESHOLD_MULTIPLIER, MIN_THRESHOLD_PCT, MAX_THRESHOLD_PCT)`를 산출한다.
   - 기본 상수: `THRESHOLD_MULTIPLIER = 6`, `MIN_THRESHOLD_PCT = 5.0`, `MAX_THRESHOLD_PCT = 25.0`. 코드 상수로 정의하고 추후 튜닝 가능하게 한다(규모 세그먼트의 percentile 컷값과 동일한 방침).
   - 30일 변동성 계산이 불가능한(상장 30일 미만) 코인은 가용 기간으로 축소해 계산한다(`_compute_volatility()`의 기존 동작 재사용).
2. **ZigZag 스윙 탐지**: 종가 기준으로 순회하며, 현재 스윙 방향과 반대로 `threshold_pct` 이상 되돌림이 발생할 때마다 직전 극값을 스윙 고점/저점으로 확정한다. 확정된 스윙 사이 구간이 상승/하락 레그가 된다.
3. **횡보 병합**: 등락폭이 `threshold_pct` 미만인 연속된 레그들을 하나의 "횡보" 구간으로 병합한다(연속 저변동 구간 병합 방식).
4. **최소기간 필터**: 병합 후에도 14일(`MIN_SEGMENT_DAYS = 14`) 미만인 구간이 남으면 다음 구간에 흡수한다(마지막 구간이면 이전 구간에 흡수). 흡수로 합쳐진 구간은 시작가→종료가 등락률을 기준으로 상승/하락/횡보를 처음부터 다시 판정한다(등락률 절댓값이 `threshold_pct` 이상이면 방향에 따라 상승/하락, 미만이면 횡보).
5. **전반/후반 재분류**: 최종 확정된 각 구간을 기간 중간 날짜로 절반씩 나눈다. 각 절반의 시작가→종료가 등락률을 `threshold_pct / 2`와 비교해 절반별로 상승/하락/횡보를 판정하고, 아래 9패턴 테이블로 `pattern_label`을 매긴다.

   | 전반 \ 후반 | 상승 | 하락 | 횡보 |
   |---|---|---|---|
   | **상승** | 지속형 상승 | 상승 후 반전 | 상승 후 둔화 |
   | **하락** | 하락 후 반등 | 지속형 하락 | 하락 후 멈춤 |
   | **횡보** | 횡보 이탈(상승) | 횡보 이탈(하락) | 지속형 횡보 |

## 저장 (`engine/cache.py`에 테이블 추가)

```sql
CREATE TABLE IF NOT EXISTS trend_segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  market TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  days INTEGER NOT NULL,
  return_pct REAL NOT NULL,
  trend TEXT NOT NULL,               -- 'up' | 'down' | 'sideways'
  first_half_trend TEXT NOT NULL,    -- 'up' | 'down' | 'sideways'
  second_half_trend TEXT NOT NULL,   -- 'up' | 'down' | 'sideways'
  pattern_label TEXT NOT NULL,       -- 9라벨 중 하나 (한글)
  threshold_pct REAL NOT NULL,       -- 이 코인에 적용된 임계값 (구간마다 동일값 중복 저장)
  computed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trend_segments_market ON trend_segments(market);
```

- market 단위 replace: 재계산 시 해당 `market`의 기존 행을 delete 후 insert. 규모 세그먼트와 동일하게 히스토리 보관 없이 최신 1회분만 유지한다.

## 계산 트리거 — 코인 단위 온디맨드

규모 세그먼트 배치(서버 기동마다 전체 271개 마켓 재계산)와 달리, 이 기능은 상장일부터 전체 일봉을 코인마다 새로 계산해야 해서 훨씬 무겁다. 목적도 "여러 코인을 훑어보는" 것이 아니라 "관심 코인 몇 개를 깊게 보고 전략을 결정해 라이브로 보내는" 로컬 분석 워크플로우이므로, 전체 코인 일괄 배치 대신 **선택한 코인만 온디맨드로 계산**한다.

- 서버 기동 시 자동 배치 없음.
- 사용자가 코인을 선택하면 캐시를 조회하고, 없으면 그 자리에서 계산 후 저장한다.
- "갱신" 버튼으로 캐시를 무시하고 강제 재계산할 수 있다(기존 백테스트 상세의 "최신 데이터로 갱신" 패턴과 동일).

## API

- `GET /api/v1/analysis/trend-segments/{market}` — 캐시에 있으면 그대로 반환, 없으면 계산 후 저장·반환.
  - 응답: `{ market, threshold_pct, computed_at, segments: [...], ohlcv: [...] }`
  - `segments[]`: `start_date`, `end_date`, `days`, `return_pct`, `trend`, `first_half_trend`, `second_half_trend`, `pattern_label`
  - `ohlcv[]`: 차트 렌더링에 쓰이는 일봉 배열(같은 응답에 포함해 프론트에서 별도 캔들 조회 왕복을 만들지 않는다).
- `POST /api/v1/analysis/trend-segments/{market}/refresh` — 캐시를 무시하고 강제 재계산.

## 프론트엔드

- `AnalysisSidebarView.tsx`의 `Section` 타입에 `'trend'`를 추가하고 사이드바에 `추세 기반` 항목을 추가한다(`규모` / `섹터`와 같은 레벨).
- 신규 컴포넌트 `TrendSegmentView.tsx`:
  - 코인 검색/선택 드롭다운(기존 마켓 목록 재사용).
  - 코인 선택 시 `GET /api/v1/analysis/trend-segments/{market}` 호출.
  - `PriceChart.tsx`와 같은 lightweight-charts 패턴으로 일봉 캔들을 그리고, 구간별 배경색(상승=초록, 하락=빨강, 횡보=회색)을 오버레이한다.
  - 차트 하단에 구간 표: 기간(시작~종료), 일수, 등락률, 9패턴 라벨.
  - "갱신" 버튼으로 `POST .../refresh` 호출.
  - 최초 선택 시 로딩 상태 표시(온디맨드 계산이라 상장 기간이 긴 코인은 수 초 걸릴 수 있음을 안내).

## 비범위
- 구간별 백테스트 자동 실행 및 결과 매트릭스 적재는 2단계로 별도 브레인스토밍한다.
- 임계값 배수·clamp 범위·최소기간(14일) 등 파라미터의 자동 튜닝/백테스트 검증은 하지 않는다.
- 전체 코인 일괄 배치는 하지 않는다(온디맨드만).
- 세그먼트(섹터) 분석과는 무관하다(그쪽은 여전히 별도 작업으로 준비 중).

## 검증
- 여러 코인(변동성 낮은 대형주 / 변동성 높은 잡주)으로 `GET /api/v1/analysis/trend-segments/{market}`을 호출해 구간이 상식적으로 나오는지 확인한다 — 뚜렷한 상승장이 하나의 "지속형 상승" 구간으로 잡히는지, 잦은 등락이 "지속형 횡보"로 병합되는지.
- `/analysis` → `세그먼트` → `추세 기반`에서 코인 선택 → 차트/표 렌더링 → "갱신" 버튼 동작을 브라우저로 확인한다.
- `threshold_pct`가 코인마다 다르게(변동성에 비례해) 산출되는지 확인한다.
