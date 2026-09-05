# 백테스트 결과 — "복사" 컬럼(설정 프리셋 이동) 설계

- 작성일: 2026-07-26
- 상태: 승인 대기 (사용자 리뷰 전)

## 목적

`/backtests`(백테스트 결과) 목록의 "상세" 옆에 "복사" 컬럼을 추가한다. 클릭하면 `/`(백테스트 설정)로 이동하면서 해당 실행의 코인/봉타입/기간/매수·매도 조건이 폼에 동일한 값으로 미리 채워진다(자동 실행은 아님 — 프리셋만 채우고 사용자가 확인 후 실행).

## 결정된 사항 (사용자 승인)

- 프리셋 대상: 코인(`market`), 봉타입(`timeframe`), 기간(`start`/`end`, 날짜+시간), 매수·매도 조건(`buy_conditions`/`sell_conditions`).
- 제목/설명/운용자금은 프리셋에서 제외 — 기존 기본값(빈 제목, 기본 자금 1,000,000원) 그대로 유지.
- 백엔드 변경 없음 — `/backtests` 페이지가 이미 서버에서 받아둔 `BacktestRunSummary`(코인/봉타입/기간/조건 전부 포함)를 그대로 URL 쿼리스트링에 실어 `/`로 넘긴다. 별도 API 호출이나 신규 엔드포인트는 만들지 않는다.

## 설계

### 1. `BacktestRunsTable.tsx` — "복사" 컬럼

"상세" 컬럼 바로 다음에 "복사" `TableHead`/`TableCell`을 추가한다. 각 행에서 `URLSearchParams`로 아래 값을 담아 `/?...`로 향하는 링크를 만든다:

- `market`: `run.market` 그대로
- `timeframe`: `run.timeframe` 그대로
- `start`: `run.start.slice(0, 10)` (날짜), `startTime`: `run.start.slice(11, 16)` (시:분)
- `end`: `run.end.slice(0, 10)`, `endTime`: `run.end.slice(11, 16)`
- `buy`: `JSON.stringify(run.buy_conditions)`
- `sell`: `JSON.stringify(run.sell_conditions)`

"보기" 링크와 동일한 패턴(`Button variant="link" render={<Link/>} nativeButton={false} role="link"`)을 쓰고, 아이콘은 `Copy`(lucide-react).

### 2. `app/page.tsx` — `Suspense` 래핑

`PortSetupForm`이 `useSearchParams()`를 쓰게 되므로(Next.js App Router 요구사항), `<PortSetupForm />`을 `<Suspense>`로 감싼다.

### 3. `PortSetupForm.tsx` — 프리셋 초기값

`next/navigation`의 `useSearchParams()`로 위 6개 파라미터를 읽어, `market`/`timeframe`/`startDate`/`startTime`/`endDate`/`endTime`/`buyConditions`/`sellConditions`의 `useState` 초기값을 프리셋이 있으면 그 값으로, 없으면(직접 `/` 접속 시) 기존 기본값(빈 코인, 15분봉, 최근 90일, 빈 조건)으로 채운다. `buy`/`sell`은 `JSON.parse` 실패 시(잘못된/조작된 URL) 조용히 빈 조건 그룹으로 폴백한다 — 에러를 사용자에게 보여주지 않는다(이 정도 방어면 충분, URL을 직접 조작하는 비정상 케이스임).

기존에 `market`이 비어 있을 때만 첫 번째 코인을 자동 선택하는 로직(`setMarket((prev) => prev || sorted[0].market)`)은 그대로 둔다 — 프리셋으로 이미 `market`이 채워져 있으면 자동 선택이 개입하지 않는다(이미 성립하는 동작, 코드 변경 불필요).

## 범위 밖

- 제목/설명/운용자금 프리셋 — 위 결정 사항대로 제외.
- 클릭 시 자동 실행(바로 백테스트 돌리기) — 프리셋만 채우고 실행은 사용자가 직접.
- `/backtests` 외의 다른 화면(예: `/heatmap`, `/ranking`)에 동일 기능 확장 — 이번 범위 아님.

## Self-Review 결과

- **스펙 커버리지**: 사용자가 승인한 프리셋 대상(코인/봉타입/기간/매수매도)과 제외 대상(제목/설명/자금)이 1번/결정된 사항에 반영됨.
- **내부 정합성**: 기존 `market` 자동 선택 로직과 프리셋의 상호작용을 명시적으로 확인(코드 변경 없이 이미 올바르게 동작).
- **대상 파일 목록**: `frontend/components/BacktestRunsTable.tsx`, `frontend/app/page.tsx`, `frontend/components/PortSetupForm.tsx`.
