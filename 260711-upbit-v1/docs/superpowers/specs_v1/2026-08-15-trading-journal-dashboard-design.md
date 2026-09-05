# 매매일지 대시보드(3단계 분석 대시보드) 설계 (2026-08-15)

이 문서는 `2026-08-04-live-trading-foundation-design.md`의 로드맵 표가 "3. 분석 대시보드"로
정의한 항목("백테스트 vs 실매매 대조, 슬리피지 추적, Daily/누적/MDD/승률 — 매매일지 화면")의
상세 스펙이다.

## 배경

`2026-08-09-live-trading-roadmap-sequencing.md`가 확정한 순서는 1단계 완결 → 4단계(상시
서버 배포) → 2/3단계(텔레그램/대시보드) 순이었다. 1단계는 완결됐고(소액 실전 테스트까지
완주), 4단계도 배포 스크립트(`deploy/setup.sh`, `deploy/update.sh`)까지는 완료됐지만 실제
클라우드 서버 가동은 아직([[upbit-v1-server-deployment-shipped]], AWS 전환 결정 진행 중).
사용자가 이번 세션에서 순서를 재조정해 **분석 대시보드(3단계)를 먼저** 진행하고, 상시 서버
배포 마무리와 텔레그램(2단계)은 그 이후로 미루기로 했다.

데이터 기반은 이미 갖춰져 있다: `positions`(진입/청산/realized_pnl/close_reason),
`orders`(slippage_pct, expected_price), `daily_performance`(`risk_manager.py`가 포지션
청산마다 upsert하는 일별 손익/승패/MDD 집계), `live_strategies.source_run_id`(백테스트
결과와의 조인 키)가 1단계 구현 때부터 쌓이고 있다. 이 스펙은 새 저장 구조를 만들지 않고
기존 데이터를 조회 시점에 집계해서 보여주는 화면/API만 정의한다(원본 스펙의 "중복 저장 안
함" 원칙 유지).

현재 실거래 이력은 소액 테스트 1건(KRW-DOGE, +1.17%, 1사이클 완주 후 중지)뿐이라, 데이터가
거의 없는 상태에서도 화면이 자연스럽게 동작해야 한다.

## 이 스펙에서 다루지 않는 것

- 텔레그램 알림/제어(2단계) — 별도 스펙
- 상시 서버 배포 마무리(4단계 나머지) — `2026-08-14-live-trading-server-deployment.md` 참고
- 매매 제어(승인/일시정지/중지) — 기존 `/live-strategies` 페이지의 역할, 이 스펙은 읽기
  전용 분석 화면만 다룬다
- 서킷브레이커 이력, manual_intervention_events 표시 — 원본 3단계 정의 범위 밖

## 페이지 구조 & 네비게이션

`NavTabs`에 새 상단 탭 **"매매일지"**를 추가한다(라우트 `/journal`). 기존
`/live-strategies`(제어 전용)와 역할을 분리한다.

화면 구성(위→아래, 단일 페이지):

1. **계좌 전체 요약** — 누적손익(원화)/MDD/승률 카드 + 일별 누적손익 라인차트(모든 라이브
   전략 합산)
2. **전략별 카드 목록** — 코인·타임프레임별 누적손익/거래횟수 요약 카드.
   `approved_at IS NOT NULL`인 전략만 표시(한 번도 승인 안 된 `draft` 전략은 데이터가 없으므로
   제외)
3. **전략 드릴다운** — 카드를 클릭하면 펼쳐짐:
   - 지표 카드: 누적손익/MDD/승률 + 평균·최대 슬리피지
   - 백테스트 vs 실매매 지표 대조표(승률/평균수익률/MDD/거래횟수를 나란히, %p 차이 표시).
     `source_run_id`가 없으면 "백테스트 비교 불가" 안내로 대체
   - 매매일지 테이블: 청산된(`status='closed'`) 포지션만, 진입/청산 시간·가격·수량, 손익,
     청산사유를 최신순으로

새로고침은 폴링 없이 **수동 새로고침 버튼 + 페이지 진입 시 1회 로드**로 한다(분석 화면이라
실시간성보다 서버 부하가 우선).

## 백엔드 설계

### 새 모듈: `backend/trading_analytics_service.py`

`backend/main.py`가 이미 1271줄로 커서(기존 관례상 `grid_search_service.py`처럼 분리)
집계 로직을 이 모듈로 분리하고, `main.py`에는 얇은 엔드포인트만 둔다.

MDD 계산은 `engine/metrics.py`의 `calculate_metrics()`를 재사용하지 않는다 — 그 함수는
백테스트용으로 CAGR/샤프/소르티노/칼마 등 실매매 초기(거래 1~2건, 며칠치 데이터)엔 안 맞는
지표까지 계산한다. 대신 이 모듈에 계좌/전략 자산 시계열에서 MDD만 뽑는 순수함수를 둔다:

```python
def _mdd(values: pd.Series) -> float:
    cummax = values.cummax()
    drawdown = (values - cummax) / cummax * 100.0
    return float(drawdown.min()) if not drawdown.empty else 0.0
```

### 엔드포인트

**`GET /api/v1/journal/summary`** — 계좌 전체 요약
- `daily_performance`를 `trading_date`로 GROUP BY, 승인 이력 있는 모든 전략의
  `ending_balance` 합산 → 일별 계좌 총자산 시계열 → 누적손익 라인차트 데이터 + 위 `_mdd()`로
  MDD 계산
- 승률 = `SUM(win_count) / SUM(win_count + loss_count)` 전체 합산
- 전략 카드 목록(코인/타임프레임/누적손익/거래횟수) 포함

**`GET /api/v1/journal/strategies/{id}`** — 전략 드릴다운
- 지표 카드: `positions`에서 승률/MDD/누적손익, `orders`에서 평균·최대 `slippage_pct`
- BT vs 실매매 대조표: `source_run_id`가 있으면 `backtest_results`에서 동일 지표를 조회해
  같이 반환(없으면 `null`)
- 거래횟수 < 10이면 응답에 표본 부족 경고 플래그 포함
- 매매일지: `positions`를 `status='closed'`, `entry_time` 역순으로

## 프론트엔드 설계

- `frontend/app/journal/page.tsx` — 얇은 wrapper
- `frontend/components/JournalPage.tsx` — 계좌 요약(카드 3개 + Recharts `LineChart`, 기존
  `ComboHistoryChart.tsx`와 동일한 recharts 사용 패턴) + 전략 카드 목록 + 선택된 전략 id 상태
- `frontend/components/JournalStrategyDetail.tsx` — 드릴다운: 지표 카드 + BT 대조표 + 매매일지
  테이블
- `frontend/lib/api/journal.ts`, `frontend/lib/types/journal.ts` — 기존
  `liveStrategies.ts`/`liveStrategies.ts` 타입 패턴 그대로

## 엣지 케이스

- 승인 이력 있는 전략이 하나도 없음 → "아직 실거래 이력이 없습니다" 안내, 카드/차트 숨김
- 청산된 포지션 0건(열린 포지션만 있음) → 매매일지 빈 상태 문구, 지표는 0/N/A
- `daily_performance` 1일치만 있음 → 라인차트 점 1개, MDD는 0(정상 동작)
- `source_run_id` 없는 전략 → BT 대조표 대신 "백테스트 비교 불가" 안내
- 거래횟수 < 10 → 표본 부족 경고 표시
- ~~알려진 한계: stopped된 전략의 자금이 계좌 합산에서 사라짐~~ — **구현 플랜(2026-08-15)
  단계에서 해소**. 잔고(ending_balance)를 날짜별로 합산하는 대신 일별 손익(realized_pnl)
  flow를 누적하는 방식으로 바꿔, 전략이 stopped된 뒤에도 과거 손익이 계좌 합산에 그대로
  남는다. 상세 설계는 `docs/superpowers/plans_v1/2026-08-15-trading-journal-dashboard.md`의
  "설계 노트" 참고.

## 테스트 계획

- 백엔드: `tests/test_trading_analytics_service.py` — `_mdd()` 단위테스트, summary/drilldown
  엔드포인트를 fixture DB(빈 상태/전략 1개/청산 0건/표본 부족 등)로 검증. 기존
  `test_risk_manager.py` 등과 동일한 트레이딩 DB fixture 패턴 재사용
- 프론트엔드: 이 프로젝트엔 프론트 자동 테스트가 없음(기존 관례와 동일) — dev 서버로 브라우저
  직접 확인(빈 상태, 소액테스트 실데이터 상태 둘 다)

## 다음 세션 시작 시 바로 할 일

이 스펙을 사용자가 리뷰한 뒤, writing-plans 스킬로 구현 플랜을 작성한다.
