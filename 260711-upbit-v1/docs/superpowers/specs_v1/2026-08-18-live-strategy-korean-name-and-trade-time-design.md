# 라이브 전략 카드 — 코인명 한글 표기 & 직전 매수/매도 시각 설계

## 배경

라이브 전략 관리 페이지(`/live-strategies`)의 카드는 코인을 `KRW-DOGE` 같은 티커로만 표기하고,
직전 매수/매도가 언제 일어났는지 알 수 있는 정보가 없다. 사용자가 한눈에 코인을 알아보고
거래 활성도를 확인할 수 있도록 두 가지를 추가한다.

## 요구사항

1. 코인명을 한글로 표기한다 (티커 대신 한글명만). 한글명을 못 찾으면 티커로 폴백한다.
2. 카드 통계 영역 아래에 직전 매수/매도 일자·시간을 작은 글씨로 표기한다.

## 변경 범위

### 1. 코인명 한글 표기

- `LiveStrategiesPage.tsx` 마운트 시 `getMarkets()`를 1회 호출해 `market → korean_name` 맵을 만든다.
  (5초 폴링과는 별개 — 실시간 시세가 필요한 게 아니라 이름만 필요하므로 한 번만 불러온다.)
- 다음 세 위치에서 `s.market` 대신 한글명(폴백: `s.market`)을 표기한다:
  - 카드 헤더: `{koreanName} · {formatTimeframe(s.timeframe)}`
  - "전략 설정 보기" 다이얼로그 제목
  - "전략 교체" 다이얼로그 제목
- 마켓 목록을 못 불러온 경우(API 에러) 전부 티커로 폴백하며, 별도 에러 UI는 없다(기존 카드 렌더링을 막지 않음).

### 2. 직전 매수/매도 일자·시간

**백엔드 (`backend/main.py`, `_live_strategy_response`)**

- `trading_db.list_closed_positions(strategy_id)`(이미 `entry_time DESC` 정렬)를 사용해 두 필드를 계산한다:
  - `last_buy_at`: 열린 포지션이 있으면 그 `entry_time`; 없으면 가장 최근 closed position의 `entry_time`; 둘 다 없으면 `null`.
  - `last_sell_at`: 가장 최근 closed position의 `exit_time`; 없으면 `null`.
  - 포지션은 동시에 하나만 열려 있으므로 `entry_time DESC` 정렬 첫 행이 곧 가장 최근 거래(진입/청산 모두)다.
- 응답에 `last_buy_at`, `last_sell_at`(ISO 문자열 또는 `null`)을 추가한다. 기존 `_to_utc_iso` 변환 패턴을 따른다(capital_adjustments의 `adjusted_at`과 동일하게 UTC ISO로 통일).
- `list_live_strategies_endpoint`와 `_full_live_strategy_response` 양쪽 모두 `_live_strategy_response`를 거치므로 자동으로 반영된다.

**프론트엔드**

- `frontend/lib/types/liveStrategies.ts`의 `LiveStrategy`에 `last_buy_at: string | null`, `last_sell_at: string | null` 추가.
- `frontend/lib/format.ts`에 카드용 축약 포맷 함수를 추가한다: `formatDateTimeShort(iso: string): "MM-DD HH:MM"` (기존 `formatDateTime`과 같은 KST 변환 로직 재사용, 초 단위·연도 생략).
- `LiveStrategiesPage.tsx`의 통계 영역(`<div className="flex px-4">...</div>`) 바로 아래에 한 줄 추가:
  - `last_buy_at`, `last_sell_at` 둘 다 `null`이면 렌더링하지 않는다.
  - 하나만 있으면 있는 쪽만 표시한다 (예: `매수 08-17 14:23`).
  - 둘 다 있으면 `매수 08-17 14:23 · 매도 08-16 09:10` 형태로 표시한다.
  - 스타일: `text-xs text-muted-foreground`, 카드 좌우 패딩(`px-4`)에 맞춘다.

## 테스트

- 백엔드: `_live_strategy_response`가 (a) 열린 포지션 있음 (b) closed position 있음/없음 (c) 아무 거래 없음 세 케이스에서 `last_buy_at`/`last_sell_at`을 올바르게 계산하는지 단위 테스트로 검증한다.
- 프론트엔드: 수동으로 개발 서버에서 라이브 전략 카드가 한글명과 직전 매수/매도 시각을 올바르게 표기하는지 확인한다 (이 프로젝트에 프론트 자동 테스트 스위트 없음, 기존 관례).

## 범위 밖

- 다이얼로그가 아닌 다른 화면(매매일지 등)의 마켓 표기는 이번 변경에 포함하지 않는다.
- 여러 세대(타임프레임 변경/재시작)에 걸친 "코인 단위 합산" 거래 이력(저널 페이지의 로직)은 건드리지 않는다 — 이번 필드는 이 `live_strategy_id` 하나에 한정된다.
