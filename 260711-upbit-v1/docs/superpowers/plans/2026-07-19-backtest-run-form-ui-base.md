# 백테스트 실행 폼 UI/UX 베이스 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/backtests` 탭에 코인젠포트를 레퍼런스로 한 `BacktestRunForm` 정적 목업(섹션 카드 + 업비트 블루 강조색 + 전략 pill 토글)을 만들어, 실제 API 연동 없이 화면 디자인을 먼저 완성한다.

**Architecture:** `frontend/app/globals.css`의 색상 토큰을 블루 계열로 교체하고, 코인/봉타입/전략(더미 배열)/기간 입력을 갖춘 `BacktestRunForm.tsx`를 신규 작성해 `/backtests/page.tsx`에서 렌더링한다. 실제 백엔드 호출(`getSignals`/`runBacktest`)은 이 계획에 포함하지 않는다.

**Tech Stack:** Next.js App Router (14.2.35), React 18, TypeScript, Tailwind CSS 3, 기존 shadcn 스타일 `Card`/`Button` 컴포넌트.

## Global Constraints

- 프런트엔드에는 단위테스트 프레임워크가 없다(프로젝트 관례) — 각 태스크는 `npx tsc --noEmit` / `npm run build` / 수동 브라우저 확인으로 검증한다.
- 실제 API 호출(`GET /api/v1/eda/signals`, `POST /api/v1/backtests/run`)은 이 계획의 범위 밖이다 — 전략 목록은 하드코딩된 더미 배열을 쓰고, 실행 버튼은 `console.log`만 남긴다.
- 4단계 스텝 위저드, 매수/매도 조건 빌더, 관심코인 탭 등 레퍼런스의 기능적 요소는 만들지 않는다.
- 대상 필드는 코인/봉타입/전략(다중 선택)/기간(시작~종료)/실행 버튼뿐이다.
- 기존 `/backtests/[runId]/page.tsx`, 백엔드, `lib/api/eda.ts`, `lib/types/eda.ts`는 변경하지 않는다.

---

### Task 1: 색상 토큰을 업비트 블루로 교체

**Files:**
- Modify: `frontend/app/globals.css`

**Interfaces:**
- Produces: `--primary`, `--primary-foreground`, `--ring`, `--sidebar-primary`, `--sidebar-ring` CSS 변수(라이트/다크 모두) — Task 2의 `Button`/pill 토글/포커스 링이 이 값을 그대로 사용한다.

- [ ] **Step 1: `:root` 블록의 컬러 변수 교체**

`frontend/app/globals.css`에서 `:root` 블록 안의 아래 5개 줄을 찾아:

```css
    --primary: oklch(0.205 0 0);
    --primary-foreground: oklch(0.985 0 0);
```
```css
    --ring: oklch(0.708 0 0);
```
```css
    --sidebar-primary: oklch(0.205 0 0);
    --sidebar-primary-foreground: oklch(0.985 0 0);
```
```css
    --sidebar-ring: oklch(0.708 0 0);
```

아래 값으로 교체(각 줄의 순서와 나머지 변수는 그대로 둔다):

```css
    --primary: oklch(0.55 0.18 255);
    --primary-foreground: oklch(0.985 0 0);
```
```css
    --ring: oklch(0.55 0.18 255 / 60%);
```
```css
    --sidebar-primary: oklch(0.55 0.18 255);
    --sidebar-primary-foreground: oklch(0.985 0 0);
```
```css
    --sidebar-ring: oklch(0.55 0.18 255 / 60%);
```

- [ ] **Step 2: `.dark` 블록의 컬러 변수 교체**

`.dark` 블록 안의 아래 줄들을 찾아:

```css
    --primary: oklch(0.922 0 0);
    --primary-foreground: oklch(0.205 0 0);
```
```css
    --ring: oklch(0.556 0 0);
```
```css
    --sidebar-primary: oklch(0.488 0.243 264.376);
    --sidebar-primary-foreground: oklch(0.985 0 0);
```
```css
    --sidebar-ring: oklch(0.556 0 0);
```

아래 값으로 교체:

```css
    --primary: oklch(0.72 0.16 255);
    --primary-foreground: oklch(0.145 0 0);
```
```css
    --ring: oklch(0.72 0.16 255 / 60%);
```
```css
    --sidebar-primary: oklch(0.72 0.16 255);
    --sidebar-primary-foreground: oklch(0.145 0 0);
```
```css
    --sidebar-ring: oklch(0.72 0.16 255 / 60%);
```

- [ ] **Step 3: 빌드 확인**

Run (in `frontend/`): `npx tsc --noEmit`
Expected: 에러 없음(CSS만 변경했으므로 타입 에러가 날 이유가 없음 — 통과 확인용)

- [ ] **Step 4: 커밋**

```bash
git add frontend/app/globals.css
git commit -m "style: switch primary accent color to upbit blue"
```

---

### Task 2: `BacktestRunForm` 정적 목업 컴포넌트 작성 및 `/backtests` 페이지 교체

**Files:**
- Create: `frontend/components/BacktestRunForm.tsx`
- Modify: `frontend/app/backtests/page.tsx`

**Interfaces:**
- Consumes: `Button`(`frontend/components/ui/button.tsx`, 기존), `Card`/`CardHeader`/`CardTitle`/`CardContent`(`frontend/components/ui/card.tsx`, 기존), Task 1의 `--primary`/`--primary-foreground` 토큰.
- Produces: `export default function BacktestRunForm()` — 실제 API 연동 없는 정적 목업. `/backtests/page.tsx`가 그대로 렌더링한다.

- [ ] **Step 1: `BacktestRunForm.tsx` 작성**

`frontend/components/BacktestRunForm.tsx` 새로 생성:

```tsx
'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';

const MARKETS = ['KRW-BTC', 'KRW-ETH'];

const TIMEFRAMES = [
  { value: 'days', label: '일봉' },
  { value: 'minutes240', label: '4시간봉' },
  { value: 'minutes60', label: '1시간봉' },
  { value: 'minutes15', label: '15분봉' },
];

const DUMMY_SIGNALS = ['macd_cross', 'rsi_zone', 'sma_cross', 'bollinger_band'];

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

export default function BacktestRunForm() {
  const [market, setMarket] = useState(MARKETS[0]);
  const [timeframe, setTimeframe] = useState(TIMEFRAMES[0].value);
  const [selectedSignals, setSelectedSignals] = useState<string[]>([DUMMY_SIGNALS[0]]);
  const [start, setStart] = useState(defaultDate(90));
  const [end, setEnd] = useState(defaultDate(0));

  function toggleSignal(key: string) {
    setSelectedSignals((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }

  return (
    <div className="max-w-xl space-y-4">
      <Card>
        <CardHeader className="bg-primary/10">
          <CardTitle>기본 설정</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4">
          <div>
            <label className="mb-1 block text-sm font-medium">코인</label>
            <select
              className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
              value={market}
              onChange={(e) => setMarket(e.target.value)}
            >
              {MARKETS.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-sm font-medium">봉타입</label>
            <select
              className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
            >
              {TIMEFRAMES.map((tf) => (
                <option key={tf.value} value={tf.value}>
                  {tf.label}
                </option>
              ))}
            </select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="bg-primary/10">
          <CardTitle>전략 선택</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          {DUMMY_SIGNALS.map((key) => {
            const selected = selectedSignals.includes(key);
            return (
              <button
                key={key}
                type="button"
                onClick={() => toggleSignal(key)}
                className={
                  selected
                    ? 'rounded-full border border-primary bg-primary px-3 py-1 text-xs font-medium text-primary-foreground'
                    : 'rounded-full border border-input bg-background px-3 py-1 text-xs font-medium text-muted-foreground hover:bg-muted'
                }
              >
                {key}
              </button>
            );
          })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="bg-primary/10">
          <CardTitle>운용 기간</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <div className="flex items-center gap-2">
            <input
              type="date"
              className="rounded-md border border-input bg-background px-2 py-1 text-sm"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
            <span className="text-sm text-muted-foreground">~</span>
            <input
              type="date"
              className="rounded-md border border-input bg-background px-2 py-1 text-sm"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </div>
          <p className="text-xs text-muted-foreground">
            기간이 길고 봉타입이 짧을수록 최초 조회 시 시간이 걸릴 수 있습니다.
          </p>
        </CardContent>
      </Card>

      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          선택한 조건으로 백테스트를 실행합니다.
        </p>
        <Button
          type="button"
          onClick={() =>
            console.log('run backtest (mock)', { market, timeframe, selectedSignals, start, end })
          }
        >
          실행
        </Button>
      </div>

      <p className="invisible text-sm text-red-600 dark:text-red-400">에러 자리</p>
    </div>
  );
}
```

- [ ] **Step 2: `/backtests` 페이지 교체**

`frontend/app/backtests/page.tsx` 전체를 아래로 교체:

```tsx
import BacktestRunForm from '@/components/BacktestRunForm';

export default function BacktestsIndexPage() {
  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">백테스트 실행</h1>
      <BacktestRunForm />
    </div>
  );
}
```

- [ ] **Step 3: 타입 체크 확인**

Run (in `frontend/`): `npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 4: 빌드 확인**

Run (in `frontend/`): `npm run build`
Expected: 빌드 성공(타입/린트 에러 없음)

- [ ] **Step 5: 수동 확인**

```bash
cd frontend && npm run dev
```

브라우저(또는 Playwright)로 `http://localhost:3000/backtests` 접속 후:
1. "기본 설정"/"전략 선택"/"운용 기간" 3개 카드가 옅은 블루 헤더와 함께 렌더링되는지 확인
2. 전략 pill 버튼 클릭 시 선택/해제되며 선택된 pill이 블루로 채워지는지 확인
3. 코인/봉타입 select, 날짜 입력이 정상적으로 값이 바뀌는지 확인
4. "실행" 버튼이 블루(primary) 색상이고, 클릭 시 콘솔에 목업 로그만 찍히는지 확인(페이지 이동 없음)
5. 에러 문구 자리는 화면에 보이지 않지만 레이아웃 공간은 차지하는지 확인(개발자 도구로 `invisible` 요소 존재 확인)

Expected: 위 5가지 모두 통과

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/BacktestRunForm.tsx frontend/app/backtests/page.tsx
git commit -m "feat: add BacktestRunForm UI/UX mockup with coingenport-inspired layout"
```

## Self-Review 결과

- **스펙 커버리지**: `2026-07-19-backtest-run-form-ui-design.md`의 컬러 토큰 변경(Task 1), 화면 구성(3개 섹션 카드 + pill 토글 + 기간 입력 + 액션 바 + invisible 에러 영역, Task 2), 상호작용 범위(정적 목업, API 호출 없음)를 모두 다룸.
- **타입 일관성**: `BacktestRunForm`이 사용하는 `Button`/`Card`/`CardHeader`/`CardTitle`/`CardContent` import 경로와 export 이름이 기존 `frontend/components/ui/button.tsx`, `frontend/components/ui/card.tsx`의 실제 export와 일치함을 확인.
- **범위 확인**: `lib/api/eda.ts`, `lib/types/eda.ts`, 백엔드, `/backtests/[runId]/page.tsx`는 어떤 태스크에도 포함하지 않음(스펙에서 범위 밖으로 명시).
