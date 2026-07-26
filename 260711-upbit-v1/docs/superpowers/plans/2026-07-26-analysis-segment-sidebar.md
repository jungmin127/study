# 분석 탭 세그먼트 사이드바+테이블화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/analysis` 페이지를 좌측 사이드바(세그먼트(규모)/세그먼트(섹터)) + 우측 콘텐츠 레이아웃으로 바꾸고, 세그먼트(규모)의 코인 리스트를 그룹별 고정 높이 스크롤 테이블로 전환하면서 실시간 현재가/등락률/거래대금을 추가한다.

**Architecture:** `CoinSelect.tsx`의 비공개 포맷 함수를 공용 파일로 추출 → 그 위에 신규 `SegmentSizeTable.tsx`(테이블) 작성 → 신규 `AnalysisSidebarView.tsx`(사이드바+콘텐츠 전환)로 감싸고 `analysis/page.tsx`가 세그먼트 데이터와 실시간 마켓 데이터를 조인해서 내려준다.

**Tech Stack:** Next.js 14 (App Router) / TypeScript / Tailwind v4 / shadcn Table / lucide-react

**참고 스펙:** `docs/superpowers/specs/2026-07-26-analysis-segment-sidebar-design.md`

## Global Constraints

- 백엔드(`backend/`, `engine/`) 변경 없음 — 프론트엔드만 수정한다.
- 새 UI 라이브러리를 추가하지 않는다.
- 이 프론트엔드에는 자동화된 테스트 러너가 없다. 각 태스크의 검증은 `npm run dev` 구동 후 Playwright(또는 브라우저)로 실제 렌더링/콘솔 에러 유무를 확인하는 방식으로 한다.
- `npm run build`를 `npm run dev`가 떠 있는 상태에서 실행하지 않는다(`.next` 오염 이슈) — 컴파일 확인은 `npx tsc --noEmit`으로 한다.
- `CoinSelect.tsx`의 동작(정렬, 검색, 표시 포맷, 색상)은 이번 작업으로 전혀 변경되지 않아야 한다 — 포맷 함수는 위치만 옮기고 로직은 그대로 유지한다.
- 세그먼트(섹터)는 여전히 placeholder로 남긴다 — 이번 작업 범위 아님.

---

## Task 1: 포맷 함수 공용화 (`lib/market-format.ts`)

**Files:**
- Create: `frontend/lib/market-format.ts`
- Modify: `frontend/components/CoinSelect.tsx:1-53`

**Interfaces:**
- Produces: `changeColorClass(rate: number | null): string`, `formatPrice(price: number | null): string`, `formatChangeRate(rate: number | null): string`, `formatChangePrice(price: number | null): string`, `formatTradePrice24h(value: number | null): string` — 모두 `frontend/lib/market-format.ts`에서 export. Task 2(`SegmentSizeTable.tsx`)가 이 5개 함수를 그대로 소비한다.

- [ ] **Step 1: `lib/market-format.ts` 신규 작성**

`frontend/components/CoinSelect.tsx`의 26~53행(비공개 함수 5개)을 그대로 옮겨 `export`만 추가:

```ts
export function changeColorClass(rate: number | null): string {
  if (!rate) return 'text-foreground';
  return rate > 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}

export function formatPrice(price: number | null): string {
  if (price === null) return '-';
  if (price === 0) return '0';
  if (price >= 100) return Math.round(price).toLocaleString('ko-KR');
  const magnitude = Math.floor(Math.log10(Math.abs(price)));
  const decimals = Math.max(0, 2 - magnitude);
  return price.toLocaleString('ko-KR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

export function formatChangeRate(rate: number | null): string {
  if (rate === null) return '-';
  return `${(Math.abs(rate) * 100).toFixed(2)}%`;
}

export function formatChangePrice(price: number | null): string {
  if (price === null) return '-';
  return formatPrice(Math.abs(price));
}

export function formatTradePrice24h(value: number | null): string {
  if (value === null) return '-';
  return `${Math.round(value / 1_000_000).toLocaleString('ko-KR')}백만`;
}
```

- [ ] **Step 2: `CoinSelect.tsx`에서 로컬 정의 제거하고 import로 교체**

`frontend/components/CoinSelect.tsx`의 1~9행(import 블록)을 아래로 교체:

```tsx
'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react';
import type { Market } from '@/lib/types/eda';
import { getMarkets } from '@/lib/api/eda';
import { INPUT_CLASS } from '@/lib/ui-classes';
import { changeColorClass, formatChangePrice, formatChangeRate, formatPrice, formatTradePrice24h } from '@/lib/market-format';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
```

그리고 26~53행(기존 5개 함수의 로컬 정의)을 완전히 삭제한다. 파일의 나머지 부분(`sortMarkets`, `CoinSelectProps`, `CoinSelect` 컴포넌트 본문)은 전혀 건드리지 않는다.

- [ ] **Step 3: 컴파일 확인**

```bash
cd frontend
npx tsc --noEmit
```

Expected: 에러 없음

- [ ] **Step 4: 브라우저 검증 — `CoinSelect` 동작 동일 확인**

```bash
cd frontend && npm run dev
```

Playwright로 `/` 접속 → "코인 선택" 트리거 클릭 → 검색("비트코인"), 정렬(전일대비/거래대금 토글), 코인 선택이 이전과 동일하게 동작하는지, 가격/등락률 표시 포맷이 동일한지(색상 포함) 확인. 콘솔 에러 0건 확인.

- [ ] **Step 5: 커밋**

```bash
git add frontend/lib/market-format.ts frontend/components/CoinSelect.tsx
git commit -m "refactor: CoinSelect의 시세 포맷 함수를 lib/market-format.ts로 추출"
```

---

## Task 2: `SegmentSizeTable.tsx` 신규 (테이블 + 실시간 시세 컬럼)

**Files:**
- Create: `frontend/components/SegmentSizeTable.tsx`
- Delete: `frontend/components/SegmentSizeCard.tsx`

**Interfaces:**
- Consumes: Task 1의 `changeColorClass`/`formatPrice`/`formatChangeRate`/`formatTradePrice24h`(`@/lib/market-format`).
- Produces: `export interface SegmentRow extends SegmentSizeEntry { price: number | null; change_rate: number | null; change_price: number | null; trade_price_24h: number | null }`, `export default function SegmentSizeTable({ rows }: { rows: SegmentRow[] })` — Task 3(`AnalysisSidebarView.tsx`)이 이 타입과 컴포넌트를 그대로 소비.

- [ ] **Step 1: `SegmentSizeTable.tsx` 작성**

```tsx
import { AlertTriangle } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { changeColorClass, formatChangeRate, formatPrice, formatTradePrice24h } from '@/lib/market-format';
import type { SegmentSizeEntry } from '@/lib/types/eda';

export interface SegmentRow extends SegmentSizeEntry {
  price: number | null;
  change_rate: number | null;
  change_price: number | null;
  trade_price_24h: number | null;
}

const SEGMENT_ORDER: SegmentRow['segment'][] = ['large', 'mid', 'junk'];
const SEGMENT_LABELS: Record<SegmentRow['segment'], string> = {
  large: '대형주',
  mid: '중형주',
  junk: '잡주',
};

function formatVolatility(value: number | null): string {
  if (value === null) return '-';
  return `${(value * 100).toFixed(2)}%`;
}

export function groupBySegment(rows: SegmentRow[]): { segment: SegmentRow['segment']; rows: SegmentRow[] }[] {
  return SEGMENT_ORDER.map((segment) => ({
    segment,
    rows: rows.filter((r) => r.segment === segment),
  }));
}

export default function SegmentSizeTable({ rows }: { rows: SegmentRow[] }) {
  if (rows.length === 0) {
    return <p className="text-muted-foreground">배치 실행 중입니다. 잠시 후 새로고침해 주세요.</p>;
  }

  return (
    <div className="flex flex-col gap-6">
      {groupBySegment(rows).map(({ segment, rows: group }) => (
        <div key={segment}>
          <p className="mb-2 text-sm font-semibold">
            {SEGMENT_LABELS[segment]} ({group.length})
          </p>
          <div className="max-h-80 overflow-y-auto rounded-md border">
            <Table>
              <TableHeader className="sticky top-0 z-10 bg-background">
                <TableRow>
                  <TableHead>코인</TableHead>
                  <TableHead className="text-right">현재가</TableHead>
                  <TableHead className="text-right">전일대비등락률</TableHead>
                  <TableHead className="text-right">거래대금</TableHead>
                  <TableHead className="text-right">변동성(30일)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {group.map((r) => (
                  <TableRow key={r.market}>
                    <TableCell>
                      {r.korean_name}
                      {r.is_caution && (
                        <span className="ml-2 inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                          <AlertTriangle className="size-3.5" />
                          유의종목
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{formatPrice(r.price)}</TableCell>
                    <TableCell className={`text-right tabular-nums ${changeColorClass(r.change_rate)}`}>
                      {formatChangeRate(r.change_rate)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatTradePrice24h(r.trade_price_24h)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {formatVolatility(r.volatility_30d)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      ))}
    </div>
  );
}
```

`sticky top-0`가 `max-h-80 overflow-y-auto` 컨테이너 안에서 정상 동작하는지는 Step 3에서 실제로 확인한다(과거 heatmap 작업에서 `Table`의 내부 `overflow-x-auto` 래퍼가 sticky를 방해한 전례가 있음 — 여기서는 그룹별 `max-h-80` 컨테이너 자체가 스크롤 컨테이너이므로 다른 상황이지만, 실제로 스크롤해서 헤더가 고정되는지 반드시 눈으로 확인).

- [ ] **Step 2: `SegmentSizeCard.tsx` 삭제**

```bash
rm frontend/components/SegmentSizeCard.tsx
```

(이 시점에는 아직 아무도 `SegmentSizeTable`을 import하지 않으므로 `analysis/page.tsx`가 삭제된 `SegmentSizeCard`를 계속 참조해 빌드가 깨질 수 있다 — Task 3에서 함께 교체되므로, 이 태스크의 `tsc`/브라우저 확인은 `analysis/page.tsx`가 아직 `SegmentSizeCard`를 참조하는 상태에서 실패하는 게 정상이다. 대신 `SegmentSizeTable.tsx` 파일 자체만 독립적으로 타입 체크한다.)

- [ ] **Step 3: `SegmentSizeTable.tsx` 단독 타입 체크**

```bash
cd frontend
npx tsc --noEmit 2>&1 | grep -v "analysis/page.tsx"
```

Expected: `SegmentSizeTable.tsx` 관련 에러 없음. (`analysis/page.tsx`가 삭제된 `SegmentSizeCard`를 참조해서 나는 에러는 Task 3에서 해결되므로 이 단계에서는 무시한다.)

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/SegmentSizeTable.tsx
git rm frontend/components/SegmentSizeCard.tsx
git commit -m "feat: SegmentSizeTable 추가 (SegmentSizeCard 대체, 테이블+실시간 시세 컬럼)"
```

(이 커밋 시점에는 `analysis/page.tsx`가 아직 옛 `SegmentSizeCard`를 참조해 컴파일이 깨진 중간 상태다 — Task 3에서 바로 이어서 고친다. 브라우저 최종 검증은 Task 3에서 수행한다.)

---

## Task 3: `AnalysisSidebarView.tsx` + `analysis/page.tsx` 재작성 (사이드바 + 데이터 조인)

**Files:**
- Create: `frontend/components/AnalysisSidebarView.tsx`
- Modify: `frontend/app/analysis/page.tsx` (전체 교체)

**Interfaces:**
- Consumes: Task 2의 `SegmentSizeTable`(기본 export), `SegmentRow`(named export) — `frontend/lib/api/eda.ts`의 `getMarkets(): Promise<Market[]>`, `getSegmentSizeAnalysis(): Promise<SegmentSizeEntry[]>` (기존, 변경 없음).

- [ ] **Step 1: `AnalysisSidebarView.tsx` 작성**

```tsx
'use client';

import { useState } from 'react';
import { BarChart3, PieChart } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import SegmentSizeTable, { type SegmentRow } from '@/components/SegmentSizeTable';

type Section = 'size' | 'sector';

const SECTIONS: { key: Section; label: string; icon: typeof BarChart3 }[] = [
  { key: 'size', label: '세그먼트(규모)', icon: BarChart3 },
  { key: 'sector', label: '세그먼트(섹터)', icon: PieChart },
];

export default function AnalysisSidebarView({ segmentSizeRows }: { segmentSizeRows: SegmentRow[] }) {
  const [section, setSection] = useState<Section>('size');

  return (
    <div className="flex gap-6">
      <nav className="flex w-44 shrink-0 flex-col gap-1">
        {SECTIONS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setSection(key)}
            className={
              section === key
                ? 'flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-sm font-medium text-foreground'
                : 'flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground'
            }
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </nav>

      <div className="min-w-0 flex-1">
        {section === 'size' ? (
          <SegmentSizeTable rows={segmentSizeRows} />
        ) : (
          <Card>
            <CardContent className="pt-4">
              <p className="text-muted-foreground">준비 중입니다.</p>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: `analysis/page.tsx` 전체 교체**

```tsx
import { getMarkets, getSegmentSizeAnalysis } from '@/lib/api/eda';
import AnalysisSidebarView from '@/components/AnalysisSidebarView';

export default async function AnalysisPage() {
  const [segmentSizeEntries, markets] = await Promise.all([
    getSegmentSizeAnalysis(),
    getMarkets(),
  ]);

  const marketByCode = new Map(markets.map((m) => [m.market, m]));
  const segmentSizeRows = segmentSizeEntries.map((entry) => {
    const market = marketByCode.get(entry.market);
    return {
      ...entry,
      price: market?.price ?? null,
      change_rate: market?.change_rate ?? null,
      change_price: market?.change_price ?? null,
      trade_price_24h: market?.trade_price_24h ?? null,
    };
  });

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">분석</h1>
      <AnalysisSidebarView segmentSizeRows={segmentSizeRows} />
    </div>
  );
}
```

- [ ] **Step 3: 컴파일 확인**

```bash
cd frontend
npx tsc --noEmit
```

Expected: 에러 없음 (Task 2에서 남아있던 `SegmentSizeCard` 참조 에러도 이제 해결됨)

- [ ] **Step 4: 브라우저 검증**

```bash
cd frontend && npm run dev
```

백엔드가 안 떠 있으면 시작:
```bash
cd /c/Users/jungm/personal/study/260711-upbit-v1 && uvicorn backend.main:app --reload --port 8000
```

Playwright로 `/analysis` 접속:
1. 좌측에 "세그먼트(규모)"/"세그먼트(섹터)" 두 항목이 아이콘과 함께 보이는지 확인
2. 기본 선택은 "세그먼트(규모)"이고, 대형주/중형주/잡주 3개 테이블이 각각 `max-h-80` 안에서 스크롤되는지(페이지 자체 길이가 과거보다 훨씬 짧아졌는지) 확인
3. 각 코인 행에 현재가/전일대비등락률(색상 포함)/거래대금/변동성이 모두 채워져 있는지 확인 — 값이 `/`(백테스트 설정)의 코인선택에서 같은 코인을 봤을 때와 동일한지 하나 정도 대조
4. "세그먼트(섹터)" 클릭 시 우측이 "준비 중입니다." Card로 바뀌고, 다시 "세그먼트(규모)" 클릭 시 테이블이 그대로 복원되는지(로컬 상태 유지) 확인
5. 유의종목이 있는 코인에 경고 아이콘이 보이는지 확인
6. 콘솔 에러 0건 확인

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/AnalysisSidebarView.tsx frontend/app/analysis/page.tsx
git commit -m "feat: 분석 탭에 좌측 사이드바 추가 및 세그먼트(규모) 실시간 시세 조인"
```

---

## Self-Review 결과

- **스펙 커버리지**: 사이드바+콘텐츠 레이아웃(Task 3), 테이블+그룹별 고정 높이 스크롤(Task 2), 실시간 현재가/등락률/거래대금 조인(Task 3), 변동성 컬럼 유지(Task 2), 포맷 함수 공용화(Task 1) 모두 매핑됨.
- **Placeholder 스캔**: 없음. 단, Task 2의 커밋 시점에 `analysis/page.tsx`가 일시적으로 삭제된 `SegmentSizeCard`를 참조해 컴파일이 깨지는 중간 상태가 되는데, 이는 실제 결함이 아니라 3-태스크 순차 실행의 정상적인 중간 상태이며 Task 3 Step 3에서 즉시 해결됨을 명시했다.
- **타입/시그니처 일관성**: `SegmentRow`(Task 2에서 정의) 필드명이 `analysis/page.tsx`의 조인 로직(Task 3)에서 만드는 객체 필드명과 정확히 일치(`price`/`change_rate`/`change_price`/`trade_price_24h`). `SegmentSizeTable`의 `rows` prop과 `AnalysisSidebarView`의 `segmentSizeRows` prop 이름은 다르지만 타입은 동일(`SegmentRow[]`) — 의도된 것으로, prop 이름 불일치가 실제 컴파일 문제를 일으키지 않음을 확인.
- **대상 파일 목록**: `frontend/lib/market-format.ts`(신규), `frontend/components/CoinSelect.tsx`, `frontend/components/SegmentSizeTable.tsx`(신규), `frontend/components/SegmentSizeCard.tsx`(삭제), `frontend/components/AnalysisSidebarView.tsx`(신규), `frontend/app/analysis/page.tsx`.
