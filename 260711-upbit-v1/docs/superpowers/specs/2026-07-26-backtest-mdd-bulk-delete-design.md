# 백테스트 결과 — MDD 컬럼 + 체크박스 일괄삭제 설계

- 작성일: 2026-07-26
- 상태: 승인 대기 (사용자 리뷰 전)

## 목적

`/backtests`(백테스트 결과) 목록에 두 가지를 추가한다:
1. "수익률(%)" 옆에 "MDD(%)" 컬럼 추가.
2. 각 행 맨 앞에 체크박스, 테이블 최상단에 "선택 삭제" 버튼을 추가해 여러 건을 한 번에 삭제. 개별 "삭제" 컬럼(`DeleteRunButton`)은 제거.

## 결정된 사항 (사용자 승인)

- `max_drawdown`은 이미 `BacktestRunSummary` 타입과 API 응답에 존재한다(백엔드/타입 변경 불필요) — 프론트 컬럼만 추가.
- MDD 표시는 `heatmap`/`ranking` 페이지가 이미 쓰는 컨벤션(부호 반전·색상 없이 `toFixed(2)`, 우측 정렬)을 그대로 따른다.
- 개별 "삭제" 컬럼과 `DeleteRunButton.tsx`는 완전히 제거하고 일괄삭제로 대체한다.
- 일괄삭제는 새 백엔드 엔드포인트 없이, 기존 `DELETE /api/v1/backtests/{run_id}`(→ `deleteBacktestRun()`)를 선택된 항목 수만큼 병렬 호출한다.

## 설계

### 1. shadcn `Checkbox` 프리미티브 추가

`frontend/components/ui/checkbox.tsx`가 아직 없음 — `npx shadcn@latest add checkbox`로 추가한다. 이 프로젝트의 다른 shadcn 컴포넌트(`base-ui` 기반)와 마찬가지로 실제 생성된 파일의 정확한 export/props(`checked`/`onCheckedChange`가 표준 이름이지만 확인 필요)는 CLI 실행 후 확인한다.

### 2. `BacktestRunsTable.tsx` — MDD 컬럼

"수익률(%)" `TableHead`/`TableCell` 바로 다음에 "MDD(%)" 추가:
```tsx
<TableCell className="text-right tabular-nums">{run.max_drawdown?.toFixed(2) ?? '-'}</TableCell>
```

### 3. `BacktestRunsTable.tsx` — 체크박스 + 일괄삭제

- 맨 앞 컬럼에 `Checkbox` 추가(헤더: 전체 선택/해제, 각 행: 개별 선택). 선택 상태는 `Set<string>`(run_id)으로 로컬 관리.
- 테이블 바로 위에 툴바: "N개 선택됨" 텍스트 + "선택 삭제" 버튼(선택 0개면 비활성화). 버튼은 기존 `DeleteRunButton.tsx`가 쓰던 패턴(`AlertDialogTrigger`에 `buttonVariants` className 직접 적용, 이 프로젝트의 base-ui `Button`은 `AlertDialogTrigger` 안에서 `render`로 합성하면 문제가 있었던 전례가 있어 이 방식을 그대로 재사용)을 따른다.
- 확인 다이얼로그(`AlertDialog`) → 확인 시 선택된 모든 `run_id`에 대해 `deleteBacktestRun()`을 `Promise.allSettled`로 병렬 호출(일부 실패해도 나머지는 계속 처리) → 실패 건수가 있으면 다이얼로그 안에 인라인 에러 표시 → 성공/실패와 무관하게 선택 초기화 후 `router.refresh()`.
- 다이얼로그를 닫았다 다시 열 때 이전 에러가 남아있지 않도록 `onOpenChange`에서 에러 상태를 초기화한다(이전 작업에서 발견된 "다이얼로그 재오픈 시 stale error" 버그를 처음부터 피한다).
- 개별 "삭제" 컬럼과 `frontend/components/DeleteRunButton.tsx` 삭제.

## 범위 밖

- 삭제 대상 필터링(제목 패턴 등으로 자동 선택) — 사용자가 직접 체크박스로 고른다.
- MDD 컬럼 정렬 기능 — 이번 범위 아님(수익률처럼 정렬 가능하게 하려면 별도 요청 필요).
- 새 백엔드 엔드포인트(진짜 배치 DELETE API) — 기존 단건 삭제 API를 병렬 호출하는 것으로 충분.

## Self-Review 결과

- **스펙 커버리지**: MDD 컬럼 추가, 체크박스+일괄삭제, 개별 삭제 컬럼 제거가 각각 2/3번 섹션에 반영됨.
- **내부 정합성**: `Checkbox`가 아직 없다는 사실과 그로 인한 1번(CLI 추가) 선행 작업의 필요성이 일관되게 명시됨.
- **범위 확인**: 새 백엔드 엔드포인트 없음, MDD 정렬 없음을 명시.
- **대상 파일 목록**: `frontend/components/ui/checkbox.tsx`(신규), `frontend/components/BacktestRunsTable.tsx`, `frontend/components/DeleteRunButton.tsx`(삭제).
