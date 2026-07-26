# UI 개선 — TypeScript/Tailwind v4/shadcn/lucide-react 기반 전면 정비 설계

- 작성일: 2026-07-26
- 상태: 승인 대기 (사용자 리뷰 전)
- 범위: 프론트엔드 전체 페이지 (`/`, `/backtests`, `/backtests/[runId]`, `/analysis`, `/heatmap`, `/history`, `/ranking`, `/model-accuracy`)

## 배경 및 목적

시각적 일관성 확보, 아이콘 도입을 통한 가독성 향상, 레이아웃/정보 밀도 개선, 다크모드 마무리를 목표로 UI 전반을 정비한다. TypeScript는 이미 전면 적용되어 있으나, shadcn/ui는 `components.json`에 설정만 있고 실제로는 `button`/`badge`/`card`/`table` 4개만 존재하며, `lucide-react`는 `package.json`에 설치만 되어 있고 코드 어디에서도 import되지 않는다.

조사 결과 확인된 중요한 사실: 기존 `components/ui/button.tsx`, `badge.tsx`는 이미 **Tailwind v4 전용 문법**(`ring-3`, `in-data-[slot=...]`, `not-aria-[...]`, `[&_svg]:size-3!` 등 v4에서만 유효한 변형자)으로 shadcn CLI가 스캐폴딩한 상태다. 그런데 실제 설치된 Tailwind는 v3.4.19다. 즉 이 프로젝트는 이미 버전 불일치 상태이며, Tailwind v4 마이그레이션은 선호가 아니라 **기존 컴포넌트의 정합성을 맞추는 선행 작업**이다.

## 결정된 사항 (사용자 승인)

- 범위는 전체 페이지. 우선순위를 나눠 단계적으로 진행한다.
- 개선 목적 4가지(시각적 일관성/아이콘/레이아웃 밀도/다크모드) 모두 이번 계획에 포함한다.
- 상단 탭 내비게이션(`NavTabs`)은 shadcn `Tabs`로 교체하지 않는다 — 각 탭이 실제 URL(`/`, `/backtests`, `/analysis`)이므로 현재 `Link` 기반 구조를 유지하고 아이콘만 추가한다. (`Tabs`는 페이지 이동이 아닌 한 페이지 내 콘텐츠 전환용이라 URL 라우팅과 맞지 않음)
- Tailwind v4 마이그레이션은 빌드 파이프라인 단위 작업이라 페이지별로 쪼갤 수 없다 — Phase 0에서 한 번에 수행하고 전 페이지 시각적 회귀 여부를 확인한다.
- 새로운 외부 의존성(toast 라이브러리 등)은 추가하지 않는다 — 사용자가 명시한 스택(TypeScript/Tailwind v4/shadcn/lucide-react) 범위 내에서 기존 shadcn 프리미티브(`AlertDialog`, 인라인 에러 텍스트)로 해결한다.

## Phase 0 — Tailwind v4 마이그레이션 (인프라)

대상 파일: `frontend/tailwind.config.ts`(삭제), `frontend/postcss.config.mjs`, `frontend/app/globals.css`, `frontend/package.json`

1. `tailwindcss`를 v4로, `postcss.config.mjs`의 플러그인을 `@tailwindcss/postcss`로 교체
2. `globals.css` 최상단을 `@tailwind base/components/utilities` 3줄에서 `@import "tailwindcss";`로 교체
3. `tailwind.config.ts`의 `theme.extend.colors`/`borderRadius`를 `globals.css` 내 `@theme` 블록으로 이전:
   ```css
   @theme {
     --color-background: var(--background);
     --color-foreground: var(--foreground);
     /* ... 기존 colors 매핑 전부 동일하게 이전 ... */
     --radius-lg: var(--radius);
     --radius-md: calc(var(--radius) - 2px);
     --radius-sm: calc(var(--radius) - 4px);
   }
   ```
   기존 `:root`/`.dark`의 oklch 변수 값 자체는 변경하지 않는다.
4. `content` 배열(v3 전용 설정)은 v4에서 자동 감지로 대체되어 불필요 — 제거
5. `tailwind.config.ts` 파일 삭제
6. **완료 기준**: `npm run dev` 기동 후 이미 v4 문법으로 작성된 `button.tsx`/`badge.tsx`가 의도대로 렌더링되는지(`ring-3` 포커스 링, `size-3!` 아이콘 크기 등) 확인하고, 전 페이지를 브라우저로 순회하며 마이그레이션 전과 시각적으로 동일한지 확인한다(회귀 없음이 목표— 이 단계에서 디자인을 바꾸지 않는다).

## Phase 1 — shadcn 프리미티브 확장

인벤토리에서 확인된 재발명/중복 패턴을 근거로 아래 컴포넌트를 `shadcn` CLI로 한 번에 추가한다 (대상: `frontend/components/ui/`):

| 추가 컴포넌트 | 대체 대상 |
|---|---|
| `Input` | `lib/ui-classes.ts`의 `INPUT_CLASS`로 스타일링된 네이티브 `<input>` (PortSetupForm 다수 필드, CoinSelect 검색창) |
| `Select` | `SELECT_CLASS`/네이티브 `<select>` (PortSetupForm 봉데이터, StrategyConditionBuilder 지표·연산자 `<optgroup>` 포함, ComboHistoryChart) |
| `Popover` + `Command` | CoinSelect의 커스텀 절대위치 리스트박스 + 검색 (컴보박스 패턴, 키보드 내비게이션 확보) |
| `Dialog` / `AlertDialog` | PortSetupForm의 raw `fixed inset-0` validation 모달, DeleteRunButton의 `window.confirm`/`window.alert` |
| `Tooltip` | `MetricTile.tsx`/`StrategyConditionBuilder.tsx`에 각각 중복 구현된 `InfoTooltip` → 하나로 통합 |

`Badge`/`Card`/`Table`은 이미 있고 잘 쓰이고 있어 그대로 유지.

## Phase 2 — 페이지별 적용 (우선순위 순서)

### 2-1. Nav 셸 — `layout.tsx`, `NavTabs.tsx`
- 탭별 아이콘 추가: 백테스트 설정=`Settings`, 백테스트 결과=`FlaskConical`, 분석=`BarChart3`
- 구조는 현재 `Link` 기반 유지(위 결정 사항 참고)

### 2-2. 백테스트 설정 — `PortSetupForm.tsx`, `CoinSelect.tsx`, `StrategyConditionBuilder.tsx`
- `PortSetupForm`: 네이티브 input/select → `Input`/`Select`, validation 모달 → `AlertDialog`, 에러 텍스트 `text-red-600` → `text-destructive` 토큰 + `TriangleAlert` 아이콘, "최근 최대 기간 설정" 버튼에 `CalendarRange` 아이콘, 제출/취소 버튼에 `Play`/`X` 아이콘
- `CoinSelect`: `Popover`+`Command` 기반으로 재작성, 검색창에 `Search` 아이콘, 정렬 헤더의 유니코드 화살표(`⇅▼▲`) → `ArrowUpDown`/`ArrowUp`/`ArrowDown`
- `StrategyConditionBuilder`: `<select>` → `Select`(그룹 지원), 삭제 버튼(`✕` 문자) → `X`/`Trash2` 아이콘, `InfoTooltip` → 공용 `Tooltip`(Phase 1에서 만든 것 재사용, 중복 제거), "+ 조건 추가"/"+ 괄호 묶음 추가" → `Button variant="outline"` + `Plus` 아이콘, 카테고리 색상 점 옆에 `TrendingUp`/`Activity`/`BarChart2` 등 카테고리 아이콘 추가

### 2-3. 백테스트 결과 — `BacktestRunsTable.tsx`, `DeleteRunButton.tsx`, `backtests/[runId]/page.tsx`, `MetricTile.tsx`
- `BacktestRunsTable`: 정렬 화살표 아이콘화(2-2와 동일 패턴), "상세"/"삭제" raw 링크 → `Button variant="link"`(`Eye` 아이콘)/`Button variant="ghost" size="icon"`(`Trash2` 아이콘)
- `DeleteRunButton`: `window.confirm` → `AlertDialog`, `window.alert` 실패 메시지 → 인라인 에러 텍스트(새 의존성 추가 안 함)
- `backtests/[runId]/page.tsx`: 중복된 두 종류의 "metric tile" 패턴(수동 레이아웃 5개 div + `MetricTile` 컴포넌트)을 `MetricTile` 하나로 통합, 라이브 재평가 안내 문구에 `Clock`/`AlertTriangle` 아이콘 추가
- `MetricTile`: 중복 `InfoTooltip` 제거하고 공용 `Tooltip` 사용, 지표 종류별 아이콘(`Percent`/`TrendingDown`/`Repeat` 등) 추가

### 2-4. 분석 — `analysis/page.tsx`, `SegmentSizeCard.tsx`, `ranking/page.tsx`
- 이미 `Card` 기반이라 구조 변경 최소화
- `SegmentSizeCard`의 "⚠ 유의종목" 리터럴 이모지 → `AlertTriangle` 아이콘
- 랭킹 순위 표기에 상위 3위 한정 `Trophy`/`Medal` 아이콘(선택적 폴리시)

### 2-5. 나머지 — `heatmap/page.tsx`, `history/page.tsx`, `model-accuracy/page.tsx`, `ComboHistoryChart.tsx`, `PriceChart.tsx`
- 남은 "보기" raw 링크(`heatmap/page.tsx` 등, 총 3곳) → `Button variant="link"`로 통일
- `ComboHistoryChart`의 인라인 `<select>` → `Select`, 하드코딩 `#3b82f6` → `globals.css`의 `--chart-1` 등 CSS 변수로 교체
- `PriceChart`의 범례/차트 색상(`bg-blue-500`, `#dc2626` 등 하드코딩 hex) → `--chart-*` 토큰으로 교체
- heatmap 8열 테이블: sticky header 적용, 숫자 컬럼 `tabular-nums` 통일(다른 곳엔 이미 적용됨)

## Phase 3 — 마무리

- **다크모드 토글**: 현재 `.dark` CSS 변수는 정의돼 있지만 이를 토글하는 메커니즘이 전무하다(next-themes 등 미설치, `layout.tsx`에 어떤 provider도 없음). `layout.tsx`에 최소한의 다크모드 토글(로컬스토리지 + `html`에 `.dark` 클래스 추가/제거, 새 외부 의존성 없이 구현)과 Nav 셸에 토글 버튼(`Sun`/`Moon` 아이콘) 추가
- **색상 토큰 통일**: `bg-slate-50 dark:bg-slate-800` 하드코딩 8곳(`lib/ui-classes.ts`, `CoinSelect.tsx`, `StrategyConditionBuilder.tsx`×4, `PortSetupForm.tsx`) → `bg-muted`로 교체. 단, 수익률 빨강/파랑(한국식 컨벤션, `lib/return-rate-color.ts`)은 디자인 토큰이 아닌 도메인 컨벤션이므로 그대로 유지
- **반응형/정보 밀도 최종 점검**: 전 페이지 좁은 뷰포트에서 레이아웃 확인

## 범위 밖

- 백엔드(`backend/`, `engine/`) 변경 없음 — 이번 계획은 프론트엔드 UI 전용
- 새 기능/데이터 추가 없음 — 기존 화면의 컴포넌트 교체와 시각적 정비만
- toast/sonner 등 새 UI 라이브러리 도입 — 위 "결정된 사항"에 명시된 대로 기존 스택 내에서 해결
- shadcn `Tabs`로의 NavTabs 전환 — 위 "결정된 사항"에 명시된 대로 하지 않음
- 차트 라이브러리(`lightweight-charts`, `recharts`) 자체 교체나 차트 상호작용 고도화

## Self-Review 결과

- **스펙 커버리지**: 사용자가 승인한 4가지 목적(시각적 일관성/아이콘/레이아웃 밀도/다크모드)이 각각 Phase 1~3에 대응하는 섹션에 반영됨. "전체 페이지" 범위도 Phase 2의 5개 하위 그룹으로 전 페이지를 커버함.
- **내부 정합성**: Tailwind v4 마이그레이션이 "빌드 단위 작업이라 페이지별로 쪼갤 수 없다"는 점을 배경/결정된 사항/Phase 0에 일관되게 명시. NavTabs를 Tabs로 바꾸지 않는다는 결정이 "결정된 사항"과 "범위 밖" 양쪽에서 모순 없이 일치.
- **범위 확인**: 백엔드 변경 없음, 새 기능 없음, 새 의존성(toast) 없음, Tabs 전환 없음을 명시적으로 범위 밖에 기재.
- **대상 파일 목록**: `tailwind.config.ts`(삭제), `postcss.config.mjs`, `app/globals.css`, `components/ui/{input,select,popover,command,dialog,alert-dialog,tooltip}.tsx`(신규), `components/NavTabs.tsx`, `app/layout.tsx`, `components/PortSetupForm.tsx`, `components/CoinSelect.tsx`, `components/StrategyConditionBuilder.tsx`, `components/BacktestRunsTable.tsx`, `components/DeleteRunButton.tsx`, `app/backtests/[runId]/page.tsx`, `components/MetricTile.tsx`, `app/analysis/page.tsx`, `components/SegmentSizeCard.tsx`, `app/ranking/page.tsx`, `app/heatmap/page.tsx`, `app/history/page.tsx`, `app/model-accuracy/page.tsx`, `components/ComboHistoryChart.tsx`, `components/PriceChart.tsx`, `lib/ui-classes.ts`.
