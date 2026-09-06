# 코인별 매매일지 UX 정리 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 매매일지 페이지의 "코인별 매매일지" 영역에서 KST 시간 표기 버그를 고치고, 매매일지 리스트를 고정 높이 스크롤박스로 바꿔 무한스크롤을 막고, 코인별 달력을 제거하고, 백테스트 비교를 한 줄 압축 스탯 바로 바꾸고, 매매일지 각 행에 손익 색상(수익 빨강/손실 파랑)을 넣고 수량 표기를 뺀다.

**Architecture:** 전부 프론트엔드(Next.js/React/TSX) 변경. 백엔드 API·DB 스키마는 바꾸지 않는다. `frontend/lib/format.ts`(공용 시간 포맷 유틸), `frontend/components/JournalMarketDetail.tsx`(코인별 상세 뷰), `frontend/components/JournalPage.tsx`(달력 렌더 위치) 세 파일만 수정한다.

**Tech Stack:** Next.js 14 (App Router), React 18, TypeScript, Tailwind CSS.

## Global Constraints

- 스펙: `docs/superpowers/specs_v2/2026-09-06-journal-market-detail-ux-design.md`
- 색상 코딩은 매매일지 리스트 행에만 적용한다 — 다른 pnl 표시(계좌 요약, 코인 요약 카드 등)는 건드리지 않는다.
- `entry_time`/`exit_time`을 만드는 백엔드(`trading/db.py`)는 바꾸지 않는다 — 프론트 파싱만 고친다.
- 코인별 매매일지의 백엔드 응답 필드(`daily`, `daily_pnl_30d`)는 그대로 둔다 — 프론트에서 렌더링만 뺀다.
- **이 프로젝트 프론트엔드에는 테스트 프레임워크(Jest/Vitest 등)가 설치돼 있지 않다** (`frontend/package.json` 확인됨, 기존 TSX 컴포넌트들도 전부 테스트 없이 브라우저 수동 확인으로만 검증돼 있음). 이 관례를 따라 새 프레임워크를 들여오지 않고, 순수 로직(`toUtcDate`)은 `node -e`로 즉석 검증하고 나머지는 개발 서버로 브라우저에서 직접 확인한다.
- 색상 클래스는 이 코드베이스의 기존 관례(`text-{color}-600 dark:text-{color}-400`, 예: `frontend/components/SegmentSizeTable.tsx`의 amber 배지)를 따른다.

---

### Task 1: KST 시간 표기 수정

**Files:**
- Modify: `frontend/lib/format.ts:1-22`

**Interfaces:**
- Consumes: 없음(독립 유틸 수정)
- Produces: `formatDateTime(iso: string): string`, `formatDateTimeShort(iso: string): string` — 시그니처는 그대로, 내부 파싱만 고쳐서 이후 태스크에서 그대로 재사용

- [ ] **Step 1: `toUtcDate` 헬퍼 추가 + 두 포맷 함수에서 사용**

`frontend/lib/format.ts`의 다음 블록을:

```ts
const KST_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

export function formatDateTime(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`;
}

export function formatDateTimeShort(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(new Date(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}
```

다음으로 교체한다:

```ts
const KST_FORMATTER = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
});

// 백엔드(SQLite datetime('now'))가 타임존 마커 없는 UTC 문자열("2026-09-06 05:30:00")을
// 그대로 내려주기 때문에, new Date()에 곧바로 넘기면 브라우저가 로컬 시간으로 오인한다.
// 마커가 없으면 UTC로 명시해서 파싱한다.
function toUtcDate(iso: string): Date {
  const hasTimezone = /[Zz]|[+-]\d{2}:\d{2}$/.test(iso);
  return new Date(hasTimezone ? iso : `${iso.replace(' ', 'T')}Z`);
}

export function formatDateTime(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(toUtcDate(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`;
}

export function formatDateTimeShort(iso: string): string {
  const parts = KST_FORMATTER.formatToParts(toUtcDate(iso));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
  return `${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`;
}
```

- [ ] **Step 2: `toUtcDate` 로직을 node로 즉석 검증**

Run:

```bash
node -e "
function toUtcDate(iso) {
  const hasTimezone = /[Zz]|[+-]\d{2}:\d{2}$/.test(iso);
  return new Date(hasTimezone ? iso : iso.replace(' ', 'T') + 'Z');
}
const KST = new Intl.DateTimeFormat('en-US', {
  timeZone: 'Asia/Seoul', year:'numeric', month:'2-digit', day:'2-digit',
  hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false,
});
const get = (parts, type) => parts.find(p => p.type === type)?.value ?? '';
const a = KST.formatToParts(toUtcDate('2026-09-06 05:30:00'));
console.log('naive UTC 05:30 ->', get(a,'hour') + ':' + get(a,'minute'), '(expect 14:30)');
const b = KST.formatToParts(toUtcDate('2026-09-06T05:30:00Z'));
console.log('Z-suffixed 05:30 ->', get(b,'hour') + ':' + get(b,'minute'), '(expect 14:30)');
"
```

Expected output:

```
naive UTC 05:30 -> 14:30 (expect 14:30)
Z-suffixed 05:30 -> 14:30 (expect 14:30)
```

두 줄 다 `14:30`이 나오면(타임존 마커 유무와 무관하게 같은 절대시각이 같은 KST로 변환됨) 통과. 다르면 정규식이나 문자열 치환을 다시 확인한다.

- [ ] **Step 3: 프론트 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(이 파일 관련 타입 에러 0건).

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/format.ts
git commit -m "fix: 타임존 마커 없는 UTC 시각을 로컬시간으로 오인하던 KST 변환 버그 수정"
```

---

### Task 2: 매매일지 리스트 — 스크롤박스 + 손익 색상 + 수량 표기 제거

**Files:**
- Modify: `frontend/components/JournalMarketDetail.tsx`

**Interfaces:**
- Consumes: `formatDateTime` (Task 1에서 수정된 버전, 시그니처 동일), `JournalTradeLogEntry`(`frontend/lib/types/journal.ts`: `entry_time`, `entry_price`, `exit_time`, `exit_price`, `realized_pnl`, `realized_pnl_pct`, `close_reason`, `position_id`)
- Produces: 이 파일 안에서만 쓰는 `pnlColorClass(value: number): string` 헬퍼(Task 4에서도 재사용하지 않음 — 백테스트 비교 섹션은 별도 값이라 색상 미적용)

- [ ] **Step 1: `pnlColorClass` 헬퍼 추가**

`frontend/components/JournalMarketDetail.tsx` 상단의 다음 블록:

```tsx
function fmtCloseReason(reason: string): string {
  return CLOSE_REASON_LABELS[reason] ?? reason;
}
```

을 다음으로 교체(뒤에 헬퍼 추가):

```tsx
function fmtCloseReason(reason: string): string {
  return CLOSE_REASON_LABELS[reason] ?? reason;
}

function pnlColorClass(value: number): string {
  return value >= 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}
```

- [ ] **Step 2: 매매일지 섹션을 스크롤박스로 감싸고, 수량 표기 제거, 손익에 색상 적용**

다음 블록(파일의 "매매일지" 섹션 전체):

```tsx
      <div>
        <h3 className="mb-2 text-sm font-semibold">매매일지</h3>
        {detail.trade_log.length === 0 ? (
          <p className="text-sm text-muted-foreground">청산된 거래가 없습니다.</p>
        ) : (
          <>
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>진입</TableHead>
                    <TableHead>청산</TableHead>
                    <TableHead>손익</TableHead>
                    <TableHead>청산사유</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.trade_log.map((t) => (
                    <TableRow key={t.position_id}>
                      <TableCell>
                        {formatDateTime(t.entry_time)}
                        <br />
                        {Math.round(t.entry_price).toLocaleString()}원 × {t.entry_qty}
                      </TableCell>
                      <TableCell>
                        {formatDateTime(t.exit_time)}
                        <br />
                        {Math.round(t.exit_price).toLocaleString()}원 × {t.exit_qty}
                      </TableCell>
                      <TableCell>
                        {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)})
                      </TableCell>
                      <TableCell>{fmtCloseReason(t.close_reason)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
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
        )}
      </div>
```

을 다음으로 교체:

```tsx
      <div>
        <h3 className="mb-2 text-sm font-semibold">
          매매일지
          {detail.trade_log.length > 0 && (
            <span className="ml-2 text-xs font-normal text-muted-foreground">
              전체 {detail.trade_log.length}건
            </span>
          )}
        </h3>
        {detail.trade_log.length === 0 ? (
          <p className="text-sm text-muted-foreground">청산된 거래가 없습니다.</p>
        ) : (
          <div className="max-h-[320px] overflow-y-auto pr-1">
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>진입</TableHead>
                    <TableHead>청산</TableHead>
                    <TableHead>손익</TableHead>
                    <TableHead>청산사유</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {detail.trade_log.map((t) => (
                    <TableRow key={t.position_id}>
                      <TableCell>
                        {formatDateTime(t.entry_time)}
                        <br />
                        {Math.round(t.entry_price).toLocaleString()}원
                      </TableCell>
                      <TableCell>
                        {formatDateTime(t.exit_time)}
                        <br />
                        {Math.round(t.exit_price).toLocaleString()}원
                      </TableCell>
                      <TableCell className={pnlColorClass(t.realized_pnl)}>
                        {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)})
                      </TableCell>
                      <TableCell>{fmtCloseReason(t.close_reason)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="space-y-2 md:hidden">
              {detail.trade_log.map((t) => (
                <div key={t.position_id} className="rounded-md border p-3 text-sm">
                  <p className="text-xs text-muted-foreground">
                    진입 {formatDateTime(t.entry_time)} · {Math.round(t.entry_price).toLocaleString()}원
                  </p>
                  <p className="text-xs text-muted-foreground">
                    청산 {formatDateTime(t.exit_time)} · {Math.round(t.exit_price).toLocaleString()}원
                  </p>
                  <p className="mt-1">
                    <span className={pnlColorClass(t.realized_pnl)}>
                      {fmtKrw(t.realized_pnl)} ({fmtPct(t.realized_pnl_pct)})
                    </span>
                    {' · '}
                    {fmtCloseReason(t.close_reason)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
```

- [ ] **Step 3: 프론트 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음.

- [ ] **Step 4: 개발 서버로 브라우저 확인**

Run: `cd frontend && npm run dev` (백엔드도 별도 터미널에서 이미 떠 있어야 함 — `uvicorn backend.main:app --reload` 등 기존 실행 방식 그대로)

브라우저에서 `/journal` → 거래 건수가 많은 코인 선택 → 확인할 것:
1. 매매일지 리스트가 스크롤박스 안에서만 스크롤되고 페이지 전체 길이는 늘어나지 않는다.
2. 제목 옆에 "전체 N건"이 보인다.
3. 손익이 양수인 행은 빨간색, 음수인 행은 파란색으로 보인다(청산사유 텍스트는 색 없음).
4. 진입/청산가 옆에 더 이상 `× 수량`이 안 보인다.
5. `formatDateTime`으로 찍힌 시각이 실제 KST와 맞는지(현재 시각 기준 대략 확인).

- [ ] **Step 5: Commit**

```bash
git add frontend/components/JournalMarketDetail.tsx
git commit -m "feat: 매매일지 리스트 스크롤박스+손익 색상 코딩+수량 표기 제거"
```

---

### Task 3: 코인별 매매일지 달력 제거

**Files:**
- Modify: `frontend/components/JournalPage.tsx:160-172`

**Interfaces:**
- Consumes: 없음(렌더 호출 제거만)
- Produces: 없음

- [ ] **Step 1: 코인별 상세 블록에서 `JournalCalendar` 호출 제거**

`frontend/components/JournalPage.tsx`의 다음 블록:

```tsx
          {detail && (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                {detail.timeframes.map(formatTimeframe).join(', ')} · {detail.statuses.join(', ')}
              </p>

              <JournalCalendar daily={detail.daily} />

              <JournalMarketDetailView detail={detail} />
            </div>
          )}
```

을 다음으로 교체(계좌 전체 요약 쪽의 `<JournalCalendar daily={summary.daily} />` 호출은 그대로 둔다):

```tsx
          {detail && (
            <div className="space-y-4">
              <p className="text-xs text-muted-foreground">
                {detail.timeframes.map(formatTimeframe).join(', ')} · {detail.statuses.join(', ')}
              </p>

              <JournalMarketDetailView detail={detail} />
            </div>
          )}
```

- [ ] **Step 2: 프론트 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음(`JournalCalendar` import는 계좌 전체 요약 쪽에서 계속 쓰이므로 안 지운다 — 지우면 그 쪽 렌더가 깨진다).

- [ ] **Step 3: 개발 서버로 브라우저 확인**

`/journal`에서: 계좌 전체 요약 아래 달력은 그대로 보이고, 코인을 선택했을 때 그 아래(코인별 매매일지)에는 달력이 안 보이는지 확인.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/JournalPage.tsx
git commit -m "feat: 코인별 매매일지에서 달력 제거(계좌 전체 요약 달력은 유지)"
```

---

### Task 4: 백테스트 vs 실매매 — 한 줄 압축 스탯 바

**Files:**
- Modify: `frontend/components/JournalMarketDetail.tsx`

**Interfaces:**
- Consumes: `JournalBacktestComparison`(`frontend/lib/types/journal.ts`: `backtest`/`live`는 각각 `JournalMetricSet` = `{ win_rate_pct, avg_return_pct, mdd_pct, trade_count }`, `sample_size_warning: boolean`), `fmtPct`(파일 내 기존 헬퍼, 그대로 재사용)
- Produces: 없음(이 섹션은 다른 태스크가 의존하지 않음)

- [ ] **Step 1: 백테스트 비교 섹션을 한 줄 압축 스탯 바로 교체**

다음 블록("백테스트 vs 실매매" 섹션 전체, Task 2에서 이미 손댄 "매매일지" 섹션과는 다른 블록):

```tsx
      <div>
        <h3 className="mb-2 text-sm font-semibold">백테스트 vs 실매매</h3>
        {comparison === null ? (
          <p className="text-sm text-muted-foreground">
            백테스트 비교 불가(연결된 백테스트 결과가 없습니다).
          </p>
        ) : (
          <>
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead></TableHead>
                    <TableHead>백테스트</TableHead>
                    <TableHead>실매매</TableHead>
                    <TableHead>차이</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow>
                    <TableCell>승률</TableCell>
                    <TableCell>{comparison.backtest.win_rate_pct.toFixed(1)}%</TableCell>
                    <TableCell>{comparison.live.win_rate_pct.toFixed(1)}%</TableCell>
                    <TableCell>
                      {fmtPct(comparison.live.win_rate_pct - comparison.backtest.win_rate_pct)}p
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>평균수익률</TableCell>
                    <TableCell>{fmtPct(comparison.backtest.avg_return_pct)}</TableCell>
                    <TableCell>{fmtPct(comparison.live.avg_return_pct)}</TableCell>
                    <TableCell>
                      {fmtPct(comparison.live.avg_return_pct - comparison.backtest.avg_return_pct)}p
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>MDD</TableCell>
                    <TableCell>{fmtPct(comparison.backtest.mdd_pct)}</TableCell>
                    <TableCell>{fmtPct(comparison.live.mdd_pct)}</TableCell>
                    <TableCell>
                      {fmtPct(comparison.live.mdd_pct - comparison.backtest.mdd_pct)}p
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell>거래횟수</TableCell>
                    <TableCell>{comparison.backtest.trade_count}건</TableCell>
                    <TableCell>{comparison.live.trade_count}건</TableCell>
                    <TableCell>-</TableCell>
                  </TableRow>
                </TableBody>
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
        )}
      </div>
```

을 다음으로 교체:

```tsx
      <div>
        <h3 className="mb-2 text-sm font-semibold">백테스트 vs 실매매</h3>
        {comparison === null ? (
          <p className="text-sm text-muted-foreground">
            백테스트 비교 불가(연결된 백테스트 결과가 없습니다).
          </p>
        ) : (
          <>
            <div className="flex flex-wrap gap-3 rounded-md border p-2 text-xs">
              <div className="min-w-[70px] flex-1 text-center">
                <p className="text-muted-foreground">승률</p>
                <p className="font-medium">
                  {comparison.backtest.win_rate_pct.toFixed(1)}%
                  <span className="text-muted-foreground"> → </span>
                  {comparison.live.win_rate_pct.toFixed(1)}%
                </p>
              </div>
              <div className="min-w-[70px] flex-1 text-center">
                <p className="text-muted-foreground">평균수익률</p>
                <p className="font-medium">
                  {fmtPct(comparison.backtest.avg_return_pct)}
                  <span className="text-muted-foreground"> → </span>
                  {fmtPct(comparison.live.avg_return_pct)}
                </p>
              </div>
              <div className="min-w-[70px] flex-1 text-center">
                <p className="text-muted-foreground">MDD</p>
                <p className="font-medium">
                  {fmtPct(comparison.backtest.mdd_pct)}
                  <span className="text-muted-foreground"> → </span>
                  {fmtPct(comparison.live.mdd_pct)}
                </p>
              </div>
              <div className="min-w-[70px] flex-1 text-center">
                <p className="text-muted-foreground">거래횟수</p>
                <p className="font-medium">
                  {comparison.backtest.trade_count}
                  <span className="text-muted-foreground"> → </span>
                  {comparison.live.trade_count}건
                </p>
              </div>
            </div>
            {comparison.sample_size_warning && (
              <p className="mt-2 text-xs text-amber-600">
                실매매 표본이 10건 미만이라 통계적으로 신뢰하기 이릅니다.
              </p>
            )}
          </>
        )}
      </div>
```

- [ ] **Step 2: 프론트 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음. (`Table`/`TableHeader`/`TableBody`/`TableRow`/`TableHead`/`TableCell` import는 Task 2에서 손댄 "매매일지" 섹션이 계속 쓰므로 그대로 둔다.)

- [ ] **Step 3: 개발 서버로 브라우저 확인**

`/journal`에서 백테스트 결과가 연결된 코인 선택 → "백테스트 vs 실매매"가 한 줄(또는 좁은 화면에서 두 줄) 압축 바로 보이는지, 승률/평균수익률/MDD/거래횟수 4개 값이 다 나오는지, 표본 10건 미만 경고 문구가 조건대로 뜨는지 확인. 연결된 백테스트가 없는 코인에서는 기존처럼 "백테스트 비교 불가" 문구가 그대로 뜨는지도 확인.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/JournalMarketDetail.tsx
git commit -m "feat: 백테스트 vs 실매매 비교를 한 줄 압축 스탯 바로 축소"
```

---

## 완료 후

모든 태스크 커밋 후 `git push`한다(프로젝트 CLAUDE.md 지침: main에서 직접 작업, 완료 후 병합 방식 안 묻고 바로 push).
