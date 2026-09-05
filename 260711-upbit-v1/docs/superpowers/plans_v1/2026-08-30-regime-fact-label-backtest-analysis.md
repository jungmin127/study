# 장세 fact 라벨 기반 백테스트 성과 분석 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BTC/XLM minutes60 저장 백테스트 61건의 거래를 fact 라벨(하락/하락아님)로
재분류해, 장세별 전략 성과가 실제로 갈리는지 보여주는 1회성 콘솔 리포트 스크립트를
만든다.

**Architecture:** `scripts/analyze_regime_fact_performance.py` 단일 스크립트. 마켓별
fact 라벨 시계열(`compute_triple_barrier_labels` 재사용) → `list_backtest_runs()`로
저장된 거래 로드 → 각 거래의 entryTime을 라벨에 매칭 → 전체 풀링 비교표 + run별
랭킹(하락 상위 10 vs 하락아님 상위 10, 겹치는 개수) 출력. 새 백테스트/DB 쓰기/엔진
변경 없음 — 순수 읽기 전용 분석.

**Tech Stack:** Python, pandas. 기존 `engine.regime_ml_labels.compute_triple_barrier_labels`,
`engine.regime_math`, `engine.cache.list_backtest_runs`, `upbit_data_service.get_candles`만
재사용.

## Global Constraints

- 라벨링 파라미터는 프로덕션 상수를 그대로 쓴다: `half_life_bars = half_life_bars_for_timeframe("minutes60")`(=24), `n_bars = round(half_life_bars * N_MULTIPLIER)`(=60), `k = 6.25`(`scripts/train_regime_ml.py:BARRIER_K`와 동일 값을 이 스크립트에도 상수로 둔다 — import는 하지 않는다. train_regime_ml.py를 이 스크립트가 import하면 무관한 학습 모듈 의존성이 생기므로, 값만 복제하고 주석으로 출처를 남긴다)
- 대상: `KRW-BTC`, `KRW-XLM`의 `minutes60` 저장 백테스트만 (다른 마켓/타임프레임은 다루지 않음)
- 새 백테스트/그리드서치 실행 없음, DB 쓰기 없음, `engine/`·프론트엔드 변경 없음
- 자동화 테스트를 추가하지 않는다(스펙의 "테스트 전략" 절 — 1회성 분석 스크립트).
  대신 스크립트 실행 후 표본 거래 1건을 원본 캔들과 수동 대조해 라벨 매칭 정확성을
  검증한다
- 스크립트는 go/no-go를 자동 판정하지 않는다 — 리포트만 출력

---

### Task 1: 장세 fact 라벨 성과 분석 스크립트

**Files:**
- Create: `scripts/analyze_regime_fact_performance.py`

**Interfaces:**
- Consumes:
  - `engine.regime_ml_labels.compute_triple_barrier_labels(df: pd.DataFrame, half_life_bars: float, n_bars: int, k: float) -> pd.Series` (라벨 값은 `"하락"` / `"하락아님"` 문자열 또는 NaN)
  - `engine.regime_math.half_life_bars_for_timeframe(timeframe: str) -> float`, `engine.regime_math.N_MULTIPLIER: float`
  - `engine.cache.list_backtest_runs(strategy_name="ConditionTreeStrategy", limit=1000, market=None) -> list[dict]` — 각 dict는 `run_id`, `title`(str | None), `market`, `timeframe`, `trades`(list[dict], 각 trade는 `entryTime`(ISO 문자열, naive), `returnRate`(float, %) 포함) 키를 가진다
  - `upbit_data_service.get_candles(market: str, timeframe: str, start: datetime, end: datetime) -> pd.DataFrame` — `candle_time`(tz-aware UTC), `close` 컬럼 포함
- Produces: 콘솔 출력만 (다른 태스크 없음 — 이 플랜은 단일 태스크)

- [ ] **Step 1: 스크립트 파일 뼈대 + 라벨 시계열 빌더 작성**

`scripts/analyze_regime_fact_performance.py` 생성:

```python
"""
scripts/analyze_regime_fact_performance.py

장세 fact 라벨(compute_triple_barrier_labels) 기준으로, 저장된 BTC/XLM minutes60
백테스트 결과의 거래를 진입 시점 라벨로 재분류해 "장세별로 전략 성과가 실제로
갈리는가"를 확인한다. 새 백테스트는 돌리지 않고 engine.cache에 이미 저장된 결과만
재분석한다. 설계 문서:
docs/superpowers/specs_v1/2026-08-30-regime-fact-label-backtest-analysis-design.md

Run: PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/analyze_regime_fact_performance.py
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from engine.cache import list_backtest_runs
from engine.regime_math import N_MULTIPLIER, half_life_bars_for_timeframe
from engine.regime_ml_labels import compute_triple_barrier_labels
from upbit_data_service import get_candles

TIMEFRAME = "minutes60"
MARKETS = ["KRW-BTC", "KRW-XLM"]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc)
# scripts/train_regime_ml.py:BARRIER_K와 동일 값(2026-08-29 select_barrier_k.py로 결정,
# 프로덕션 학습 파이프라인이 실제로 쓰는 값). 이 스크립트는 학습 모듈을 import하지
# 않으므로 값만 복제한다.
BARRIER_K = 6.25
MIN_TRADES_FOR_RANKING = 5
TOP_N = 10


def build_label_lookup(market: str) -> pd.Series:
    """market의 minutes60 fact 라벨 시계열을, tz를 제거한 naive UTC candle_time을
    인덱스로 반환한다. tz를 제거하는 이유: 저장된 거래의 entryTime은
    engine/runner.py가 backtrader에 넘기기 전 tz를 벗겨낸 naive datetime의
    isoformat이라(원래 tz-aware UTC였던 값을 wall-clock 그대로 넘김), 같은 기준으로
    맞춰야 매칭된다."""
    df = get_candles(market, TIMEFRAME, START, END)
    half_life_bars = half_life_bars_for_timeframe(TIMEFRAME)
    n_bars = round(half_life_bars * N_MULTIPLIER)
    labels = compute_triple_barrier_labels(df, half_life_bars, n_bars, BARRIER_K)
    candle_time = pd.to_datetime(df["candle_time"]).dt.tz_localize(None)
    return pd.Series(labels.to_numpy(), index=candle_time).sort_index()


if __name__ == "__main__":
    for market in MARKETS:
        lookup = build_label_lookup(market)
        print(f"{market}: {len(lookup)}봉, 라벨 분포:\n{lookup.value_counts(dropna=False)}")
```

- [ ] **Step 2: 라벨 시계열 빌더 수동 검증**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/analyze_regime_fact_performance.py`

Expected: 두 마켓 각각 "하락"/"하락아님"/NaN 세 값의 개수가 출력되고, NaN이 대략
마지막 60개 내외(± 워밍업으로 인한 초반 결측 없음 — EWM은 첫 행부터 값이 나오므로
초반 NaN은 거의 없어야 함)로 소수만 나와야 한다. 만약 NaN이 훨씬 많거나 두 라벨
분포가 극단적으로 한쪽에 쏠려 있으면(예: 99% 하락아님) `BARRIER_K` 값을 재확인한다.
에러 없이 실행되고 분포가 이 범위 안에 들면 다음 단계로 진행.

- [ ] **Step 3: 거래 로드 + 라벨 매칭 함수 추가**

`scripts/analyze_regime_fact_performance.py`의 `if __name__ == "__main__":` 블록을
지우고 그 자리에 다음 함수들을 추가한다(파일 하단에 위치, `build_label_lookup` 다음):

```python
def label_for_entry(lookup: pd.Series, entry_time_str: str) -> object:
    """entryTime(ISO 문자열)에 가장 가까운 과거 캔들의 라벨을 반환한다. lookup 시작
    시각보다 이전이면 None(있을 수 없지만 방어적으로), 그 외엔 라벨 값(문자열 또는
    NaN)을 그대로 반환한다."""
    entry_time = pd.to_datetime(entry_time_str)
    pos = lookup.index.searchsorted(entry_time, side="right") - 1
    if pos < 0:
        return None
    return lookup.iloc[pos]


def load_labeled_trades(lookup_by_market: dict[str, pd.Series]) -> list[dict]:
    """저장된 BTC/XLM minutes60 백테스트 결과의 모든 거래를 진입 시점 라벨과 함께
    평평한 행 리스트로 반환한다. 라벨이 없는(NaN 또는 매칭 실패) 거래는 제외하고
    제외 건수를 출력한다."""
    rows: list[dict] = []
    excluded = 0
    for market in MARKETS:
        lookup = lookup_by_market[market]
        runs = [r for r in list_backtest_runs(market=market) if r["timeframe"] == TIMEFRAME]
        for run in runs:
            for trade in run["trades"]:
                label = label_for_entry(lookup, trade["entryTime"])
                if label is None or (isinstance(label, float) and pd.isna(label)):
                    excluded += 1
                    continue
                rows.append({
                    "run_id": run["run_id"],
                    "title": run["title"] or "(제목없음)",
                    "market": market,
                    "label": label,
                    "return_rate": trade["returnRate"],
                })
    print(f"라벨 없음으로 제외된 거래: {excluded}건")
    return rows
```

- [ ] **Step 4: 집계/출력 함수 + main 추가**

같은 파일에 이어서 추가:

```python
def print_pooled_comparison(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    print("\n=== 전체 풀링 비교 ===")
    print(f"{'라벨':>8} | {'거래수':>6} | {'승률':>7} | {'평균수익률':>10} | {'총수익기여':>10}")
    for label, group in df.groupby("label"):
        win_rate = (group["return_rate"] > 0).mean() * 100
        avg_return = group["return_rate"].mean()
        total_return = group["return_rate"].sum()
        print(f"{label:>8} | {len(group):>6} | {win_rate:6.1f}% | {avg_return:9.2f}% | {total_return:9.1f}%")


def print_run_ranking(rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    categories = sorted(df["label"].unique())

    per_run: dict[str, dict] = {}
    for (run_id, title, market), group in df.groupby(["run_id", "title", "market"]):
        stats = {"run_id": run_id, "title": title, "market": market}
        for label in categories:
            sub = group[group["label"] == label]
            stats[f"{label}_n"] = len(sub)
            stats[f"{label}_avg"] = sub["return_rate"].mean() if len(sub) else float("nan")
        per_run[run_id] = stats
    per_run_df = pd.DataFrame(per_run.values())

    rankings: dict[str, set] = {}
    for label in categories:
        eligible = per_run_df[per_run_df[f"{label}_n"] >= MIN_TRADES_FOR_RANKING]
        excluded_low_sample = per_run_df[per_run_df[f"{label}_n"] < MIN_TRADES_FOR_RANKING]
        ranked = eligible.sort_values(f"{label}_avg", ascending=False).head(TOP_N)
        rankings[label] = set(ranked["run_id"])
        print(f"\n=== '{label}' 진입 거래 평균수익률 상위 {TOP_N} (최소 {MIN_TRADES_FOR_RANKING}건) ===")
        for _, row in ranked.iterrows():
            print(f"  {row['market']:>8} | {row[f'{label}_avg']:7.2f}% ({int(row[f'{label}_n'])}건) | {row['title']}")
        print(f"  (표본 부족으로 랭킹 제외된 run: {len(excluded_low_sample)}개)")

    if len(categories) == 2:
        a, b = categories
        overlap = rankings[a] & rankings[b]
        print(f"\n'{a}' 상위 {TOP_N}와 '{b}' 상위 {TOP_N}의 겹치는 전략: {len(overlap)}개")


def main() -> None:
    lookup_by_market = {market: build_label_lookup(market) for market in MARKETS}
    rows = load_labeled_trades(lookup_by_market)
    print_pooled_comparison(rows)
    print_run_ranking(rows)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 전체 실행 + 표본 거래 수동 검증**

Run: `cd C:\Users\jungm\personal\study\260711-upbit-v1 && PYTHONPATH=. PYTHONIOENCODING=utf-8 python scripts/analyze_regime_fact_performance.py`

Expected: 에러 없이 "전체 풀링 비교" 표(하락/하락아님 각각 거래수·승률·평균수익률·
총수익기여) → "'하락' 진입 거래 평균수익률 상위 10" 목록 → "'하락아님' 진입 거래
평균수익률 상위 10" 목록 → 겹치는 전략 개수까지 순서대로 출력된다.

이어서 라벨 매칭이 실제로 맞는지 표본 1건을 수동 대조한다. 아래를 실행해 임의의
거래 하나를 뽑고:

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "
from scripts.analyze_regime_fact_performance import build_label_lookup, label_for_entry, MARKETS, TIMEFRAME, START, END
from engine.cache import list_backtest_runs
lookup = build_label_lookup('KRW-BTC')
runs = [r for r in list_backtest_runs(market='KRW-BTC') if r['timeframe'] == TIMEFRAME]
trade = runs[0]['trades'][0]
print('entryTime:', trade['entryTime'])
print('label:', label_for_entry(lookup, trade['entryTime']))
"
```

그다음 같은 진입 시각 전후 61봉(진입 시각부터 60봉 뒤까지)의 종가를 직접 찍어 손으로
검증한다:

```bash
PYTHONPATH=. PYTHONIOENCODING=utf-8 python -c "
import pandas as pd
from upbit_data_service import get_candles
from scripts.analyze_regime_fact_performance import MARKETS, TIMEFRAME, START, END
df = get_candles('KRW-BTC', TIMEFRAME, START, END)
entry_time = pd.to_datetime('<위에서_출력된_entryTime>')
idx = df[pd.to_datetime(df['candle_time']).dt.tz_localize(None) <= entry_time].index[-1]
window = df.iloc[idx:idx+61][['candle_time', 'close']]
entry_close = window['close'].iloc[0]
print(window.assign(pct=(window['close']/entry_close - 1) * 100))
"
```

출력된 pct 열을 보고 ±(BARRIER_K * 해당 시점 변동성)을 먼저 터치하는 방향이
`label_for_entry`가 반환한 라벨과 일치하는지 육안으로 확인한다(정확한 변동성 수치까지
재계산할 필요는 없다 — 명백히 하락 방향으로 큰 폭 하락한 윈도우인데 라벨이
"하락아님"으로 나오는 등 방향성 자체가 어긋나지 않는지 확인하는 수준). 어긋나면
`label_for_entry`/`build_label_lookup`의 tz 처리나 인덱스 정렬을 재점검한다.

- [ ] **Step 6: 커밋**

```bash
git add scripts/analyze_regime_fact_performance.py
git commit -m "$(cat <<'EOF'
feat: 장세 fact 라벨 기반 백테스트 성과 분석 스크립트 추가

BTC/XLM minutes60 저장 백테스트의 거래를 진입 시점 fact 라벨로 재분류해
장세별 전략 성과 차이를 확인하는 1회성 리포트 스크립트. 새 백테스트 실행 없음.
EOF
)"
```

---

## 완료 후

Task 1 완료 후 스크립트 실행 결과를 사용자에게 보여주고 "장세별 전략 성과가 실제로
갈리는가"에 대한 1차 결론을 함께 정리한다. 그다음 `docs/regime-ml-backlog.md`를
이번 세션 결과와 ②③ 착수 여부 판단으로 갱신한다(이 플랜의 범위 밖 — 사용자와 논의 후
별도로 갱신).
