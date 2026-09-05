# 라이브 전략 소프트 삭제 설계

## 배경

2026-08-24 22:05, KRW-DOGE 라이브 전략(`58a49bcb-32f4-4538-89bf-84ed639bdd3b`)에 대해
"중지" → 4초 뒤 "삭제"가 연속으로 호출됐다. "중지"(`POST .../stop`)는 `UPDATE
live_strategies SET status='stopped'`만 실행해 원래 안전했지만, "삭제"
(`DELETE .../{id}`, `trading/db.py::delete_live_strategy`)는 `signals`/`orders`/
`positions`/`daily_performance`/`circuit_breaker_state`/`capital_adjustments`/
`live_strategies` 행을 전부 하드 삭제해서, 실제 거래 이력(진입가/청산가/실현손익)이
DB에서 영구히 사라졌다.

복구는 업비트 자체 주문 이력(거래소 쪽 원장)을 대조해서 2026-08-27 세션에서 수동으로
해냈지만(손실 -66,598원, 거래 10건, `positions`/`orders`/`daily_performance`에
재기록 완료), 이런 수작업 복구가 매번 가능하다는 보장이 없다. 재발 방지가 이 설계의
목적이다.

## 요구사항

"삭제" 버튼을 누르면:
1. "라이브 전략 관리" 화면(전략 카드 목록)에서는 지금처럼 사라져야 한다 — 사용자가
   더 이상 관리할 필요 없는 전략을 목록에서 치우고 싶어서 누르는 액션이므로, 이 UX는
   그대로 유지한다.
2. 그러나 `positions`/`orders` 등 실제 거래 데이터는 DB에서 사라지면 안 된다 —
   매매일지(코인별 조회, `/api/v1/journal/markets/{market}`)와 계좌 합산
   (`/api/v1/journal/summary`)에 계속 집계돼야 한다.

"중지"는 이미 요구사항을 만족하므로(상태만 바뀌고 아무것도 안 지움) 변경 대상이 아니다.

## 설계

`live_strategies`에 `deleted_at TEXT` 컬럼(nullable, 기본 NULL)을 추가한다. "삭제"
액션은 하드 `DELETE` 대신 `UPDATE live_strategies SET deleted_at = datetime('now')
WHERE id = ? AND status = 'stopped' AND deleted_at IS NULL`을 실행한다 — 기존
`delete_live_strategy`와 동일하게 `status='stopped'`인 행에만 적용되는 가드를
유지하고, `deleted_at IS NULL` 조건으로 이중 삭제를 방지한다(이미 삭제된 행에
재호출하면 매칭 0건 → False 반환, 하드 삭제 시절과 동일한 실패 시맨틱).

자식 테이블(`signals`/`orders`/`positions`/`daily_performance`/
`circuit_breaker_state`/`capital_adjustments`)은 전혀 건드리지 않는다.

### 왜 이 방식이 최소 변경으로 요구사항을 만족하는가

`get_journal_summary()`/`get_market_journal()`(`backend/trading_analytics_service.py`)은
이미 전략을 고를 때 `status`가 아니라 `approved_at IS NOT NULL`만 본다(기존 코드,
변경 없음) — `deleted_at`은 이 필터에 전혀 관여하지 않으므로, 소프트 삭제된 전략도
지금과 똑같이 매매일지에 계속 집계된다. 즉 매매일지 쪽은 **코드를 한 줄도 바꿀 필요가
없다.**

바꿔야 하는 건 "라이브 전략 관리" 목록 엔드포인트
(`GET /api/v1/live-strategies`, `backend/main.py::list_live_strategies_endpoint`)
딱 한 곳뿐이다 — 여기에만 `deleted_at IS NULL` 필터를 추가해서, 소프트 삭제된
전략이 관리 화면에서 사라지게 한다.

기존 `delete_live_strategy`(하드 삭제) 함수는 삭제하지 않고 그대로 둔다 — 그 함수를
검증하는 기존 테스트(`tests/test_trading_db.py`)를 건드릴 필요가 없고, 향후 정말
완전 삭제가 필요한 관리자용 스크립트가 생기면 재사용할 수 있다. API 엔드포인트만
새로 만드는 `soft_delete_live_strategy`를 호출하도록 바꾼다.

### 영향받는 곳 점검

- `list_live_strategies()`(필터 없는 원본 조회)를 호출하는 다른 두 곳
  (`trading_analytics_service.py`의 매매일지 집계, `scripts/backfill_entry_fee.py`)은
  모두 소프트 삭제된 행도 계속 포함되길 원하는 용도라 변경 불필요.
- 승인 시 잔고 검증(`approve_live_strategy_endpoint`)과 데몬의 활성 전략 폴링은
  `list_active_strategies()`(별도 함수, `status` 기준 필터)를 쓰므로 `deleted_at`과
  무관 — 소프트 삭제된 전략은 애초에 `status='stopped'`라 이 목록에 절대 안 잡힌다.
  같은 마켓으로 새 전략을 다시 만드는 흐름도 지금과 동일하게 막힘 없이 동작한다.

## 프론트엔드 변경

`frontend/components/LiveStrategiesPage.tsx`의 삭제 확인 다이얼로그 문구를 수정한다.

- 기존(이제 거짓이 될 문구): "이 전략의 거래·주문 내역과 매매일지 기록도 함께
  삭제되며, 되돌릴 수 없습니다"
- 변경: "이 전략을 목록에서 삭제합니다. 매매일지에 남은 거래 기록은 계속 보존되며,
  이 목록에서는 다시 볼 수 없습니다"

매매일지 화면(`JournalPage.tsx`/`JournalMarketDetail.tsx`)은 전략 `status`/
`deleted_at`을 표시하지 않으므로(현재도 `statuses` 배열만 보여줌, 소프트 삭제 여부를
구분 표시하지 않음) 변경 불필요 — 소프트 삭제된 전략도 다른 stopped 전략과 시각적으로
구분 없이 그대로 보인다(사용자 확인: 구분 표시는 요구사항이 아님).

## 스코프 밖 (YAGNI)

- 소프트 삭제된 전략을 되살리는(un-delete) UI — 요청되지 않음.
- 매매일지 화면에서 소프트 삭제된 전략을 시각적으로 구분 표시 — 요청되지 않음.
- "중지" 자체의 동작 변경 — 이미 안전해서 대상이 아님.
- 기존에 하드 삭제로 이미 사라진 다른 전략들의 재복구 — 이번 세션에서 DOGE 건은
  이미 수동 복구 완료. 그 외 과거 삭제 건은 이번 설계의 범위가 아니다.

## 테스트 계획 개요

- `trading/db.py`: `soft_delete_live_strategy` 신규 테스트 — status='stopped'에서
  성공(`deleted_at` 채워짐, 자식 행 보존 확인), status가 다르면 실패(False),
  이미 삭제된 행에 재호출하면 실패(False).
- `backend/main.py`: DELETE 엔드포인트가 소프트 삭제를 호출하는지, 관리 목록
  엔드포인트가 소프트 삭제된 행을 제외하는지, 매매일지 엔드포인트는 소프트 삭제된
  행을 계속 포함하는지 — 3가지 엔드포인트 레벨 회귀테스트.
- 기존 `delete_live_strategy`(하드 삭제) 테스트는 그대로 유지(회귀 없음 확인).

구체적인 태스크 분할과 마이그레이션(컬럼 추가 가드) 세부사항은 구현 계획(plan)
단계에서 정한다.
