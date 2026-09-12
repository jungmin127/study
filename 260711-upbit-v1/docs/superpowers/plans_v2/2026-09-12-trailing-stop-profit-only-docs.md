# 계단식 트레일링 스탑 "수익 확보 전용" 문서/가이드 정합화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `TRAILING_STOP_STEP_PCT`의 실제 동작(커밋 `9fae2cb`에서 이미 "본전부터
활성화" 정책으로 수정·AWS 배포 완료)을, 사용자가 개별 백테스트 전략을 만들 때
보는 설명/예시 3곳(백엔드 인디케이터 카탈로그, 프론트 가이드 문서, 프론트 예시
테이블 생성 로직)에 정확하게 반영한다.

**Architecture:** 계산 로직 자체(`engine/condition_tree.py`의
`trailing_stop_step_level()`)는 이미 수정 완료 상태라 건드리지 않는다. 이 플랜은
그 로직을 설명/시뮬레이션하는 세 표면(백엔드 카탈로그 문자열,
프론트 정적 가이드 텍스트, 프론트 예시 테이블 계산 로직)의 카피와 예시 계산을
새 정책에 맞게 고치는 순수 문서/표시 정합화 작업이다. 세 파일은 서로 의존하지
않으므로 각각 독립된 태스크로 분리한다.

**Tech Stack:** Python(FastAPI 카탈로그 문자열), TypeScript(프론트 가이드/예시
빌더 lib)

## Global Constraints

- 계산 로직(`engine/condition_tree.py`)은 이미 수정·배포 완료 상태이므로 이
  플랜에서 다시 수정하지 않는다 — 설명/예시만 그 로직에 맞춘다.
- 코드 주석/식별자는 기존 관례대로 한글 설명 + 영어 식별자 혼용.
- 각 태스크 완료 후 커밋 메시지 끝에 다음 트레일러를 반드시 포함한다:
  ```
  Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01NMNyorwXpZMaFgt9v862qQ
  ```
- 이 프로젝트는 항상 `main`에서 직접 작업하고, 각 태스크는 개별 커밋만 하며,
  전체 푸시는 마지막 태스크(3번) 완료 시 수행한다(프로젝트 CLAUDE.md 규칙).
- 프론트엔드는 기존 관례대로 자동화 테스트가 없다 — 구현 후 Playwright MCP로
  `/guide` 페이지에서 `TRAILING_STOP_STEP_PCT` 항목을 열어 텍스트/예시 테이블이
  올바르게 보이는지 육안 확인한다(각 태스크의 마지막 검증 단계에 명시).
- 참고 근거: `docs/superpowers/references/upbit-v1-trailing-stop-breakeven-arming-policy.md`
  는 없음 — 정책 근거는 커밋 `9fae2cb`와 `engine/condition_tree.py`의
  `trailing_stop_step_level()` docstring, 그리고 이 세션의 사용자 결정
  ("트레일링을 손절 용도로 쓰고 싶은 게 아니다", 2026-09-12)이다.

---

## File Structure

- **Modify: `backend/main.py`** — 인디케이터 카탈로그의 `TRAILING_STOP_STEP_PCT`
  항목 `description`/`example` 문자열(~line 494-501)
- **Modify: `frontend/lib/indicator-guide.ts`** — `TRAILING_STOP_STEP_PCT` 가이드
  항목의 `formula`/`thresholdExample` 문자열(~line 312-320)
- **Modify: `frontend/lib/indicator-example-builder.ts`** — `TRAILING_STOP_STEP_PCT`
  예시 테이블 생성 로직(~line 744-775), 손절선이 음수일 때 "비활성" 표시로 변경

---

### Task 1: `backend/main.py` — 인디케이터 카탈로그 설명 보강

**Files:**
- Modify: `backend/main.py:494-502`

**Interfaces:**
- Consumes: 없음(정적 문자열 변경)
- Produces: 없음(다른 태스크가 이 문자열에 의존하지 않음)

- [ ] **Step 1: 현재 문자열 확인**

`backend/main.py`에서 `"TRAILING_STOP_STEP_PCT"` 카탈로그 항목(494번째 줄 근처)을
연다. 현재 내용:

```python
    {
        "value": "TRAILING_STOP_STEP_PCT", "label": "계단식 트레일링 스탑 (%)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": "<=",
        "description": "포지션 진입 후 도달한 최고 수익률(%)을 추적해, 그 값이 임계값(계단 폭)의 "
            "배수를 넘을 때마다 손절선을 한 단계씩 끌어올립니다. 임계값은 계단 폭(step)이며, "
            "실제 손절선은 floor(최고수익률/step - 1)*step으로 계산됩니다.",
        "example": "임계값(step) 5를 넣으면: 최고수익률이 +5%를 넘으면 손절선 0%(본절), "
            "+10%를 넘으면 손절선 +5%, +15%를 넘으면 손절선 +10%로 따라 올라갑니다. "
            "현재 수익률이 그 손절선 이하로 내려오면 매도합니다.",
    },
```

- [ ] **Step 2: `description`/`example`을 새 정책 반영하도록 교체**

위 블록 전체를 다음으로 교체한다:

```python
    {
        "value": "TRAILING_STOP_STEP_PCT", "label": "계단식 트레일링 스탑 (%)", "category": "손익",
        "params": [], "sellOnly": True, "fixedOperator": "<=",
        "description": "포지션 진입 후 도달한 최고 수익률(%)을 추적해, 그 값이 임계값(계단 폭)의 "
            "배수를 넘을 때마다 손절선을 한 단계씩 끌어올립니다. 손절 용도가 아니라 이미 난 수익을 "
            "지키는 용도라, 최고 수익률이 아직 첫 계단도 못 넘었으면 이 조건 자체가 완전히 "
            "비활성화됩니다(원금 손실 구간에서는 발동하지 않고, 같은 조건그룹의 STOP_LOSS_PCT가 "
            "손절을 전담합니다). 임계값은 계단 폭(step)이며, 활성화된 뒤의 실제 손절선은 "
            "floor(최고수익률/step - 1)*step(항상 0% 이상)으로 계산됩니다.",
        "example": "임계값(step) 5를 넣으면: 최고수익률이 아직 +5% 미만이면 이 조건은 비활성 "
            "상태입니다. +5%를 넘으면 손절선 0%(본절), +10%를 넘으면 손절선 +5%, +15%를 넘으면 "
            "손절선 +10%로 따라 올라갑니다. 현재 수익률이 그 손절선 이하로 내려오면 매도합니다.",
    },
```

- [ ] **Step 3: 백엔드 재시작 없이 문자열 확인**

Run: `PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "from backend.main import CONDITION_INDICATOR_CATALOG; item = next(i for i in CONDITION_INDICATOR_CATALOG if i['value'] == 'TRAILING_STOP_STEP_PCT'); print(item['description']); print(); print(item['example'])"`

(카탈로그 리스트 변수명이 다르면 `backend/main.py`에서 494번째 줄을 담고 있는
실제 변수명을 먼저 확인해 그 이름으로 바꿔 실행할 것.)

Expected: "비활성화" 문구가 `description`과 `example` 양쪽에 모두 출력됨.

- [ ] **Step 4: 커밋**

```bash
git add backend/main.py
git commit -m "$(cat <<'EOF'
docs: 트레일링 스탑 카탈로그 설명에 본전부터 활성화 정책 반영

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMNyorwXpZMaFgt9v862qQ
EOF
)"
```

---

### Task 2: `frontend/lib/indicator-guide.ts` — 가이드 텍스트 보강

**Files:**
- Modify: `frontend/lib/indicator-guide.ts:312-320`

**Interfaces:**
- Consumes: 없음(정적 문자열 변경)
- Produces: 없음

- [ ] **Step 1: 현재 항목 확인**

`frontend/lib/indicator-guide.ts`의 `TRAILING_STOP_STEP_PCT` 항목(312번째 줄
근처) 현재 내용:

```typescript
  TRAILING_STOP_STEP_PCT: {
    meaning:
      '캔들 지표가 아니라 보유 포지션이 진입 후 지금까지 도달한 "최고 수익률(%)"을 기준으로, 그 최고 수익률이 임계값(계단 폭)의 배수를 넘을 때마다 손절선이 한 단계씩 따라 올라가는 지표입니다. STOP_LOSS_PCT처럼 손절선이 고정돼 있지 않습니다.',
    params: [],
    formula: '손절선(%) = (floor(최고 수익률 ÷ step) − 1) × step  (step = threshold)',
    thresholdExample:
      '매도 조건 전용(sellOnly), 연산자 "≤" 고정. threshold는 계단 폭(step, 보통 양수, 예: 5)입니다. 최고 수익률이 아직 첫 계단도 못 넘었으면 손절선이 음수로 계산돼 사실상 미발동 상태가 됩니다(같은 조건그룹의 STOP_LOSS_PCT가 먼저 걸리는 게 자연스러운 폴백). 예: step=5, 최고 수익률 +16%면 손절선은 +10% — 수익률이 +10% 이하로 떨어지는 순간 매도.',
    usage: 'STOP_LOSS_PCT/TAKE_PROFIT_PCT처럼 고정된 라인이 아니라, 크게 오른 뒤 그 이익을 얼마나 반납하면 청산할지를 계단식으로 관리하고 싶을 때 씁니다. 보통 STOP_LOSS_PCT와 OR로 함께 걸어, 트레일링이 발동하기 전까지는 기존 손절선이 안전망 역할을 하게 합니다.',
  },
```

주의: `thresholdExample`의 "손절선이 음수로 계산돼 사실상 미발동 상태가
됩니다"는 **옛 코드 기준으로도 부정확했던 문구**다(옛 코드는 음수 손절선에서도
실제로 발동했다 — 이번 정책 변경으로 비로소 진짜 비활성이 됨). 그대로 두면
사실과 다시 어긋나므로 아래처럼 명확하게 고친다.

- [ ] **Step 2: `formula`/`thresholdExample` 교체**

위 블록에서 `formula`와 `thresholdExample` 두 줄만 다음으로 교체한다(`meaning`/
`usage`는 그대로 둔다):

```typescript
    formula: '손절선(%) = (floor(최고 수익률 ÷ step) − 1) × step  (step = threshold, 단 계산값이 음수면 None = 비활성)',
    thresholdExample:
      '매도 조건 전용(sellOnly), 연산자 "≤" 고정. threshold는 계단 폭(step, 보통 양수, 예: 5)입니다. 최고 수익률이 아직 첫 계단도 못 넘었으면 이 조건은 완전히 비활성화됩니다(손절 용도가 아니라 수익 확보 전용이라, 원금 손실 구간에서는 발동하지 않고 같은 조건그룹의 STOP_LOSS_PCT가 손절을 전담합니다). 예: step=5, 최고 수익률 +16%면 손절선은 +10% — 수익률이 +10% 이하로 떨어지는 순간 매도. 최고 수익률이 아직 +5% 미만이면(첫 계단 미달) 이 조건은 매도를 일으키지 않습니다.',
```

- [ ] **Step 3: dev 서버로 확인**

Run: `cd frontend && npm run dev` (이미 떠 있으면 생략)

브라우저에서 `/guide` 페이지 → `TRAILING_STOP_STEP_PCT`("계단식 트레일링 스탑")
항목을 펼쳐 `formula`/`thresholdExample`에 "비활성화" 문구가 반영됐는지 확인한다.

- [ ] **Step 4: 커밋**

```bash
git add frontend/lib/indicator-guide.ts
git commit -m "$(cat <<'EOF'
docs: 트레일링 스탑 가이드 문서에 본전부터 활성화 정책 반영

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMNyorwXpZMaFgt9v862qQ
EOF
)"
```

---

### Task 3: `frontend/lib/indicator-example-builder.ts` — 예시 테이블 로직 수정

**Files:**
- Modify: `frontend/lib/indicator-example-builder.ts:744-775`

**Interfaces:**
- Consumes: 없음
- Produces: 없음(이 case 블록을 호출하는 다른 코드의 시그니처는 변경하지 않음 —
  반환 타입 `{ columns, rows, chart }` 구조 그대로 유지)

- [ ] **Step 1: 현재 로직 확인**

`frontend/lib/indicator-example-builder.ts`의 `case 'TRAILING_STOP_STEP_PCT':`
블록(744번째 줄 근처) 현재 내용:

```typescript
    case 'TRAILING_STOP_STEP_PCT': {
      const entry = 100000;
      const step = 5;
      const path = [100000, 106000, 109000, 115000, 103000];
      let peak = -Infinity;
      const rows = path.map((price, i) => {
        const returnPct = ((price - entry) / entry) * 100;
        peak = Math.max(peak, returnPct);
        const stopLevel = (Math.floor(peak / step) - 1) * step;
        return {
          bar: i,
          cells: {
            bars: String(i),
            price: n(price, 0),
            returnPct: `${(returnPct >= 0 ? '+' : '') + n(returnPct)}%`,
            peakPct: `${(peak >= 0 ? '+' : '') + n(peak)}%`,
            stopLevel: `${(stopLevel >= 0 ? '+' : '') + n(stopLevel)}%`,
          },
        };
      });
      return {
        columns: [
          { key: 'bars', label: '봉' },
          { key: 'price', label: '현재가' },
          { key: 'returnPct', label: '진입가 대비 수익률' },
          { key: 'peakPct', label: '최고 수익률(고점)' },
          { key: 'stopLevel', label: '손절선(step=5)' },
        ],
        rows,
        chart: { type: 'none' },
      };
    }
```

이 코드는 `stopLevel`이 음수일 때도 그대로 숫자를 보여줘서(예: bar 0에서
`peak=0` → `stopLevel=-5%`), 실제 런타임(음수면 비활성)과 다른 예시를 보여준다.
또한 `peak`의 초깃값이 `-Infinity`인데, 실제 시스템(`trading/db.py`의
`positions.peak_return_pct` 기본값 0, `trading/daemon.py`의
`local_peak_return_pct: float = 0.0`)은 고점을 0에서 시작한다 — 이 예시의 첫 봉
수익률이 우연히 0%라 결과는 같지만, 의미를 명확히 하기 위해 초깃값도 0으로
맞춘다.

- [ ] **Step 2: 로직 교체 — 음수면 "비활성" 표시**

위 블록 전체를 다음으로 교체한다:

```typescript
    case 'TRAILING_STOP_STEP_PCT': {
      const entry = 100000;
      const step = 5;
      const path = [100000, 106000, 109000, 115000, 103000];
      let peak = 0;
      const rows = path.map((price, i) => {
        const returnPct = ((price - entry) / entry) * 100;
        peak = Math.max(peak, returnPct);
        const rawStopLevel = (Math.floor(peak / step) - 1) * step;
        const stopLevel = rawStopLevel < 0 ? null : rawStopLevel;
        return {
          bar: i,
          cells: {
            bars: String(i),
            price: n(price, 0),
            returnPct: `${(returnPct >= 0 ? '+' : '') + n(returnPct)}%`,
            peakPct: `${(peak >= 0 ? '+' : '') + n(peak)}%`,
            stopLevel: stopLevel === null ? '비활성(수익 미확보)' : `${(stopLevel >= 0 ? '+' : '') + n(stopLevel)}%`,
          },
        };
      });
      return {
        columns: [
          { key: 'bars', label: '봉' },
          { key: 'price', label: '현재가' },
          { key: 'returnPct', label: '진입가 대비 수익률' },
          { key: 'peakPct', label: '최고 수익률(고점)' },
          { key: 'stopLevel', label: '손절선(step=5)' },
        ],
        rows,
        chart: { type: 'none' },
      };
    }
```

- [ ] **Step 3: 계산 결과 수동 검증**

이 path/step 조합으로 예상되는 각 봉의 값(구현이 맞는지 확인용):

| 봉 | 현재가 | 수익률 | 고점 | 손절선 |
|---|---|---|---|---|
| 0 | 100000 | +0% | +0% | 비활성(수익 미확보) |
| 1 | 106000 | +6% | +6% | +0% |
| 2 | 109000 | +9% | +9% | +0% |
| 3 | 115000 | +15% | +15% | +10% |
| 4 | 103000 | +3% | +15% | +10% |

- [ ] **Step 4: dev 서버로 확인**

브라우저에서 `/guide` 페이지 → `TRAILING_STOP_STEP_PCT` 항목의 예시 테이블을
펼쳐 위 표와 정확히 일치하는지 확인한다(특히 0번째 행이 "비활성(수익 미확보)"로
보이는지).

- [ ] **Step 5: 커밋 + 푸시**

```bash
git add frontend/lib/indicator-example-builder.ts
git commit -m "$(cat <<'EOF'
docs: 트레일링 스탑 예시 테이블이 본전부터 활성화 정책을 반영하도록 수정

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01NMNyorwXpZMaFgt9v862qQ
EOF
)"
git push
```

---

## 최종 완료 기준

- `backend/main.py`의 인디케이터 카탈로그(개별 백테스트 전략 조건 빌더가
  사용하는 설명)가 "본전부터 활성화" 정책을 정확히 설명한다.
- `frontend/lib/indicator-guide.ts`의 `/guide` 페이지 가이드 텍스트가 옛
  부정확한 문구("음수로 계산돼 사실상 미발동") 대신 실제 동작(완전 비활성)을
  정확히 설명한다.
- `frontend/lib/indicator-example-builder.ts`의 예시 테이블이 고점이 첫 계단을
  못 넘은 구간을 숫자(음수)가 아니라 "비활성" 상태로 명시적으로 보여준다.
- 세 곳 모두 `.claude/skills/regime-strategy-pipeline/SKILL.md`(이미 커밋
  `9fae2cb`에서 갱신됨)와 동일한 정책을 일관되게 설명한다.
- `engine/condition_tree.py`의 계산 로직 자체는 변경하지 않는다(이미 완료).
