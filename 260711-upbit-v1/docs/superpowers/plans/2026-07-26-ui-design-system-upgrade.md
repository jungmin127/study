# UI 개선(Tailwind v4/shadcn/lucide-react 전면 정비) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `frontend/` 전 페이지의 UI를 Tailwind v4 + shadcn/ui + lucide-react 기반으로 정비해 시각적 일관성, 아이콘을 통한 가독성, 레이아웃/정보 밀도, 다크모드를 개선한다.

**Architecture:** 빌드 인프라(Tailwind v4 마이그레이션) → 공용 shadcn 프리미티브 확장 → 페이지별 우선순위 순 적용 → 마무리(색상 토큰 통일, 다크모드 토글, 반응형 점검) 순서로 진행한다. 백엔드/데이터 계층은 변경하지 않는다.

**Tech Stack:** Next.js 14 (App Router) / TypeScript / Tailwind CSS v4 / shadcn/ui(`base-nova` 스타일, `@base-ui/react` 기반) / lucide-react

**참고 스펙:** `docs/superpowers/specs/2026-07-26-ui-design-system-upgrade-design.md`

## Global Constraints

- 백엔드(`backend/`, `engine/`) 변경 없음 — 프론트엔드(`frontend/`)만 수정한다.
- 새 UI 라이브러리(toast/sonner 등)를 추가하지 않는다. shadcn CLI로 추가하는 컴포넌트(`input`, `select`, `popover`, `command`, `dialog`, `alert-dialog`, `tooltip`)와 이미 설치된 `lucide-react` 범위 내에서 해결한다.
- 상단 탭(`NavTabs`)은 `Link` 기반 구조를 유지한다 — shadcn `Tabs`로 교체하지 않는다.
- 이 프론트엔드에는 자동화된 테스트 러너(Jest/Vitest 등)가 없다. 각 태스크의 "테스트" 단계는 기존 프로젝트 관례대로 `npm run dev` 구동 후 Playwright(또는 브라우저)로 실제 렌더링/콘솔 에러 유무를 확인하는 방식으로 한다.
- `npm run build`(프로덕션 빌드)를 `npm run dev`가 이미 떠 있는 상태에서 실행하면 같은 `.next` 디렉터리를 오염시켜 dev 서버가 `MODULE_NOT_FOUND`를 던지기 시작한다(과거 확인된 이슈). 컴파일만 확인하려면 `npx tsc --noEmit`을 쓰고, `npm run build`는 dev 서버를 끈 뒤에만 실행한다.
- shadcn CLI(`npx shadcn@latest add ...`)가 생성하는 컴포넌트의 정확한 export 이름/props는 `components.json`의 `style: "base-nova"`(`@base-ui/react` 기반) 설정에 따라 달라질 수 있다. 이 컴포넌트를 소비하는 모든 태스크는 **먼저 생성된 파일을 열어 실제 export를 확인하는 단계**를 포함한다 — 아래 코드의 `Tooltip`/`TooltipTrigger`/`TooltipContent`, `Select`/`SelectTrigger`/`SelectContent`/`SelectItem`, `Popover`/`PopoverTrigger`/`PopoverContent`, `Command`/`CommandInput`/`CommandList`/`CommandItem`, `Dialog`/`DialogContent`/`DialogHeader`/`DialogTitle`/`DialogFooter`, `AlertDialog`/`AlertDialogTrigger`/`AlertDialogContent`/`AlertDialogAction`/`AlertDialogCancel`은 shadcn/ui 공식 문서 기준 표준 API이며, 생성된 파일이 이와 다르면(예: `base-nova` 스타일이 다른 이름을 쓰면) 실제 export에 맞춰 사용처 코드의 import만 조정한다(로직 변경 없음).
- 수익률 빨강(+)/파랑(-) 컨벤션(`lib/return-rate-color.ts`)은 디자인 토큰이 아닌 한국식 도메인 컨벤션이므로 그대로 유지한다 — `bg-muted` 통일 대상에서 제외.
- 커밋은 태스크 단위로 한다(각 태스크 마지막 스텝).

---

## Task 1: Tailwind v4 마이그레이션

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/postcss.config.mjs`
- Modify: `frontend/app/globals.css`
- Delete: `frontend/tailwind.config.ts`

**Interfaces:**
- Produces: `--color-*`/`--radius-*` 테마 토큰이 `@theme` 블록(CSS)에서 정의됨 — 이후 모든 태스크의 Tailwind 유틸리티(`bg-primary`, `text-muted-foreground` 등)가 계속 동일하게 동작하는 전제.
- Produces: `@custom-variant dark (&:where(.dark, .dark *));` — Task 4의 다크모드 토글이 `.dark` 클래스로 동작하기 위한 전제 조건.

- [ ] **Step 1: Tailwind v4 패키지 설치**

```bash
cd frontend
npm install -D tailwindcss@^4.0.0 @tailwindcss/postcss@^4.0.0
```

- [ ] **Step 2: PostCSS 설정을 v4 플러그인으로 교체**

`frontend/postcss.config.mjs` 전체를 아래로 교체:

```js
/** @type {import('postcss-load-config').Config} */
const config = {
  plugins: {
    "@tailwindcss/postcss": {},
  },
};

export default config;
```

- [ ] **Step 3: `globals.css`를 v4 CSS-first 설정으로 재작성**

`frontend/app/globals.css` 전체를 아래로 교체 (기존 `:root`/`.dark`의 oklch 값은 그대로 유지, `@theme` 블록과 `@custom-variant dark`만 추가):

```css
@import "tailwindcss";

@custom-variant dark (&:where(.dark, .dark *));

@theme {
  --color-background: var(--background);
  --color-foreground: var(--foreground);
  --color-card: var(--card);
  --color-card-foreground: var(--card-foreground);
  --color-popover: var(--popover);
  --color-popover-foreground: var(--popover-foreground);
  --color-primary: var(--primary);
  --color-primary-foreground: var(--primary-foreground);
  --color-secondary: var(--secondary);
  --color-secondary-foreground: var(--secondary-foreground);
  --color-muted: var(--muted);
  --color-muted-foreground: var(--muted-foreground);
  --color-accent: var(--accent);
  --color-accent-foreground: var(--accent-foreground);
  --color-destructive: var(--destructive);
  --color-border: var(--border);
  --color-input: var(--input);
  --color-ring: var(--ring);
  --color-chart-1: var(--chart-1);
  --color-chart-2: var(--chart-2);
  --color-chart-3: var(--chart-3);
  --color-chart-4: var(--chart-4);
  --color-chart-5: var(--chart-5);
  --color-sidebar: var(--sidebar);
  --color-sidebar-foreground: var(--sidebar-foreground);
  --color-sidebar-primary: var(--sidebar-primary);
  --color-sidebar-primary-foreground: var(--sidebar-primary-foreground);
  --color-sidebar-accent: var(--sidebar-accent);
  --color-sidebar-accent-foreground: var(--sidebar-accent-foreground);
  --color-sidebar-border: var(--sidebar-border);
  --color-sidebar-ring: var(--sidebar-ring);
  --radius-lg: var(--radius);
  --radius-md: calc(var(--radius) - 2px);
  --radius-sm: calc(var(--radius) - 4px);
}

@layer utilities {
  .text-balance {
    text-wrap: balance;
  }
}

@layer base {
  .theme {
    --font-heading: var(--font-sans);
    --font-sans: var(--font-sans);
  }
  :root {
    --background: oklch(1 0 0);
    --foreground: oklch(0.145 0 0);
    --card: oklch(1 0 0);
    --card-foreground: oklch(0.145 0 0);
    --popover: oklch(1 0 0);
    --popover-foreground: oklch(0.145 0 0);
    --primary: oklch(0.55 0.18 255);
    --primary-foreground: oklch(0.985 0 0);
    --secondary: oklch(0.97 0 0);
    --secondary-foreground: oklch(0.205 0 0);
    --muted: oklch(0.97 0 0);
    --muted-foreground: oklch(0.556 0 0);
    --accent: oklch(0.97 0 0);
    --accent-foreground: oklch(0.205 0 0);
    --destructive: oklch(0.577 0.245 27.325);
    --border: oklch(0.922 0 0);
    --input: oklch(0.922 0 0);
    --ring: oklch(0.55 0.18 255 / 60%);
    --chart-1: oklch(0.87 0 0);
    --chart-2: oklch(0.556 0 0);
    --chart-3: oklch(0.439 0 0);
    --chart-4: oklch(0.371 0 0);
    --chart-5: oklch(0.269 0 0);
    --radius: 0.625rem;
    --sidebar: oklch(0.985 0 0);
    --sidebar-foreground: oklch(0.145 0 0);
    --sidebar-primary: oklch(0.55 0.18 255);
    --sidebar-primary-foreground: oklch(0.985 0 0);
    --sidebar-accent: oklch(0.97 0 0);
    --sidebar-accent-foreground: oklch(0.205 0 0);
    --sidebar-border: oklch(0.922 0 0);
    --sidebar-ring: oklch(0.55 0.18 255 / 60%);
  }
  .dark {
    --background: oklch(0.145 0 0);
    --foreground: oklch(0.985 0 0);
    --card: oklch(0.205 0 0);
    --card-foreground: oklch(0.985 0 0);
    --popover: oklch(0.205 0 0);
    --popover-foreground: oklch(0.985 0 0);
    --primary: oklch(0.72 0.16 255);
    --primary-foreground: oklch(0.145 0 0);
    --secondary: oklch(0.269 0 0);
    --secondary-foreground: oklch(0.985 0 0);
    --muted: oklch(0.269 0 0);
    --muted-foreground: oklch(0.708 0 0);
    --accent: oklch(0.269 0 0);
    --accent-foreground: oklch(0.985 0 0);
    --destructive: oklch(0.704 0.191 22.216);
    --border: oklch(1 0 0 / 10%);
    --input: oklch(1 0 0 / 15%);
    --ring: oklch(0.72 0.16 255 / 60%);
    --chart-1: oklch(0.87 0 0);
    --chart-2: oklch(0.556 0 0);
    --chart-3: oklch(0.439 0 0);
    --chart-4: oklch(0.371 0 0);
    --chart-5: oklch(0.269 0 0);
    --sidebar: oklch(0.205 0 0);
    --sidebar-foreground: oklch(0.985 0 0);
    --sidebar-primary: oklch(0.72 0.16 255);
    --sidebar-primary-foreground: oklch(0.145 0 0);
    --sidebar-accent: oklch(0.269 0 0);
    --sidebar-accent-foreground: oklch(0.985 0 0);
    --sidebar-border: oklch(1 0 0 / 10%);
    --sidebar-ring: oklch(0.72 0.16 255 / 60%);
  }
  * {
    @apply border-border;
  }
  body {
    @apply bg-background text-foreground;
  }
  html {
    @apply font-sans;
  }
}
```

- [ ] **Step 4: `tailwind.config.ts` 삭제**

```bash
rm frontend/tailwind.config.ts
```

- [ ] **Step 5: dev 서버 재기동 및 검증**

```bash
cd frontend
rm -rf .next
npm run dev
```

브라우저(Playwright)로 확인:
1. `http://localhost:3000` 접속, 콘솔 에러 0건 확인
2. `/` 페이지의 "백테스트 실행" 버튼에 마우스를 올려 `hover:bg-primary/80`(opacity modifier) 틴트가 실제로 보이는지 확인 — v3에서는 CSS var 색상에 opacity modifier가 no-op이었던 것이 v4의 `color-mix()` 기반 엔진에서는 정상 렌더링되는 것이 기대 동작(회귀 아님, 개선).
3. 버튼 포커스 시 `ring-3 ring-ring/50` 포커스 링이 보이는지 확인
4. `/backtests`, `/analysis`, `/heatmap`, `/history`, `/ranking`, `/model-accuracy` 순회하며 마이그레이션 전과 레이아웃이 동일한지(회귀 없음) 확인

- [ ] **Step 6: 커밋**

```bash
git add frontend/package.json frontend/package-lock.json frontend/postcss.config.mjs frontend/app/globals.css
git rm frontend/tailwind.config.ts
git commit -m "chore: Tailwind v4로 마이그레이션 (CSS-first 설정)"
```

---

## Task 2: shadcn 프리미티브 일괄 추가

**Files:**
- Create: `frontend/components/ui/input.tsx`
- Create: `frontend/components/ui/select.tsx`
- Create: `frontend/components/ui/popover.tsx`
- Create: `frontend/components/ui/command.tsx`
- Create: `frontend/components/ui/dialog.tsx`
- Create: `frontend/components/ui/alert-dialog.tsx`
- Create: `frontend/components/ui/tooltip.tsx`
- Modify: `frontend/package.json` (CLI가 필요한 의존성을 자동 추가 — 예: `cmdk`, `@base-ui/react`의 관련 서브모듈)

**Interfaces:**
- Produces: 이후 Task 3, 5, 6, 7, 8, 11에서 import할 `components/ui/{input,select,popover,command,dialog,alert-dialog,tooltip}.tsx`의 실제 export 목록 (Step 2에서 확인 후 기록).

- [ ] **Step 1: shadcn CLI로 프리미티브 추가**

```bash
cd frontend
npx shadcn@latest add input select popover command dialog alert-dialog tooltip
```

- [ ] **Step 2: 생성된 파일의 export 확인**

```bash
grep -n "^export" frontend/components/ui/input.tsx frontend/components/ui/select.tsx frontend/components/ui/popover.tsx frontend/components/ui/command.tsx frontend/components/ui/dialog.tsx frontend/components/ui/alert-dialog.tsx frontend/components/ui/tooltip.tsx
```

각 파일의 실제 export 이름을 확인하고, Global Constraints에 명시한 표준 이름(`Select`/`SelectTrigger`/`SelectContent`/`SelectItem` 등)과 다르면 이후 태스크에서 사용할 이름을 이 단계 결과에 맞춰 메모해 둔다.

- [ ] **Step 3: 타입 체크로 컴파일 확인**

```bash
cd frontend
npx tsc --noEmit
```

Expected: 에러 없음 (기존 코드가 아직 이 컴포넌트들을 사용하지 않으므로 새 파일 자체의 타입 오류만 없으면 됨)

- [ ] **Step 4: dev 서버로 런타임 확인**

```bash
cd frontend
npm run dev
```

`http://localhost:3000`을 Playwright로 열어 콘솔에 새 컴포넌트 관련 import 에러가 없는지 확인 (아직 아무 곳에서도 사용하지 않으므로 페이지 자체는 변화 없음).

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/ui/input.tsx frontend/components/ui/select.tsx frontend/components/ui/popover.tsx frontend/components/ui/command.tsx frontend/components/ui/dialog.tsx frontend/components/ui/alert-dialog.tsx frontend/components/ui/tooltip.tsx frontend/package.json frontend/package-lock.json
git commit -m "feat: shadcn Input/Select/Popover/Command/Dialog/AlertDialog/Tooltip 추가"
```

---

## Task 3: 공용 InfoTooltip을 shadcn Tooltip 기반으로 통합

**Files:**
- Create: `frontend/components/InfoTooltip.tsx`
- Modify: `frontend/app/layout.tsx` (TooltipProvider로 감싸기)
- Modify: `frontend/components/MetricTile.tsx:1-28` (자체 `InfoTooltip` 정의 제거, 공용 컴포넌트 import)
- Modify: `frontend/components/StrategyConditionBuilder.tsx:95-118` (자체 `InfoTooltip` 정의 제거, 공용 컴포넌트 import)

**Interfaces:**
- Consumes: Task 2에서 생성된 `components/ui/tooltip.tsx`의 `Tooltip`/`TooltipTrigger`/`TooltipContent`/`TooltipProvider` (정확한 이름은 Task 2 Step 2 확인 결과 기준).
- Produces: `InfoTooltip({ text: string }): JSX.Element` — `MetricTile.tsx`, `StrategyConditionBuilder.tsx`가 이 시그니처로 소비.

- [ ] **Step 1: `components/InfoTooltip.tsx` 신규 작성**

```tsx
'use client';

import { CircleHelp } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

export default function InfoTooltip({ text }: { text: string }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="flex h-4 w-4 shrink-0 items-center justify-center text-muted-foreground hover:text-foreground"
          aria-label="설명 보기"
        >
          <CircleHelp className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-64 whitespace-pre-line text-left">{text}</TooltipContent>
    </Tooltip>
  );
}
```

- [ ] **Step 2: `layout.tsx`에 `TooltipProvider` 추가**

`frontend/app/layout.tsx` 전체를 아래로 교체:

```tsx
import type { Metadata } from 'next';
import NavTabs from '@/components/NavTabs';
import { TooltipProvider } from '@/components/ui/tooltip';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <TooltipProvider>
          <NavTabs />
          <main className="p-6">{children}</main>
        </TooltipProvider>
      </body>
    </html>
  );
}
```

(Task 2 Step 2에서 `tooltip.tsx`가 `TooltipProvider`를 export하지 않는 것으로 확인되면 — 일부 `base-ui` 스타일은 `Tooltip` 컴포넌트 자체가 내부적으로 provider를 포함함 — 이 스텝은 건너뛰고 `layout.tsx`는 원본 그대로 둔다.)

- [ ] **Step 3: `MetricTile.tsx`에서 중복 `InfoTooltip` 제거**

`frontend/components/MetricTile.tsx`의 1~28행(자체 `InfoTooltip` 함수 정의 + `useState` import)을 삭제하고 공용 컴포넌트를 import하도록 교체. 파일 전체를 아래로 교체:

```tsx
import InfoTooltip from '@/components/InfoTooltip';

interface MetricTileProps {
  label: string;
  value: string;
  colorClass?: string;
  tooltip?: string;
}

export default function MetricTile({ label, value, colorClass, tooltip }: MetricTileProps) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </div>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}
```

- [ ] **Step 4: `StrategyConditionBuilder.tsx`에서 중복 `InfoTooltip` 제거**

`frontend/components/StrategyConditionBuilder.tsx`의 95~118행(자체 `InfoTooltip` 함수 정의)을 삭제하고, 파일 상단 import 블록(1~7행)에 아래 줄을 추가:

```tsx
import InfoTooltip from '@/components/InfoTooltip';
```

(`useState` import는 `ConditionBlockEditor`/`ConditionGroupEditor`가 계속 쓰지 않으므로 — 실제로는 이 파일에서 `useState`는 `InfoTooltip` 내부에서만 쓰였다. 삭제 후 `'use client';` 바로 아래의 `import { useState } from 'react';` 줄도 함께 제거한다.)

- [ ] **Step 5: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 `/` 접속 → "매수 조건"에서 지표를 하나 선택 → 지표명 옆 물음표 아이콘에 마우스를 올려 툴팁이 뜨는지 확인. `/backtests/{임의 run_id}` 상세 페이지의 "성과 지표" 그리드에서도 툴팁이 동일하게 동작하는지 확인.

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/InfoTooltip.tsx frontend/app/layout.tsx frontend/components/MetricTile.tsx frontend/components/StrategyConditionBuilder.tsx
git commit -m "refactor: 중복 InfoTooltip을 shadcn Tooltip 기반 공용 컴포넌트로 통합"
```

---

## Task 4: Nav 셸 아이콘 + 다크모드 토글

**Files:**
- Create: `frontend/components/ThemeToggle.tsx`
- Modify: `frontend/components/NavTabs.tsx` (전체 교체)
- Modify: `frontend/app/layout.tsx` (FOUC 방지 인라인 스크립트 추가)

**Interfaces:**
- Produces: `ThemeToggle` — `NavTabs`가 렌더링에 사용. 외부 상태 없음(자체 `localStorage` + `document.documentElement.classList` 조작).

- [ ] **Step 1: `ThemeToggle.tsx` 작성**

```tsx
'use client';

import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';

function persistTheme(dark: boolean) {
  document.documentElement.classList.toggle('dark', dark);
  localStorage.setItem('theme', dark ? 'dark' : 'light');
}

export default function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains('dark'));
  }, []);

  function toggle() {
    const next = !dark;
    setDark(next);
    persistTheme(next);
  }

  return (
    <Button type="button" variant="ghost" size="icon" onClick={toggle} aria-label="다크모드 전환">
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  );
}
```

- [ ] **Step 2: `layout.tsx`에 FOUC 방지 스크립트 추가**

`frontend/app/layout.tsx`의 `<html>` 내부, `<body>` 이전에 `<head>`를 명시적으로 추가 (Task 3에서 `TooltipProvider`를 이미 넣었다면 그 구조 위에 `<head>`만 추가):

```tsx
import type { Metadata } from 'next';
import NavTabs from '@/components/NavTabs';
import { TooltipProvider } from '@/components/ui/tooltip';
import './globals.css';

export const metadata: Metadata = {
  title: 'Upbit 전략 EDA 대시보드',
};

const THEME_INIT_SCRIPT = `(function(){try{var t=localStorage.getItem('theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;document.documentElement.classList.toggle('dark',d);}catch(e){}})();`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <TooltipProvider>
          <NavTabs />
          <main className="p-6">{children}</main>
        </TooltipProvider>
      </body>
    </html>
  );
}
```

(Task 3에서 `TooltipProvider` export가 없어 그 스텝을 건너뛴 경우, 여기서도 `TooltipProvider` 래핑 없이 `<NavTabs /><main>...</main>`만 유지한다.)

- [ ] **Step 3: `NavTabs.tsx`에 아이콘 + 토글 추가**

`frontend/components/NavTabs.tsx` 전체를 아래로 교체:

```tsx
'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { BarChart3, FlaskConical, Settings } from 'lucide-react';
import ThemeToggle from '@/components/ThemeToggle';

const STEPS = [
  { href: '/', title: '백테스트 설정', icon: Settings },
  { href: '/backtests', title: '백테스트 결과', icon: FlaskConical },
  { href: '/analysis', title: '분석', icon: BarChart3 },
];

function isActive(pathname: string, href: string): boolean {
  if (href === '/') return pathname === '/';
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function NavTabs() {
  const pathname = usePathname();

  return (
    <header className="flex items-center justify-between border-b px-6">
      <nav className="flex gap-6">
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
      <ThemeToggle />
    </header>
  );
}
```

- [ ] **Step 4: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 `/` 접속:
1. 상단 탭 3개에 각각 아이콘(Settings/FlaskConical/BarChart3)이 보이는지 확인
2. 우측 토글 버튼 클릭 → `<html>`에 `.dark` 클래스가 붙고 배경/텍스트 색이 다크 테마로 바뀌는지 확인 (Sun/Moon 아이콘도 전환)
3. 페이지 새로고침 후에도 다크모드가 유지되는지(localStorage 확인) 검증

- [ ] **Step 5: 커밋**

```bash
git add frontend/components/ThemeToggle.tsx frontend/components/NavTabs.tsx frontend/app/layout.tsx
git commit -m "feat: 상단 탭 아이콘 추가 및 다크모드 토글 구현"
```

---

## Task 5: PortSetupForm 리팩터

**Files:**
- Modify: `frontend/components/PortSetupForm.tsx` (전체 교체)
- Modify: `frontend/lib/ui-classes.ts` (`SECTION_HEADER_CLASS`의 `bg-slate-50 dark:bg-slate-800` → `bg-muted`)

**Interfaces:**
- Consumes: Task 2의 `Input`(`components/ui/input.tsx`), `Select`/`SelectTrigger`/`SelectContent`/`SelectItem`(`components/ui/select.tsx`), `AlertDialog`류(`components/ui/alert-dialog.tsx`).

- [ ] **Step 1: `lib/ui-classes.ts`의 하드코딩 색상 교체**

`frontend/lib/ui-classes.ts` 전체를 아래로 교체:

```ts
export const INPUT_CLASS =
  'h-10 rounded-md border border-input bg-background px-3 text-sm shadow-sm outline-none focus:ring-2 focus:ring-ring';

export const SELECT_CLASS = `${INPUT_CLASS} w-full`;

export const SECTION_HEADER_CLASS =
  'border-b bg-muted px-4 py-2 text-sm font-medium';
```

- [ ] **Step 2: `PortSetupForm.tsx` 리팩터**

`frontend/components/PortSetupForm.tsx` 전체를 아래로 교체:

```tsx
'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { CalendarRange, Play, TriangleAlert, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import CoinSelect, { sortMarkets } from '@/components/CoinSelect';
import StrategyConditionBuilder from '@/components/StrategyConditionBuilder';
import type { ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem, Market } from '@/lib/types/eda';
import { getIndicatorCatalog, getMarkets, runBacktest, validateBacktest } from '@/lib/api/eda';
import { ApiError } from '@/lib/api/client';
import { SECTION_HEADER_CLASS } from '@/lib/ui-classes';

const CANDLE_UNITS = [
  { label: '15분', timeframe: 'minutes15' },
  { label: '30분', timeframe: 'minutes30' },
  { label: '1시간', timeframe: 'minutes60' },
  { label: '1일', timeframe: 'days' },
];

const EMPTY_CONDITION_GROUP: ConditionGroup = { type: 'AND', conditions: [] };

function defaultDate(daysAgo: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  return d.toISOString().slice(0, 10);
}

function formatCapital(digits: string): string {
  if (!digits) return '';
  return Number(digits).toLocaleString('ko-KR');
}

export default function PortSetupForm() {
  const router = useRouter();

  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');

  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');

  const [catalog, setCatalog] = useState<IndicatorCatalogItem[]>([]);
  const [catalogError, setCatalogError] = useState<string | null>(null);

  const [buyConditions, setBuyConditions] = useState<ConditionGroup>(EMPTY_CONDITION_GROUP);
  const [sellConditions, setSellConditions] = useState<ConditionGroup>(EMPTY_CONDITION_GROUP);
  const [capital, setCapital] = useState('1000000');
  const [timeframe, setTimeframe] = useState(CANDLE_UNITS[0].timeframe);
  const [startDate, setStartDate] = useState(defaultDate(90));
  const [startTime, setStartTime] = useState('00:00');
  const [endDate, setEndDate] = useState(defaultDate(0));
  const [endTime, setEndTime] = useState('00:00');

  const [submitting, setSubmitting] = useState(false);
  const [validationErrors, setValidationErrors] = useState<string[] | null>(null);

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const sorted = sortMarkets(data, 'change_rate', 'desc');
        if (sorted.length > 0) setMarket((prev) => prev || sorted[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));

    getIndicatorCatalog()
      .then(setCatalog)
      .catch((err) => setCatalogError(err instanceof ApiError ? err.message : '지표 목록을 불러오지 못했습니다.'));
  }, []);

  async function handleRun() {
    const request = {
      market,
      timeframe,
      start: startDate,
      end: endDate,
      initial_capital: Number(capital),
      buy_conditions: buyConditions,
      sell_conditions: sellConditions,
      title: title || null,
      description: description || null,
    };

    setSubmitting(true);
    try {
      const validation = await validateBacktest(request);
      if (!validation.valid) {
        setValidationErrors(validation.errors);
        return;
      }

      const { run_id } = await runBacktest(request);
      router.push(`/backtests/${run_id}`);
    } catch (err) {
      setValidationErrors([err instanceof ApiError ? err.message : '백테스트 실행 중 오류가 발생했습니다.']);
    } finally {
      setSubmitting(false);
    }
  }

  const selectedMarketPrice = markets.find((m) => m.market === market)?.price ?? null;

  return (
    <div className="max-w-5xl space-y-6 rounded-xl border p-6 shadow-sm">
      <div className="grid grid-cols-[1fr_1fr_4fr] gap-6">
        <div>
          <label className="mb-1.5 block text-sm font-medium">포트 제목</label>
          <Input
            type="text"
            placeholder="포트폴리오 제목을 입력해 주세요."
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">
            포트 설명 <span className="font-normal text-muted-foreground">(선택사항)</span>
          </label>
          <Input
            type="text"
            placeholder="포트폴리오에 대한 설명을 100자 이내로 남겨주세요."
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택</label>
          <CoinSelect markets={markets} value={market} onChange={setMarket} />
          {marketsError && (
            <p className="mt-1 flex items-center gap-1 text-xs text-destructive">
              <TriangleAlert className="size-3.5" />
              {marketsError}
            </p>
          )}
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">전략 선택</h2>
        {catalogError && (
          <p className="mb-2 flex items-center gap-1 text-xs text-destructive">
            <TriangleAlert className="size-3.5" />
            {catalogError}
          </p>
        )}
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-md border">
            <StrategyConditionBuilder
              label="매수 조건"
              group={buyConditions}
              catalog={catalog.filter((c) => !c.sellOnly)}
              currentPrice={selectedMarketPrice}
              onChange={setBuyConditions}
            />
          </div>
          <div className="rounded-md border">
            <StrategyConditionBuilder
              label="매도 조건"
              group={sellConditions}
              catalog={catalog}
              currentPrice={selectedMarketPrice}
              onChange={setSellConditions}
            />
          </div>
        </div>
      </div>

      <div>
        <h2 className="mb-2 text-sm font-semibold">기본 조건</h2>
        <div className="grid grid-cols-[1fr_1fr_3fr] divide-x rounded-md border">
          <div>
            <div className={SECTION_HEADER_CLASS}>운용자금</div>
            <div className="flex items-center gap-2 p-4">
              <Input
                type="text"
                inputMode="numeric"
                value={formatCapital(capital)}
                onChange={(e) => setCapital(e.target.value.replace(/[^0-9]/g, ''))}
              />
              <span className="text-sm text-muted-foreground">원</span>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>봉데이터 선택</div>
            <div className="space-y-2 p-4">
              <Select value={timeframe} onValueChange={setTimeframe}>
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CANDLE_UNITS.map((u) => (
                    <SelectItem key={u.timeframe} value={u.timeframe}>
                      {u.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div>
            <div className={SECTION_HEADER_CLASS}>운용기간</div>
            <div className="space-y-2 p-4">
              <div className="flex flex-nowrap items-center gap-2">
                <Input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} />
                <Input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
                <span className="text-sm text-muted-foreground">~</span>
                <Input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} />
                <Input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />
              </div>
              <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
                <p className="text-xs text-muted-foreground">기간이 길고 봉타입이 짧을수록 최초 조회 시 시간이 걸릴 수 있습니다.</p>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setStartDate(defaultDate(90));
                    setEndDate(defaultDate(0));
                  }}
                >
                  <CalendarRange className="size-3.5" />
                  최근 최대 기간 설정
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t pt-4">
        <Button type="button" variant="outline" onClick={() => console.log('cancel (mock)')}>
          <X className="size-4" />
          취소
        </Button>
        <Button type="button" onClick={handleRun} disabled={submitting || !market}>
          <Play className="size-4" />
          {submitting ? '검증 중...' : '백테스트 실행'}
        </Button>
      </div>

      <AlertDialog open={!!validationErrors} onOpenChange={(open) => !open && setValidationErrors(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-1.5 text-destructive">
              <TriangleAlert className="size-4" />
              백테스트를 실행할 수 없습니다
            </AlertDialogTitle>
          </AlertDialogHeader>
          <ul className="mb-4 list-disc space-y-1 pl-5 text-sm text-muted-foreground">
            {(validationErrors ?? []).map((error, i) => (
              <li key={i}>{error}</li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogAction onClick={() => setValidationErrors(null)}>확인</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
```

`Select`의 `onValueChange`/`value` prop 이름과 `AlertDialog`의 `open`/`onOpenChange` 제어형(controlled) 사용법은 Task 2 Step 2에서 확인한 실제 export 시그니처에 맞춰 조정한다(표준 shadcn API 기준으로 작성됨).

- [ ] **Step 3: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 `/` 접속:
1. 포트 제목/설명 입력, 운용자금 입력, 봉데이터 select 변경이 정상 동작하는지 확인
2. 코인 미선택 상태로 "백테스트 실행" 클릭 시 validation 에러가 `AlertDialog`(모달)로 뜨는지 확인 — 이전엔 raw `fixed inset-0` div였던 것이 shadcn Dialog 스타일(배경 dim, 포커스 트랩)로 바뀌었는지 확인
3. "최근 최대 기간 설정" 버튼에 `CalendarRange` 아이콘이 보이는지 확인

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/PortSetupForm.tsx frontend/lib/ui-classes.ts
git commit -m "refactor: PortSetupForm을 shadcn Input/Select/AlertDialog + 아이콘 기반으로 리팩터"
```

---

## Task 6: CoinSelect를 Popover+Command 기반으로 재작성

**Files:**
- Modify: `frontend/components/CoinSelect.tsx` (전체 교체)

**Interfaces:**
- Consumes: Task 2의 `Popover`/`PopoverTrigger`/`PopoverContent`, `Command`/`CommandInput`/`CommandList`/`CommandEmpty`/`CommandItem`.
- Produces: 기존과 동일한 `export default function CoinSelect({ markets, value, onChange }: CoinSelectProps)`, `export function sortMarkets(...)`, `export type MarketSortKey` 시그니처 유지 — `PortSetupForm.tsx`(Task 5)가 이 시그니처를 그대로 소비하므로 변경 금지.

- [ ] **Step 1: `CoinSelect.tsx` 재작성**

`frontend/components/CoinSelect.tsx` 전체를 아래로 교체 (정렬/검색 로직은 기존 그대로 유지, 트리거+리스트만 Popover+Command로 교체):

```tsx
'use client';

import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from 'lucide-react';
import type { Market } from '@/lib/types/eda';
import { getMarkets } from '@/lib/api/eda';
import { INPUT_CLASS } from '@/lib/ui-classes';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Command, CommandEmpty, CommandInput, CommandItem, CommandList } from '@/components/ui/command';

export type MarketSortKey = 'change_rate' | 'trade_price_24h';
type SortDir = 'asc' | 'desc';

export function sortMarkets(list: Market[], key: MarketSortKey, dir: SortDir): Market[] {
  const factor = dir === 'asc' ? 1 : -1;
  return [...list].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    return (av - bv) * factor;
  });
}

function changeColorClass(rate: number | null): string {
  if (!rate) return 'text-foreground';
  return rate > 0 ? 'text-red-600 dark:text-red-400' : 'text-blue-600 dark:text-blue-400';
}

function formatPrice(price: number | null): string {
  if (price === null) return '-';
  if (price === 0) return '0';
  if (price >= 100) return Math.round(price).toLocaleString('ko-KR');
  const magnitude = Math.floor(Math.log10(Math.abs(price)));
  const decimals = Math.max(0, 2 - magnitude);
  return price.toLocaleString('ko-KR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function formatChangeRate(rate: number | null): string {
  if (rate === null) return '-';
  return `${(Math.abs(rate) * 100).toFixed(2)}%`;
}

function formatChangePrice(price: number | null): string {
  if (price === null) return '-';
  return formatPrice(Math.abs(price));
}

function formatTradePrice24h(value: number | null): string {
  if (value === null) return '-';
  return `${Math.round(value / 1_000_000).toLocaleString('ko-KR')}백만`;
}

interface CoinSelectProps {
  markets: Market[];
  value: string;
  onChange: (market: string) => void;
}

export default function CoinSelect({ markets, value, onChange }: CoinSelectProps) {
  const [open, setOpen] = useState(false);
  const [liveMarkets, setLiveMarkets] = useState(markets);
  const [refreshing, setRefreshing] = useState(false);
  const [sortKey, setSortKey] = useState<MarketSortKey>('change_rate');
  const [sortDir, setSortDir] = useState<SortDir>('desc');
  const [query, setQuery] = useState('');

  useEffect(() => {
    setLiveMarkets(markets);
  }, [markets]);

  const sorted = useMemo(() => sortMarkets(liveMarkets, sortKey, sortDir), [liveMarkets, sortKey, sortDir]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return sorted;
    return sorted.filter(
      (m) => m.korean_name.toLowerCase().includes(q) || m.market.replace('KRW-', '').toLowerCase().includes(q)
    );
  }, [sorted, query]);
  const selected = liveMarkets.find((m) => m.market === value) ?? null;

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setRefreshing(true);
      getMarkets()
        .then(setLiveMarkets)
        .catch(() => {})
        .finally(() => setRefreshing(false));
    } else {
      setQuery('');
    }
  }

  function toggleSort(key: MarketSortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function SortIcon({ sortKeyOf }: { sortKeyOf: MarketSortKey }) {
    if (sortKey !== sortKeyOf) return <ArrowUpDown className="size-3.5" />;
    return sortDir === 'desc' ? <ArrowDown className="size-3.5" /> : <ArrowUp className="size-3.5" />;
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={`${INPUT_CLASS} flex w-full items-center justify-between gap-3`}
          disabled={liveMarkets.length === 0}
        >
          {selected ? (
            <>
              <span className="truncate font-medium">
                {selected.korean_name} <span className="text-xs text-muted-foreground">({selected.market})</span>
              </span>
              <span className="flex shrink-0 items-center gap-3 tabular-nums">
                <span className="font-semibold">{formatPrice(selected.price)}</span>
                <span className={`font-semibold ${changeColorClass(selected.change_rate)}`}>
                  {formatChangeRate(selected.change_rate)}
                </span>
              </span>
            </>
          ) : (
            <span className="text-muted-foreground">불러오는 중...</span>
          )}
        </button>
      </PopoverTrigger>
      <PopoverContent className="w-[var(--radix-popover-trigger-width)] min-w-80 p-0" align="start">
        <Command shouldFilter={false}>
          <div className="flex items-center gap-2 border-b px-3">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <CommandInput
              placeholder="한글명 또는 티커로 검색 (예: 비트코인, BTC)"
              value={query}
              onValueChange={setQuery}
              className="border-0 focus:ring-0"
            />
          </div>
          <div className="grid grid-cols-[2fr_1fr_1fr_1fr] gap-2 border-b bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
            <span>{refreshing ? '새로고침 중...' : '한글명'}</span>
            <span className="text-right">현재가</span>
            <button type="button" className="flex items-center justify-end gap-1 hover:text-foreground" onClick={() => toggleSort('change_rate')}>
              전일대비 <SortIcon sortKeyOf="change_rate" />
            </button>
            <button type="button" className="flex items-center justify-end gap-1 hover:text-foreground" onClick={() => toggleSort('trade_price_24h')}>
              거래대금 <SortIcon sortKeyOf="trade_price_24h" />
            </button>
          </div>
          <CommandList className="max-h-80">
            <CommandEmpty>검색 결과가 없습니다.</CommandEmpty>
            {filtered.map((m) => (
              <CommandItem
                key={m.market}
                value={m.market}
                onSelect={() => {
                  onChange(m.market);
                  setOpen(false);
                }}
                className={`grid grid-cols-[2fr_1fr_1fr_1fr] items-center gap-2 ${m.market === value ? 'bg-muted' : ''}`}
              >
                <span>
                  <span className="block font-medium">{m.korean_name}</span>
                  <span className="block text-xs text-muted-foreground">{m.market.replace('KRW-', '')}/KRW</span>
                </span>
                <span className="text-right font-semibold tabular-nums">{formatPrice(m.price)}</span>
                <span className={`text-right tabular-nums ${changeColorClass(m.change_rate)}`}>
                  <span className="block font-semibold">{formatChangeRate(m.change_rate)}</span>
                  <span className="block text-xs">{formatChangePrice(m.change_price)}</span>
                </span>
                <span className="text-right tabular-nums text-muted-foreground">
                  {formatTradePrice24h(m.trade_price_24h)}
                </span>
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
```

`PopoverContent`의 `w-[var(--radix-popover-trigger-width)]`는 Radix 계열 shadcn 스타일의 트리거 너비 동기화 CSS 변수명이다. `base-nova`(`@base-ui/react`) 스타일은 다른 변수명(예: `--anchor-width`)을 쓸 수 있으므로, Task 2 Step 2에서 확인한 `popover.tsx` 내부 구현을 보고 실제 변수명으로 교체한다(없으면 `w-80` 등 고정폭으로 대체).

- [ ] **Step 2: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 `/` 접속 → "코인 선택" 트리거 클릭 → Popover가 열리고 검색창에 자동 포커스되는지, "비트코인"/"BTC" 검색 시 필터링되는지, 정렬 헤더(전일대비/거래대금) 클릭 시 아이콘이 `ArrowUpDown → ArrowUp/ArrowDown`으로 바뀌는지, 항목 선택 시 Popover가 닫히고 트리거 텍스트가 갱신되는지 확인. 키보드로 위/아래 화살표 이동이 되는지도 확인(Command의 기본 제공 기능).

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/CoinSelect.tsx
git commit -m "refactor: CoinSelect를 shadcn Popover+Command 기반으로 재작성"
```

---

## Task 7: StrategyConditionBuilder 리팩터

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx` (Task 3에서 `InfoTooltip` 중복 제거가 이미 반영된 상태 기준으로 추가 수정)

**Interfaces:**
- Consumes: Task 2의 `Select`/`SelectTrigger`/`SelectContent`/`SelectGroup`/`SelectLabel`/`SelectItem`(`optgroup` 대응).
- Produces: 기존과 동일한 `export default function StrategyConditionBuilder({ label, group, catalog, currentPrice, onChange })` 시그니처 유지 — `PortSetupForm.tsx`가 이 시그니처를 그대로 소비하므로 변경 금지.

- [ ] **Step 1: 카테고리 아이콘 매핑 추가 및 네이티브 select → Select 교체**

`frontend/components/StrategyConditionBuilder.tsx` 상단 import 및 상수 블록(1~25행, Task 3 반영 후 기준)을 아래로 교체:

```tsx
'use client';

import { Activity, BarChart3, DollarSign, Plus, TrendingUp, Users, X } from 'lucide-react';
import type { ComparisonOperator, ConditionBlock, ConditionGroup } from '@/lib/types/strategy';
import type { IndicatorCatalogItem } from '@/lib/types/eda';
import { INPUT_CLASS, SECTION_HEADER_CLASS } from '@/lib/ui-classes';
import { OPERATOR_SYMBOLS, isConditionBlock, summarizeGroup } from '@/lib/condition-summary';
import InfoTooltip from '@/components/InfoTooltip';
import { Select, SelectContent, SelectGroup, SelectItem, SelectLabel, SelectTrigger, SelectValue } from '@/components/ui/select';

const CATEGORY_ORDER = ['추세', '오실레이터', '거래량', '손익', '시장 심리'];

const CATEGORY_DOT_COLOR: Record<string, string> = {
  추세: 'bg-blue-500',
  오실레이터: 'bg-violet-500',
  거래량: 'bg-teal-500',
  손익: 'bg-orange-500',
  '시장 심리': 'bg-rose-500',
};

const CATEGORY_ICON: Record<string, typeof TrendingUp> = {
  추세: TrendingUp,
  오실레이터: Activity,
  거래량: BarChart3,
  손익: DollarSign,
  '시장 심리': Users,
};

const OPERATORS: { value: ComparisonOperator; label: string }[] = [
  { value: '>', label: '초과 (>)' },
  { value: '<', label: '미만 (<)' },
  { value: '>=', label: '이상 (≥)' },
  { value: '<=', label: '이하 (≤)' },
  { value: '==', label: '같음 (=)' },
];
```

(이 파일의 나머지 순수 로직 함수들 — `groupByCategory`, `defaultParamsFor`, `OSCILLATOR_BOUNDS`, `ZERO_CROSS_INDICATORS`, `PRICE_SCALE_INDICATORS`, `POSITION_RELATIVE_DEFAULTS`, `recommendedThreshold`, `createDefaultBlock`, `createDefaultGroup` — 은 그대로 둔다.)

- [ ] **Step 2: `ConditionBlockEditor`의 지표 select를 shadcn Select로 교체**

`ConditionBlockEditor` 함수의 return 블록(기존 파일의 지표 select 부분)을 아래로 교체:

```tsx
  return (
    <div className="rounded-md border">
      <div className={`flex items-center gap-2 rounded-t-md border-b px-3 py-2 ${SECTION_HEADER_CLASS.includes('bg-muted') ? 'bg-muted' : ''}`}>
        <span className={`h-2 w-2 shrink-0 rounded-full ${dotColor}`} />
        <Select value={block.indicator} onValueChange={handleIndicatorChange}>
          <SelectTrigger className="h-auto flex-1 border-0 bg-transparent p-0 text-sm font-medium shadow-none focus:ring-0">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {categories.map((cat) => {
              const CategoryIcon = CATEGORY_ICON[cat.label] ?? TrendingUp;
              return (
                <SelectGroup key={cat.label}>
                  <SelectLabel className="flex items-center gap-1.5">
                    <CategoryIcon className="size-3.5" />
                    {cat.label}
                  </SelectLabel>
                  {cat.items.map((ind) => (
                    <SelectItem key={ind.value} value={ind.value}>
                      {ind.label}
                    </SelectItem>
                  ))}
                </SelectGroup>
              );
            })}
          </SelectContent>
        </Select>
        {tooltip && <InfoTooltip text={tooltip} />}
        <button
          type="button"
          onClick={onDelete}
          className="shrink-0 text-muted-foreground hover:text-red-500"
          aria-label="조건 삭제"
        >
          <X className="size-4" />
        </button>
      </div>
```

(이후 파라미터 입력 영역, 연산자/threshold 영역은 그대로 두되, 연산자 `<select>`도 아래처럼 교체한다.)

- [ ] **Step 3: 연산자 select 교체**

`ConditionBlockEditor`의 연산자 select 부분을 아래로 교체:

```tsx
        {catalogItem?.fixedOperator ? (
          <span className="flex h-7 shrink-0 items-center rounded border border-input bg-muted px-2 font-mono text-xs text-muted-foreground">
            {OPERATOR_SYMBOLS[catalogItem.fixedOperator]} 고정
          </span>
        ) : (
          <Select
            value={block.operator}
            onValueChange={(v) => onChange({ ...block, operator: v as ComparisonOperator })}
          >
            <SelectTrigger className="h-7 w-auto border-input bg-background px-1 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {OPERATORS.map((op) => (
                <SelectItem key={op.value} value={op.value}>
                  {op.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
```

같은 블록의 `<span className="rounded bg-slate-100 ... dark:bg-slate-800">`(지표명 배지)는 `bg-muted`로, 파라미터 입력값을 감싸는 배경도 동일하게 `bg-slate-100 dark:bg-slate-800` → `bg-muted`로 교체한다.

- [ ] **Step 4: 삭제 아이콘(✕ → X) 및 추가 버튼 아이콘화**

`ConditionGroupEditor`의 괄호 그룹 삭제 버튼(`✕` 문자)을 아래로 교체:

```tsx
                <button
                  type="button"
                  onClick={() => deleteCondition(index)}
                  className="text-xs text-muted-foreground hover:text-red-500"
                  aria-label="괄호 묶음 삭제"
                >
                  <X className="size-3.5" />
                </button>
```

"+ 조건 추가"/"+ 괄호 묶음 추가" 버튼을 아래로 교체:

```tsx
      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={addBlock}
          className={`flex flex-1 items-center justify-center gap-1 ${INPUT_CLASS} bg-background text-xs font-medium hover:bg-muted`}
        >
          <Plus className="size-3.5" />
          조건 추가
        </button>
        {depth < 2 && (
          <button
            type="button"
            onClick={addGroup}
            className="flex flex-1 items-center justify-center gap-1 rounded-md border border-primary px-2 py-1.5 text-xs font-medium text-primary hover:bg-muted"
          >
            <Plus className="size-3.5" />
            괄호 묶음 추가
          </button>
        )}
      </div>
```

- [ ] **Step 5: 나머지 `bg-slate-50/800`/`bg-slate-100/800` → `bg-muted` 일괄 교체**

파일 내 남은 `bg-slate-50 dark:bg-slate-800`, `bg-slate-100 dark:bg-slate-800`, `hover:bg-slate-50 dark:hover:bg-slate-800` 패턴(하단 `StrategyConditionBuilder`의 조건식 요약 바 등)을 모두 `bg-muted`/`hover:bg-muted`로 교체한다.

- [ ] **Step 6: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 `/` 접속 → 매수 조건에서 지표 Select를 열어 카테고리 그룹(아이콘 포함)이 보이는지, 지표 변경이 정상 동작하는지, 조건 삭제 버튼(X 아이콘), "조건 추가"/"괄호 묶음 추가" 버튼(Plus 아이콘)이 정상 동작하는지 확인.

- [ ] **Step 7: 커밋**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "refactor: StrategyConditionBuilder를 shadcn Select + lucide 아이콘 기반으로 리팩터"
```

---

## Task 8: BacktestRunsTable + DeleteRunButton 리팩터

**Files:**
- Modify: `frontend/components/BacktestRunsTable.tsx` (전체 교체)
- Modify: `frontend/components/DeleteRunButton.tsx` (전체 교체)

**Interfaces:**
- Consumes: Task 2의 `AlertDialog`류.
- Produces: `DeleteRunButton({ runId: string })` 시그니처 유지 — `BacktestRunsTable.tsx`가 그대로 소비.

- [ ] **Step 1: `DeleteRunButton.tsx`를 AlertDialog 기반으로 재작성**

`frontend/components/DeleteRunButton.tsx` 전체를 아래로 교체:

```tsx
'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import { deleteBacktestRun } from '@/lib/api/eda';

export default function DeleteRunButton({ runId }: { runId: string }) {
  const router = useRouter();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleDelete() {
    setPending(true);
    setError(null);
    try {
      await deleteBacktestRun(runId);
      router.refresh();
    } catch {
      setError('삭제에 실패했습니다. 잠시 후 다시 시도해 주세요.');
      setPending(false);
    }
  }

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button type="button" variant="ghost" size="icon-sm" disabled={pending} aria-label="삭제">
          <Trash2 className="size-4 text-destructive" />
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>이 백테스트 결과를 삭제하시겠습니까?</AlertDialogTitle>
          <AlertDialogDescription>삭제 후에는 되돌릴 수 없습니다.</AlertDialogDescription>
        </AlertDialogHeader>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <AlertDialogFooter>
          <AlertDialogCancel>취소</AlertDialogCancel>
          <AlertDialogAction onClick={handleDelete}>삭제</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
```

- [ ] **Step 2: `BacktestRunsTable.tsx` 정렬 아이콘 + "보기" 링크 교체**

`frontend/components/BacktestRunsTable.tsx` 전체를 아래로 교체:

```tsx
'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowDown, ArrowUp, ArrowUpDown, Eye } from 'lucide-react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import DeleteRunButton from '@/components/DeleteRunButton';
import { returnRateColor } from '@/lib/return-rate-color';
import { summarizeGroup } from '@/lib/condition-summary';
import { formatDateTime } from '@/lib/format';
import type { BacktestRunSummary } from '@/lib/types/eda';

type SortKey = 'return_rate' | 'created_at' | 'market' | 'timeframe';
type SortDir = 'asc' | 'desc';

function sortRuns(runs: BacktestRunSummary[], key: SortKey | null, dir: SortDir): BacktestRunSummary[] {
  if (!key) return runs;
  const factor = dir === 'asc' ? 1 : -1;
  return [...runs].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av === null && bv === null) return 0;
    if (av === null) return 1;
    if (bv === null) return -1;
    if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
    return String(av).localeCompare(String(bv)) * factor;
  });
}

interface BacktestRunsTableProps {
  runs: BacktestRunSummary[];
}

export default function BacktestRunsTable({ runs }: BacktestRunsTableProps) {
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDir, setSortDir] = useState<SortDir>('desc');

  const sorted = useMemo(() => sortRuns(runs, sortKey, sortDir), [runs, sortKey, sortDir]);

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

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>제목</TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('market')}>
              코인 <SortIcon sortKeyOf="market" />
            </button>
          </TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('timeframe')}>
              봉타입 <SortIcon sortKeyOf="timeframe" />
            </button>
          </TableHead>
          <TableHead>기간</TableHead>
          <TableHead>매수전략</TableHead>
          <TableHead>매도전략</TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('return_rate')}>
              수익률(%) <SortIcon sortKeyOf="return_rate" />
            </button>
          </TableHead>
          <TableHead>
            <button type="button" className="flex items-center gap-1 hover:text-foreground" onClick={() => toggleSort('created_at')}>
              실행 시각 <SortIcon sortKeyOf="created_at" />
            </button>
          </TableHead>
          <TableHead>상세</TableHead>
          <TableHead>삭제</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {sorted.map((run) => (
          <TableRow key={run.run_id}>
            <TableCell>
              {run.title || <span className="text-muted-foreground">(제목 없음)</span>}
              {run.description && <p className="text-xs text-muted-foreground">{run.description}</p>}
            </TableCell>
            <TableCell>{run.market}</TableCell>
            <TableCell>{run.timeframe}</TableCell>
            <TableCell>
              {run.start.slice(0, 10)} ~ {run.end.slice(0, 10)}
            </TableCell>
            <TableCell className="max-w-[240px] whitespace-normal font-mono text-xs">
              {summarizeGroup(run.buy_conditions)}
            </TableCell>
            <TableCell className="max-w-[240px] whitespace-normal font-mono text-xs">
              {summarizeGroup(run.sell_conditions)}
            </TableCell>
            <TableCell className={returnRateColor(run.return_rate)}>
              {run.return_rate?.toFixed(2) ?? '-'}
              {run.is_live && <span className="ml-1 text-xs text-muted-foreground">(실시간)</span>}
            </TableCell>
            <TableCell>{formatDateTime(run.created_at)}</TableCell>
            <TableCell>
              <Button type="button" variant="link" size="sm" asChild className="px-0">
                <Link href={`/backtests/${run.run_id}`}>
                  <Eye className="size-3.5" />
                  보기
                </Link>
              </Button>
            </TableCell>
            <TableCell>
              <DeleteRunButton runId={run.run_id} />
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
```

- [ ] **Step 3: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 `/backtests` 접속(실행된 백테스트가 없으면 먼저 `/`에서 하나 실행): 정렬 헤더 아이콘 클릭 동작, "보기" 링크의 Eye 아이콘, "삭제" 버튼(Trash2 아이콘) 클릭 시 AlertDialog 확인창이 뜨는지, "취소"/"삭제" 버튼이 각각 정상 동작하는지(삭제 시 목록에서 사라짐) 확인.

- [ ] **Step 4: 커밋**

```bash
git add frontend/components/BacktestRunsTable.tsx frontend/components/DeleteRunButton.tsx
git commit -m "refactor: BacktestRunsTable/DeleteRunButton을 AlertDialog + 아이콘 기반으로 리팩터"
```

---

## Task 9: 백테스트 상세 페이지(`backtests/[runId]`) 리팩터

**Files:**
- Modify: `frontend/app/backtests/[runId]/page.tsx` (전체 교체)
- Modify: `frontend/components/MetricTile.tsx` (아이콘 prop 추가)

**Interfaces:**
- Consumes: `MetricTile`(Task 3에서 이미 InfoTooltip 통합됨).
- Produces: `MetricTile`에 선택적 `icon?: LucideIcon` prop 추가 — 기존 호출부(`tooltip`/`colorClass`만 쓰는 곳)는 하위 호환.

- [ ] **Step 1: `MetricTile.tsx`에 아이콘 prop 추가**

`frontend/components/MetricTile.tsx` 전체를 아래로 교체:

```tsx
import type { LucideIcon } from 'lucide-react';
import InfoTooltip from '@/components/InfoTooltip';

interface MetricTileProps {
  label: string;
  value: string;
  colorClass?: string;
  tooltip?: string;
  icon?: LucideIcon;
}

export default function MetricTile({ label, value, colorClass, tooltip, icon: Icon }: MetricTileProps) {
  return (
    <div className="rounded-md border p-3">
      <div className="flex items-center gap-1 text-xs text-muted-foreground">
        {Icon && <Icon className="size-3.5 shrink-0" />}
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </div>
      <p className={`mt-1 text-base font-semibold ${colorClass ?? ''}`}>{value}</p>
    </div>
  );
}
```

- [ ] **Step 2: 상세 페이지 리팩터 — 중복 metric strip 제거, 아이콘 추가**

`frontend/app/backtests/[runId]/page.tsx` 전체를 아래로 교체:

```tsx
import { Clock } from 'lucide-react';
import { getBacktestDetail } from '@/lib/api/eda';
import PriceChart from '@/components/PriceChart';
import MetricTile from '@/components/MetricTile';
import { Badge } from '@/components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';
import { formatDateTime } from '@/lib/format';
import type { BacktestMetrics } from '@/lib/types/eda';
import { Percent, Gauge, Repeat, Scale } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

function fmtPct(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function MetricsGrid({ metrics }: { metrics: BacktestMetrics }) {
  const tiles: { label: string; value: string; colorClass?: string; tooltip: string; icon: LucideIcon }[] = [
    {
      label: '총 수익률', value: fmtPct(metrics.total_return), colorClass: returnRateColor(metrics.total_return),
      tooltip: '초기 자본 대비 최종 자산의 증감률입니다.', icon: Percent,
    },
    {
      label: 'CAGR', value: fmtPct(metrics.cagr), colorClass: returnRateColor(metrics.cagr),
      tooltip: '연평균 복리 성장률입니다. 백테스트 기간과 무관하게 "연 단위로 환산하면 몇 %인가"를 보여줍니다.', icon: Percent,
    },
    {
      label: 'Buy&Hold', value: fmtPct(metrics.buy_and_hold_return), colorClass: returnRateColor(metrics.buy_and_hold_return),
      tooltip: '같은 기간 동안 그냥 사서 들고만 있었을 때의 수익률입니다. 전략이 단순 보유보다 나은지 비교하는 기준입니다.', icon: Percent,
    },
    {
      label: 'MDD', value: fmtPct(metrics.mdd), colorClass: returnRateColor(metrics.mdd),
      tooltip: '최대 낙폭(Max Drawdown). 자산이 고점 대비 가장 많이 떨어졌던 비율입니다. 작을수록(0에 가까울수록) 좋습니다.', icon: Percent,
    },
    {
      label: '샤프 비율', value: metrics.sharpe_ratio.toFixed(2),
      tooltip: '위험(변동성) 대비 수익률입니다. 무위험수익률 0%를 가정하며, 높을수록 안정적으로 수익을 냈다는 뜻입니다.', icon: Gauge,
    },
    {
      label: '소르티노', value: metrics.sortino_ratio.toFixed(2),
      tooltip: '샤프 비율과 비슷하지만 하락 변동성만 위험으로 봅니다. 상승 변동은 페널티로 치지 않아 샤프보다 후하게 나올 수 있습니다.', icon: Gauge,
    },
    {
      label: '칼마 비율', value: metrics.calmar_ratio.toFixed(2),
      tooltip: 'CAGR을 MDD(절대값)로 나눈 값입니다. 수익뿐 아니라 "그 수익을 위해 감수한 최대 손실"까지 함께 고려합니다.', icon: Gauge,
    },
    {
      label: '총 거래', value: `${metrics.total_trades}건`,
      tooltip: '백테스트 기간 동안 체결된 매수→매도 거래 쌍의 개수입니다.', icon: Repeat,
    },
    {
      label: '승률', value: `${metrics.win_rate.toFixed(1)}%`,
      tooltip: '전체 거래 중 수익이 난(pnl > 0) 거래의 비율입니다.', icon: Percent,
    },
    {
      label: '손익비', value: metrics.profit_factor.toFixed(2),
      tooltip: '총 이익 금액을 총 손실 금액으로 나눈 값입니다(Profit Factor). 1보다 크면 이익이 손실보다 큽니다.', icon: Scale,
    },
    {
      label: '평균 보유', value: `${metrics.avg_holding_period.toFixed(1)}일`,
      tooltip: '한 번 진입해서 청산까지 평균적으로 보유한 기간(일)입니다.', icon: Clock,
    },
    {
      label: '최대연속손실', value: `${metrics.max_consecutive_loss}건`,
      tooltip: '연속으로 손실이 난 거래의 최대 횟수입니다. 클수록 연속 손실 구간에서 심리적/자금 압박이 컸다는 뜻입니다.', icon: Repeat,
    },
  ];

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold">성과 지표</h2>
      <div className="grid grid-cols-6 gap-3">
        {tiles.map((tile) => (
          <MetricTile
            key={tile.label} label={tile.label} value={tile.value}
            colorClass={tile.colorClass} tooltip={tile.tooltip} icon={tile.icon}
          />
        ))}
      </div>
    </div>
  );
}

export default async function BacktestDetailPage({ params }: { params: { runId: string } }) {
  const detail = await getBacktestDetail(params.runId);

  return (
    <div>
      <h1 className="mb-1 text-lg font-semibold">백테스트 상세</h1>
      <p className="mb-1 text-sm text-muted-foreground">
        {detail.market} · {detail.timeframe} · {detail.start.slice(0, 10)} ~ {detail.end.slice(0, 10)}
      </p>
      {detail.live_price_as_of && (
        <p className="mb-4 flex items-center gap-1.5 text-xs text-amber-600 dark:text-amber-400">
          <Clock className="size-3.5" />
          미청산 포지션이 있어 현재가 기준으로 재평가됨 ({formatDateTime(detail.live_price_as_of)} 기준)
        </p>
      )}

      <div className="mb-6">
        <MetricTile label="총 수익률" value={fmtPct(detail.metrics.total_return)} colorClass={returnRateColor(detail.metrics.total_return)} icon={Percent} />
      </div>

      <div className="mb-6 grid grid-cols-5 gap-3 rounded-md border p-4">
        <MetricTile label="MDD" value={fmtPct(detail.metrics.mdd)} colorClass={returnRateColor(detail.metrics.mdd)} icon={Percent} />
        <MetricTile label="총 거래" value={`${detail.metrics.total_trades}건`} icon={Repeat} />
        <MetricTile label="최초 투입금" value={`${Math.round(detail.initial_capital).toLocaleString()}원`} />
        <MetricTile label="최종 금액" value={`${Math.round(detail.final_value).toLocaleString()}원`} />
      </div>

      <div className="mb-6">
        <MetricsGrid metrics={detail.metrics} />
      </div>

      <h2 className="mb-2 font-medium">가격 차트</h2>
      <PriceChart ohlcv={detail.ohlcv} trades={detail.trades} timeframe={detail.timeframe} backtestEnd={detail.end} />

      <h2 className="mt-6 mb-2 font-medium">거래 내역 ({detail.trades.length}건)</h2>
      {detail.trades.length === 0 ? (
        <p className="text-muted-foreground">거래 내역이 없습니다.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>진입</TableHead>
              <TableHead>청산</TableHead>
              <TableHead>수익률(%)</TableHead>
              <TableHead>매수가</TableHead>
              <TableHead>매도가</TableHead>
              <TableHead>수익금</TableHead>
              <TableHead>보유기간</TableHead>
              <TableHead>상태</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.trades.map((t, i) => (
              <TableRow key={i}>
                <TableCell>{formatDateTime(t.entryTime)}</TableCell>
                <TableCell>{formatDateTime(t.exitTime)}</TableCell>
                <TableCell className={returnRateColor(t.returnRate)}>{t.returnRate.toFixed(2)}</TableCell>
                <TableCell>{t.entryPrice.toLocaleString()}</TableCell>
                <TableCell>{t.exitPrice.toLocaleString()}</TableCell>
                <TableCell className={returnRateColor(t.pnl)}>{Math.round(t.pnl).toLocaleString()}</TableCell>
                <TableCell>{t.holdingPeriod}</TableCell>
                <TableCell>
                  {t.forceClosed ? (
                    <Badge
                      variant="secondary"
                      title={
                        detail.live_price_as_of
                          ? '매도 조건을 만족하지 못한 채 아직 보유 중입니다. 현재가로 재평가된 수익률입니다.'
                          : '매도 조건을 만족하지 못해 백테스트 종료 시점 종가로 평가된 상태입니다.'
                      }
                    >
                      보유중(기간종료)
                    </Badge>
                  ) : (
                    <Badge variant="outline">청산됨</Badge>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
```

기존에 있던 수동 5칸 요약(총 수익률/MDD/총 거래/최초 투입금/최종 금액)과 `MetricsGrid`가 중복 정의하던 "총 수익률/MDD/총 거래" 3개 타일을 통합해, 상단 요약은 `MetricTile`을 재사용하는 한 벌로만 남긴다(총 수익률은 강조를 위해 단독 행, 나머지 4개는 한 행).

- [ ] **Step 3: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 임의의 `run_id`에 대해 `/backtests/{runId}` 접속 → 상단 요약과 "성과 지표" 그리드에 각 지표별 아이콘이 보이는지, 라이브 재평가 안내 문구에 `Clock` 아이콘이 보이는지 확인.

- [ ] **Step 4: 커밋**

```bash
git add frontend/app/backtests/[runId]/page.tsx frontend/components/MetricTile.tsx
git commit -m "refactor: 백테스트 상세 페이지의 중복 metric tile 통합 및 아이콘 추가"
```

---

## Task 10: 분석 페이지 정비 (SegmentSizeCard)

**Files:**
- Modify: `frontend/components/SegmentSizeCard.tsx`

**Interfaces:** 없음 (독립 변경, props 시그니처 불변)

인벤토리 확인 결과 `app/analysis/page.tsx`, `app/ranking/page.tsx`는 이미 `Card` 기반으로 잘 정리되어 있어 구조 변경이 필요 없다 — 이번 태스크는 `SegmentSizeCard`의 이모지 아이콘화 1건으로 한정한다(랭킹 페이지의 순위 아이콘은 설계 스펙에서 "선택적 폴리시"로 명시된 항목이라 YAGNI 원칙에 따라 이번 범위에서 제외).

- [ ] **Step 1: "⚠ 유의종목" 이모지를 `AlertTriangle` 아이콘으로 교체**

`frontend/components/SegmentSizeCard.tsx`의 import 줄과 유의종목 표시 부분을 수정. 상단 import에 추가:

```tsx
import { AlertTriangle } from 'lucide-react';
```

52~55행의 유의종목 표시를 아래로 교체:

```tsx
                        {e.is_caution && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400">
                            <AlertTriangle className="size-3.5" />
                            유의종목
                          </span>
                        )}
```

- [ ] **Step 2: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 `/analysis` 접속 → 유의종목이 있는 경우 경고 아이콘이 이모지 대신 렌더링되는지 확인.

- [ ] **Step 3: 커밋**

```bash
git add frontend/components/SegmentSizeCard.tsx
git commit -m "refactor: SegmentSizeCard 유의종목 표시를 이모지에서 lucide 아이콘으로 교체"
```

---

## Task 11: 나머지 페이지 정비 (heatmap, ComboHistoryChart, PriceChart)

**Files:**
- Modify: `frontend/app/heatmap/page.tsx`
- Modify: `frontend/components/ComboHistoryChart.tsx`
- Modify: `frontend/components/PriceChart.tsx`
- Modify: `frontend/app/globals.css` (도메인 전용 색상 토큰 추가)

**Interfaces:** 없음 (각 파일 독립 변경)

`app/history/page.tsx`, `app/model-accuracy/page.tsx`는 인벤토리 확인 결과 변경할 내용이 없는 trivial wrapper이므로 이번 태스크에서 다루지 않는다.

- [ ] **Step 1: `globals.css`에 캔들/마커 전용 색상 토큰 추가**

`frontend/app/globals.css`의 `:root` 블록 마지막(`--sidebar-ring` 다음 줄)에 추가:

```css
    --price-up: oklch(0.577 0.245 27.325);
    --price-down: oklch(0.546 0.245 262.881);
    --marker-entry: oklch(0.546 0.245 262.881);
    --marker-exit: oklch(0.705 0.213 47.604);
    --marker-boundary: oklch(0.708 0 0);
```

`.dark` 블록 마지막(`--sidebar-ring` 다음 줄)에도 동일하게 추가 (다크 테마에서도 캔들/마커 색상은 브랜드 인식용 고정색이라 라이트와 동일 값 사용):

```css
    --price-up: oklch(0.577 0.245 27.325);
    --price-down: oklch(0.546 0.245 262.881);
    --marker-entry: oklch(0.546 0.245 262.881);
    --marker-exit: oklch(0.705 0.213 47.604);
    --marker-boundary: oklch(0.708 0 0);
```

- [ ] **Step 2: `PriceChart.tsx` — 캔들/마커 색상을 CSS 변수에서 읽어오도록 교체**

**중요:** `lightweight-charts`는 Canvas 2D로 렌더링하므로 `fillStyle`/`strokeStyle`에 해당하는 색상 옵션에 `var(--x)` 문자열을 그대로 넘기면 브라우저가 이를 해석하지 못한다(CSS 커스텀 프로퍼티는 CSS 컨텍스트에서만 resolve됨). 따라서 `getComputedStyle`로 실제 색상 문자열을 읽어와 전달해야 한다. 반면 아래쪽 범례(HTML `<span>`)는 인라인 `style`로 `var(--x)`를 직접 써도 정상 동작한다(일반 CSS이므로).

`frontend/components/PriceChart.tsx`의 `useEffect` 시작 부분(35행 근처, `if (!containerRef.current...) return;` 다음)에 아래 색상 resolve 로직을 추가하고, 이후 `upColor`/`downColor`/`wickUpColor`/`wickDownColor`/마커 `color` 값들을 하드코딩 hex 대신 이 변수로 교체:

```tsx
    const rootStyle = getComputedStyle(document.documentElement);
    const priceUp = rootStyle.getPropertyValue('--price-up').trim();
    const priceDown = rootStyle.getPropertyValue('--price-down').trim();
    const markerEntry = rootStyle.getPropertyValue('--marker-entry').trim();
    const markerExit = rootStyle.getPropertyValue('--marker-exit').trim();
    const markerBoundary = rootStyle.getPropertyValue('--marker-boundary').trim();

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 320,
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#d1d5db' },
      rightPriceScale: { borderColor: '#d1d5db' },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: priceUp,
      downColor: priceDown,
      borderVisible: false,
      wickUpColor: priceUp,
      wickDownColor: priceDown,
    });
```

이어지는 두 분기(`intradayMode` 참/거짓)의 마커 정의에서 `color: '#2563eb'`(매수) → `color: markerEntry`, `color: '#d97706'`(매도) → `color: markerExit`, `color: '#9ca3af'`(종료 경계) → `color: markerBoundary`로 각각 교체한다. 나머지 로직(정렬, 집계)은 변경하지 않는다.

범례 부분(135~149행)을 아래로 교체:

```tsx
      <div className="mb-2 flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-entry)' }} />
          매수 (B)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-exit)' }} />
          매도 (S)
        </span>
        <span className="flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: 'var(--marker-boundary)' }} />
          백테스트 종료
        </span>
      </div>
```

- [ ] **Step 3: `ComboHistoryChart.tsx` — select 교체 및 라인 색상 토큰화**

`frontend/components/ComboHistoryChart.tsx` 전체를 아래로 교체:

```tsx
'use client';

import { useEffect, useState } from 'react';
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts';
import { getCombos, getHistory } from '@/lib/api/eda';
import type { Combo, SweepResult } from '@/lib/types/eda';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';

function comboKey(c: Combo): string {
  return `${c.signal_set_name}|${c.market}|${c.timeframe}|${c.is_combined}`;
}

export default function ComboHistoryChart() {
  const [combos, setCombos] = useState<Combo[]>([]);
  const [selectedKey, setSelectedKey] = useState<string>('');
  const [history, setHistory] = useState<SweepResult[]>([]);

  useEffect(() => {
    getCombos().then((cs) => {
      setCombos(cs);
      if (cs.length > 0) setSelectedKey(comboKey(cs[0]));
    });
  }, []);

  useEffect(() => {
    const combo = combos.find((c) => comboKey(c) === selectedKey);
    if (!combo) return;
    let ignore = false;
    getHistory(combo).then((h) => {
      if (!ignore) setHistory(h);
    });
    return () => {
      ignore = true;
    };
  }, [selectedKey, combos]);

  if (combos.length === 0) {
    return <p className="text-muted-foreground">아직 스윕 데이터가 없습니다.</p>;
  }

  return (
    <div>
      <Select value={selectedKey} onValueChange={setSelectedKey}>
        <SelectTrigger className="mb-4 w-auto min-w-64">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {combos.map((c) => (
            <SelectItem key={comboKey(c)} value={comboKey(c)}>
              {c.signal_set_name}{c.is_combined ? '(혼합)' : ''} / {c.market} / {c.timeframe}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <ResponsiveContainer width="100%" height={320}>
        <LineChart data={history}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="swept_at" tick={{ fontSize: 11 }} />
          <YAxis tick={{ fontSize: 11 }} />
          <Tooltip />
          <Line type="monotone" dataKey="return_rate" stroke="var(--color-chart-1)" name="수익률(%)" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
```

(recharts의 `stroke`는 SVG 프리젠테이션 속성으로 렌더링되며 최신 브라우저는 여기서도 CSS 커스텀 프로퍼티를 resolve하므로 `var(--color-chart-1)` 문자열을 그대로 써도 된다 — `PriceChart`의 Canvas 렌더링과는 다른 경로임에 주의.)

- [ ] **Step 4: `heatmap/page.tsx` — "보기" 링크 통일 + sticky header + tabular-nums**

`frontend/app/heatmap/page.tsx` 전체를 아래로 교체:

```tsx
import Link from 'next/link';
import { Eye } from 'lucide-react';
import { getHeatmap } from '@/lib/api/eda';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { returnRateColor } from '@/lib/return-rate-color';

export default async function HeatmapPage() {
  const rows = await getHeatmap();

  return (
    <div>
      <h1 className="text-lg font-semibold mb-4">전략 × 코인 × 봉타입 수익률</h1>
      {rows.length === 0 ? (
        <p className="text-muted-foreground">아직 스윕 데이터가 없습니다. run_sweep()을 먼저 실행하세요.</p>
      ) : (
        <div className="max-h-[70vh] overflow-y-auto rounded-md border">
          <Table>
            <TableHeader className="sticky top-0 z-10 bg-background">
              <TableRow>
                <TableHead>전략</TableHead>
                <TableHead>코인</TableHead>
                <TableHead>봉타입</TableHead>
                <TableHead className="text-right">수익률(%)</TableHead>
                <TableHead className="text-right">Sharpe</TableHead>
                <TableHead className="text-right">MDD(%)</TableHead>
                <TableHead>스윕 시각</TableHead>
                <TableHead>상세</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={`${row.signal_set_name}-${row.market}-${row.timeframe}`}>
                  <TableCell>
                    {row.signal_set_name}
                    {row.is_combined && <Badge className="ml-2" variant="secondary">혼합</Badge>}
                  </TableCell>
                  <TableCell>{row.market}</TableCell>
                  <TableCell>{row.timeframe}</TableCell>
                  <TableCell className={`text-right tabular-nums ${returnRateColor(row.return_rate)}`}>
                    {row.return_rate?.toFixed(2) ?? '-'}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{row.sharpe?.toFixed(2) ?? '-'}</TableCell>
                  <TableCell className="text-right tabular-nums">{row.max_drawdown?.toFixed(2) ?? '-'}</TableCell>
                  <TableCell>{row.swept_at}</TableCell>
                  <TableCell>
                    <Button type="button" variant="link" size="sm" asChild className="px-0">
                      <Link href={`/backtests/${row.run_id}`}>
                        <Eye className="size-3.5" />
                        보기
                      </Link>
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 5: 브라우저 검증**

```bash
cd frontend && npm run dev
```

Playwright로 확인:
1. `/backtests/{runId}` — 가격 차트의 캔들 상승/하락 색, 매수/매도/종료 마커 색이 마이그레이션 전과 동일하게 보이는지(값은 같은 red/blue/amber/gray 계열이므로 시각적으로 동일해야 함)
2. `/history` — select로 조합 전환 시 라인 차트가 갱신되는지
3. `/heatmap` — "보기" 링크가 Eye 아이콘과 함께 렌더링되는지, 브라우저 창 높이를 줄였을 때 헤더가 sticky로 고정되는지

- [ ] **Step 6: 커밋**

```bash
git add frontend/app/heatmap/page.tsx frontend/components/ComboHistoryChart.tsx frontend/components/PriceChart.tsx frontend/app/globals.css
git commit -m "refactor: heatmap/ComboHistoryChart/PriceChart 색상 토큰화 및 아이콘/sticky header 적용"
```

---

## Task 12: 반응형/정보 밀도 최종 점검

**Files:**
- Modify (필요 시): 각 페이지 파일 — 이 태스크는 사전에 정해진 코드 변경이 아니라 아래 절차로 발견된 문제만 고친다.

**Interfaces:** 없음

- [ ] **Step 1: 좁은 뷰포트로 전 페이지 순회**

```bash
cd frontend && npm run dev
```

Playwright `browser_resize`로 뷰포트를 `375x812`(모바일 폭)로 설정한 뒤 아래 경로를 순서대로 `browser_navigate` + `browser_take_screenshot`:

`/`, `/backtests`, `/backtests/{임의 runId}`, `/analysis`, `/heatmap`, `/history`, `/ranking`, `/model-accuracy`

각 스크린샷에서 페이지 전체를 가로로 미는 수평 스크롤(콘텐츠가 뷰포트 밖으로 잘려 `body`에 가로 스크롤바가 생기는 현상)이 있는지 확인한다.

- [ ] **Step 2: 발견된 오버플로우 수정**

수평 오버플로우가 발견된 컨테이너에는 `Table` 컴포넌트가 이미 쓰고 있는 것과 동일한 패턴(`components/ui/table.tsx:9-12`의 `overflow-x-auto` 래퍼)을 적용한다. 예를 들어 `PortSetupForm.tsx`의 `grid grid-cols-[1fr_1fr_3fr]` 같은 고정 그리드가 375px에서 깨지면, 해당 grid 컨테이너 바깥을 `<div className="overflow-x-auto">...</div>`로 감싸거나 `grid-cols-[1fr_1fr_3fr]`에 `sm:` 접두사를 붙여 375px에서는 `grid-cols-1`(세로 쌓기)로 폴백시킨다 — 실제로 어떤 페이지에서 어떤 문제가 발견되는지는 Step 1의 스크린샷 결과에 따라 결정한다.

- [ ] **Step 3: 데스크톱 뷰포트로 복귀 후 최종 확인**

Playwright `browser_resize`를 `1280x800`으로 되돌리고, Step 2에서 수정한 페이지들이 데스크톱에서도 기존과 동일하게 보이는지(회귀 없음) 확인한다.

- [ ] **Step 4: 커밋**

```bash
git add -A
git commit -m "fix: 좁은 뷰포트에서의 레이아웃 오버플로우 수정"
```

(Step 1~2에서 수정할 문제가 전혀 발견되지 않았다면 이 태스크는 커밋 없이 "변경 사항 없음"으로 종료한다.)

---

## Self-Review 결과

- **스펙 커버리지**: design spec의 Phase 0(Task 1), Phase 1(Task 2), Phase 2-1(Task 4), Phase 2-2(Task 5·6·7), Phase 2-3(Task 8·9), Phase 2-4(Task 10), Phase 2-5(Task 11), Phase 3 다크모드(Task 4)/색상 토큰 통일(Task 5·6·7에 분산 반영)/반응형(Task 12)이 각각 대응하는 태스크에 매핑됨. `InfoTooltip` 중복 제거는 spec에 없던 선행 의존성이라 별도 Task 3으로 분리(Phase 1 산출물을 Phase 2-2/2-3보다 먼저 정리해야 하는 순서 문제 해결).
- **Placeholder 스캔**: "TBD"/"적절히 처리" 류 표현 없음. 다만 Task 2 이후 태스크들이 소비하는 shadcn 생성 컴포넌트의 정확한 export 이름은 CLI 실행 전에는 100% 확정할 수 없어, Global Constraints에 "표준 API로 작성하되 실제 생성 결과와 다르면 import만 조정" 원칙을 명시하고 각 소비 태스크에도 동일 caveat을 반복 기재함 — 이는 미확정 요구사항이 아니라 외부 생성 도구의 출력에 대한 정상적인 통합 리스크이므로 플레이스홀더로 보지 않는다.
- **타입/시그니처 일관성**: `CoinSelect`(Task 6)의 `sortMarkets`/`MarketSortKey`/`CoinSelectProps` 시그니처가 `PortSetupForm`(Task 5)과 일치. `StrategyConditionBuilder`(Task 7)의 최상위 export 시그니처 불변 확인. `MetricTile`(Task 3→9)의 `icon` prop이 옵셔널이라 Task 3 시점의 호출부(아이콘 없음)와 Task 9 시점의 호출부(아이콘 있음) 모두와 호환. `DeleteRunButton`(Task 8)의 `{ runId: string }` 시그니처 불변.
- **파일 간 의존 순서 확인**: Task 3(공용 InfoTooltip)이 Task 5·7·9보다 먼저 와야 하는데, Task 5·7·9는 각각 Task 3에서 만든 `InfoTooltip`을 그대로 재사용(Task 5는 직접 사용하지 않고 CoinSelect/StrategyConditionBuilder를 통해 간접 사용, Task 7이 직접 import)하도록 순서가 맞게 배치됨. Task 2(shadcn 프리미티브)가 Task 3·5·6·7·8·11보다 먼저 옴.
- **대상 파일 목록**: `frontend/{package.json,postcss.config.mjs,tailwind.config.ts(삭제)}`, `frontend/app/{globals.css,layout.tsx,page.tsx(무변경),backtests/page.tsx(무변경),backtests/[runId]/page.tsx,analysis/page.tsx(무변경),heatmap/page.tsx,history/page.tsx(무변경),ranking/page.tsx(무변경),model-accuracy/page.tsx(무변경)}`, `frontend/components/{NavTabs,ThemeToggle(신규),InfoTooltip(신규),PortSetupForm,CoinSelect,StrategyConditionBuilder,BacktestRunsTable,DeleteRunButton,MetricTile,SegmentSizeCard,ComboHistoryChart,PriceChart}.tsx`, `frontend/components/ui/{input,select,popover,command,dialog,alert-dialog,tooltip}.tsx(신규)`, `frontend/lib/ui-classes.ts`.
