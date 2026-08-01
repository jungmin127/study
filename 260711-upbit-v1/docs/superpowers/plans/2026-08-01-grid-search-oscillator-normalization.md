# Grid Search 오실레이터 정규화 확장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 지표 카탈로그에 정규화 지표 4개(BB_PERCENT_B/MACD_PPO/MACD_PPO_signal/ATR_PCT)를 추가하고, grid search의 오실레이터 범위를 5개→9개로 넓힌다. 기존 BB_upper/BB_middle/BB_lower/MACD_line/MACD_signal/ATR 카탈로그 항목은 그대로 유지(대체 아님).

**Architecture:** 엔진 레벨(`engine/indicators/*.py`, `engine/condition_tree.py`)에 지표 4개를 새로 등록하고, 카탈로그(`backend/main.py`)·지표 가이드(`frontend/lib/indicator-guide.ts`)·조건식 빌더 threshold 추천(`frontend/components/StrategyConditionBuilder.tsx`)에 반영한다. `scripts/grid_search.py`는 기존 `OSCILLATORS` 딕셔너리(지표당 period 1개 + low/high 3개 전제)를 `OSCILLATOR_SPECS`(파라미터 조합 리스트 + 단방향/양방향 플래그)로 리팩터링해 9개 오실레이터(그중 ATR_PCT만 양방향)를 지원한다.

**Tech Stack:** Python(backtrader — `engine.indicators`/`engine.condition_tree`), pytest, TypeScript(Next.js 프론트, 자동 테스트 없음 — 수동 검증).

## Global Constraints

- 스펙 문서: `docs/superpowers/specs/2026-08-01-grid-search-oscillator-normalization.md`.
- 기존 카탈로그 6개(BB_upper/BB_middle/BB_lower/MACD_line/MACD_signal/ATR)는 삭제/변경 금지 — 새 4개는 독립 항목으로 추가.
- MACD_PPO/MACD_PPO_signal은 `fast`/`slow`/`signal` 파라미터를 각각 `bt.indicators.PPO`의 `period1`/`period2`/`period_signal`로 매핑한다(카탈로그 파라미터 키는 기존 MACD_line/signal과 동일하게 `fast`/`slow`/`signal` 유지).
- grid search 그리드: BB_PERCENT_B는 `period`[10,14,20] + threshold 저[0.0,0.1,0.2]/고[0.8,0.9,1.0], 단방향(저=매수`<`/고=매도`>`). MACD_PPO/MACD_PPO_signal은 `fast`[12,16]×`slow`[26,32]×`signal`[9,12](8조합) + threshold 저[-3,-2,-1]/고[1,2,3], 단방향. ATR_PCT는 `period`[10,14,20] + threshold 풀[0.5,1,2,3,5,8], **양방향**(매수·매도 리스트 둘 다에 `<`/`>` 둘 다 반영).
- 전체 조합 수는 138(매수)×150(매도)=20,700개가 되어야 한다(스펙에서 계산 검증됨).
- dedup 대표 선택 기준(`_effective_period`)은 `period`/`k_period`가 없으면 `fast`+`slow`+`signal` 합을 쓰도록 확장한다.
- SKILL.md 등 `.claude/` 설정 파일은 한국어로 작성(기술 용어는 영어 유지).
- 프론트에는 테스트 프레임워크가 없다 — 신규 도입하지 않는다. `frontend/lib/indicator-guide.ts`/`StrategyConditionBuilder.tsx` 변경은 `npx tsc --noEmit` + 수동 브라우저 확인으로 검증.
- 지표 가이드(`frontend/lib/indicator-guide.ts`)에서 신규 4개 지표는 반드시 `INDICATOR_GUIDE`에 자체 항목이 있어야 한다 — `IndicatorGuideView.tsx`의 `IndicatorCard`가 `INDICATOR_GUIDE[item.value]`가 없으면 `return null`(빈 화면)을 반환하기 때문. "sub로 기재"라는 사용자 요구는, 이 항목들을 짧게(원본 지표를 참조하는 방식으로) 작성하고 원본 6개 항목의 `usage`에 정규화 버전을 안내하는 문장을 덧붙이는 방식으로 충족한다 — 신규 4개를 자체 항목 없이 생략하지 않는다.

---

### Task 1: 엔진 — `BB_PERCENT_B`/`ATR_PCT` 구현

**Files:**
- Modify: `engine/indicators/volatility.py`
- Modify: `engine/indicators/__init__.py`
- Modify: `engine/condition_tree.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Produces: `INDICATOR_FACTORY["BB_PERCENT_B"]`, `INDICATOR_FACTORY["ATR_PCT"]` — 각각 `create_bb_percent_b`/`create_atr_pct`(`data: bt.feeds.PandasData, **params -> bt.Indicator`). `get_indicator_value("BB_PERCENT_B"/"ATR_PCT", obj) -> float`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py` 끝에 추가:

```python
def test_bb_percent_b_matches_position_within_bands():
    top_values = _run_probe("BB_upper", {"period": 14})
    bot_values = _run_probe("BB_lower", {"period": 14})
    percent_b_values = _run_probe("BB_PERCENT_B", {"period": 14})
    df = make_oscillating_df()
    close = df["close"].iloc[-1]
    manual = (close - bot_values[-1]) / (top_values[-1] - bot_values[-1])
    assert abs(percent_b_values[-1] - manual) < 1e-6


def test_atr_pct_matches_atr_over_close():
    atr_values = _run_probe("ATR", {"period": 14})
    atr_pct_values = _run_probe("ATR_PCT", {"period": 14})
    df = make_oscillating_df()
    close = df["close"].iloc[-1]
    manual = atr_values[-1] / close * 100
    assert abs(atr_pct_values[-1] - manual) < 1e-6
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_indicators.py -k "bb_percent_b or atr_pct" -v`
Expected: FAIL — `KeyError: 'BB_PERCENT_B'` (INDICATOR_FACTORY에 아직 없음)

- [ ] **Step 3: `engine/indicators/volatility.py`에 create 함수 추가**

파일 끝에 추가:

```python
def create_bb_percent_b(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 20))
    return bt.indicators.BollingerBands(data, period=period, devfactor=2.0)


def create_atr_pct(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    period = int(params.get("period", 14))
    return bt.indicators.ATR(data, period=period)
```

- [ ] **Step 4: `engine/condition_tree.py::get_indicator_value`에 분기 추가**

`elif indicator_name == "BB_middle":` 블록 바로 다음에 추가:

```python
    elif indicator_name == "BB_PERCENT_B":
        top, bot = float(obj.top[0]), float(obj.bot[0])
        return (float(obj.data.close[0]) - bot) / (top - bot) if top != bot else 0.5
```

`elif indicator_name == "VPVR_VAL":` 블록(마지막 elif, `else:` 바로 앞) 다음에 추가:

```python
    elif indicator_name == "ATR_PCT":
        close = float(obj.data.close[0])
        return float(obj.atr[0]) / close * 100 if close else 0.0
```

- [ ] **Step 5: `engine/indicators/__init__.py`에 등록**

`from .volatility import create_atr, create_bb_lower, create_bb_middle, create_bb_upper` 줄을 아래로 교체:

```python
from .volatility import create_atr, create_atr_pct, create_bb_lower, create_bb_middle, create_bb_percent_b, create_bb_upper
```

`INDICATOR_FACTORY` 딕셔너리의 `"ATR": create_atr,` 줄 바로 다음에 추가:

```python
    "BB_PERCENT_B": create_bb_percent_b,
    "ATR_PCT": create_atr_pct,
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS (전체 통과, 신규 2개 포함)

- [ ] **Step 7: 커밋**

```bash
git add engine/indicators/volatility.py engine/indicators/__init__.py engine/condition_tree.py tests/test_indicators.py
git commit -m "feat: add BB_PERCENT_B and ATR_PCT normalized indicators"
```

---

### Task 2: 엔진 — `MACD_PPO`/`MACD_PPO_signal` 구현

**Files:**
- Modify: `engine/indicators/momentum.py`
- Modify: `engine/indicators/__init__.py`
- Modify: `engine/condition_tree.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: Task 1에서 등록한 `INDICATOR_FACTORY`/`get_indicator_value` 패턴(동일 구조 반복).
- Produces: `INDICATOR_FACTORY["MACD_PPO"]`, `INDICATOR_FACTORY["MACD_PPO_signal"]`. `get_indicator_value("MACD_PPO"/"MACD_PPO_signal", obj) -> float`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_indicators.py`에 추가:

```python
def test_macd_ppo_param_mapping_actually_changes_output():
    values_default = _run_probe("MACD_PPO", {"fast": 12, "slow": 26, "signal": 9})
    values_different = _run_probe("MACD_PPO", {"fast": 5, "slow": 10, "signal": 3})
    assert values_default[-1] != values_different[-1]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_indicators.py -k macd_ppo -v`
Expected: FAIL — `KeyError: 'MACD_PPO'`

- [ ] **Step 3: `engine/indicators/momentum.py`에 create 함수 추가**

파일 끝에 추가:

```python
def create_macd_ppo(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.PPO(data, period1=fast, period2=slow, period_signal=signal)


def create_macd_ppo_signal(data: bt.feeds.PandasData, **params) -> bt.Indicator:
    fast = int(params.get("fast", 12))
    slow = int(params.get("slow", 26))
    signal = int(params.get("signal", 9))
    return bt.indicators.PPO(data, period1=fast, period2=slow, period_signal=signal)
```

- [ ] **Step 4: `engine/condition_tree.py::get_indicator_value`에 분기 추가**

`elif indicator_name == "MACD_signal":` 블록 바로 다음에 추가:

```python
    elif indicator_name == "MACD_PPO":
        return float(obj.ppo[0])
    elif indicator_name == "MACD_PPO_signal":
        return float(obj.signal[0])
```

- [ ] **Step 5: `engine/indicators/__init__.py`에 등록**

`from .momentum import (` 블록을 아래로 교체:

```python
from .momentum import (
    create_cci,
    create_macd_line,
    create_macd_ppo,
    create_macd_ppo_signal,
    create_macd_signal,
    create_momentum_pct,
    create_rsi,
    create_stoch_d,
    create_stoch_k,
    create_williams_r,
)
```

`INDICATOR_FACTORY` 딕셔너리의 `"MACD_signal": create_macd_signal,` 줄 바로 다음에 추가:

```python
    "MACD_PPO": create_macd_ppo,
    "MACD_PPO_signal": create_macd_ppo_signal,
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_indicators.py -v`
Expected: PASS (전체 통과, 신규 1개 포함)

- [ ] **Step 7: 커밋**

```bash
git add engine/indicators/momentum.py engine/indicators/__init__.py engine/condition_tree.py tests/test_indicators.py
git commit -m "feat: add MACD_PPO and MACD_PPO_signal normalized indicators"
```

---

### Task 3: 카탈로그 4개 추가 (`backend/main.py`)

**Files:**
- Modify: `backend/main.py`

**Interfaces:**
- Consumes: Task 1-2에서 `INDICATOR_FACTORY`에 등록된 4개 지표 이름.
- 기존 테스트 `tests/test_backend.py::test_get_indicator_catalog_covers_all_registered_indicators`가 `INDICATOR_CATALOG`의 value 집합이 `INDICATOR_FACTORY.keys() | POSITION_RELATIVE_INDICATORS`와 정확히 일치해야 한다고 검증한다 — Task 1-2 완료 후 이 테스트가 이미 실패 상태(카탈로그에 4개가 아직 없어서)이므로, 이 Task는 새 테스트를 작성하지 않고 이 기존 테스트를 통과시키는 것으로 검증한다.

- [ ] **Step 1: 실패 확인**

Run: `pytest tests/test_backend.py -k test_get_indicator_catalog_covers_all_registered_indicators -v`
Expected: FAIL — `assert catalog_values == set(...)` 불일치(카탈로그에 BB_PERCENT_B 등 4개 없음)

- [ ] **Step 2: `INDICATOR_CATALOG`에 4개 항목 추가**

`backend/main.py`에서 `"value": "ATR", ...` 항목(현재 187번째 줄 부근, `},`로 끝나는 블록) 바로 다음, `"value": "OBV", ...` 항목 바로 앞에 추가:

```python
    {
        "value": "BB_PERCENT_B", "label": "%B (볼린저밴드 정규화)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 20}],
        "description": "종가가 볼린저밴드 내에서 어느 위치에 있는지를 0~1 사이 값으로 정규화합니다(하단=0, 상단=1). 코인 시세와 무관하게 항상 같은 범위입니다.",
        "example": "%B < 0.2면 하단 근접(과매도), %B > 0.8이면 상단 근접(과매수)으로 흔히 해석합니다.",
    },
    {
        "value": "MACD_PPO", "label": "PPO (MACD 정규화)", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "MACD Line을 장기 EMA 대비 비율(%)로 표현해 코인 가격과 무관하게 만든 지표입니다.",
        "example": "PPO = (EMA(12) − EMA(26)) / EMA(26) × 100. 0보다 크면 상승 모멘텀.",
    },
    {
        "value": "MACD_PPO_signal", "label": "PPO Signal", "category": "오실레이터",
        "params": [
            {"key": "fast", "label": "단기", "default": 12},
            {"key": "slow", "label": "장기", "default": 26},
            {"key": "signal", "label": "시그널", "default": 9},
        ],
        "description": "PPO를 다시 지수이동평균한 시그널 라인입니다.",
        "example": "PPO가 Signal을 상향 돌파하면 흔히 매수 신호로 봅니다.",
    },
    {
        "value": "ATR_PCT", "label": "ATR% (변동성 정규화)", "category": "오실레이터",
        "params": [{"key": "period", "label": "기간", "default": 14}],
        "description": "ATR을 현재가 대비 비율(%)로 표현해 코인마다 다른 가격 스케일을 제거한 지표입니다.",
        "example": "ATR% = ATR / 종가 × 100. 예: ATR%=2면 최근 변동폭이 종가의 2% 수준.",
    },
```

- [ ] **Step 3: 테스트 통과 확인**

Run: `pytest tests/test_backend.py -v`
Expected: PASS (전체 통과)

- [ ] **Step 4: 커밋**

```bash
git add backend/main.py
git commit -m "feat: add normalized oscillator entries to indicator catalog"
```

---

### Task 4: 지표 가이드 (`frontend/lib/indicator-guide.ts`)

**Files:**
- Modify: `frontend/lib/indicator-guide.ts`

**Interfaces:**
- Consumes: Task 3에서 카탈로그에 추가된 4개 지표의 `value`/`params` 키(`period`, `fast`/`slow`/`signal`)와 정확히 일치해야 함(`IndicatorGuideView.tsx`가 `item.params`와 `guide.params`를 키로 매칭).

- [ ] **Step 1: `MACD_line` 항목의 `usage`에 정규화 버전 안내 추가**

현재:
```ts
    usage: 'MACD_line과 MACD_signal을 매수/매도 조건에 각각 넣어, "Line이 Signal보다 크면 매수 유지, 작아지면 매도"처럼 두 지표를 짝지어 씁니다.',
```
다음으로 교체:
```ts
    usage: 'MACD_line과 MACD_signal을 매수/매도 조건에 각각 넣어, "Line이 Signal보다 크면 매수 유지, 작아지면 매도"처럼 두 지표를 짝지어 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 MACD_PPO를 대신 쓰세요.',
```

- [ ] **Step 2: `MACD_signal` 항목의 `usage`에 정규화 버전 안내 추가**

현재:
```ts
    usage: 'MACD_line ">" 조건과 MACD_signal 값을 서로 다른 블록에 넣기보다, 보통 "MACD_line > 0"과 "MACD_signal > 0"을 함께 걸어 상승 모멘텀 구간만 남기는 식으로 씁니다.',
```
다음으로 교체:
```ts
    usage: 'MACD_line ">" 조건과 MACD_signal 값을 서로 다른 블록에 넣기보다, 보통 "MACD_line > 0"과 "MACD_signal > 0"을 함께 걸어 상승 모멘텀 구간만 남기는 식으로 씁니다. 정규화 버전은 MACD_PPO_signal입니다.',
```

- [ ] **Step 3: `BB_upper` 항목의 `usage`에 정규화 버전 안내 추가**

현재:
```ts
    usage: '종가가 상단을 넘나드는지 자체보다는, "지금 가격이 상단 밴드 값보다 높은 절대 레벨"인지 필터로 쓰거나 ATR과 함께 변동성 국면을 가늠하는 보조 지표로 씁니다.',
```
다음으로 교체:
```ts
    usage: '종가가 상단을 넘나드는지 자체보다는, "지금 가격이 상단 밴드 값보다 높은 절대 레벨"인지 필터로 쓰거나 ATR과 함께 변동성 국면을 가늠하는 보조 지표로 씁니다. 코인 시세와 무관한 정규화 버전이 필요하면 BB_PERCENT_B를 대신 쓰세요.',
```

- [ ] **Step 4: `ATR` 항목의 `usage`에 정규화 버전 안내 추가**

현재:
```ts
    usage: '손절폭이나 돌파 매매 기준가를 "고정 %"가 아니라 "그 코인 특유의 변동성"에 맞춰 정할 때 씁니다.',
```
다음으로 교체:
```ts
    usage: '손절폭이나 돌파 매매 기준가를 "고정 %"가 아니라 "그 코인 특유의 변동성"에 맞춰 정할 때 씁니다. 코인마다 다른 가격 스케일을 제거한 정규화 버전이 필요하면 ATR_PCT를 대신 쓰세요.',
```

- [ ] **Step 5: 신규 4개 항목 추가**

`ATR: { ... },` 블록(Step 4에서 수정한 바로 그 블록) 바로 다음에 추가:

```ts
  BB_PERCENT_B: {
    meaning:
      '볼린저밴드 상단/하단 안에서 종가가 차지하는 위치를 0~1 사이 값으로 정규화한 지표입니다(하단=0, 상단=1). 계산에 쓰는 볼린저밴드 자체는 BB_upper/BB_lower와 같은 공식입니다 — 자세한 배경은 BB_upper 가이드를 참고하세요.',
    params: [{ key: 'period', role: '볼린저밴드(중간선/표준편차) 계산에 쓰는 봉 개수. BB_upper 등과 동일한 의미.' }],
    formula: '%B = (종가 − 하단) ÷ (상단 − 하단)',
    thresholdExample:
      '%B < 0.2 → 하단 근접(과매도). %B > 0.8 → 상단 근접(과매수). BB_upper/lower와 달리 코인 시세와 무관하게 항상 0~1 범위라 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다.',
    usage: 'BB_upper/BB_lower를 절대가격 필터로 쓰기 애매할 때(코인마다 가격 스케일이 달라서), 대신 이 지표로 "밴드 내 상대 위치"를 오실레이터처럼 씁니다.',
  },
  MACD_PPO: {
    meaning:
      'MACD Line을 장기 EMA 대비 비율(%)로 표현해 코인 가격과 무관하게 만든 지표입니다. backtrader PPO 지표의 ppo 서브라인을 읽습니다 — 계산 배경은 MACD_line 가이드를 참고하세요.',
    params: [
      { key: 'fast', role: '단기 EMA 기간. MACD_line의 fast와 동일한 의미.' },
      { key: 'slow', role: '장기 EMA 기간. MACD_line의 slow와 동일한 의미.' },
      { key: 'signal', role: 'PPO 자체를 다시 평활화하는 EMA 기간 — MACD_PPO_signal 지표가 이 값을 씁니다.' },
    ],
    formula: 'PPO = (EMA(fast) − EMA(slow)) ÷ EMA(slow) × 100',
    thresholdExample:
      'PPO > 0 → 상승 모멘텀 우세. MACD_line과 해석은 같지만 값이 %라서, 코인마다 가격 스케일이 달라도 같은 threshold(예: PPO > 1)를 여러 코인에 그대로 쓸 수 있습니다.',
    usage: 'MACD_line 대신 여러 코인에 동일한 threshold로 grid search/백테스트를 돌리고 싶을 때 씁니다.',
  },
  MACD_PPO_signal: {
    meaning:
      'PPO를 다시 signal기간 EMA로 평활화한 시그널 라인입니다. MACD_signal의 정규화 버전 — 계산 배경은 MACD_signal 가이드를 참고하세요.',
    params: [
      { key: 'fast', role: 'PPO 계산에 쓰는 단기 EMA 기간(같은 PPO 객체를 공유).' },
      { key: 'slow', role: 'PPO 계산에 쓰는 장기 EMA 기간.' },
      { key: 'signal', role: 'PPO를 평활화하는 기간.' },
    ],
    formula: 'PPO Signal = PPO의 signal기간 EMA',
    thresholdExample: 'PPO가 Signal을 상향 돌파하면 흔히 매수 신호로 봅니다. threshold는 보통 0 근처를 씁니다.',
    usage: 'MACD_PPO ">" 조건과 함께 "PPO > 0"과 "PPO Signal > 0"을 같이 걸어 상승 모멘텀 구간만 남기는 식으로 씁니다.',
  },
  ATR_PCT: {
    meaning:
      'ATR을 현재가 대비 비율(%)로 표현해 코인마다 다른 가격 스케일을 제거한 변동성 지표입니다 — 계산 배경은 ATR 가이드를 참고하세요.',
    params: [{ key: 'period', role: 'True Range를 평활할 봉 개수. ATR과 동일한 의미.' }],
    formula: 'ATR% = ATR ÷ 종가 × 100',
    thresholdExample:
      'ATR%=2면 최근 변동폭이 종가의 2% 수준입니다. ATR과 달리 이 값은 코인 가격과 무관해서 여러 코인에 같은 threshold를 그대로 쓸 수 있습니다. RSI처럼 과매수/과매도 방향이 있는 지표는 아니라서, "낮으면 매수/높으면 매도"라는 해석보다는 변동성 수준 자체를 필터로 보는 편이 자연스럽습니다.',
    usage: '변동성이 코인마다 크게 다를 때, ATR 대신 이 지표로 "변동성이 어느 수준을 넘었는지/밑돌았는지"를 여러 코인에 동일한 기준으로 씁니다.',
  },
```

- [ ] **Step 6: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 7: 커밋**

```bash
git add frontend/lib/indicator-guide.ts
git commit -m "feat: add normalized indicator entries to indicator guide"
```

---

### Task 5: 조건식 빌더 threshold 추천 (`frontend/components/StrategyConditionBuilder.tsx`)

**Files:**
- Modify: `frontend/components/StrategyConditionBuilder.tsx`

**Interfaces:**
- Consumes: Task 3에서 카탈로그에 추가된 4개 지표 이름(`BB_PERCENT_B`, `MACD_PPO`, `MACD_PPO_signal`, `ATR_PCT`).

- [ ] **Step 1: `OSCILLATOR_BOUNDS`에 `BB_PERCENT_B` 추가**

현재:
```ts
const OSCILLATOR_BOUNDS: Record<string, { low: number; high: number }> = {
  RSI: { low: 30, high: 70 },
  STOCH_K: { low: 20, high: 80 },
  STOCH_D: { low: 20, high: 80 },
  CCI: { low: -100, high: 100 },
  WILLIAMS_R: { low: -80, high: -20 },
  FEAR_GREED_CMC: { low: 20, high: 80 },
  VPIN: { low: 0.35, high: 0.55 },
};
```
다음으로 교체:
```ts
const OSCILLATOR_BOUNDS: Record<string, { low: number; high: number }> = {
  RSI: { low: 30, high: 70 },
  STOCH_K: { low: 20, high: 80 },
  STOCH_D: { low: 20, high: 80 },
  CCI: { low: -100, high: 100 },
  WILLIAMS_R: { low: -80, high: -20 },
  FEAR_GREED_CMC: { low: 20, high: 80 },
  VPIN: { low: 0.35, high: 0.55 },
  BB_PERCENT_B: { low: 0.2, high: 0.8 },
};
```

- [ ] **Step 2: `ZERO_CROSS_INDICATORS`에 `MACD_PPO`/`MACD_PPO_signal` 추가**

현재:
```ts
const ZERO_CROSS_INDICATORS = new Set(['MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM']);
```
다음으로 교체:
```ts
const ZERO_CROSS_INDICATORS = new Set(['MACD_line', 'MACD_signal', 'MARKET_TREND', 'MOMENTUM_PCT', 'KOREA_PREMIUM', 'MACD_PPO', 'MACD_PPO_signal']);
```

- [ ] **Step 3: `recommendedThreshold()`에 `ATR_PCT` 분기 추가**

현재:
```ts
  if (indicator === 'ATR') return currentPrice ? Math.round(currentPrice * 0.01) : 1;
  if (indicator === 'BTC_CORRELATION' || indicator === 'USDT_CORRELATION') {
```
다음으로 교체:
```ts
  if (indicator === 'ATR') return currentPrice ? Math.round(currentPrice * 0.01) : 1;
  if (indicator === 'ATR_PCT') return 2;
  if (indicator === 'BTC_CORRELATION' || indicator === 'USDT_CORRELATION') {
```

- [ ] **Step 4: 타입체크**

Run: `cd frontend && npx tsc --noEmit`
Expected: 에러 없음

- [ ] **Step 5: 수동 브라우저 확인**

`npm run dev`(localhost:3000)가 떠 있는 상태에서 `/`(백테스트 설정) 페이지의 조건식 빌더를 열어: (a) "오실레이터" 카테고리 드롭다운에 %B/PPO/PPO Signal/ATR%가 기존 6개와 함께 보이는지, (b) BB_PERCENT_B 선택 후 연산자를 `<`로 바꾸면 threshold가 0.2로, `>`로 바꾸면 0.8로 채워지는지, (c) MACD_PPO 선택 시 threshold가 0으로 채워지는지(ZERO_CROSS), (d) ATR_PCT 선택 시 threshold가 2로 채워지는지(currentPrice와 무관하게 고정값).

- [ ] **Step 6: 커밋**

```bash
git add frontend/components/StrategyConditionBuilder.tsx
git commit -m "feat: add threshold recommendations for normalized oscillators"
```

---

### Task 6: `scripts/grid_search.py` — 9개 오실레이터로 확장

**Files:**
- Modify: `scripts/grid_search.py`
- Modify: `tests/test_grid_search.py`

**Interfaces:**
- Consumes: Task 1-2에서 등록한 `BB_PERCENT_B`/`MACD_PPO`/`MACD_PPO_signal`/`ATR_PCT` 지표 이름과 params 키.
- Produces: `build_condition_grid()`가 여전히 `tuple[list[dict], list[dict]]`를 반환하지만 이제 매수 138개/매도 150개. `_effective_period(params)`가 `fast`+`slow`+`signal` 합도 처리(다른 함수 시그니처는 불변).

- [ ] **Step 1: 실패하는 테스트로 기존 테스트 갱신 + 신규 테스트 작성**

`tests/test_grid_search.py`의 `test_build_condition_grid_combo_counts` 함수를 아래로 교체:

```python
def test_build_condition_grid_combo_counts():
    buy_conditions, sell_conditions = build_condition_grid()
    assert len(buy_conditions) == 138
    assert len(sell_conditions) == 150
```

`test_build_condition_grid_uses_period_for_non_stochastic_oscillators` 함수를 아래로 교체:

```python
def test_build_condition_grid_uses_period_for_non_stochastic_oscillators():
    buy_conditions, _ = build_condition_grid()
    for indicator in ("RSI", "CCI", "WILLIAMS_R", "BB_PERCENT_B", "ATR_PCT"):
        blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        assert {b["params"]["period"] for b in blocks} == {10, 14, 20}
```

파일 끝(`test_dedup_reports_dup_count_for_group_size` 다음)에 추가:

```python
def test_build_condition_grid_bb_percent_b_thresholds():
    buy_conditions, sell_conditions = build_condition_grid()
    bb_buy = [b for b in buy_conditions if b["indicator"] == "BB_PERCENT_B"]
    bb_sell = [b for b in sell_conditions if b["indicator"] == "BB_PERCENT_B"]
    assert len(bb_buy) == 9
    assert {b["threshold"] for b in bb_buy} == {0.0, 0.1, 0.2}
    assert all(b["operator"] == "<" for b in bb_buy)
    assert len(bb_sell) == 9
    assert {b["threshold"] for b in bb_sell} == {0.8, 0.9, 1.0}
    assert all(b["operator"] == ">" for b in bb_sell)


def test_build_condition_grid_macd_ppo_param_grid_and_thresholds():
    buy_conditions, sell_conditions = build_condition_grid()
    for indicator in ("MACD_PPO", "MACD_PPO_signal"):
        buy_blocks = [b for b in buy_conditions if b["indicator"] == indicator]
        sell_blocks = [b for b in sell_conditions if b["indicator"] == indicator]
        assert len(buy_blocks) == 24
        assert len(sell_blocks) == 24
        param_combos = {tuple(sorted(b["params"].items())) for b in buy_blocks}
        assert len(param_combos) == 8
        assert {b["threshold"] for b in buy_blocks} == {-3, -2, -1}
        assert all(b["operator"] == "<" for b in buy_blocks)
        assert {b["threshold"] for b in sell_blocks} == {1, 2, 3}
        assert all(b["operator"] == ">" for b in sell_blocks)


def test_build_condition_grid_atr_pct_is_bidirectional():
    buy_conditions, sell_conditions = build_condition_grid()
    for conditions in (buy_conditions, sell_conditions):
        atr_blocks = [b for b in conditions if b["indicator"] == "ATR_PCT"]
        assert len(atr_blocks) == 36
        assert {b["operator"] for b in atr_blocks} == {"<", ">"}
        assert {b["threshold"] for b in atr_blocks} == {0.5, 1, 2, 3, 5, 8}
        assert {b["params"]["period"] for b in atr_blocks} == {10, 14, 20}


_MACD_SAME_TRADES = [{"entryTime": "2026-06-01T00:00:00", "exitTime": "2026-06-02T00:00:00"}]


def _make_macd_result(return_pct, fast, slow, signal, trades):
    return {
        "return_pct": return_pct,
        "buy_block": {
            "indicator": "MACD_PPO",
            "params": {"fast": fast, "slow": slow, "signal": signal},
            "operator": "<",
            "threshold": -2,
        },
        "sell_block": {"indicator": "RSI", "params": {"period": 14}, "operator": ">", "threshold": 70},
        "trades": trades,
        "final_value": 1_000_000 * (1 + return_pct / 100),
    }


def test_dedup_keeps_smallest_fast_slow_signal_sum_for_macd_style_params():
    results = [
        _make_macd_result(5.0, fast=16, slow=32, signal=12, trades=_MACD_SAME_TRADES),
        _make_macd_result(5.0, fast=12, slow=26, signal=9, trades=_MACD_SAME_TRADES),
    ]
    deduped = dedup_top_results(results, top_n=20)
    assert len(deduped) == 1
    assert deduped[0]["buy_block"]["params"] == {"fast": 12, "slow": 26, "signal": 9}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `pytest tests/test_grid_search.py -v`
Expected: FAIL — `test_build_condition_grid_combo_counts`(45/57 vs 138/150 기대값 불일치), 신규 테스트들은 `KeyError`나 빈 리스트 assert 실패.

- [ ] **Step 3: `scripts/grid_search.py`의 그리드 정의·생성 로직 교체**

`PERIOD_GRID = [10, 14, 20]`부터 `build_condition_grid()` 함수 끝(`return buy_conditions, sell_conditions`)까지 전체를 아래로 교체:

```python
PERIOD_GRID = [10, 14, 20]


def _period_grid(key: str = "period") -> list[dict]:
    return [{key: p} for p in PERIOD_GRID]


OSCILLATOR_SPECS: dict[str, dict] = {
    "RSI": {"param_grid": _period_grid(), "low": [20, 30, 40], "high": [60, 70, 80], "bidirectional": False},
    "STOCH_K": {"param_grid": _period_grid("k_period"), "low": [10, 20, 30], "high": [70, 80, 90], "bidirectional": False},
    "STOCH_D": {"param_grid": _period_grid("k_period"), "low": [10, 20, 30], "high": [70, 80, 90], "bidirectional": False},
    "CCI": {"param_grid": _period_grid(), "low": [-140, -100, -60], "high": [60, 100, 140], "bidirectional": False},
    "WILLIAMS_R": {"param_grid": _period_grid(), "low": [-90, -80, -70], "high": [-30, -20, -10], "bidirectional": False},
    "BB_PERCENT_B": {"param_grid": _period_grid(), "low": [0.0, 0.1, 0.2], "high": [0.8, 0.9, 1.0], "bidirectional": False},
    "MACD_PPO": {
        "param_grid": [
            {"fast": f, "slow": s, "signal": sig} for f in [12, 16] for s in [26, 32] for sig in [9, 12]
        ],
        "low": [-3, -2, -1],
        "high": [1, 2, 3],
        "bidirectional": False,
    },
    "MACD_PPO_signal": {
        "param_grid": [
            {"fast": f, "slow": s, "signal": sig} for f in [12, 16] for s in [26, 32] for sig in [9, 12]
        ],
        "low": [-3, -2, -1],
        "high": [1, 2, 3],
        "bidirectional": False,
    },
    "ATR_PCT": {"param_grid": _period_grid(), "low": [0.5, 1, 2, 3, 5, 8], "high": [], "bidirectional": True},
}

SELL_ONLY: dict[str, tuple[str, list[int]]] = {
    "STOP_LOSS_PCT": ("<=", [-3, -5, -7, -10]),
    "TAKE_PROFIT_PCT": (">=", [5, 10, 15, 20]),
    "HOLDING_PERIOD_BARS": (">=", [5, 10, 20, 40]),
}


def build_condition_grid() -> tuple[list[dict], list[dict]]:
    """오실레이터 9종 + 매도전용 3종의 매수/매도 ConditionBlock 그리드를 생성한다.

    Returns:
        (buy_conditions, sell_conditions) — 각각 ConditionBlock 딕셔너리 리스트
        ({"indicator": str, "params": dict, "operator": str, "threshold": float}).
    """
    buy_conditions: list[dict] = []
    sell_conditions: list[dict] = []

    for indicator, spec in OSCILLATOR_SPECS.items():
        for params in spec["param_grid"]:
            if spec["bidirectional"]:
                for t in spec["low"]:
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})
            else:
                for t in spec["low"]:
                    buy_conditions.append({"indicator": indicator, "params": params, "operator": "<", "threshold": t})
                for t in spec["high"]:
                    sell_conditions.append({"indicator": indicator, "params": params, "operator": ">", "threshold": t})

    for indicator, (operator, thresholds) in SELL_ONLY.items():
        for t in thresholds:
            sell_conditions.append({"indicator": indicator, "params": {}, "operator": operator, "threshold": t})

    return buy_conditions, sell_conditions
```

(`PERIOD_PARAM_KEY` 딕셔너리는 삭제됨 — `_period_grid("k_period")` 호출로 대체.)

- [ ] **Step 4: `_effective_period()` 확장**

현재:
```python
def _effective_period(params: dict) -> int:
    return params.get("period", params.get("k_period", 0))
```
다음으로 교체:
```python
def _effective_period(params: dict) -> int:
    if "period" in params:
        return params["period"]
    if "k_period" in params:
        return params["k_period"]
    return params.get("fast", 0) + params.get("slow", 0) + params.get("signal", 0)
```

- [ ] **Step 5: 모듈 docstring 갱신**

파일 상단 docstring을:
```python
"""
scripts/grid_search.py

'grid search' 스킬의 연산 엔진. 오실레이터 5종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) +
매도전용 3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드를
계산하고, 거래 시퀀스가 동일한 조합은 dedup한 뒤 상위 N개만 백테스트 결과에 저장한다.
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
"""
```
다음으로 교체:
```python
"""
scripts/grid_search.py

'grid search' 스킬의 연산 엔진. 오실레이터 9종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/
BB_PERCENT_B/MACD_PPO/MACD_PPO_signal/ATR_PCT — ATR_PCT만 양방향) + 매도전용 3종
(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(20,700개 조합)를
계산하고, 거래 시퀀스가 동일한 조합은 dedup한 뒤 상위 N개만 백테스트 결과에 저장한다.
Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-ETH --timeframe minutes60 \
     --capital 10000000 --start 2026-06-01 --end 2026-07-31 --top-n 20
"""
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `pytest tests/test_grid_search.py -v`
Expected: PASS (전체 통과)

- [ ] **Step 7: 커밋**

```bash
git add scripts/grid_search.py tests/test_grid_search.py
git commit -m "feat: expand grid search to 9 oscillators with bidirectional ATR_PCT"
```

---

### Task 7: `.claude/skills/grid-search/SKILL.md` 갱신

**Files:**
- Modify: `.claude/skills/grid-search/SKILL.md`

- [ ] **Step 1: 오실레이터 개수/조합 수/소요시간 문구 갱신**

현재:
```markdown
`grid search` 명령을 받으면 오실레이터 5종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) + 매도전용
3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(2,565개 조합)를
`scripts/grid_search.py`로 계산하고, 중복 거래를 제거한 상위 N개를 "백테스트 결과"에 저장한다.
```
다음으로 교체:
```markdown
`grid search` 명령을 받으면 오실레이터 9종(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/BB_PERCENT_B/
MACD_PPO/MACD_PPO_signal/ATR_PCT — ATR_PCT만 매수·매도 양방향) + 매도전용 3종
(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)의 전 교차 그리드(20,700개 조합)를
`scripts/grid_search.py`로 계산하고, 중복 거래를 제거한 상위 N개를 "백테스트 결과"에 저장한다.
```

- [ ] **Step 2: 예상 소요 시간 문구 갱신**

현재:
```markdown
2. 파싱 결과를 표로 정리해 사용자에게 보여주고 확인을 받는다. 이 표에는 반드시
   마켓코드/timeframe 코드/운용자금(원 단위 숫자)/시작일/종료일/상위N개가 포함되어야 한다.
   예상 소요 시간(약 9분, 2,565개 조합 기준)도 함께 안내한다.
```
다음으로 교체:
```markdown
2. 파싱 결과를 표로 정리해 사용자에게 보여주고 확인을 받는다. 이 표에는 반드시
   마켓코드/timeframe 코드/운용자금(원 단위 숫자)/시작일/종료일/상위N개가 포함되어야 한다.
   예상 소요 시간(1시간봉 기준 약 1.2시간, 20,700개 조합. 일봉처럼 캔들 수가 적은
   timeframe은 훨씬 빠름)도 함께 안내한다.
```

- [ ] **Step 3: 커밋**

```bash
git add .claude/skills/grid-search/SKILL.md
git commit -m "docs: update grid-search SKILL.md for 9-oscillator expansion"
```

---

### Task 8: 통합 검증

**Files:** 없음 (검증 전용 태스크)

- [ ] **Step 1: 전체 테스트 스위트 확인**

Run: `pytest -q`
Expected: 전부 통과(기존 245개 + Task 1/2/3/6에서 추가한 신규 테스트).

- [ ] **Step 2: 빠른 스모크 — 일봉으로 그리드 실행**

캔들 수가 적어 훨씬 빠르게(수십 분 내) 20,700개 조합 로직 전체를 검증할 수 있도록 일봉으로 실행한다:

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py --market KRW-LINK --timeframe days \
  --capital 10000000 --start 2026-01-01 --end 2026-08-01 --top-n 20
```

확인할 것:
- `[2]` 로그의 총 조합 수가 `20,700`인지.
- 에러 없이 끝까지 실행되고 `RESULT_JSON: {...}`이 출력되는지, `json.loads`로 파싱 가능한지.
- `RESULT_JSON.saved`에 `BB_PERCENT_B`/`MACD_PPO`/`MACD_PPO_signal`/`ATR_PCT`를 쓴 조합이 하나라도 섞여 있는지(9종이 실제로 다 그리드에 반영됐는지 육안 확인).
- ATR_PCT를 쓴 조합 중 매수 조건에 `>` 연산자가 쓰인 것도 있는지(양방향이 실제로 동작하는지 — 매수=`<`만 쓰였다면 버그).

- [ ] **Step 3: 백엔드/프론트 확인**

`uvicorn backend.main:app --reload --port 8000`과 `cd frontend && npm run dev`가 떠 있는 상태에서:
- `http://localhost:3000/backtests`에서 Step 2의 `[Grid]` 결과들을 확인, params 표기(예: `ATR_PCT(period=14)>3`)가 올바른지.
- `http://localhost:3000`의 지표 가이드 탭에서 볼린저밴드/MACD/ATR 항목에 정규화 버전 안내 문장이 보이는지, %B/PPO/PPO Signal/ATR% 항목을 클릭했을 때 빈 화면이 아니라 내용이 뜨는지(Global Constraints에서 언급한 `IndicatorCard`의 `return null` 회귀 확인).

- [ ] **Step 4: 결과 보고**

위 3단계가 모두 통과하면 "오실레이터 정규화 확장 구현 및 검증 완료"로 사용자에게 보고한다. 1시간봉 기준 실제 운영 소요 시간(약 1.2시간)이 필요하면 별도로 사용자에게 확인 후 실행할 것을 안내한다. 실패하는 항목이 있으면 어느 Task로 돌아가 고쳐야 하는지 명시한다.
