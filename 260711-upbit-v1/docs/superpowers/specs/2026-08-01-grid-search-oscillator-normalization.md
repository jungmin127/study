# Grid Search 오실레이터 정규화 확장 설계

- 작성일: 2026-08-01
- 상태: 승인 대기 (사용자 리뷰 전)
- 전제: [[2026-08-01-grid-search-skill-design.md]] (grid search 스킬, 이미 구현·배포됨)를 확장하는 후속 스펙.

## 목적

지표 카탈로그의 "오실레이터" 카테고리 11개 중 6개(BB_upper/BB_middle/BB_lower/MACD_line/MACD_signal/ATR)는 값이 코인 절대시세에 종속돼(BB=가격 자체, MACD=스케일이 가격 종속, ATR=변동성 크기) grid search 1단계에서 제외돼 있었다. 이 6개를 대체할 수 있는 정규화 지표(BB_PERCENT_B/MACD_PPO/MACD_PPO_signal/ATR_PCT — 항상 코인 시세와 무관하게 고정 범위/비율로 표현됨)를 새로 추가하고, grid search의 오실레이터 범위를 5개→9개로 넓힌다.

가격대(FIB/PIVOT/VPVR)·추세(SMA/EMA/WMA)·거래대금(TRADE_VALUE 등) 카테고리의 동일한 정규화 작업은 이번 범위 밖 — [[upbit-v1-catalog-normalization-roadmap]] 메모리에 남겨두고 각각 별도 세션에서 브레인스토밍부터 다시 시작한다.

## 결정된 사항 (사용자 승인)

### 기존 6개 카탈로그 항목 처리

BB_upper/BB_middle/BB_lower/MACD_line/MACD_signal/ATR은 그대로 유지한다(삭제/변경 없음). 새 정규화 지표 4개는 이 6개를 **대체하지 않고** 카탈로그에 독립 항목으로 추가한다 — 기존에 이 지표들을 쓰는 저장된 백테스트/전략이 깨지지 않게 하기 위함.

### 지표 가이드(`frontend/lib/indicator-guide.ts`) 표기 방식

정규화 버전은 별도 최상위 가이드 항목을 만들지 않고, 기존 볼린저밴드/MACD/ATR 가이드 항목의 설명 텍스트 안에 "정규화 버전이 필요하면 X를 쓰라"는 sub 문단으로 덧붙인다.

### 조건식 빌더 카탈로그 노출

BB_PERCENT_B/MACD_PPO/MACD_PPO_signal/ATR_PCT는 조건식 빌더 드롭다운에서 기존 6개와 동등하게 독립적으로 선택 가능한 항목으로 노출한다("오실레이터" 카테고리).

### grid search 오실레이터 범위: 5개 → 9개

기존 5개(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R) + 신규 4개(BB_PERCENT_B/MACD_PPO/MACD_PPO_signal/ATR_PCT) = 9개. 매도전용 3종(STOP_LOSS_PCT/TAKE_PROFIT_PCT/HOLDING_PERIOD_BARS)은 변경 없음.

### 방향성(매수=저값/매도=고값) 적용 범위

RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/BB_PERCENT_B — 이 6개는 교과서적으로 방향이 확립된(과매도=낮은값=매수, 과매수=높은값=매도) 지표라 기존과 동일하게 **매수 조건엔 저값+`<`만, 매도 조건엔 고값+`>`만** 사용한다. MACD_PPO/MACD_PPO_signal도 0 기준 교차 지표라 같은 저값=매수/고값=매도 구조를 유지한다.

ATR_PCT는 방향성 자체가 없는 순수 변동성 측정치(과매수/과매도 개념 없음)라, **양방향으로 탐색**한다 — threshold 값 전부를 `<`/`>` 연산자 양쪽 다 매수·매도 조건 양쪽에 사용한다(저변동성 진입/고변동성 청산뿐 아니라 그 반대 방향도 기계적으로 시도, "진정한 grid search"). 다른 8개 지표는 방향을 반대로 시도하지 않는다 — 이미 실증된 방향을 반대로 테스트하면 조합 수만 크게 늘고(6개까지 양방향으로 확장하면 전체 조합이 16배로 뛰어 약 10만 개, 6~7시간 예상) 수익 나는 조합을 찾을 확률은 낮다는 트레이드오프를 사용자가 확인하고 ATR_PCT 하나로 한정했다.

### MACD_PPO/MACD_PPO_signal 파라미터 그리드

fast/slow/signal 세 파라미터 전부 그리드로 돌린다(period 하나만 도는 다른 지표들과 다름). 밀도는 2×2×2(fast:[12,16], slow:[26,32], signal:[9,12], 8개 조합)로 확정 — 3×3×3(27개 조합, 전체 소요 약 4시간)과 1×1×1(기본값 고정, 약 23분) 대비 소요시간(약 1.2시간)과 탐색 폭의 균형을 고려해 선택.

## 설계

### 1. 엔진 구현 — 지표 4개 추가

| 지표 | 파일/함수 | 라인 소스 | 계산 |
|---|---|---|---|
| `BB_PERCENT_B` | `engine/indicators/volatility.py::create_bb_percent_b` | 새 `bt.indicators.BollingerBands` 인스턴스(기존 BB_upper 등과 별개 — 기존 BB_upper/middle/lower도 각자 독립 인스턴스를 만드는 기존 컨벤션과 동일) | `(종가 - bot) / (top - bot)` |
| `MACD_PPO` | `engine/indicators/momentum.py::create_macd_ppo` | 새 `bt.indicators.PPO` 인스턴스 (`period1`=fast, `period2`=slow, `period_signal`=signal로 매핑) | `.ppo[0]` |
| `MACD_PPO_signal` | `engine/indicators/momentum.py::create_macd_ppo_signal` | 위와 별개의 `PPO` 인스턴스(동일 컨벤션) | `.signal[0]` |
| `ATR_PCT` | `engine/indicators/volatility.py::create_atr_pct` | 새 `bt.indicators.ATR` 인스턴스 | `.atr[0] / 종가 × 100` |

`engine/condition_tree.py::get_indicator_value()`에 4개 분기를 추가한다. 종가는 `obj.data.close[0]`로 접근한다(backtrader 지표는 생성 시 받은 data feed를 `.data`로 계속 참조함 — 세션에서 실측 확인).

```python
elif indicator_name == "BB_PERCENT_B":
    top, bot = float(obj.top[0]), float(obj.bot[0])
    return (float(obj.data.close[0]) - bot) / (top - bot) if top != bot else 0.5
elif indicator_name == "MACD_PPO":
    return float(obj.ppo[0])
elif indicator_name == "MACD_PPO_signal":
    return float(obj.signal[0])
elif indicator_name == "ATR_PCT":
    close = float(obj.data.close[0])
    return float(obj.atr[0]) / close * 100 if close else 0.0
```

`engine/indicators/__init__.py`의 `INDICATOR_FACTORY`에 4개 등록:

```python
"BB_PERCENT_B": create_bb_percent_b,
"MACD_PPO": create_macd_ppo,
"MACD_PPO_signal": create_macd_ppo_signal,
"ATR_PCT": create_atr_pct,
```

`create_macd_ppo`/`create_macd_ppo_signal`은 카탈로그 파라미터 키(`fast`/`slow`/`signal`, 기존 MACD_line/signal과 동일한 이름)를 받아 내부적으로 `bt.indicators.PPO(data, period1=params.get("fast", 12), period2=params.get("slow", 26), period_signal=params.get("signal", 9))`로 변환한다.

### 2. 카탈로그 항목 4개 (`backend/main.py` INDICATOR_CATALOG)

기존 6개 옆에 독립 항목으로 추가, 카테고리는 동일하게 `"오실레이터"`:

| value | label | params |
|---|---|---|
| `BB_PERCENT_B` | %B (볼린저밴드 정규화) | `period`(20) |
| `MACD_PPO` | PPO (MACD 정규화) | `fast`(12) / `slow`(26) / `signal`(9) |
| `MACD_PPO_signal` | PPO Signal | `fast`(12) / `slow`(26) / `signal`(9) |
| `ATR_PCT` | ATR% (변동성 정규화) | `period`(14) |

description/example 예시:
- BB_PERCENT_B: "종가가 볼린저밴드 내에서 어느 위치에 있는지를 0~1 사이 값으로 정규화합니다(하단=0, 상단=1). 코인 시세와 무관하게 항상 같은 범위입니다." / "%B < 0.2면 하단 근접(과매도), %B > 0.8이면 상단 근접(과매수)으로 흔히 해석합니다."
- MACD_PPO: "MACD Line을 장기 EMA 대비 비율(%)로 표현해 코인 가격과 무관하게 만든 지표입니다." / "PPO = (EMA(12) − EMA(26)) / EMA(26) × 100. 0보다 크면 상승 모멘텀."
- MACD_PPO_signal: "PPO를 다시 지수이동평균한 시그널 라인입니다." / "PPO가 Signal을 상향 돌파하면 흔히 매수 신호로 봅니다."
- ATR_PCT: "ATR을 현재가 대비 비율(%)로 표현해 코인마다 다른 가격 스케일을 제거한 지표입니다." / "ATR% = ATR / 종가 × 100. 예: ATR%=2면 최근 변동폭이 종가의 2% 수준."

### 3. 지표 가이드 sub 기재 (`frontend/lib/indicator-guide.ts`)

기존 볼린저밴드/MACD/ATR 가이드 항목의 설명 텍스트 끝에 정규화 버전 안내 문장을 추가한다. 예: 볼린저밴드 항목 설명 끝에 "→ 코인 시세에 무관한 정규화 버전이 필요하면 %B(BB_PERCENT_B)를 사용하세요."

### 4. 조건식 빌더 threshold 추천 (`frontend/components/StrategyConditionBuilder.tsx`)

- `OSCILLATOR_BOUNDS`에 `BB_PERCENT_B: { low: 0.2, high: 0.8 }` 추가.
- `ZERO_CROSS_INDICATORS`에 `MACD_PPO`, `MACD_PPO_signal` 추가(기존 MACD_line/signal과 동일하게 0 기준).
- `recommendedThreshold()`에 `if (indicator === 'ATR_PCT') return 2;` 분기 추가(고정값, `currentPrice` 미사용).

### 5. `scripts/grid_search.py` 그리드 확장

**그리드 정의:**

| 지표 | 파라미터 그리드 | threshold |
|---|---|---|
| BB_PERCENT_B | period: [10,14,20] | 매수(`<`): 0.0, 0.1, 0.2 / 매도(`>`): 0.8, 0.9, 1.0 |
| MACD_PPO | fast:[12,16] × slow:[26,32] × signal:[9,12] = 8조합 | 매수(`<`): -3, -2, -1 / 매도(`>`): 1, 2, 3 |
| MACD_PPO_signal | 위와 동일 | 위와 동일 |
| ATR_PCT | period: [10,14,20] | 0.5, 1, 2, 3, 5, 8 — **양방향**(6값×`<`/`>` 둘 다, 매수·매도 리스트 둘 다에 12개씩 반영) |

**전체 조합 수 계산:**
- 매수: 6개 방향확립 지표(RSI/STOCH_K/STOCH_D/CCI/WILLIAMS_R/BB_PERCENT_B) 각 9개 = 54, MACD_PPO/PPO_signal 각 8파라미터×3threshold=24개 → 2종 48, ATR_PCT 3period×6threshold×2연산자=36. 합계 54+48+36=**138**.
- 매도: 위와 동일 구조 138 + 매도전용 3종×4threshold=12. 합계 **150**.
- 총 조합: 138×150=**20,700개**. 1시간봉 실측 속도(0.217초/조합, 2026-08-01 세션 실측) 기준 **약 1.2시간** 예상.

**코드 구조 변경**: 기존 `OSCILLATORS` 딕셔너리(지표당 period 1개 + low/high 3개씩이라는 단일 구조 전제)를 확장해, (a) 지표별로 서로 다른 파라미터 조합 리스트(단일 period 또는 fast×slow×signal), (b) 단방향/양방향 여부를 함께 표현하는 구조로 바꾼다. 예:

```python
OSCILLATOR_SPECS: dict[str, dict] = {
    "RSI": {"param_grid": [{"period": p} for p in PERIOD_GRID], "low": [20,30,40], "high": [60,70,80], "bidirectional": False},
    ...
    "MACD_PPO": {
        "param_grid": [{"fast": f, "slow": s, "signal": sig} for f in [12,16] for s in [26,32] for sig in [9,12]],
        "low": [-3,-2,-1], "high": [1,2,3], "bidirectional": False,
    },
    "ATR_PCT": {"param_grid": [{"period": p} for p in PERIOD_GRID], "low": [0.5,1,2,3,5,8], "high": [], "bidirectional": True},
}
```

`bidirectional=True`인 지표는 `low` 리스트를 매수/매도 양쪽에 `<`/`>` 둘 다로 전개한다(정확한 순회 로직은 플랜에서 코드로 확정). `build_condition_grid()`가 `PERIOD_PARAM_KEY` 매핑 대신 이 `param_grid`를 직접 순회하도록 리팩터링한다 — `STOCH_K`/`STOCH_D`의 `k_period` 처리도 이 구조 안에 자연스럽게 편입된다(`param_grid`에 이미 올바른 키로 들어감).

**dedup 대표 선택 기준 확장**: 기존 `_effective_period(params)`는 `period` 또는 `k_period` 키만 봤는데, MACD_PPO/PPO_signal의 `params`는 `fast`/`slow`/`signal` 키를 쓴다. 이 세 값의 합을 "반응 속도" 대리 지표로 써서 확장한다:

```python
def _effective_period(params: dict) -> int:
    if "period" in params:
        return params["period"]
    if "k_period" in params:
        return params["k_period"]
    return params.get("fast", 0) + params.get("slow", 0) + params.get("signal", 0)
```

(파라미터 없는 매도전용 3종은 기존과 동일하게 0.)

## 검증 절차 (구현 완료 후)

1. 엔진 단위: `BB_PERCENT_B`/`MACD_PPO`/`MACD_PPO_signal`/`ATR_PCT` 각각 합성 데이터로 값 범위가 예상대로인지(%B는 0~1 근방, ATR%는 항상 양수 등) 단위 테스트.
2. `PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/grid_search.py ...` 직접 실행 — **약 1.2시간 소요**, 실행 전 사용자에게 이 시간을 다시 한번 안내. 확인할 것: (a) 총 조합 수 20,700개, (b) `RESULT_JSON` 정상 출력, (c) ATR_PCT가 매수/매도 양쪽에 `<`/`>` 둘 다로 나타나는지, (d) dedup에 MACD_PPO 결과가 섞여도 정상 동작하는지.
3. 프론트 조건식 빌더에서 신규 4개 지표 선택·threshold 추천값 확인, "백테스트 결과"에서 params 표기(`ATR_PCT(period=14)>3` 형태) 확인.
4. 지표 가이드 페이지에서 볼린저밴드/MACD/ATR 항목에 정규화 버전 sub 문단이 보이는지 확인.

## 범위 밖

- 가격대(FIB_382/500/618, PIVOT_P/R1/S1, VPVR_POC/VAH/VAL) 정규화 — [[upbit-v1-catalog-normalization-roadmap]]로 이연, 별도 브레인스토밍 필요(공식 미정).
- 추세(SMA/EMA/WMA) 정규화 — 위와 동일하게 이연.
- 거래대금(TRADE_VALUE/TRADE_VALUE_SMA) 정규화 — 위와 동일하게 이연.
- ATR_PCT 외 8개 지표의 양방향(방향 반대) 탐색 — 조합 폭발 대비 실익이 낮다고 판단해 제외.
- 기존 BB_upper/BB_middle/BB_lower/MACD_line/MACD_signal/ATR 카탈로그 항목 삭제/변경 — 하위 호환을 위해 그대로 유지.

## Self-Review 결과

- **스펙 커버리지**: 브레인스토밍에서 확정한 모든 결정(6개 유지, 4개 독립 추가, 가이드 sub 표기, ATR_PCT만 양방향, MACD 2×2×2 그리드, 전체 조합 수 20,700개/약 1.2시간)이 반영됨.
- **내부 정합성**: "방향성 확립 지표는 반대 방향 안 씀"과 "ATR_PCT만 양방향" 규칙이 grid search 섹션의 실제 조합 수 계산과 일치하는지 재확인 — 138×150=20,700 계산에서 ATR_PCT의 36개(양방향)가 매수·매도 양쪽에 각각 반영되고, 나머지 8개 지표는 단방향으로만 반영됨을 확인함.
- **dedup 확장**: MACD_PPO의 파라미터 키가 기존 `_effective_period`에 안 걸리는 문제를 스펙 단계에서 미리 발견해 확장 로직을 포함시킴(지난 세션에 STOCH_K/STOCH_D의 `k_period` 문제를 플랜 작성 중에야 발견했던 것과 같은 종류의 문제라, 이번엔 스펙에서 먼저 잡음).
- **대상 파일 목록**: `engine/indicators/volatility.py`, `engine/indicators/momentum.py`, `engine/indicators/__init__.py`, `engine/condition_tree.py`, `backend/main.py`, `frontend/lib/indicator-guide.ts`, `frontend/components/StrategyConditionBuilder.tsx`, `scripts/grid_search.py`, `.claude/skills/grid-search/SKILL.md`(오실레이터 개수 표기 5→9, 조합 수/예상 시간 갱신).
