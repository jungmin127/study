# 추세 세그먼트 → Grid Search 복사 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** "세그먼트 - 추세기반" 탭의 세그먼트 표에서, 각 행의 패턴 셀에 있는 아이콘 버튼을 누르면 해당 코인·구간(시작일~종료일)이 프리필된 `/grid-search` 탭으로 이동한다.

**Architecture:** `TrendSegmentTable.tsx`(순수 표시 컴포넌트)에 `market` prop과 쿼리스트링 빌더 함수를 추가하고, 부모 `TrendSegmentView.tsx`가 현재 선택된 코인 코드를 내려준다. `/grid-search` 페이지는 이미 `market`/`start`/`end` 쿼리파라미터를 읽어 폼을 프리필하도록 구현돼 있으므로(변경 없음), `next/link`로 그 URL을 열기만 하면 된다.

**Tech Stack:** Next.js 14 (App Router), React, TypeScript, Tailwind, base-ui 기반 `Button`/`Table` 컴포넌트, `lucide-react` 아이콘.

## Global Constraints

- 쿼리파라미터는 `market`/`start`/`end` 세 개만 채운다. `timeframe`/`capital`/`topN`은 넘기지 않는다 (스펙: `docs/superpowers/specs/2026-08-17-trend-segment-grid-search-copy-design.md`).
- 복사 버튼은 아이콘 전용(텍스트 라벨 없음)이며 `aria-label`을 반드시 붙인다.
- 이동은 `next/link`를 이용한 클라이언트 사이드 내비게이션이며 새 탭이 아니라 같은 탭에서 화면 전환한다.
- 프론트엔드에는 단위 테스트 프레임워크가 구성돼 있지 않다(스펙에 명시) — 이 변경도 자동화 테스트를 새로 추가하지 않고 브라우저 수동 확인으로 검증한다.

---

### Task 1: 세그먼트 표에 그리드서치 복사 버튼 배선

**Files:**
- Modify: `frontend/components/TrendSegmentTable.tsx`
- Modify: `frontend/components/TrendSegmentView.tsx:92`

**Interfaces:**
- Consumes: `TrendSegment` 타입(`frontend/lib/types/eda.ts:98-107`, 필드: `start_date`, `end_date`, `days`, `return_pct`, `trend`, `first_half_trend`, `second_half_trend`, `pattern_label` — 이미 존재, 변경 없음). `Market`/`selectedMarket` — `TrendSegmentView.tsx`에 이미 존재(`useState(markets[0]?.market ?? '')`).
- Produces: `TrendSegmentTable`가 새로 받는 prop `market: string` — `TrendSegmentView.tsx`가 렌더링 시 넘겨준다.

- [ ] **Step 1: `TrendSegmentTable.tsx`에 필요한 import 추가**

`frontend/components/TrendSegmentTable.tsx` 최상단 import 블록을 다음과 같이 바꾼다(기존 4줄을 아래 6줄로 교체):

```tsx
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Copy } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import type { TrendSegment } from '@/lib/types/eda';
```

- [ ] **Step 2: 쿼리스트링 빌더 함수 추가**

`TREND_RANK`/`patternRank` 함수 정의(`frontend/components/TrendSegmentTable.tsx:20-24`) 바로 아래, `type SortKey` 선언(현재 26번째 줄) 바로 위에 다음 함수를 추가한다:

```tsx
function buildGridSearchHref(market: string, seg: TrendSegment): string {
  const params = new URLSearchParams({
    market,
    start: seg.start_date,
    end: seg.end_date,
  });
  return `/grid-search?${params.toString()}`;
}
```

- [ ] **Step 3: 컴포넌트 시그니처에 `market` prop 추가**

`export default function TrendSegmentTable({ segments }: { segments: TrendSegment[] }) {` (현재 29번째 줄)를 다음으로 교체:

```tsx
export default function TrendSegmentTable({ segments, market }: { segments: TrendSegment[]; market: string }) {
```

- [ ] **Step 4: 패턴 셀에 복사 버튼 추가**

현재 패턴 셀:

```tsx
              <TableCell>{seg.pattern_label}</TableCell>
```

이것을 다음으로 교체한다:

```tsx
              <TableCell>
                <div className="flex items-center gap-1.5">
                  <span>{seg.pattern_label}</span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    nativeButton={false}
                    role="link"
                    aria-label="그리드서치로 복사"
                    title="그리드서치로 복사"
                    render={<Link href={buildGridSearchHref(market, seg)} />}
                  >
                    <Copy className="size-3.5" />
                  </Button>
                </div>
              </TableCell>
```

- [ ] **Step 5: `TrendSegmentView.tsx`에서 `market` prop 전달**

`frontend/components/TrendSegmentView.tsx:92`의 다음 줄:

```tsx
          <TrendSegmentTable segments={data.segments} />
```

을 다음으로 교체:

```tsx
          <TrendSegmentTable segments={data.segments} market={selectedMarket} />
```

- [ ] **Step 6: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 (기존에 에러가 있었다면 이 변경으로 새로 늘어나지 않았는지만 확인).

- [ ] **Step 7: 개발 서버로 브라우저 수동 확인**

1. 저장소 루트에서 `uvicorn backend.main:app --port 8000` 실행 (백엔드).
2. `frontend`에서 `npm run dev` 실행 (프론트엔드, `localhost:3000`).
3. `http://localhost:3000/analysis` 접속 후 "세그먼트 - 추세기반" 탭으로 이동, 코인 하나 선택.
4. 세그먼트 표가 뜨면 아무 행의 패턴 셀 옆 복사 아이콘을 확인 — 아이콘만 보이고 hover 시 툴팁("그리드서치로 복사")이 뜨는지 확인.
5. 아이콘 클릭 → `http://localhost:3000/grid-search?market=<코인마켓코드>&start=<시작일>&end=<종료일>`로 이동하는지 확인.
6. Grid Search 폼에 코인/시작일/종료일이 채워져 있고, timeframe은 "1시간", 운용자금은 1,000,000, 상위N개는 20(둘 다 페이지 기본값)인지 확인.
7. 서로 다른 두 행에서 각각 복사를 눌러 시작일/종료일이 행마다 올바르게 바뀌는지 확인.

Expected: 위 6단계 모두 기대대로 동작.

- [ ] **Step 8: 커밋**

```bash
git add frontend/components/TrendSegmentTable.tsx frontend/components/TrendSegmentView.tsx
git commit -m "feat: 추세 세그먼트 패턴에서 Grid Search로 코인/기간 복사 버튼 추가"
```

---

## 완료 조건

- Task 1의 모든 단계 완료.
- 브라우저 수동 확인(Step 7)에서 6개 확인 항목 전부 통과.
