# 장세 판별 ML 마켓 확장(B) + UI 단순화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/regime` 페이지의 ML 장세판별 학습 대상을 3개 마켓(BTC/ETH/XRP)에서 14개로
확장하고, 더 이상 유효하지 않은 "언젠가 전체 마켓/타임프레임을 지원할 수도 있다"는
전제로 만들어졌던 UI(봉데이터 버튼, 전체마켓 코인선택)를 단순화한다.

**Architecture:** 학습/추론 파이프라인은 `engine/regime_ml_constants.py:TRAINING_MARKETS`
하나만 바뀌면 되는 구조(2026-08-27 리팩터로 이미 단일화됨) — 코드 변경 없이 상수만
갱신. 프론트는 같은 마켓 목록을 별도 상수(`TRAINED_MARKETS`)로 유지하되 기존 가드
테스트가 두 값의 동기화를 자동 검증한다. UI는 기존 `CoinSelect` 공용 컴포넌트를
건드리지 않고(다른 탭에 영향 주지 않기 위해) 이 페이지 전용의 작은 버튼 그룹으로
대체한다.

**Tech Stack:** FastAPI(Python), Next.js/React(TypeScript), LightGBM, pytest, tsc

## Global Constraints

- 확장 마켓 목록(정확히 이 14개, 순서 무관):
  `KRW-BTC, KRW-ETH, KRW-XRP, KRW-SOL, KRW-DOGE, KRW-LINK, KRW-ADA, KRW-XLM, KRW-TRX, KRW-TRUMP, KRW-BCH, KRW-BSV, KRW-QTUM, KRW-ALGO`
- 백엔드 테스트: `python -m pytest tests/<file> -v` (repo 루트에서 실행, `pytest.ini`의
  `pythonpath = .` 덕분에 별도 `PYTHONPATH` 설정 불필요).
- `scripts/train_regime_ml.py`를 pytest가 아니라 **직접** `python`으로 실행할 때는
  `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py`처럼
  `PYTHONPATH=.`를 반드시 붙일 것 — 안 붙이면 `ModuleNotFoundError: No module
  named 'backend'`가 난다(pytest.ini의 pythonpath 설정은 pytest 실행에만 적용됨).
- 프론트 타입 체크: `frontend/`에서 `npx tsc --noEmit`.
- **`npm run dev`가 이미 떠 있는 상태에서 절대 `npm run build`를 실행하지 말 것** —
  같은 `.next` 디렉터리를 공유해서 dev 서버가 `MODULE_NOT_FOUND`로 깨진다(확인된
  프로젝트 gotcha). 빌드 확인이 필요하면 `npx tsc --noEmit`으로 충분하다.
- `CoinSelect`(`frontend/components/CoinSelect.tsx`)는 팝오버가 열릴 때
  `getMarkets()`를 다시 호출해 `liveMarkets`를 전체 마켓으로 덮어쓴다 — 이 컴포넌트를
  이 작업에서 재사용하거나 수정하지 말 것(다른 탭에 영향).

---

## Task 1: 마켓 목록 14개로 확장 (백엔드 상수 + 프론트 상수, 동기화 유지)

**Files:**
- Modify: `engine/regime_ml_constants.py`
- Modify: `frontend/components/RegimeMlCurrentPrediction.tsx:12` (배열만 — 레이아웃은 Task 2)
- Modify: `tests/test_regime_ml_service.py:100-102`
- Test: `tests/test_regime_ml_service.py`, `tests/test_regime_ml_constants_frontend_sync.py`

**Interfaces:**
- Consumes: 없음(이 플랜의 첫 태스크)
- Produces: `engine.regime_ml_constants.TRAINING_MARKETS`(14개 리스트) —
  Task 4가 재학습 시 이 값을 그대로 씀. `RegimeMlCurrentPrediction.tsx`의
  `TRAINED_MARKETS`(named export, 14개 배열, 순서는 백엔드와 무관 — 동기화 테스트가
  정렬 후 비교함) — Task 3의 `RegimeDashboard.tsx`가 이 export를 그대로 import해서 씀.

- [ ] **Step 1: 새 마켓이 허용되는지 확인하는 실패 테스트 작성**

`tests/test_regime_ml_service.py`에서 `test_predict_current_ml_regime_rejects_untrained_market`
함수 바로 뒤에 추가:

```python
def test_predict_current_ml_regime_accepts_newly_expanded_market(tmp_path, monkeypatch):
    """KRW-SOL은 이번에 TRAINING_MARKETS에 새로 추가되는 마켓이다 — "학습 안 된
    마켓" ValueError가 아니라, 모델 파일이 없다는 FileNotFoundError가 나야 한다
    (마켓 검증은 통과했다는 뜻)."""
    monkeypatch.setattr(regime_ml_service, "MODEL_DIR", tmp_path)
    with pytest.raises(FileNotFoundError, match="학습된 ML 모델이 없습니다"):
        predict_current_ml_regime("KRW-SOL", "minutes60")
```

- [ ] **Step 2: RED 확인**

Run: `python -m pytest tests/test_regime_ml_service.py::test_predict_current_ml_regime_accepts_newly_expanded_market -v`

Expected: FAIL — `KRW-SOL`이 아직 `TRAINING_MARKETS`(3개)에 없어서
`ValueError: 이 모델은 KRW-BTC, KRW-ETH, KRW-XRP로만 학습되어 있습니다`가 나고,
`pytest.raises(FileNotFoundError)` 블록이 그 `ValueError`를 못 잡아서 테스트가
에러로 실패한다.

- [ ] **Step 3: 백엔드 마켓 상수 확장**

`engine/regime_ml_constants.py` 전체를 다음으로 교체:

```python
"""
engine/regime_ml_constants.py

장세 판별 ML 파이프라인 전체(학습+추론)가 공유하는 상수. 학습 스크립트
(scripts/train_regime_ml.py)와 추론 서비스(backend/regime_ml_service.py)가 서로
다른 마켓 목록을 갖게 되는 걸 막기 위해 단일 소스로 뽑았다. 프론트엔드
(frontend/components/RegimeMlCurrentPrediction.tsx)는 이 값을 API로 받지 않고
하드코딩된 배열을 따로 유지하며, tests/test_regime_ml_constants_frontend_sync.py가
드리프트를 감시한다.
"""
from __future__ import annotations

TRAINING_MARKETS = [
    "KRW-BTC", "KRW-ETH", "KRW-XRP",
    "KRW-SOL", "KRW-DOGE", "KRW-LINK", "KRW-ADA", "KRW-XLM", "KRW-TRX",
    "KRW-TRUMP", "KRW-BCH", "KRW-BSV", "KRW-QTUM", "KRW-ALGO",
]
```

- [ ] **Step 4: 새 테스트 GREEN 확인, 동기화 가드 테스트는 아직 RED인 것 확인**

Run: `python -m pytest tests/test_regime_ml_service.py::test_predict_current_ml_regime_accepts_newly_expanded_market tests/test_regime_ml_constants_frontend_sync.py -v`

Expected: 첫 번째는 PASS. `test_frontend_trained_markets_matches_backend_training_markets`는
아직 FAIL(백엔드 14개 vs 프론트 3개 불일치) — 다음 스텝에서 프론트를 맞추면 해결됨.

- [ ] **Step 5: 프론트 TRAINED_MARKETS 배열 갱신**

`frontend/components/RegimeMlCurrentPrediction.tsx:12`을 교체:

```typescript
export const TRAINED_MARKETS = [
  'KRW-BTC', 'KRW-ETH', 'KRW-XRP',
  'KRW-SOL', 'KRW-DOGE', 'KRW-LINK', 'KRW-ADA', 'KRW-XLM', 'KRW-TRX',
  'KRW-TRUMP', 'KRW-BCH', 'KRW-BSV', 'KRW-QTUM', 'KRW-ALGO',
];
```

- [ ] **Step 6: 기존 "미학습 마켓" 테스트를 계속 유효한 예시로 교체**

`KRW-DOGE`가 이제 학습 대상에 포함되므로, "미학습 마켓" 예시로 더 이상 못 쓴다.
`tests/test_regime_ml_service.py:100-102`(`test_predict_current_ml_regime_rejects_untrained_market`)를:

```python
def test_predict_current_ml_regime_rejects_untrained_market():
    with pytest.raises(ValueError, match="만 학습되어"):
        predict_current_ml_regime("KRW-ETC", "minutes60")
```

(`KRW-ETC`는 이번 14개 목록에 없음 — 앞으로도 없을 걸 확인하려면 이 플랜의
Global Constraints의 14개 목록과 대조.)

- [ ] **Step 7: 전체 GREEN 확인**

Run: `python -m pytest tests/test_regime_ml_service.py tests/test_regime_ml_constants_frontend_sync.py -v`

Expected: 전부 PASS (동기화 가드 포함).

- [ ] **Step 8: Commit**

```bash
git add engine/regime_ml_constants.py frontend/components/RegimeMlCurrentPrediction.tsx tests/test_regime_ml_service.py
git commit -m "feat: 장세 판별 ML 학습 마켓 3개 -> 14개 확장"
```

---

## Task 2: RegimeMlCurrentPrediction.tsx — ML 현재예측(좌) / 모델 성능(우) 2단 레이아웃

**Files:**
- Modify: `frontend/components/RegimeMlCurrentPrediction.tsx`

**Interfaces:**
- Consumes: Task 1에서 갱신된 `TRAINED_MARKETS`(이 태스크에서는 손대지 않음, 이미
  올바른 상태). 컴포넌트 자체의 `MlCurrentPrediction`/`modelPerformance` 타입,
  `data.probs`/`data.predicted_category`/`data.bar_time`/`data.model_trained_at`/
  `data.model_fold_index` 필드는 기존 그대로.
- Produces: 이 컴포넌트의 외부 API(`props: { market, timeframe }`, named export
  `TRAINED_MARKETS`)는 변경 없음 — Task 3의 `RegimeDashboard.tsx`가 그대로 씀.

이 작업엔 자동 테스트가 없다(TSX 레이아웃 전용 프로젝트라 별도 프론트 테스트
러너 없음, 기존 관례대로 `tsc --noEmit`으로 타입만 확인). RED/GREEN 대신
"변경 전 확인 -> 변경 -> tsc 확인" 순서로 진행한다.

- [ ] **Step 1: 현재 렌더링 블록(85~178줄) 구조 확인**

`data ?` 분기 안의 JSX가 다음 순서로 평평하게(flat) 나열되어 있는지 확인:
예측 카테고리+확신도 `div` → 확률 막대 `div` → 안내 문구 `p` → `모델 성능`
섹션 전체를 감싸는 `div.mt-4.border-t.pt-3`.

- [ ] **Step 2: 좌우 2단 그리드로 재구성**

`<>` ~ `</>`(85~178줄, `data ?` 분기의 렌더링 블록)를 다음으로 교체:

```tsx
<div className="grid grid-cols-1 gap-6 md:grid-cols-2">
  <div>
    <div className="mb-3 flex items-baseline gap-2">
      <span className="text-2xl font-bold">{data.predicted_category}</span>
      <span className="text-sm text-muted-foreground">
        확신도 {(data.probs[data.predicted_category] * 100).toFixed(1)}%
      </span>
    </div>
    <div className="mb-3 space-y-1.5">
      {CATEGORY_ORDER.map((label) => (
        <div key={label} className="flex items-center gap-2 text-xs">
          <span className="w-14 shrink-0 text-muted-foreground">{label}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full"
              style={{
                width: `${(data.probs[label] * 100).toFixed(1)}%`,
                backgroundColor: `var(${categoryVarName(label)})`,
              }}
            />
          </div>
          <span className="w-10 shrink-0 text-right tabular-nums">
            {(data.probs[label] * 100).toFixed(1)}%
          </span>
        </div>
      ))}
    </div>
    <p className="text-xs text-muted-foreground">
      {market} {formatTimeframe(timeframe)} 기준, {formatDateTime(data.bar_time)} 봉 데이터. (모델: {formatDateTime(data.model_trained_at)} 학습, fold {data.model_fold_index})
    </p>
  </div>
  <div className="border-t pt-3 md:border-l md:border-t-0 md:pl-6 md:pt-0">
    <h3 className="mb-1.5 flex items-center gap-1 text-xs font-semibold text-muted-foreground">
      모델 성능
      <InfoPopover>
        피어슨 상관계수(-1 ~ +1). 시점마다 예측 확률벡터를 카테고리별 기준점수(급상승 +0.35
        ~ 급하락 -0.35)로 가중평균한 &ldquo;기댓값&rdquo;과, 이후 n봉 동안의 실현수익률을 변동성으로
        정규화한 값 사이의 선형 상관관계입니다. +1에 가까울수록 예측 방향과 실제 방향이
        강하게 같이 움직이고, -1에 가까울수록 반대로 움직이며, 0에 가까우면 확률벡터에
        예측력이 거의 없다는 뜻입니다. 아래 hit-rate(예측 카테고리별 적중률)와 달리, 확률분포
        전체(강도 포함)를 반영하는 지표입니다.
      </InfoPopover>
    </h3>
    {modelPerformance ? (
      <>
        <div className="overflow-hidden rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>fold</TableHead>
                <TableHead className="text-right">train</TableHead>
                <TableHead className="text-right">test</TableHead>
                <TableHead className="text-right">상관계수</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {modelPerformance.folds.map((fold) => (
                <TableRow
                  key={fold.fold_index}
                  className={fold.fold_index === data.model_fold_index ? 'font-semibold' : ''}
                >
                  <TableCell>{fold.fold_index}</TableCell>
                  <TableCell className="text-right tabular-nums">{fold.n_train.toLocaleString()}</TableCell>
                  <TableCell className="text-right tabular-nums">{fold.n_test.toLocaleString()}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatCorrelation(fold.correlation)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          풀링 상관계수: {formatCorrelation(modelPerformance.pooled_correlation)}
        </p>
        <h4 className="mt-2 flex items-center gap-1 text-xs font-medium text-muted-foreground">
          카테고리별 hit-rate(전체 fold 합산)
          <InfoPopover>
            각 카테고리로 예측했을 때 실제로 그 카테고리가 맞았던 비율(적중건수/예측건수,
            전체 fold 합산 기준)입니다. 위 확신도·상관계수와 달리 예측이 맞았는지 여부만
            보는 단순 지표입니다.
          </InfoPopover>
        </h4>
        <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {CATEGORY_ORDER.map((label) => (
            <span key={label}>
              {label} {formatPct(modelPerformance.pooled_hit_rate[label])}
            </span>
          ))}
        </div>
      </>
    ) : (
      <p className="text-xs text-muted-foreground">성능 지표 없음(재학습 후 모델을 배포하면 표시됩니다)</p>
    )}
  </div>
</div>
```

(로딩/에러/미학습 마켓 안내 분기는 그대로 둔다 — 78~84줄은 변경 없음.)

- [ ] **Step 3: 타입 체크**

Run (`frontend/`에서): `npx tsc --noEmit`

Expected: 에러 없이 종료(exit 0).

- [ ] **Step 4: Commit**

```bash
git add frontend/components/RegimeMlCurrentPrediction.tsx
git commit -m "feat: ML 현재예측/모델 성능을 좌우 2단 레이아웃으로 재배치"
```

---

## Task 3: RegimeDashboard.tsx — 봉데이터 선택 제거, 코인 선택을 14개 버튼 그룹으로 축소

**Files:**
- Modify: `frontend/components/RegimeDashboard.tsx`

**Interfaces:**
- Consumes: `TRAINED_MARKETS`(Task 1에서 14개로 갱신됨, `RegimeMlCurrentPrediction.tsx`의
  named export), `sortMarkets(list: Market[], key: MarketSortKey, dir: 'asc'|'desc'): Market[]`
  (`frontend/components/CoinSelect.tsx`의 기존 export — 이 컴포넌트는 재사용하지만
  `CoinSelect` 위젯 자체는 쓰지 않는다), `getMarkets(): Promise<Market[]>`
  (`@/lib/api/eda`), `Market` 타입(`@/lib/types/eda`).
- Produces: 이 파일은 페이지 최상위 컴포넌트라 다른 코드가 import하지 않음.

- [ ] **Step 1: 파일 전체를 다음으로 교체**

`frontend/components/RegimeDashboard.tsx` 전체를:

```tsx
'use client';

import { useEffect, useState } from 'react';
import RegimeMlCurrentPrediction, { TRAINED_MARKETS } from '@/components/RegimeMlCurrentPrediction';
import RegimeMlAdminPanel from '@/components/RegimeMlAdminPanel';
import { sortMarkets } from '@/components/CoinSelect';
import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import { getMarkets } from '@/lib/api/eda';
import type { Market } from '@/lib/types/eda';

const TIMEFRAME = 'minutes60';

export default function RegimeDashboard() {
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketsError, setMarketsError] = useState<string | null>(null);
  const [market, setMarket] = useState('');

  useEffect(() => {
    getMarkets()
      .then((data) => {
        setMarkets(data);
        const trained = sortMarkets(
          data.filter((m) => TRAINED_MARKETS.includes(m.market)),
          'change_rate',
          'desc'
        );
        if (trained[0]) setMarket((prev) => prev || trained[0].market);
      })
      .catch((err) => setMarketsError(err instanceof ApiError ? err.message : '코인 목록을 불러오지 못했습니다.'));
  }, []);

  const trainedMarkets = sortMarkets(
    markets.filter((m) => TRAINED_MARKETS.includes(m.market)),
    'change_rate',
    'desc'
  );

  return (
    <div className="space-y-4">
      <div className="max-w-4xl space-y-4 rounded-xl border p-6 shadow-sm">
        <div>
          <label className="mb-1.5 block text-sm font-medium">코인 선택 (1시간봉 ML 학습 대상)</label>
          <div className="flex flex-wrap gap-2">
            {trainedMarkets.map((m) => (
              <Button
                key={m.market}
                type="button"
                variant={market === m.market ? 'default' : 'outline'}
                size="sm"
                onClick={() => setMarket(m.market)}
              >
                {m.korean_name}
              </Button>
            ))}
          </div>
          {marketsError && <p className="mt-1 text-xs text-destructive">{marketsError}</p>}
        </div>
      </div>
      {market && <RegimeMlCurrentPrediction market={market} timeframe={TIMEFRAME} />}
      <RegimeMlAdminPanel />
    </div>
  );
}
```

- [ ] **Step 2: 타입 체크**

Run (`frontend/`에서): `npx tsc --noEmit`

Expected: 에러 없이 종료(exit 0).

- [ ] **Step 3: 개발 서버로 눈으로 확인**

`npm run dev`가 이미 떠 있다면(Global Constraints 참고, `npm run build`는 쓰지
말 것) 브라우저에서 `/regime` 접속:
- 상단에 봉데이터 버튼 그룹이 없고, 코인 선택 버튼이 14개(한글명)로 나오는지
- 버튼을 눌러 마켓을 바꾸면 아래 "ML 현재예측"이 갱신되는지
- "ML 현재예측"과 "모델 성능"이 데스크톱 폭에서 좌우로 나란히 보이는지(Task 2 결과물)

- [ ] **Step 4: Commit**

```bash
git add frontend/components/RegimeDashboard.tsx
git commit -m "feat: /regime 봉데이터 선택 제거, 코인 선택을 학습된 14개로 축소"
```

---

## Task 4: 14개 마켓 기준 로컬 재학습 + 실측 + 최종 검증

**Files:**
- 코드 변경 없음(운영/검증 태스크) — 산출물은 `data/regime_ml_models/regime_ml_<timestamp>.txt`/`.json`(gitignore 대상, 커밋하지 않음)

**Interfaces:**
- Consumes: Task 1의 `engine.regime_ml_constants.TRAINING_MARKETS`(14개),
  `scripts/train_regime_ml.py`(기존 CLI, 변경 없음)
- Produces: 새 모델 사이드카 JSON의 `performance.pooled_correlation` — 이 태스크의
  검증 결과이자, 배포 여부를 사람이 판단할 근거. 코드를 소비하는 다음 태스크는 없음
  (이 플랜의 마지막 태스크).

- [ ] **Step 1: 재학습 전 기준값 기록**

기존(3마켓) 모델들의 풀링 상관계수 범위를 기준선으로 적어둔다(비교용):
`docs/superpowers/specs_v1/2026-08-29-regime-ml-market-expansion-ui-design.md`에
이미 "기존(3마켓, 약 0.07~0.08)"로 기록되어 있음 — 그대로 기준선으로 쓴다.

- [ ] **Step 2: 재학습 실행 및 소요시간 측정**

Run:

```bash
time (PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/train_regime_ml.py)
```

전체 콘솔 출력(fold별 상관계수, 풀링 상관계수, 카테고리별 hit-rate)을 그대로
기록해둔다. `time`이 출력하는 `real` 소요시간도 기록.

- [ ] **Step 3: 사이드카 JSON 확인**

Run:

```bash
python -c "import json, glob; f = sorted(glob.glob('data/regime_ml_models/regime_ml_*.json'))[-1]; print(f); print(json.dumps(json.load(open(f, encoding='utf-8'))['performance'], indent=2, ensure_ascii=False))"
```

Expected: 방금 학습된 모델의 `performance.folds`가 5개 fold를 담고,
`pooled_correlation` 값이 출력됨.

- [ ] **Step 4: Step 1의 기준선과 비교, 회귀 여부 판단**

`pooled_correlation`이 Step 1 기준선(0.07~0.08)보다 뚜렷하게 낮다면(예: 0.03
이하로 떨어지는 등) — **배포하지 말고, 실측 수치를 그대로 사용자에게 보고한다**
([[upbit-v1-dont-push-on-empirical-regression]] 원칙). 기준선과 비슷하거나
낫다면 계속 진행.

- [ ] **Step 5: 전체 테스트 스위트 + 타입 체크로 최종 확인**

Run:

```bash
python -m pytest tests/ -q
```

Expected: 전부 PASS(회귀 없음 확인).

Run (`frontend/`에서):

```bash
npx tsc --noEmit
```

Expected: 에러 없음.

- [ ] **Step 6: 결과 보고 (커밋 없음)**

이 태스크는 코드 변경이 없으므로 git commit 대상이 없다. 최종 보고 내용:
- 재학습 소요시간(3마켓 대비 배수)
- 새 풀링 상관계수 vs 기존 기준선
- 배포 여부는 사용자 판단으로 넘긴다(관리자 패널의 "배포" 버튼은 이미 구현되어
  있음 — 이 태스크에서 자동으로 누르지 않는다)
