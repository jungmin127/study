# Grid Search 요청 이력 테이블 재설계 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/grid-search` 탭의 "요청 이력"을 job당 3줄 카드에서, 코인/봉타입으로 필터링하고
실행시각·기간 시작일·1위 수익률로 정렬 가능한 한 줄짜리 표로 바꾼다.

**Architecture:** 백엔드 변경 없음(기존 `GET /api/v1/grid-search/jobs`가 필요한 필드를 이미
전부 내려줌). 기존 `BacktestRunsTable.tsx`가 쓰는 `BacktestCoinFilter` 컴포넌트를 코인
필터로 재사용하기 위해 `koreanName`을 optional로 일반화하고, 그리드서치 결과 title
문자열("[Grid] 매수 ... / 매도 ...")을 파싱하는 순수 헬퍼를 새로 추가한 뒤,
`GridSearchHistory.tsx`를 표 형태로 전면 재작성한다.

**Tech Stack:** Next.js 14(App Router) + TypeScript, 기존 shadcn 스타일 컴포넌트
(`@/components/ui/table`, `@/components/ui/select`), 새 의존성 없음.

## Global Constraints

- 스펙: `docs/superpowers/specs/2026-08-04-grid-search-history-table-design.md`(사용자 승인됨).
- 백엔드(`backend/main.py`, `engine/cache.py`) 변경 없음 — 이 플랜은 순수 프론트엔드.
- `scripts/grid_search.py`가 만드는 `title` 문자열 형식(`"[Grid] 매수 ... / 매도 ..."`)은
  건드리지 않는다 — 프론트엔드 렌더링 시점에만 파싱해서 표시를 바꾼다.
- 프론트엔드 검증은 `npx tsc --noEmit`과 `npx eslint <파일>`만 사용한다. **`npm run build`는
  쓰지 않는다** — 이 저장소는 `npm run dev`가 이미 떠 있는 상태에서 `npm run build`를 돌리면
  `.next`가 깨지는 알려진 문제가 있다(memory: upbit-frontend-tailwind-opacity-gotcha).
- 이 저장소에는 프론트엔드 자동 단위테스트 러너가 없다 — 검증은 `tsc`/`eslint`/수동 브라우저
  확인으로 한다(기존 관례).
- Task 1은 `frontend/components/BacktestRunsTable.tsx`(기존 호출부)의 동작을 절대 바꾸지
  않아야 한다 — `koreanName`을 항상 넘기는 기존 호출은 그대로 동일하게 동작해야 한다.
- 각 태스크는 서로 다른 파일을 다루므로 순서 의존성이 있다: Task 3은 Task 1(컴포넌트)과
  Task 2(헬퍼)의 결과물을 import하므로, Task 1·2가 먼저 끝나야 Task 3을 시작할 수 있다.

---

### Task 1: `BacktestCoinFilter`의 `koreanName`을 optional로 일반화

**Files:**
- Modify: `frontend/components/BacktestCoinFilter.tsx`

**Interfaces:**
- Consumes: 없음
- Produces: `CoinFilterOption.koreanName?: string`(기존 `string` → optional) — Task 3이
  `{ market: string }`만 넘기고 `koreanName`을 생략할 수 있게 된다.

**배경:** 이 컴포넌트는 현재 `BacktestRunsTable.tsx`에서만 쓰이고 `koreanName: string`(필수)을
요구한다. 그리드서치 job에는 한글명이 없다(스펙에서 시세 조회를 안 하기로 결정) — 이 컴포넌트를
그대로 재사용하려면 `koreanName`을 선택적으로 바꿔야 한다. 검색 필터 로직이 지금
`o.koreanName.toLowerCase()`를 무조건 호출하는데, `koreanName`이 `undefined`인 옵션이
들어오면 여기서 `TypeError`가 난다 — 이것도 같이 고쳐야 한다.

- [x] **Step 1: `CoinFilterOption` 타입을 optional로 변경**

`frontend/components/BacktestCoinFilter.tsx`의 아래 부분을:

```typescript
export interface CoinFilterOption {
  market: string;
  koreanName: string;
}
```

아래로 교체:

```typescript
export interface CoinFilterOption {
  market: string;
  koreanName?: string;
}
```

- [x] **Step 2: 검색 필터 로직에서 `koreanName`이 없을 때 안전하게 처리**

같은 파일의 아래 부분(`useMemo` 검색 필터)을:

```typescript
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) => o.koreanName.toLowerCase().includes(q) || o.market.replace('KRW-', '').toLowerCase().includes(q)
    );
  }, [options, query]);
```

아래로 교체:

```typescript
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(
      (o) =>
        (o.koreanName?.toLowerCase().includes(q) ?? false) ||
        o.market.replace('KRW-', '').toLowerCase().includes(q)
    );
  }, [options, query]);
```

- [x] **Step 3: 트리거(선택된 값 표시)에서 `koreanName`이 없으면 괄호를 생략**

같은 파일의 아래 부분을:

```tsx
        <span className="truncate text-sm">
          {selected ? (
            <>
              {selected.market} <span className="text-xs text-muted-foreground">({selected.koreanName})</span>
            </>
          ) : (
            <span className="text-muted-foreground">코인별 필터</span>
          )}
        </span>
```

아래로 교체:

```tsx
        <span className="truncate text-sm">
          {selected ? (
            <>
              {selected.market}
              {selected.koreanName && (
                <span className="text-xs text-muted-foreground"> ({selected.koreanName})</span>
              )}
            </>
          ) : (
            <span className="text-muted-foreground">코인별 필터</span>
          )}
        </span>
```

- [x] **Step 4: 목록 항목에서도 `koreanName`이 없으면 생략**

같은 파일의 아래 부분을:

```tsx
              <CommandItem
                key={o.market}
                value={o.market}
                onSelect={() => {
                  onChange(o.market);
                  setOpen(false);
                }}
                className={o.market === value ? 'bg-muted' : ''}
              >
                <span className="font-medium">{o.market}</span>
                <span className="ml-2 text-xs text-muted-foreground">{o.koreanName}</span>
              </CommandItem>
```

아래로 교체:

```tsx
              <CommandItem
                key={o.market}
                value={o.market}
                onSelect={() => {
                  onChange(o.market);
                  setOpen(false);
                }}
                className={o.market === value ? 'bg-muted' : ''}
              >
                <span className="font-medium">{o.market}</span>
                {o.koreanName && <span className="ml-2 text-xs text-muted-foreground">{o.koreanName}</span>}
              </CommandItem>
```

- [x] **Step 5: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/BacktestCoinFilter.tsx`
Expected: 에러 없음

- [x] **Step 6: 기존 호출부(`BacktestRunsTable.tsx`)가 여전히 정상 타입 체크되는지 확인**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음 — `BacktestRunsTable.tsx`는 항상 `koreanName`을 채워서 넘기므로(`marketNames[r.market] ?? r.market`),
`koreanName`이 optional이 돼도 그 호출부의 타입/동작은 바뀌지 않는다.

- [x] **Step 7: 커밋**

```bash
git add frontend/components/BacktestCoinFilter.tsx
git commit -m "refactor: BacktestCoinFilter의 koreanName을 optional로 일반화(그리드서치 재사용 대비)"
```

---

### Task 2: 그리드서치 결과 title 파싱 헬퍼 추가

**Files:**
- Create: `frontend/lib/grid-result-title.ts`

**Interfaces:**
- Consumes: 없음
- Produces: `parseGridResultTitle(title: string): ParsedGridResultTitle | null` — Task 3이
  표의 "1위 조건" 셀과 확장 행(2~N위)에서 이 함수를 사용해 "매수"/"매도"를 볼드 처리한다.
  `ParsedGridResultTitle = { buyRest: string; sellRest: string }`.

**배경:** `scripts/grid_search.py`(수정 대상 아님)가 저장하는 title은 항상
`"[Grid] 매수 {지표}{파라미터}{연산자}{임계값} / 매도 {지표}{파라미터}{연산자}{임계값}"`
형식이다. 이 문자열에서 `[Grid] ` 프리픽스를 떼고, "매수"/"매도" 뒤에 오는 나머지 부분만
뽑아내는 순수 함수를 만든다 — 렌더링 컴포넌트가 `<strong>매수</strong> {buyRest} / <strong>매도</strong>
{sellRest}` 형태로 조립할 수 있게.

- [x] **Step 1: 헬퍼 파일 작성**

`frontend/lib/grid-result-title.ts`(신규 파일):

```typescript
export interface ParsedGridResultTitle {
  buyRest: string;
  sellRest: string;
}

const GRID_PREFIX = '[Grid] ';
const BUY_SELL_PATTERN = /^매수\s+(.+?)\s+\/\s+매도\s+(.+)$/;

export function parseGridResultTitle(title: string): ParsedGridResultTitle | null {
  const withoutPrefix = title.startsWith(GRID_PREFIX) ? title.slice(GRID_PREFIX.length) : title;
  const match = withoutPrefix.match(BUY_SELL_PATTERN);
  if (!match) return null;
  return { buyRest: match[1], sellRest: match[2] };
}
```

- [x] **Step 2: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint lib/grid-result-title.ts`
Expected: 에러 없음

- [x] **Step 3: 수동으로 파싱 결과 확인**

이 프로젝트엔 프론트 유닛테스트 러너가 없으므로, `node`로 직접 동작을 확인한다(TypeScript를
그대로 실행할 수 없으니 로직만 옮겨 확인):

Run:
```bash
node -e "
const GRID_PREFIX = '[Grid] ';
const BUY_SELL_PATTERN = /^매수\s+(.+?)\s+\/\s+매도\s+(.+)\$/;
function parseGridResultTitle(title) {
  const withoutPrefix = title.startsWith(GRID_PREFIX) ? title.slice(GRID_PREFIX.length) : title;
  const match = withoutPrefix.match(BUY_SELL_PATTERN);
  if (!match) return null;
  return { buyRest: match[1], sellRest: match[2] };
}
console.log(parseGridResultTitle(\"[Grid] 매수 STOCH_K{'k_period': 10}<10 / 매도 TAKE_PROFIT_PCT{}>=5\"));
console.log(parseGridResultTitle('아무 형식도 아닌 문자열'));
"
```

Expected:
- 첫 번째 호출: `{ buyRest: "STOCH_K{'k_period': 10}<10", sellRest: 'TAKE_PROFIT_PCT{}>=5' }`
- 두 번째 호출: `null`

- [x] **Step 4: 커밋**

```bash
git add frontend/lib/grid-result-title.ts
git commit -m "feat: 그리드서치 결과 title에서 [Grid] 프리픽스 제거 + 매수/매도 파싱 헬퍼 추가"
```

---

### Task 3: `GridSearchHistory.tsx`를 필터·정렬 가능한 표로 재작성

**Files:**
- Modify: `frontend/components/GridSearchHistory.tsx` (전면 재작성)

**Interfaces:**
- Consumes: `CoinFilterOption`/`BacktestCoinFilter`(Task 1), `parseGridResultTitle`(Task 2),
  `TIMEFRAME_CODES`/`formatTimeframe`/`formatDateTime`(기존 `frontend/lib/format.ts`),
  `returnRateColor`(기존), `GridSearchJob`(기존 타입, 변경 없음)
- Produces: 없음(최상위 컴포넌트, `GridSearchPage.tsx`가 그대로 `jobs` prop만 넘김 — prop
  시그니처 불변이므로 `GridSearchPage.tsx`는 손대지 않는다)

**배경:** 스펙의 필터(코인/봉타입) + 표(상태/코인/봉타입/기간/실행시각/1위 조건/1위 수익률,
기간·실행시각·1위 수익률은 정렬 가능) + 행 클릭 확장(완료 job은 2~N위 결과, 실패 job은
에러 메시지)을 구현한다. `status='running'`인 job은 이 표에서 완전히 제외한다(진행률 카드가
따로 담당).

- [x] **Step 1: 파일 전체를 아래 내용으로 교체**

`frontend/components/GridSearchHistory.tsx`(전체 교체):

```tsx
'use client';

import { Fragment, useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronDown, ChevronRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import BacktestCoinFilter, { type CoinFilterOption } from '@/components/BacktestCoinFilter';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime, formatTimeframe, TIMEFRAME_CODES } from '@/lib/format';
import { parseGridResultTitle } from '@/lib/grid-result-title';
import type { GridSearchJob, GridSearchSavedResult } from '@/lib/types/eda';

const STATUS_LABEL: Record<GridSearchJob['status'], string> = {
  running: '진행중',
  completed: '완료',
  failed: '실패',
  canceled: '취소',
};

const STATUS_VARIANT: Record<GridSearchJob['status'], 'secondary' | 'default' | 'destructive' | 'outline'> = {
  running: 'secondary',
  completed: 'default',
  failed: 'destructive',
  canceled: 'outline',
};

const ALL_TIMEFRAMES = '__all__';

type SortKey = 'start' | 'started_at' | 'return_pct';
type SortDir = 'asc' | 'desc';

function sortValue(job: GridSearchJob, key: SortKey): string | number | null {
  if (key === 'start') return job.start;
  if (key === 'started_at') return job.started_at;
  return job.result_json?.[0]?.return_pct ?? null;
}

function sortJobs(list: GridSearchJob[], key: SortKey | null, dir: SortDir): GridSearchJob[] {
  if (!key) return list;
  const factor = dir === 'asc' ? 1 : -1;
  return [...list].sort((a, b) => {
    const av = sortValue(a, key);
    const bv = sortValue(b, key);
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}

function canExpand(job: GridSearchJob): boolean {
  if (job.status === 'failed' && !!job.error_message) return true;
  return (job.result_json?.length ?? 0) > 1;
}

function ResultTitle({ result }: { result: GridSearchSavedResult }) {
  const parsed = parseGridResultTitle(result.title);
  if (!parsed) return <>{result.title}</>;
  return (
    <>
      <strong>매수</strong> {parsed.buyRest} / <strong>매도</strong> {parsed.sellRest}
    </>
  );
}

interface GridSearchHistoryProps {
  jobs: GridSearchJob[];
}

export default function GridSearchHistory({ jobs }: GridSearchHistoryProps) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [coinFilter, setCoinFilter] = useState<string | null>(null);
  const [timeframeFilterValue, setTimeframeFilterValue] = useState<string>(ALL_TIMEFRAMES);
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const timeframeFilter = timeframeFilterValue === ALL_TIMEFRAMES ? null : timeframeFilterValue;

  const historyJobs = useMemo(() => jobs.filter((j) => j.status !== 'running'), [jobs]);

  const coinOptions = useMemo<CoinFilterOption[]>(() => {
    const seen = new Set<string>();
    const options: CoinFilterOption[] = [];
    for (const j of historyJobs) {
      if (!seen.has(j.market)) {
        seen.add(j.market);
        options.push({ market: j.market });
      }
    }
    return options.sort((a, b) => a.market.localeCompare(b.market));
  }, [historyJobs]);

  const timeframeOptions = useMemo(() => {
    const present = new Set(historyJobs.map((j) => j.timeframe));
    return TIMEFRAME_CODES.filter((tf) => present.has(tf));
  }, [historyJobs]);

  const filtered = useMemo(() => {
    return historyJobs.filter((j) => {
      if (coinFilter && j.market !== coinFilter) return false;
      if (timeframeFilter && j.timeframe !== timeframeFilter) return false;
      return true;
    });
  }, [historyJobs, coinFilter, timeframeFilter]);

  const sorted = useMemo(() => sortJobs(filtered, sortKey, sortDir), [filtered, sortKey, sortDir]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function SortIcon({ sortKeyOf }: { sortKeyOf: SortKey }) {
    if (sortKey !== sortKeyOf) return <ArrowUpDown className="size-3.5" />;
    return sortDir === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" />;
  }

  if (jobs.length === 0) {
    return <p className="text-sm text-muted-foreground">아직 실행한 grid search가 없습니다.</p>;
  }

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">요청 이력</h2>

      <div className="mb-3 flex flex-wrap items-center gap-3 rounded-md border bg-muted/30 px-3 py-2">
        <BacktestCoinFilter options={coinOptions} value={coinFilter} onChange={setCoinFilter} />
        <Select value={timeframeFilterValue} onValueChange={(value) => value !== null && setTimeframeFilterValue(value)}>
          <SelectTrigger className="w-40">
            <SelectValue>
              {(value: string | null) => (value && value !== ALL_TIMEFRAMES ? formatTimeframe(value) : '전체 봉타입')}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL_TIMEFRAMES}>전체 봉타입</SelectItem>
            {timeframeOptions.map((tf) => (
              <SelectItem key={tf} value={tf}>
                {formatTimeframe(tf)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {sorted.length === 0 ? (
        <p className="text-sm text-muted-foreground">조건에 맞는 이력이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-8" />
              <TableHead>상태</TableHead>
              <TableHead>코인</TableHead>
              <TableHead>봉타입</TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('start')}>
                  기간 <SortIcon sortKeyOf="start" />
                </button>
              </TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('started_at')}>
                  실행시각 <SortIcon sortKeyOf="started_at" />
                </button>
              </TableHead>
              <TableHead>1위 조건</TableHead>
              <TableHead>
                <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('return_pct')}>
                  1위 수익률 <SortIcon sortKeyOf="return_pct" />
                </button>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sorted.map((job) => {
              const results = job.result_json ?? [];
              const top = results[0];
              const expandable = canExpand(job);
              const isExpanded = expanded.has(job.id);

              return (
                <Fragment key={job.id}>
                  <TableRow
                    className={expandable ? 'cursor-pointer' : ''}
                    onClick={() => expandable && toggle(job.id)}
                  >
                    <TableCell>
                      {expandable &&
                        (isExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />)}
                    </TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[job.status]}>{STATUS_LABEL[job.status]}</Badge>
                    </TableCell>
                    <TableCell>{job.market.replace('KRW-', '')}</TableCell>
                    <TableCell>{formatTimeframe(job.timeframe)}</TableCell>
                    <TableCell>
                      {job.start} ~ {job.end}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDateTime(job.started_at)}</TableCell>
                    <TableCell className="max-w-[320px] truncate">
                      {top ? (
                        <Link href={`/backtests/${top.run_id}`} className="underline" onClick={(e) => e.stopPropagation()}>
                          <ResultTitle result={top} />
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {top ? (
                        <span className={returnRateColor(top.return_pct)}>{top.return_pct.toFixed(2)}%</span>
                      ) : (
                        <span className="text-muted-foreground">-</span>
                      )}
                    </TableCell>
                  </TableRow>
                  {isExpanded && job.status === 'failed' && job.error_message && (
                    <TableRow>
                      <TableCell colSpan={8} className="whitespace-normal text-sm text-destructive">
                        {job.error_message}
                      </TableCell>
                    </TableRow>
                  )}
                  {isExpanded && results.length > 1 && (
                    <TableRow>
                      <TableCell colSpan={8}>
                        <div className="space-y-1">
                          {results.slice(1).map((r) => (
                            <div key={r.run_id} className="flex items-center gap-2 text-sm">
                              <span className="text-muted-foreground">{r.rank}위</span>
                              <span className={returnRateColor(r.return_pct)}>{r.return_pct.toFixed(2)}%</span>
                              <Link href={`/backtests/${r.run_id}`} className="truncate underline">
                                <ResultTitle result={r} />
                              </Link>
                            </div>
                          ))}
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
```

- [x] **Step 2: 타입 체크 + lint**

Run: `cd frontend && npx tsc --noEmit && npx eslint components/GridSearchHistory.tsx`
Expected: 에러 없음

- [x] **Step 3: 실제 데이터로 수동 확인 (`npm run dev` + 실제 백엔드)**

`npm run dev`와 `uvicorn backend.main:app --reload --port 8000`이 떠 있는 상태에서
`/grid-search` 방문 후 아래를 확인한다(완료/취소/실패가 섞인 이력이 이미 있다면 그대로 사용,
없다면 짧은 그리드서치 1회를 완료/취소/실패 각각 만들어 확인):

1. 코인 필터 드롭다운에 실제 이력에 등장하는 코인만 뜨는지, "전체 코인" 선택 시 전부 보이는지.
2. 봉타입 필터도 마찬가지로 실제 등장하는 값만 뜨는지, "전체 봉타입"이 기본값인지.
3. 코인 필터와 봉타입 필터를 동시에 걸었을 때 AND로 좁혀지는지.
4. "기간"/"실행시각"/"1위 수익률" 헤더 클릭 시 오름차순↔내림차순이 토글되는지, 아이콘이
   바뀌는지.
5. 1위 조건 셀에 `[Grid]` 프리픽스가 안 보이고 "매수"/"매도" 글자만 볼드로 보이는지.
6. 결과가 2개 이상인 완료 job의 행을 클릭하면 바로 아래에 2위~N위가 펼쳐지는지, 다시
   클릭하면 접히는지.
7. 실패 job의 행을 클릭하면 에러 메시지가 펼쳐지는지.
8. 취소(canceled) job이나 결과가 1개뿐인 완료 job은 클릭해도 아무 반응이 없는지(chevron
   아이콘 자체가 안 보이는지).
9. 진행 중인 job이 있다면 이 표에는 안 보이고 위쪽 진행률 카드에서만 보이는지.
10. 결과 링크(`/backtests/{run_id}`)를 클릭했을 때 행이 접히지 않고 바로 상세 페이지로
    이동하는지(이벤트 버블링으로 행 클릭이 같이 발동하지 않는지).

- [x] **Step 4: 커밋**

```bash
git add frontend/components/GridSearchHistory.tsx
git commit -m "feat: 그리드서치 요청 이력을 코인/봉타입 필터 + 정렬 가능한 표로 재설계"
```

---

## 마무리 체크

- [x] `cd frontend && npx tsc --noEmit` 클린 (3개 태스크 전부 반영 후)
- [x] `npm run dev` + 백엔드가 떠 있는 상태에서 `/grid-search` 탭 수동 확인(Task 3 Step 4의
  10개 항목 전부)
- [x] 브라우저 콘솔에 에러/경고 없는지 확인
