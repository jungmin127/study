# 매매일지 — 바 차트 제거 & 계좌 전체 요약 달력 추가 설계

## 배경

매매일지 페이지(`/journal`)에는 "계좌 전체 요약"과 "코인별 매매일지" 두 섹션이 있고,
각각 최근 30일 일별 손익 바 차트(`DailyPnlBarChart`)를 갖고 있다. 사용자는 두 바
차트를 모두 없애고, 대신 계좌 전체 요약에도 코인별 매매일지가 쓰는 달력
(`JournalCalendar`)을 넣어 계좌 전체 기준 수익률/수익금을 날짜별로 보고 싶어한다.

## 변경 범위

### 1. 바 차트 제거

- `frontend/components/JournalPage.tsx`에서 `DailyPnlBarChart` import와 두 사용처
  (계좌 전체 요약 섹션, 코인별 매매일지 섹션) 모두 삭제한다.
- 다른 곳에서 쓰이지 않으므로 `frontend/components/DailyPnlBarChart.tsx` 파일을 삭제한다.

### 2. 계좌 전체 요약에 달력 추가

**백엔드 (`backend/trading_analytics_service.py`)**

- `get_journal_summary()`가 지금은 `total_baseline`/`pnl_by_date`/`equity_curve`/
  `mdd_pct` 등을 직접 손으로 계산하는데, 이미 `get_market_journal()`이 코인 단위
  합산에 쓰는 `_market_metrics(strategies)`를 재사용하도록 바꾼다.
  - `_market_metrics`는 임의의 `strategies` 리스트를 받아 합산하므로, market으로
    필터링하지 않은 전체 승인 전략 리스트를 넘기면 계좌 전체 합산이 된다.
  - `_market_metrics`가 반환하는 `daily`(`[{trading_date, pnl, pnl_pct, cumulative}]`)의
    `cumulative`는 기존 `equity_curve`의 `value`와 수학적으로 동일하므로(둘 다
    `running = baseline`에서 시작해 날짜순으로 `pnl`을 누적), `equity_curve`는
    `[{"trading_date": d["trading_date"], "value": d["cumulative"]} for d in agg["daily"]]`로
    그대로 파생시킨다.
  - `cumulative_pnl`, `cumulative_pnl_pct`, `mdd_pct`, `win_rate_pct`,
    `daily_pnl_30d`도 `_market_metrics`가 이미 동일한 공식으로 계산하므로 그 값을
    그대로 쓴다 — 계산 로직 중복 제거.
  - `strategies`(카드 리스트)는 전략별 `_strategy_metrics()` 루프를 그대로 유지한다
    (`_market_metrics` 내부에서도 전략별로 다시 계산하지만, 데이터 규모가 작아
    성능에 영향 없음).
  - 응답에 `"daily": agg["daily"]`를 추가한다. 기존 필드(`equity_curve`,
    `daily_pnl_30d` 등)는 값 그대로 유지 — 기존 테스트가 이 값들을 검증하므로
    회귀 없이 필드만 추가한다.

**프론트엔드**

- `frontend/lib/types/journal.ts`의 `JournalSummary`에 `daily: JournalDailyCell[]` 추가.
- `frontend/components/JournalPage.tsx`의 "계좌 전체 요약" 섹션에서, 통계 카드 3개
  (누적손익/MDD/승률) 바로 아래(기존 바 차트 자리)에 `<JournalCalendar daily={summary.daily} />`를
  배치한다.

## 테스트

- 백엔드: 기존 `test_journal_summary_*` 테스트가 리팩터링 후에도 값 그대로 통과하는지
  확인한다 (동치성 검증됨 — 새 테스트는 `daily` 필드 존재 여부만 추가로 확인).
- 프론트엔드: 자동 테스트 스위트 없음(기존 관례) — 개발 서버에서 계좌 전체 요약에
  달력이 뜨고 바 차트 2개가 모두 사라졌는지 수동 확인한다.

## 범위 밖

- `equity_curve`, `daily_pnl_30d` 필드를 API 응답에서 완전히 제거하는 것은 이번
  범위에 포함하지 않는다 (더 이상 프론트가 렌더링하진 않지만, 기존 테스트/계약을
  건드리지 않기 위해 필드 자체는 유지한다).
