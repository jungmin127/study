# 모바일 반응형(Responsive) 개선 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `frontend/` 웹 대시보드를 모바일 세로 화면에서 자연스럽게 쓸 수 있도록 반응형으로 개선한다. 특히 백테스트 결과 목록/상세, 라이브 전략, 매매일지는 카드형 UI로 재구성하고, 나머지 페이지는 가로 스크롤이 발생하지 않는 수준까지만 손본다. PC(≥768px) 레이아웃은 지금과 동일하게 유지한다.

**Architecture:** Tailwind CSS(v4) 반응형 유틸리티 클래스(`sm:`/`md:`/`lg:`, 기본값 = 모바일)만으로 처리한다. 구조가 달라지는 곳(네비게이션, 표→카드)은 두 마크업을 함께 서버 렌더링하고 `hidden md:block` / `block md:hidden`(또는 `md:flex` 등)으로 전환한다. 새 JS 미디어쿼리 훅이나 라이브러리는 추가하지 않는다.

**Tech Stack:** Next.js 14 (App Router), React 18, Tailwind CSS 4, `@base-ui/react` 기반 shadcn 스타일 컴포넌트(`components/ui/*`), `lightweight-charts`, `recharts`. 자동화된 프론트엔드 테스트 프레임워크(Jest/Playwright 등)는 이 저장소에 아직 없으므로, 각 작업의 검증은 로컬 dev 서버 + Playwright MCP 브라우저 도구(`mcp__plugin_playwright_playwright__*`)를 이용한 수동/반자동 확인으로 대체한다.

## Global Constraints

- 기존 기능·데이터 처리·API·비즈니스 로직은 변경하지 않는다. (스펙 "목표"/"비목표")
- PC(`md:` 이상, 768px+) 레이아웃은 지금과 시각적으로 동일하게 유지한다.
- 모바일(375px 기준)에서 페이지 전체 가로 스크롤(horizontal overflow)이 발생하지 않는다.
- 새 npm 의존성을 추가하지 않는다 — Tailwind 유틸리티와 기존 `components/ui/*`, `@base-ui/react` 프리미티브만 사용한다.
- Breakpoint는 Tailwind 기본값을 그대로 쓴다: `sm`=640px, `md`=768px, `lg`=1024px.
- 우선순위 페이지(백테스트 결과 목록/상세, 라이브 전략, 매매일지)는 카드형 UI로 재구성한다. 그 외 페이지(Grid Search, 세그먼트, 히트맵, 지표 가이드, 랭킹, 모델 정확도, 루트 `/`)는 가로 스크롤 방지 수준까지만 수정한다.
- 모바일 폰트는 지금보다 작아도 된다(`text-sm`/`text-xs` 위주 유지) — 단 줄바꿈 시 텍스트가 겹치지 않도록 `line-height`를 확보한다.
- 참고 스펙: `docs/superpowers/specs/2026-08-16-mobile-responsive-design.md`

---

## 사전 준비 — 개발 서버 확인

모든 작업에서 로컬 Next.js dev 서버가 필요하다. 작업 시작 전 아래를 확인한다.

- `frontend/` 디렉터리에서 dev 서버가 이미 떠 있는지 확인한다(예: `curl -s -o /dev/null -w "%{http_code}" http://localhost:3000` 로 200이 오는지). 이미 떠 있다면 재사용하고 새로 띄우지 않는다.
- 떠 있지 않다면 `npm run dev`로 새로 띄운다(포그라운드로 띄우면 이후 명령이 막히므로 백그라운드 실행 방식 사용).
- **`npm run build`는 실행하지 않는다** — 라이브 dev 서버의 `.next`를 손상시키는 것으로 이미 확인된 바 있다.
- 백엔드(FastAPI) 서버도 함께 떠 있어야 페이지들이 정상적으로 데이터를 렌더링한다. 백엔드 실행 방법은 저장소 루트의 `deploy/` 문서 또는 기존 dev 워크플로를 따른다.
- 각 작업의 "검증" 스텝은 Playwright MCP 브라우저 도구(`browser_navigate`, `browser_resize`, `browser_evaluate`, `browser_take_screenshot`)를 사용한다. 가로 스크롤 여부는 다음 스크립트로 확인한다:
  ```js
  () => document.documentElement.scrollWidth <= window.innerWidth
  ```
  `true`가 나와야 통과.

---

### Task 1: 전역 오버플로우 안전망 + 컨테이너 패딩 반응형

**Files:**
- Modify: `frontend/app/globals.css:134-139`
- Modify: `frontend/app/layout.tsx:21`

**Interfaces:**
- 생산: 이후 모든 작업이 의존하는 "페이지 전체가 가로로 넘치지 않는다"는 안전망. 개별 컴포넌트가 실수로 넓어져도 페이지 스크롤은 발생하지 않는다.

- [ ] **Step 1: `globals.css`에 `overflow-x-hidden` 추가**

`frontend/app/globals.css`의 기존 `body`/`html` 규칙(133~139번째 줄 부근)을 다음과 같이 수정한다.

```css
  body {
    @apply bg-background text-foreground overflow-x-hidden;
  }
  html {
    @apply font-sans overflow-x-hidden;
  }
```

- [ ] **Step 2: `layout.tsx`의 `<main>` 패딩을 반응형으로 변경**

`frontend/app/layout.tsx`의 `<main className="p-6">{children}</main>`를 다음으로 교체한다.

```tsx
        <main className="p-3 sm:p-4 md:p-6">{children}</main>
```

- [ ] **Step 3: dev 서버에서 확인**

Playwright MCP로 `http://localhost:3000/backtests`를 열고 `browser_resize`로 375×812, 1280×800 두 크기에서 각각 `document.documentElement.scrollWidth <= window.innerWidth`가 `true`인지 확인한다. 1280×800에서는 기존과 동일한 여백(24px, `p-6`)으로 보이는지 스크린샷으로 확인한다.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/globals.css frontend/app/layout.tsx
git commit -m "style: 페이지 전역 가로스크롤 방지 + 모바일 컨테이너 패딩 축소"
```

---

### Task 2: 모바일 네비게이션 — 햄버거 드로어

**Files:**
- Create: `frontend/lib/nav-active.ts`
- Create: `frontend/components/MobileNavDrawer.tsx`
- Modify: `frontend/components/NavTabs.tsx`

**Interfaces:**
- 소비: Task 1에서 만든 `overflow-x-hidden` 안전망.
- 생산: `isActive(pathname: string, href: string): boolean` (from `frontend/lib/nav-active.ts`) — 이후 다른 네비게이션 관련 코드가 필요하면 재사용 가능.
  `MobileNavDrawer` 컴포넌트는 `{ steps: { href: string; title: string; icon: LucideIcon }[] }`를 props로 받는다.

- [ ] **Step 1: `isActive`를 공용 모듈로 분리**

`frontend/lib/nav-active.ts` 신규 생성:

```ts
export function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}
```

- [ ] **Step 2: `MobileNavDrawer` 컴포넌트 작성**

`frontend/components/MobileNavDrawer.tsx` 신규 생성:

```tsx
'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Dialog as DialogPrimitive } from '@base-ui/react/dialog';
import { Menu, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import ThemeToggle from '@/components/ThemeToggle';
import { isActive } from '@/lib/nav-active';
import { cn } from '@/lib/utils';

export interface MobileNavStep {
  href: string;
  title: string;
  icon: LucideIcon;
}

export default function MobileNavDrawer({ steps }: { steps: MobileNavStep[] }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Trigger
        render={<Button type="button" variant="ghost" size="icon" aria-label="메뉴 열기" />}
      >
        <Menu className="size-5" />
      </DialogPrimitive.Trigger>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/30 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <DialogPrimitive.Popup className="fixed inset-y-0 right-0 z-50 flex h-full w-64 max-w-[80vw] flex-col gap-1 border-l bg-background p-3 outline-none data-open:animate-in data-open:slide-in-from-right data-closed:animate-out data-closed:slide-out-to-right">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-semibold">메뉴</span>
            <DialogPrimitive.Close
              render={<Button type="button" variant="ghost" size="icon-sm" aria-label="메뉴 닫기" />}
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <nav className="flex flex-col gap-1">
            {steps.map((step) => {
              const Icon = step.icon;
              const active = isActive(pathname, step.href);
              return (
                <Link
                  key={step.href}
                  href={step.href}
                  onClick={() => setOpen(false)}
                  className={cn(
                    'flex items-center gap-2 rounded-md px-3 py-2.5 text-sm',
                    active
                      ? 'bg-muted font-semibold text-foreground'
                      : 'text-muted-foreground hover:bg-muted hover:text-foreground'
                  )}
                >
                  <Icon className="size-4" />
                  {step.title}
                </Link>
              );
            })}
          </nav>
          <div className="mt-auto flex items-center justify-between border-t pt-2">
            <span className="text-xs text-muted-foreground">테마</span>
            <ThemeToggle />
          </div>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
```

- [ ] **Step 3: `NavTabs.tsx`를 데스크톱/모바일로 분기**

`frontend/components/NavTabs.tsx` 전체를 다음으로 교체한다.

```tsx
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BarChart3, BookOpen, ClipboardList, FlaskConical, Grid3x3, Rocket, Settings } from 'lucide-react';
import ThemeToggle from '@/components/ThemeToggle';
import MobileNavDrawer from '@/components/MobileNavDrawer';
import { isActive } from '@/lib/nav-active';

const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/grid-search', title: 'Grid Search', icon: Grid3x3 },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/live-strategies', title: '라이브 전략', icon: Rocket },
  { href: '/journal', title: '매매일지', icon: ClipboardList },
  { href: '/analysis', title: '세그먼트', icon: BarChart3 },
  { href: '/guide', title: '지표 가이드', icon: BookOpen },
];

export default function NavTabs() {
  const pathname = usePathname();
  const activeStep = STEPS.find((step) => isActive(pathname, step.href));

  return (
    <header className="flex items-center justify-between border-b px-3 md:px-6">
      <div className="flex w-full items-center justify-between py-2.5 md:hidden">
        <span className="truncate text-sm font-semibold">{activeStep?.title ?? 'Upbit 전략 EDA'}</span>
        <MobileNavDrawer steps={STEPS} />
      </div>

      <nav className="hidden gap-6 md:flex">
        {STEPS.map((step) => {
          const Icon = step.icon;
          const active = isActive(pathname, step.href);
          return (
            <Link
              key={step.href}
              href={step.href}
              className={
                active
                  ? 'flex items-center gap-1.5 border-b-2 border-primary py-3 font-semibold text-foreground'
                  : 'flex items-center gap-1.5 border-b-2 border-transparent py-3 text-muted-foreground hover:text-foreground'
              }
            >
              <Icon className="size-4" />
              {step.title}
            </Link>
          );
        })}
      </nav>
      <div className="hidden md:block">
        <ThemeToggle />
      </div>
    </header>
  );
}
```

- [ ] **Step 4: dev 서버에서 확인**

Playwright MCP로 375×812에서 아무 페이지나 열어 상단에 페이지 제목 + 햄버거 아이콘만 보이는지 확인. 햄버거를 클릭해 드로어가 우측에서 열리는지, 7개 메뉴가 세로로 나열되는지, 항목 클릭 시 이동하고 드로어가 닫히는지 확인. 1280×800에서는 기존과 동일하게 가로 탭 바 + ThemeToggle이 보이는지(`browser_take_screenshot`으로 비교) 확인.

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/nav-active.ts frontend/components/MobileNavDrawer.tsx frontend/components/NavTabs.tsx
git commit -m "feat: 모바일 상단 네비게이션을 햄버거 드로어로 전환"
```

---

### Task 3: `PriceChart` 모바일 높이 반응형

**Files:**
- Modify: `frontend/components/PriceChart.tsx:70-77`, `:157-166`, `:184`

**Interfaces:**
- 소비: 없음(독립 컴포넌트).
- 생산: 컨테이너 높이가 Tailwind 클래스(`h-60 md:h-80`)를 따라가고, `ResizeObserver`가 폭뿐 아니라 높이 변경도 차트에 반영한다. Task 6(거래 내역 카드)는 이 변경에 의존하지 않는다.

- [ ] **Step 1: 차트 컨테이너에 반응형 높이 클래스 부여**

`frontend/components/PriceChart.tsx` 184번째 줄:

```tsx
      <div ref={containerRef} className="w-full rounded-lg overflow-hidden border" />
```

를 다음으로 교체:

```tsx
      <div ref={containerRef} className="h-60 w-full rounded-lg overflow-hidden border md:h-80" />
```

- [ ] **Step 2: 차트 생성 시 고정 높이(320) 대신 컨테이너 높이를 사용**

70~77번째 줄:

```tsx
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 320,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });
```

를 다음으로 교체:

```tsx
    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: containerRef.current.clientHeight,
      layout: { background: { type: ColorType.Solid, color: background }, textColor: foreground },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: border },
      rightPriceScale: { borderColor: border },
    });
```

- [ ] **Step 3: 리사이즈 시 높이도 함께 반영**

157~160번째 줄:

```tsx
    const resizeObserver = new ResizeObserver(() => {
      if (containerRef.current) chart.applyOptions({ width: containerRef.current.clientWidth });
    });
```

를 다음으로 교체:

```tsx
    const resizeObserver = new ResizeObserver(() => {
      if (!containerRef.current) return;
      chart.applyOptions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    });
```

- [ ] **Step 4: dev 서버에서 확인**

Playwright MCP로 아무 백테스트 상세 페이지(`/backtests/<runId>`)를 열고 375×812에서 차트 높이가 눈에 띄게 낮아졌는지(약 240px), 캔들/마커가 정상 렌더링되는지 확인. 1280×800에서는 기존과 동일하게 320px 높이로 보이는지 확인.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/PriceChart.tsx
git commit -m "style: 모바일에서 가격 차트 높이 축소"
```

---

### Task 4: `BacktestRunsTable` 모바일 카드 리스트

**Files:**
- Create: `frontend/components/BacktestRunCard.tsx`
- Modify: `frontend/components/BacktestRunsTable.tsx`

**Interfaces:**
- 소비: 없음(기존 `filterRuns`/`sortRuns`/선택 상태 로직 재사용, 변경 없음).
- 생산: `export function buildCopyHref(run: BacktestRunSummary): string`(기존 `BacktestRunsTable.tsx`에 있던 비공개 함수를 export). `BacktestRunCard` 컴포넌트는 `{ run: BacktestRunSummary; marketName?: string; selected: boolean; onToggleSelected: (checked: boolean) => void }`를 props로 받는다.

- [ ] **Step 1: `buildCopyHref`를 export**

`frontend/components/BacktestRunsTable.tsx`의 79번째 줄:

```tsx
function buildCopyHref(run: BacktestRunSummary): string {
```

를 다음으로 교체:

```tsx
export function buildCopyHref(run: BacktestRunSummary): string {
```

- [ ] **Step 2: `BacktestRunCard` 컴포넌트 작성**

`frontend/components/BacktestRunCard.tsx` 신규 생성:

```tsx
'use client';

import Link from 'next/link';
import { Copy, Eye } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Checkbox } from '@/components/ui/checkbox';
import { returnRateColor } from '@/lib/return-rate-color';
import { summarizeGroup } from '@/lib/condition-summary';
import { formatDateTime, formatTimeframe } from '@/lib/format';
import { buildCopyHref } from '@/components/BacktestRunsTable';
import type { BacktestRunSummary } from '@/lib/types/eda';

function LastTradeStatusBadge({ status }: { status: BacktestRunSummary['last_trade_status'] }) {
  if (status === 'none') return <span className="text-muted-foreground">-</span>;
  if (status === 'open') return <Badge variant="secondary">보유중</Badge>;
  return <Badge variant="outline">청산</Badge>;
}

interface BacktestRunCardProps {
  run: BacktestRunSummary;
  marketName?: string;
  selected: boolean;
  onToggleSelected: (checked: boolean) => void;
}

export default function BacktestRunCard({ run, marketName, selected, onToggleSelected }: BacktestRunCardProps) {
  return (
    <div className="rounded-md border p-3">
      <div className="mb-2 flex items-start gap-2">
        <Checkbox
          checked={selected}
          onCheckedChange={(checked) => onToggleSelected(checked === true)}
          aria-label={`${run.title || run.run_id} 선택`}
          className="mt-0.5"
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">
            {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
          </p>
          <p className="text-xs text-muted-foreground">
            {run.market}
            {marketName ? ` · ${marketName}` : ''} · {formatTimeframe(run.timeframe)}
          </p>
          <p className="text-xs text-muted-foreground">
            {run.start.slice(0, 10)} ~ {run.end.slice(0, 10)}
          </p>
        </div>
      </div>

      <div className="mb-2 flex flex-wrap items-center gap-3 text-sm">
        <span className={returnRateColor(run.return_rate)}>
          수익률 {run.return_rate?.toFixed(2) ?? '-'}%
          {run.is_live && <span className="ml-1 text-xs text-muted-foreground">(실시간)</span>}
        </span>
        <span className="text-muted-foreground">MDD {run.max_drawdown?.toFixed(2) ?? '-'}%</span>
        <LastTradeStatusBadge status={run.last_trade_status} />
      </div>

      <details className="mb-2 text-xs text-muted-foreground">
        <summary className="cursor-pointer select-none">매수/매도 조건 보기</summary>
        <p className="mt-1 font-mono">매수: {summarizeGroup(run.buy_conditions)}</p>
        <p className="mt-1 font-mono">매도: {summarizeGroup(run.sell_conditions)}</p>
      </details>

      <p className="mb-2 text-xs text-muted-foreground">실행 시각: {formatDateTime(run.created_at)}</p>

      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          className="max-md:min-h-9 flex-1"
          nativeButton={false}
          role="link"
          render={<Link href={`/backtests/${run.run_id}`} />}
        >
          <Eye className="size-3.5" />
          보기
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="max-md:min-h-9 flex-1"
          nativeButton={false}
          role="link"
          render={<Link href={buildCopyHref(run)} />}
        >
          <Copy className="size-3.5" />
          복사
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: `BacktestRunsTable.tsx`에 모바일 정렬 셀렉트 + 카드 리스트 렌더링 추가**

`frontend/components/BacktestRunsTable.tsx` 상단 import에 추가:

```tsx
import BacktestRunCard from '@/components/BacktestRunCard';
```

`SortDir` 타입 선언 바로 아래에 정렬 옵션 상수를 추가:

```tsx
const SORT_OPTIONS: { value: string; key: SortKey; dir: SortDir; label: string }[] = [
  { value: 'created_at:desc', key: 'created_at', dir: 'desc', label: '실행 시각 (최신순)' },
  { value: 'created_at:asc', key: 'created_at', dir: 'asc', label: '실행 시각 (오래된순)' },
  { value: 'return_rate:desc', key: 'return_rate', dir: 'desc', label: '수익률 (높은순)' },
  { value: 'return_rate:asc', key: 'return_rate', dir: 'asc', label: '수익률 (낮은순)' },
  { value: 'max_drawdown:desc', key: 'max_drawdown', dir: 'desc', label: 'MDD (큰순)' },
  { value: 'max_drawdown:asc', key: 'max_drawdown', dir: 'asc', label: 'MDD (작은순)' },
];
```

기존 `return ( <div> ... <Table>...</Table> </div> )` 블록에서, `<Table>`을 감싸는 부분(265번째 줄 `<Table>`부터 387번째 줄 `</Table>`까지)을 `<div className="hidden md:block">...</div>`로 감싸고, 그 바로 다음(마지막 `</div>` 앞)에 모바일 전용 블록을 추가한다. 즉 컴포넌트의 `return` 마지막 부분을 다음 구조로 만든다:

```tsx
      <div className="hidden md:block">
        <Table>
          {/* 기존 Table 내용 그대로, 변경 없음 */}
        </Table>
      </div>

      <div className="mb-3 md:hidden">
        <select
          className="w-full rounded-md border bg-background px-3 py-2 text-sm"
          value={sortKey ? `${sortKey}:${sortDir}` : ''}
          onChange={(e) => {
            const opt = SORT_OPTIONS.find((o) => o.value === e.target.value);
            if (opt) {
              setSortKey(opt.key);
              setSortDir(opt.dir);
            } else {
              setSortKey(null);
            }
          }}
        >
          <option value="">정렬: 기본</option>
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      <div className="space-y-3 md:hidden">
        {sorted.length === 0 ? (
          <p className="text-center text-sm text-muted-foreground">조건에 맞는 결과가 없습니다.</p>
        ) : (
          sorted.map((run) => (
            <BacktestRunCard
              key={run.run_id}
              run={run}
              marketName={marketNames[run.market]}
              selected={selected.has(run.run_id)}
              onToggleSelected={(checked) => toggleOne(run.run_id, checked)}
            />
          ))
        )}
      </div>
    </div>
  );
}
```

(모바일 정렬 셀렉트는 테이블 바로 위, 즉 `<div className="hidden md:block">...</div>` 다음, 카드 리스트 앞에 위치)

- [ ] **Step 4: dev 서버에서 확인**

Playwright MCP로 `/backtests`를 375×812로 열어: (1) 카드 리스트가 보이고 테이블은 안 보이는지, (2) 체크박스 선택 후 "선택 삭제"가 동작하는지, (3) 정렬 셀렉트를 바꾸면 카드 순서가 바뀌는지, (4) "매수/매도 조건 보기"를 탭하면 펼쳐지는지, (5) 필터바(코인 필터, 체크박스들)가 줄바꿈되며 화면을 벗어나지 않는지, (6) `document.documentElement.scrollWidth <= window.innerWidth`가 `true`인지 확인. 1280×800에서는 기존과 동일한 13컬럼 테이블이 그대로 보이는지 확인.

- [ ] **Step 5: Commit**

```bash
git add frontend/components/BacktestRunsTable.tsx frontend/components/BacktestRunCard.tsx
git commit -m "feat: 백테스트 결과 목록에 모바일 카드 뷰 추가"
```

---

### Task 5: 백테스트 상세 페이지 — 지표 그리드 반응형 열 수

**Files:**
- Modify: `frontend/app/backtests/[runId]/page.tsx:73`, `:112`

**Interfaces:**
- 소비: 없음.
- 생산: 없음(순수 클래스 변경, 이후 작업에 영향 없음).

- [ ] **Step 1: `MetricsGrid`(12개 타일)의 그리드 열 수를 단계적으로**

`frontend/app/backtests/[runId]/page.tsx` 73번째 줄:

```tsx
      <div className="grid grid-cols-6 gap-3">
```

를 다음으로 교체:

```tsx
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-6">
```

- [ ] **Step 2: 요약 카드(MDD/총거래/투입금/최종금액 4열)의 그리드 열 수 조정**

112번째 줄:

```tsx
        <div className="grid grid-cols-4 gap-3">
```

를 다음으로 교체:

```tsx
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
```

- [ ] **Step 3: dev 서버에서 확인**

Playwright MCP로 아무 백테스트 상세 페이지를 375×812로 열어 지표 타일이 2열로, 요약 카드도 2열로 배치되고 라벨/값이 잘리지 않는지 확인. 640×800(`sm`)에서 지표 타일이 3열로 바뀌는지 확인. 1280×800에서는 기존과 동일하게 지표 6열, 요약 4열인지 확인.

- [ ] **Step 4: Commit**

```bash
git add "frontend/app/backtests/[runId]/page.tsx"
git commit -m "style: 백테스트 상세 지표 그리드 모바일 열 수 조정"
```

---

### Task 6: 백테스트 상세 페이지 — 거래 내역 표 모바일 카드 뷰

**Files:**
- Modify: `frontend/app/backtests/[runId]/page.tsx` (import 구문, 신규 `TradeCard` 헬퍼, 거래 내역 렌더링 블록)

**Interfaces:**
- 소비: 없음.
- 생산: 없음(페이지 내부 전용 헬퍼 컴포넌트).

- [ ] **Step 1: `Trade` 타입 import 추가**

`frontend/app/backtests/[runId]/page.tsx` 상단의

```tsx
import type { BacktestMetrics } from '@/lib/types/eda';
```

를 다음으로 교체:

```tsx
import type { BacktestMetrics, Trade } from '@/lib/types/eda';
```

- [ ] **Step 2: 페이지 내부에 `TradeCard` 헬퍼 컴포넌트 추가**

`MetricsGrid` 함수 정의 바로 다음(83번째 줄, `}` 다음 줄)에 아래 함수를 추가한다.

```tsx
function TradeCard({
  trade,
  timeframe,
  hasLivePrice,
}: {
  trade: Trade;
  timeframe: string;
  hasLivePrice: boolean;
}) {
  return (
    <div className="rounded-md border p-3 text-sm">
      <div className="mb-1 flex items-center justify-between">
        <span className={`font-semibold ${returnRateColor(trade.returnRate)}`}>
          {trade.returnRate.toFixed(2)}%
        </span>
        {trade.forceClosed ? (
          <Badge
            variant="secondary"
            title={
              hasLivePrice
                ? '매도 조건을 만족하지 못한 채 아직 보유 중입니다. 현재가로 재평가된 수익률입니다.'
                : '매도 조건을 만족하지 못해 백테스트 종료 시점 종가로 평가된 상태입니다.'
            }
          >
            보유중(기간종료)
          </Badge>
        ) : (
          <Badge variant="outline">청산됨</Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        진입 {formatDateTime(trade.entryTime)} · {trade.entryPrice.toLocaleString()}원
      </p>
      <p className="text-xs text-muted-foreground">
        청산 {formatDateTime(trade.exitTime)} · {trade.exitPrice.toLocaleString()}원
      </p>
      <p className="mt-1 text-xs">
        수익금 <span className={returnRateColor(trade.pnl)}>{Math.round(trade.pnl).toLocaleString()}원</span>
        {' · '}보유기간 {formatHoldingPeriod(trade.holdingPeriod, timeframe)}
      </p>
    </div>
  );
}
```

- [ ] **Step 3: 거래 내역 렌더링을 데스크톱 표 / 모바일 카드로 분기**

기존(127~174번째 줄 부근):

```tsx
      <h2 className="mt-6 mb-2 font-medium">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <Table>
          {/* ... 기존 TableHeader/TableBody 그대로 ... */}
        </Table>
      )}
```

를 다음 구조로 교체(표 내부는 변경 없이 그대로 두고 감싸는 부분만 바뀐다):

```tsx
      <h2 className="mt-6 mb-2 font-medium">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <>
          <div className="hidden md:block">
            <Table>
              {/* 기존 TableHeader/TableBody 내용 그대로, 변경 없음 */}
            </Table>
          </div>
          <div className="space-y-2 md:hidden">
            {detail.trades.map((t, i) => (
              <TradeCard key={i} trade={t} timeframe={detail.timeframe} hasLivePrice={!!detail.live_price_as_of} />
            ))}
          </div>
        </>
      )}
```

- [ ] **Step 4: dev 서버에서 확인**

Playwright MCP로 거래 내역이 있는 백테스트 상세 페이지를 375×812로 열어 카드 리스트가 보이고(진입/청산/수익률/상태), 표는 숨겨지는지 확인. 1280×800에서는 기존 8컬럼 표가 그대로 보이는지 확인.

- [ ] **Step 5: Commit**

```bash
git add "frontend/app/backtests/[runId]/page.tsx"
git commit -m "feat: 백테스트 상세 거래 내역에 모바일 카드 뷰 추가"
```

---

### Task 7: `JournalStrategyDetail` 모바일 카드 뷰

**Files:**
- Modify: `frontend/components/JournalStrategyDetail.tsx`

**Interfaces:**
- 소비: 없음.
- 생산: 없음.

- [ ] **Step 1: 백테스트 vs 실매매 비교 표를 데스크톱 표 / 모바일 카드로 분기**

`frontend/components/JournalStrategyDetail.tsx`에서 기존 비교 `<Table>` 블록(71~112번째 줄)을 `<div className="hidden md:block"><Table>...</Table></div>`로 감싸고, 바로 다음에 모바일 카드 블록을 추가한다. `<>` ... `</>` 안에서 다음 구조가 되도록 한다:

```tsx
          <>
            <div className="hidden md:block">
              <Table>
                {/* 기존 TableHeader/TableBody 내용 그대로 */}
              </Table>
            </div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:hidden">
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">승률</p>
                <p>
                  백테스트 {comparison.backtest.win_rate_pct.toFixed(1)}% · 실매매{' '}
                  {comparison.live.win_rate_pct.toFixed(1)}%
                </p>
                <p className="text-xs text-muted-foreground">
                  차이 {fmtPct(comparison.live.win_rate_pct - comparison.backtest.win_rate_pct)}p
                </p>
              </div>
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">평균수익률</p>
                <p>
                  백테스트 {fmtPct(comparison.backtest.avg_return_pct)} · 실매매{' '}
                  {fmtPct(comparison.live.avg_return_pct)}
                </p>
                <p className="text-xs text-muted-foreground">
                  차이 {fmtPct(comparison.live.avg_return_pct - comparison.backtest.avg_return_pct)}p
                </p>
              </div>
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">MDD</p>
                <p>
                  백테스트 {fmtPct(comparison.backtest.mdd_pct)} · 실매매 {fmtPct(comparison.live.mdd_pct)}
                </p>
                <p className="text-xs text-muted-foreground">
                  차이 {fmtPct(comparison.live.mdd_pct - comparison.backtest.mdd_pct)}p
                </p>
              </div>
              <div className="rounded-md border p-2 text-sm">
                <p className="text-xs text-muted-foreground">거래횟수</p>
                <p>
                  백테스트 {comparison.backtest.trade_count}건 · 실매매 {comparison.live.trade_count}건
                </p>
              </div>
            </div>
            {comparison.sample_size_warning && (
              <p className="mt-2 text-xs text-amber-600">
                실매매 표본이 10건 미만이라 통계적으로 신뢰하기 이릅니다.
              </p>
            )}
          </>
```

- [ ] **Step 2: 매매일지 표를 데스크톱 표 / 모바일 카드로 분기**

기존 매매일지 `<Table>` 블록(127~156번째 줄)을 다음 구조로 교체:

```tsx
          <>
            <div className="hidden md:block">
              <Table>
                {/* 기존 TableHeader/TableBody 내용 그대로 */}
              </Table>
            </div>
            <div className="space-y-2 md:hidden">
              {detail.trade_log.map((t) => (
                <div key={t.position_id} className="rounded-md border p-3 text-sm">
                  <p className="text-xs text-muted-foreground">
                    진입 {formatDateTime(t.entry_time)} · {Math.round(t.entry_price).toLocaleString()}원 ×{' '}
                    {t.entry_qty}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    청산 {formatDateTime(t.exit_time)} · {Math.round(t.exit_price).toLocaleString()}원 ×{' '}
                    {t.exit_qty}
                  </p>
                  <p className="mt-1">
                    {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)}) · {fmtCloseReason(t.close_reason)}
                  </p>
                </div>
              ))}
            </div>
          </>
```

- [ ] **Step 3: dev 서버에서 확인**

Playwright MCP로 실거래 이력이 있는 매매일지 전략 상세를 375×812에서 열어(매매일지 페이지 → 전략 카드 클릭 → 상세 펼침) 비교 카드 4개와 매매일지 카드 리스트가 보이고 표는 숨겨지는지 확인. 1280×800에서는 기존 표가 그대로 보이는지 확인.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/JournalStrategyDetail.tsx
git commit -m "feat: 매매일지 상세에 모바일 카드 뷰 추가"
```

---

### Task 8: 라이브 전략 / 매매일지 페이지 — 터치 타겟 조정

**Files:**
- Modify: `frontend/components/LiveStrategiesPage.tsx`
- Modify: `frontend/components/JournalPage.tsx`

**Interfaces:**
- 소비: 없음.
- 생산: 없음.

- [ ] **Step 1: `LiveStrategiesPage`의 액션 버튼에 모바일 전용 최소 높이 부여**

`frontend/components/LiveStrategiesPage.tsx`에서 `<Button size="sm" ...>`로 시작하는 6개 액션 버튼(승인/취소/일시정지/중지/재개/중지, 94~123번째 줄)마다 `className="max-md:min-h-9"`를 추가한다. 예를 들어:

```tsx
                    <Button size="sm" disabled={pendingId === s.id} onClick={() => runAction(s.id, approveLiveStrategy)}>
                      승인
                    </Button>
```

를

```tsx
                    <Button
                      size="sm"
                      className="max-md:min-h-9"
                      disabled={pendingId === s.id}
                      onClick={() => runAction(s.id, approveLiveStrategy)}
                    >
                      승인
                    </Button>
```

와 같이 바꾼다. 나머지 5개 버튼(취소/일시정지/중지×2/재개)도 동일하게 `className="max-md:min-h-9"`를 추가한다. 버튼 컨테이너(93번째 줄 `<div className="flex flex-wrap gap-2 pt-2">`)의 `gap-2`는 그대로 둔다(이미 8px 간격 확보).

- [ ] **Step 2: `JournalPage`의 새로고침 버튼에 동일하게 적용**

`frontend/components/JournalPage.tsx` 77번째 줄:

```tsx
        <Button size="sm" variant="outline" disabled={loading} onClick={refresh}>
          새로고침
        </Button>
```

를 다음으로 교체:

```tsx
        <Button size="sm" variant="outline" className="max-md:min-h-9" disabled={loading} onClick={refresh}>
          새로고침
        </Button>
```

- [ ] **Step 3: dev 서버에서 확인**

Playwright MCP로 `/live-strategies`, `/journal`을 375×812로 열어 버튼들이 이전보다 세로로 더 여유 있게(약 36px 높이) 보이는지 확인. 1280×800에서는 버튼 높이가 기존과 동일한지(`max-md:` 접두사이므로 `md:` 이상에서는 미적용) 확인.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/LiveStrategiesPage.tsx frontend/components/JournalPage.tsx
git commit -m "style: 라이브 전략/매매일지 액션 버튼 모바일 터치 영역 확대"
```

---

### Task 9: 보조 페이지 — 가로 스크롤 방지 감사 및 수정

**Files:**
- Modify: `frontend/components/PortSetupForm.tsx:173`
- Modify(필요 시): `frontend/app/grid-search/*`, `frontend/components/GridSearchPage.tsx`, `frontend/components/GridSearchForm.tsx`, `frontend/components/GridSearchHistory.tsx`, `frontend/app/analysis/page.tsx`, `frontend/components/AnalysisSidebarView.tsx`, `frontend/components/SegmentSizeTable.tsx`, `frontend/app/heatmap/page.tsx`, `frontend/app/guide/page.tsx`, `frontend/components/IndicatorGuideView.tsx`, `frontend/app/ranking/page.tsx`, `frontend/app/model-accuracy/page.tsx`

**Interfaces:**
- 소비: Task 1의 `overflow-x-hidden` 안전망(페이지가 깨지더라도 스크롤은 막아준다 — 이 작업은 "안 보기 좋음"이 아니라 "실제로 요소가 화면 밖으로 잘리는" 문제를 잡는다).
- 생산: 없음.

- [ ] **Step 1: 알려진 문제 수정 — `PortSetupForm`의 조건 빌더 2열 그리드**

`frontend/components/PortSetupForm.tsx` 173번째 줄:

```tsx
        <div className="grid grid-cols-2 gap-4">
```

를 다음으로 교체:

```tsx
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
```

- [ ] **Step 2: 나머지 보조 페이지를 375px에서 실측**

Playwright MCP로 아래 7개 URL을 각각 375×812로 열고 `document.documentElement.scrollWidth <= window.innerWidth`를 확인한다: `/`(루트, 조건 빌더 포함), `/grid-search`, `/analysis`, `/heatmap`, `/guide`, `/ranking`, `/model-accuracy`.

`false`가 나오는 페이지가 있으면 `browser_evaluate`로 다음을 실행해 어떤 요소가 뷰포트보다 넓은지 찾는다:

```js
() => Array.from(document.querySelectorAll('*'))
  .filter((el) => el.scrollWidth > document.documentElement.clientWidth)
  .map((el) => ({ tag: el.tagName, cls: el.className, w: el.scrollWidth }))
  .slice(0, 10)
```

찾은 요소에 대해 다음 패턴 중 해당하는 것을 적용한다(모두 `md:` 이상에서는 영향 없도록 작성):

- 고정 `grid-cols-N`(2 이상) → `grid-cols-1 sm:grid-cols-N` 또는 `grid-cols-2 md:grid-cols-N`로 완화.
- 고정 `w-[Npx]`/`min-w-[Npx]` → `w-full max-w-full sm:w-[Npx] sm:max-w-none`.
- 가로 배치 `flex` 컨테이너에 `flex-wrap`이 없으면 추가.
- 이미 `components/ui/table.tsx`가 제공하는 `overflow-x-auto` 래퍼 안에서 스크롤되는 표(예: 히트맵, Grid Search 이력)는 페이지 자체가 넘치지 않으면 수정하지 않는다(표 내부 가로 스크롤은 허용된 UX).

발견한 수정 사항을 위 "Files" 목록의 해당 파일에 반영하고, 반영할 때마다 같은 페이지를 다시 열어 `scrollWidth <= innerWidth`가 `true`가 될 때까지 반복한다.

- [ ] **Step 3: 1280×800에서 회귀 확인**

수정한 파일이 있다면 해당 페이지를 1280×800으로 열어 기존과 시각적으로 동일한지 스크린샷으로 확인한다.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/PortSetupForm.tsx
# Step 2에서 추가로 수정한 파일이 있다면 함께 add
git commit -m "fix: 보조 페이지 375px 가로 스크롤 방지"
```

---

### Task 10: 전체 브레이크포인트 교차 검증 (최종 QA)

**Files:**
- 없음(코드 변경 없음, 검증 전용 작업). 문제 발견 시 해당 원인 파일을 수정한다.

**Interfaces:**
- 소비: Task 1~9의 모든 결과물.
- 생산: 없음.

- [ ] **Step 1: 우선순위 페이지 3개 구간 스크린샷**

Playwright MCP로 다음 페이지들을 375×812 / 768×1024 / 1280×800 세 구간에서 각각 열고 스크린샷을 남긴다: `/backtests`(목록), `/backtests/<runId>`(상세, 거래 내역 있는 run), `/live-strategies`, `/journal`(요약 + 전략 상세 펼친 상태), 그리고 네비게이션(375px에서 드로어 연 상태 포함).

- [ ] **Step 2: 가로 스크롤 일괄 확인**

위 각 URL, 각 구간에서 `document.documentElement.scrollWidth <= window.innerWidth`가 모두 `true`인지 확인한다. 하나라도 `false`면 Task 9 Step 2와 동일한 방법으로 원인 요소를 찾아 수정하고 재확인한다.

- [ ] **Step 3: PC 회귀 확인**

1280×800 스크린샷을 이번 작업 시작 전(Task 1 이전) 상태와 비교해, 레이아웃·여백·폰트 크기가 육안으로 동일한지 확인한다(깃 diff 상으로도 `md:` 미만 전용 클래스만 추가되었고 `md:` 이상 클래스는 기존 값 그대로인지 각 파일의 diff를 다시 훑어 확인).

- [ ] **Step 4: 최종 커밋(필요 시)**

Task 9 Step 2~3에서 추가 수정이 있었다면 이미 Task 9에서 커밋되었을 것이므로, 이 작업 자체는 보통 커밋할 코드 변경이 없다. 만약 이 단계에서 새로 발견한 수정이 있다면:

```bash
git add <수정한 파일들>
git commit -m "fix: 최종 반응형 QA에서 발견한 잔여 이슈 수정"
```

---

## Self-Review 결과

- **스펙 커버리지:** 스펙(§1~§7)의 모든 섹션에 대응하는 작업이 있다 — §1 공통 셸→Task 2, §2 백테스트 목록→Task 4, §3 백테스트 상세→Task 3/5/6, §4 라이브전략·매매일지→Task 7/8, §5 터치타겟→Task 4/6/8 내 `max-md:min-h-9` 처리, §6 보조 페이지→Task 9, §7 테스트 방법→각 작업의 검증 스텝 + Task 10. 스펙의 "영향받는 파일" 목록도 모두 어느 작업에서 다뤄지는지 확인됨(Task 1의 `globals.css` 포함).
- **플레이스홀더 스캔:** "TBD"/"추후"/"적절히 처리" 류의 표현 없음. Task 9의 감사 스텝은 사전에 알 수 없는 대상(런타임에 실측)이라 절차와 구체적 수정 패턴 목록, 그리고 이미 확인된 구체적 수정 1건(PortSetupForm)을 명시해 placeholder가 아니게 작성함.
- **타입/시그니처 일관성:** `BacktestRunCard` props(`run`/`marketName`/`selected`/`onToggleSelected`)가 Task 4 Step 3의 호출부와 일치. `buildCopyHref`/`MobileNavStep`/`isActive`의 import 경로가 정의 파일과 일치. `TradeCard`가 쓰는 `Trade` 타입은 Task 6 Step 1에서 import 추가함.
