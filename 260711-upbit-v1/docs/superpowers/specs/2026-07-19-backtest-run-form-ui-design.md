# 백테스트 실행 폼 UI/UX 베이스 설계

- 작성일: 2026-07-19
- 상태: 승인 대기 (사용자 리뷰 전)
- 선행 문서: `docs/superpowers/specs/2026-07-17-ondemand-backtest-run-design.md` (온디맨드 백테스트 실행 기능 스펙, Task1~2 백엔드 구현 완료, Task3~5 프런트엔드 미착수)

## 배경 및 목적

선행 스펙의 Task4(`BacktestRunForm` 컴포넌트)는 기능 동작 위주로만 설계되어 있고 시각 디자인이 없다(기본 HTML input + 최소 Tailwind 클래스). 사용자가 코인젠포트(coingenport.newsystock.com)의 백테스트 설정 화면을 레퍼런스로 제시하며, 실제 API 연동보다 **화면의 시각적 기본 베이스(레이아웃/컬러/컴포넌트 구성)를 먼저 완성**해 보고 싶어한다.

이 스펙은 선행 스펙의 기능 범위를 바꾸지 않고, `BacktestRunForm`의 **시각 디자인만** 다시 설계한다. 완성 후 Task3(API 클라이언트)이 준비되면 이 컴포넌트의 더미 데이터/제출 로직만 실제 API 호출로 교체한다.

## 스코프

- 레퍼런스에서 가져오는 것: 섹션형 카드 레이아웃(라벨 바 + 콘텐츠), 토글형 pill 버튼 스타일(전략 선택), 업비트 블루 강조색.
- 레퍼런스에서 **가져오지 않는 것**: 4단계 스텝 위저드(Step1~4 탭), 매수/매도 조건 빌더, 관심코인/제외코인 탭, 조건식 입력기 — 선행 스펙에서 범위 밖으로 명시된 기능이므로 UI도 만들지 않는다.
- 대상 필드는 선행 스펙과 동일하게 코인/봉타입/전략(다중 선택)/기간(시작~종료)/실행 버튼뿐이다.
- **범위 밖**: 실제 `GET /api/v1/eda/signals`, `POST /api/v1/backtests/run` 연동, 폼 유효성 검사 로직, 로딩/에러 상태의 실제 데이터 반영(레이아웃 자리만 잡아둠).

## 컬러 토큰 변경

`frontend/app/globals.css`의 `--primary`, `--primary-foreground`, `--ring`, `--sidebar-primary`, `--sidebar-ring`을 무채색(oklch 0 chroma)에서 업비트 블루 계열로 교체한다. 이 값은 `Button`(default variant), 포커스 링, 링크 등 **앱 전체 공통 컴포넌트**에 적용되며 히트맵/랭킹/추이/모델정확도 탭에도 함께 반영된다.

```css
:root {
  --primary: oklch(0.55 0.18 255);       /* 업비트 블루 계열, 근사치 */
  --primary-foreground: oklch(0.985 0 0);
  --ring: oklch(0.55 0.18 255 / 60%);
  --sidebar-primary: oklch(0.55 0.18 255);
  --sidebar-ring: oklch(0.55 0.18 255 / 60%);
}
.dark {
  --primary: oklch(0.72 0.16 255);       /* 다크모드용 밝기 상향 */
  --primary-foreground: oklch(0.145 0 0);
  --ring: oklch(0.72 0.16 255 / 60%);
  --sidebar-primary: oklch(0.72 0.16 255);
  --sidebar-ring: oklch(0.72 0.16 255 / 60%);
}
```

정확한 업비트 공식 브랜드 hex를 확인하는 대로 이 값만 교체하면 된다(다른 파일 변경 불필요).

## 화면 구성 (`BacktestRunForm`)

```
┌─ 백테스트 실행 ────────────────────────────────┐
│                                                 │
│ ┌─ 기본 설정 ─────────────────────────────────┐│ ← CardHeader: 블루 틴트 배경(bg-primary/10)
│ │ 코인          봉타입                          ││ ← CardContent: 2칼럼 select
│ │ [KRW-BTC ▾]   [일봉 ▾]                       ││
│ └───────────────────────────────────────────── ┘│
│                                                 │
│ ┌─ 전략 선택 ─────────────────────────────────┐│
│ │ (macd_cross) (rsi_zone) (sma_cross) (...)     ││ ← 토글 pill, 선택시 블루 배경/흰 텍스트
│ └───────────────────────────────────────────── ┘│
│                                                 │
│ ┌─ 운용 기간 ─────────────────────────────────┐│
│ │ [2026-04-19] ~ [2026-07-19]                   ││
│ │ 기간이 길고 봉타입이 짧을수록...(안내 문구)     ││
│ └───────────────────────────────────────────── ┘│
│                                                 │
│                              [실행] ← primary   │
│ (에러 영역: 항상 자리 차지, invisible로 숨김)     │
└─────────────────────────────────────────────────┘
```

- 카드 3개(`Card`/`CardHeader`/`CardContent` 재사용)를 세로로 쌓는다. `CardHeader`에 `bg-primary/10` 톤을 입혀 레퍼런스의 라벨 바 느낌을 낸다.
- 전략 선택: 기존 계획의 체크박스 목록 대신, 클릭 시 선택/해제되는 pill 버튼(`rounded-full border`, 선택 시 `bg-primary text-primary-foreground`)으로 표현. 더미 배열 `['macd_cross', 'rsi_zone', 'sma_cross', 'bollinger_band']` 사용.
- 기간: `<input type="date">` 두 개를 `~`로 연결, 하단에 `text-xs text-muted-foreground` 안내 문구.
- 하단 액션 바: 우측 `실행` 버튼(`Button` default variant, 이제 블루), 좌측에 안내 문구. 에러 텍스트 자리는 `<p className="invisible ...">`로 항상 렌더링해 실제 에러가 붙어도 레이아웃이 안 흔들리게 한다.

## 상호작용 범위 (정적 목업)

- 코인/봉타입 select, 전략 pill 토글, 날짜 입력은 `useState`로 실제 값이 바뀌는 살아있는 UI(리뷰 목적).
- `실행` 버튼 클릭은 API 호출 없이 `console.log`만 남긴다(또는 아무 동작 없음).
- 전략 목록은 하드코딩된 더미 배열이며 `GET /api/v1/eda/signals` 호출 없음.

## 대상 파일

- `frontend/app/globals.css` — 컬러 토큰 교체
- `frontend/components/BacktestRunForm.tsx` — 신규 작성(정적 목업)
- `frontend/app/backtests/page.tsx` — 플레이스홀더 대신 `<BacktestRunForm />` 렌더

기존 `/backtests/[runId]/page.tsx`, 백엔드, `lib/api/eda.ts`, `lib/types/eda.ts`는 이 스펙에서 변경하지 않는다(선행 스펙의 Task3~5 기능 구현 시 별도로 다룸).

## Self-Review 결과

- **스펙 커버리지**: 사용자가 승인한 범위(현재 필드만 재구성, 앱 전체 블루 강조색, 완전 정적 목업)를 컬러 토큰/화면 구성/상호작용 범위/대상 파일 각 섹션에서 다룸.
- **선행 스펙과의 정합성**: 필드 목록(코인/봉타입/전략/기간/실행)이 `2026-07-17-ondemand-backtest-run-design.md`와 동일함을 확인. 이 스펙은 그 문서의 Task4 산출물(`BacktestRunForm.tsx`)의 시각 디자인만 먼저 만드는 것이며, Task3(API 클라이언트)과의 연결은 범위 밖으로 명시.
- **범위 확인**: 스텝 위저드, 매수/매도 조건 빌더 등 레퍼런스의 기능적 요소는 선행 스펙에서 이미 범위 밖으로 정해진 것을 다시 확인하고 제외.
